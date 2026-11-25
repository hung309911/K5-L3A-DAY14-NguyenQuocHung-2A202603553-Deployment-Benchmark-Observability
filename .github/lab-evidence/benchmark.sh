#!/usr/bin/env bash
set -euo pipefail

out="$RUNNER_TEMP/benchmark"
mkdir -p "$out"
source_dir="$GITHUB_WORKSPACE/06-lab-complete/benchmark-observability/develop"
original="$RUNNER_TEMP/day14-upstream-develop"
cp -R "$source_dir" "$original"
git -C "$GITHUB_WORKSPACE" show 83e677f:06-lab-complete/benchmark-observability/develop/utils/mock_llm.py > "$original/utils/mock_llm.py"
cd "$original"
pip install -r requirements.txt
{
  date -u
  uname -a
  lscpu | sed -n '1,18p'
  free -h
  python --version
} > "$out/environment.txt"

pids=()
trap 'for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done' EXIT
start() {
  AGENT_VERSION="$1" PORT="$2" MOCK_SLOW_RATE="$3" MOCK_ERROR_RATE="$4" python app.py > "$out/$1-$2.log" 2>&1 &
  pid=$!
  pids+=("$pid")
  for i in {1..50}; do
    if curl -fsS -o /dev/null "http://127.0.0.1:$2/health"; then return 0; fi
    sleep 0.2
  done
  cat "$out/$1-$2.log"
  return 1
}
stop() {
  kill -TERM "$1"
  wait "$1" || true
}
load() {
  if ! python load_test.py --url http://127.0.0.1:8002 --requests 300 --concurrency "$1" --out "$out/$2"; then
    test -s "$out/$2"
  fi
}

start v2 8002 0.03 0.01
curl -fsS -H 'Content-Type: application/json' -d '{"question":"Docker container dùng để làm gì?"}' http://127.0.0.1:8002/ask > "$out/ask-example.json"
curl -fsS http://127.0.0.1:8002/metrics > "$out/metrics.txt"
for concurrency in 1 10 50 100; do
  load "$concurrency" "load_v2_c$concurrency.json"
done
stop "$pid"

for rate in 0 0.03 0.1; do
  start v2 8002 "$rate" 0.01
  load 20 "load_v2_slow_$rate.json"
  stop "$pid"
done

# Turn off random upstream failures for all evals so the change measures the KB.
start v1 8001 0.03 0
v1_pid=$pid
start v2 8002 0.03 0
v2_pid=$pid
python eval/run_eval.py --url http://127.0.0.1:8001 --out "$out/eval_v1.json" || test -s "$out/eval_v1.json"
python eval/run_eval.py --url http://127.0.0.1:8002 --baseline "$out/eval_v1.json" --out "$out/eval_v2.json" || test -s "$out/eval_v2.json"
stop "$v1_pid"
stop "$v2_pid"

cd "$source_dir"
start v2 8002 0.03 0
python eval/run_eval.py --url http://127.0.0.1:8002 --min-accuracy 1.0 --baseline "$out/eval_v2.json" --out "$out/eval_v2_fixed.json"
stop "$pid"
