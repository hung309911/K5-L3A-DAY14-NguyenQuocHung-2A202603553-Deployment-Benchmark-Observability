#!/usr/bin/env bash
set -euo pipefail

out="$RUNNER_TEMP/missions"
mkdir -p "$out"
wait_http() {
  for i in {1..45}; do
    if curl -fsS -o /dev/null "$1"; then return 0; fi
    sleep 1
  done
  return 1
}
base=http://127.0.0.1:8000
payload='{"question":"Hello"}'

pip install -r 04-api-gateway/production/requirements.txt -r 05-scaling-reliability/production/requirements.txt
(cd 01-localhost-vs-production/develop && exec python -m uvicorn app:app) > "$out/part1-server.log" 2>&1 &
pid=$!
wait_http "$base/"
curl -sSi -X POST "$base/ask?question=Hello" > "$out/part1-develop.txt"
kill -TERM "$pid"
wait "$pid" || true

(
  docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
  docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .
  docker images my-agent > "$out/part2-images.txt"
  docker image inspect --format '{{.Size}}' my-agent:develop my-agent:advanced > "$out/part2-image-bytes.txt"
  trap 'docker rm -f day14-develop >/dev/null 2>&1 || true' EXIT
  docker run -d --name day14-develop -p 8000:8000 my-agent:develop
  wait_http "$base/"
  curl -sSi -X POST "$base/ask?question=What%20is%20Docker%3F" > "$out/part2-develop.txt"
) || echo 'Failure in Docker image exercise' >&2

(
  set -e
  cd 02-docker/production
  touch .env.local
  trap 'docker compose -p k5-day14-docker logs --tail=25 > "$out/part2-stack.log"; docker compose -p k5-day14-docker down -v' EXIT
  docker compose -p k5-day14-docker up -d --build
  wait_http http://127.0.0.1/health
  {
    curl -sSi http://127.0.0.1/health
    printf '\n'
    curl -sSi -X POST -H 'Content-Type: application/json' -d '{"question":"Explain microservices"}' http://127.0.0.1/ask
  } > "$out/part2-nginx.txt"
) || echo 'Failure in Part 2 Compose' >&2

(cd 04-api-gateway/develop && AGENT_API_KEY=secret-key-123 exec python -m uvicorn app:app) > "$out/part4-develop.log" 2>&1 &
pid=$!
wait_http "$base/"
{
  curl -sSi -H 'Content-Type: application/json' -d "$payload" "$base/ask"
  printf '\n'
  curl -sSi -H 'X-API-Key: secret-key-123' -H 'Content-Type: application/json' -d "$payload" "$base/ask"
} > "$out/part4-api-key.txt"
kill -TERM "$pid"
wait "$pid" || true

(cd 04-api-gateway/production && exec python -m uvicorn app:app) > "$out/part4-production.log" 2>&1 &
pid=$!
wait_http "$base/health"
auth_response=$(curl -fsS -H 'Content-Type: application/json' -d '{"username":"student","password":"demo123"}' "$base/auth/token")
token=$(jq -er .access_token <<< "$auth_response")
{
  jq 'del(.access_token,.hint)' <<< "$auth_response"
  for i in {1..20}; do
    printf '\nRequest %s\n' "$i"
    curl -sSi -H "Authorization: Bearer $token" -H 'Content-Type: application/json' -d "$payload" "$base/ask"
  done
} > "$out/part4-jwt.txt"
kill -TERM "$pid"
wait "$pid" || true

(cd 05-scaling-reliability/develop && exec python -m uvicorn app:app) > "$out/part5-develop.log" 2>&1 &
pid=$!
wait_http "$base/health"
{
  curl -sSi "$base/health"
  printf '\n'
  curl -sSi "$base/ready"
} > "$out/part5-probes.txt"
curl -sSi -X POST "$base/ask?question=Long%20task" > "$out/part5-shutdown.txt" &
request_pid=$!
sleep 0.05
kill -TERM "$pid"
wait "$request_pid" || true
wait "$pid" || true

(
  set -e
  cd 05-scaling-reliability/production
  touch .env.local
  trap 'docker compose -p k5-day14-scaling down -v' EXIT
  docker compose -p k5-day14-scaling up -d --build --scale agent=3
  wait_http http://127.0.0.1:8080/health
  python test_stateless.py | tee "$out/part5-stateless.txt"
  replica=$(docker compose -p k5-day14-scaling ps -q agent | head -n 1)
  docker stop "$replica"
  python test_stateless.py | tee "$out/part5-after-stop.txt"
)
