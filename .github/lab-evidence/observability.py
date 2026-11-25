"""Run the Docker lab and capture observations from actual request traffic."""

import csv
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from playwright.sync_api import TimeoutError as BrowserTimeout
from playwright.sync_api import sync_playwright

COMPOSE = ["docker", "compose", "-p", "k5-day14-observability"]
PROM = "http://127.0.0.1:9090"
JAEGER = "http://127.0.0.1:16686"
GRAFANA = "http://127.0.0.1:3000"
HEALTH = "http://127.0.0.1:8080/health"
OUT = Path(os.environ["RUNNER_TEMP"]) / "observability"
RESULTS = Path("benchmark-results")
SCREENSHOTS = OUT / "screenshots"
OBSERVATIONS = OUT / "observations.json"
OUT.mkdir(exist_ok=True)
SCREENSHOTS.mkdir(exist_ok=True)
RESULTS.mkdir(exist_ok=True)
# Locust writes to this bind mount as a different Linux user.
RESULTS.chmod(0o777)
observations = {}


def save():
    OBSERVATIONS.write_text(json.dumps(observations, indent=2, ensure_ascii=False) + "\n")


def compose(*args, **kwargs):
    return subprocess.run([*COMPOSE, *args], check=True, text=True, **kwargs)


def read(url):
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def wait_for(probe, timeout=50):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if result := probe():
                return result
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise TimeoutError(f"No result within {timeout} seconds")


def shot(page, url, filename, text=None):
    page.goto(url, wait_until="domcontentloaded")
    if text:
        page.get_by_text(text, exact=False).first.wait_for(timeout=25_000)
    page.wait_for_timeout(1500)
    page.screenshot(path=SCREENSHOTS / filename, full_page=True)


def locust(name, users, rate, duration):
    file = (OUT / f"{name}.log").open("w")
    try:
        return subprocess.Popen(
            [
                *COMPOSE, "run", "--rm", "--no-deps", "locust", "-f", "/mnt/locust/locustfile.py",
                "--host", "http://nginx", "--headless", "-u", str(users), "-r", str(rate),
                "-t", duration, "--csv", f"/mnt/results/{name}",
            ],
            stdout=file, stderr=subprocess.STDOUT, text=True,
        )
    finally:
        file.close()


def finish_scaling(process, name):
    code = process.wait(timeout=180)
    stats = RESULTS / f"{name}_stats.csv"
    if not stats.exists():
        raise RuntimeError(f"Locust exit {code} without CSV. Check {name}.log")
    with stats.open() as source:
        aggregate = list(csv.DictReader(source))[-1]
    if aggregate["Name"] != "Aggregated" or int(aggregate["Request Count"]) == 0:
        raise RuntimeError(f"Locust recorded no traffic: {aggregate}")
    shutil.copyfile(stats, OUT / stats.name)


def agent_events():
    logs = compose("logs", "--no-color", "--no-log-prefix", "agent", capture_output=True).stdout
    return [json.loads(line) for line in logs.splitlines() if line.startswith("{")]


def trace_spans(trace_id):
    url = f"{JAEGER}/api/v3/traces/{trace_id}"
    with urlopen(url, timeout=5) as response:
        chunks = [json.loads(line) for line in response if line.strip()]
    return [
        span
        for chunk in chunks
        for resource in chunk.get("result", chunk).get("resourceSpans", [])
        for scope in resource["scopeSpans"]
        for span in scope["spans"]
    ]


def save_trace(page, entry, kind):
    trace_id = entry["trace_id"]
    spans = wait_for(lambda: trace_spans(trace_id))
    model_span = next(s for s in spans if s["name"].startswith("chat "))
    start = min(int(s["startTimeUnixNano"]) for s in spans)
    end = max(int(s["endTimeUnixNano"]) for s in spans)
    span_duration = int(model_span["endTimeUnixNano"]) - int(model_span["startTimeUnixNano"])
    page.goto(f"{JAEGER}/trace/{trace_id}", wait_until="domcontentloaded")
    model = page.get_by_text(model_span["name"], exact=True).first
    model.wait_for(timeout=25_000)
    model.click()
    if kind == "error":
        try:
            page.get_by_text("Logs", exact=False).last.click(timeout=3000)
        except BrowserTimeout:
            pass
    page.wait_for_timeout(1000)
    page.screenshot(path=SCREENSHOTS / f"jaeger-{kind}-trace.png", full_page=True)
    observations[f"{kind}_trace"] = {
        "trace_id": trace_id,
        "duration_ms": (end - start) / 1_000_000,
        "span": model_span["name"],
        "span_ms": span_duration / 1_000_000,
        "span_events": model_span.get("events", []),
        "matching_log": entry,
    }
    save()


def tracing(page):
    process = locust("traces", 30, 5, "40s")
    process.wait(timeout=75)
    time.sleep(6)
    events = [row for row in agent_events() if row.get("trace_id")]
    slow = max((row for row in events if row["event"] == "llm_call"), key=lambda row: row["llm_ms"])
    error = next(row for row in events if row["event"] == "llm_error")
    save_trace(page, slow, "slow")
    save_trace(page, error, "error")


def firing():
    result = read(f"{PROM}/api/v1/alerts")
    return {a["labels"]["alertname"] for a in result["data"]["alerts"] if a["state"] == "firing"}


def watch(names, start, timeout):
    print(f"Watching {sorted(names)} for up to {timeout}s")
    seen = {}
    while time.monotonic() - start < timeout:
        current = firing()
        for name in current & names:
            seen.setdefault(name, round(time.monotonic() - start, 1))
        if names <= current:
            return seen
        time.sleep(2)
    return seen


def configure(values):
    env = os.environ.copy()
    env.update(values)
    started = time.monotonic()
    compose("up", "-d", "--no-deps", "--force-recreate", "--scale", "agent=3", "agent", env=env)
    wait_for(lambda: read(HEALTH))
    time.sleep(12)
    return started


def start():
    print("Starting the observability stack")
    compose("up", "-d", "--build", "--scale", "agent=2")
    wait_for(lambda: read(HEALTH), timeout=75)
    wait_for(lambda: read(f"{GRAFANA}/api/health"))
    wait_for(lambda: read(f"{PROM}/api/v1/targets"))
    compose(
        "exec", "-T", "prometheus", "promtool", "check", "rules",
        "/etc/prometheus/alert_rules.yml",
    )
    with (OUT / "promtool.txt").open("w") as f:
        subprocess.run(
            ["docker", "run", "--rm", "-v", f"{Path('prometheus').resolve()}:/p:ro",
             "--entrypoint", "promtool", "prom/prometheus:v3.16.0",
             "test", "rules", "/p/alert_rules_test.yml"],
            stdout=f, stderr=subprocess.STDOUT, check=False,
        )

def query(expression):
    return read(f"{PROM}/api/v1/query?{urlencode({'query': expression})}")["data"]["result"]


def baseline(page):
    active = locust("grafana_baseline", 30, 5, "1m15s")
    time.sleep(55)
    dashboard = f"{GRAFANA}/d/day14-agent-overview/agent-overview?from=now-5m&to=now&refresh=5s"
    shot(page, dashboard, "grafana-dashboard.png", text="Traffic (req/s)")
    requests = 'http_requests_total{job="agent",route="/ask"}'
    seconds = 'http_request_duration_seconds'
    latency = '{job="agent",route="/ask"}[1m]'
    observations["grafana_baseline"] = {
        "rps_per_instance": query(f"sum by (instance) (rate({requests}[1m]))"),
        "p95_ms": float(query(f"histogram_quantile(0.95, sum by (le) (rate({seconds}_bucket{latency})))")[0]["value"][1]) * 1000,
        "avg_ms": float(query(f"sum(rate({seconds}_sum{latency})) / sum(rate({seconds}_count{latency}))")[0]["value"][1]) * 1000,
        "tokens_per_second": query('sum by (type) (rate(llm_tokens_total{job="agent"}[1m]))'),
    }
    try:
        read("http://127.0.0.1:8080/metrics")
    except HTTPError as exc:
        observations["public_metrics_http_status"] = exc.code
    save()
    active.wait(timeout=45)


def live_targets():
    targets = read(f"{PROM}/api/v1/targets")["data"]["activeTargets"]
    return [t["scrapeUrl"] for t in targets if t["labels"]["job"] == "agent" and t["health"] == "up"]


def scaling():
    for replicas in (1, 3):
        print(f"Locust 100 users, scale {replicas}")
        compose("up", "-d", "--scale", f"agent={replicas}")
        wait_for(lambda: len(live_targets()) == replicas)
        before = live_targets()
        name = f"scale_{replicas}"
        finish_scaling(locust(name, 100, 10, "2m"), name)
        after = live_targets()
        if len(after) != replicas:
            raise RuntimeError(f"Replica count changed during Locust: {before} -> {after}")
        observations[f"agent_targets_at_scale_{replicas}"] = after
        save()


def alerts(page):
    print("Starting combined A/B faults under load")
    initial = firing()
    locust_a_b = locust("faults_a_b", 30, 5, "3m")
    started = configure({"MOCK_ERROR_RATE": "0.2", "MOCK_SLOW_RATE": "0.3"})
    expected = {"AgentHighErrorRate", "AgentHighLatencyP95"}
    observations["initial_firing_at_a_b"] = sorted(initial)
    observations["faults_a_b"] = watch(expected, started, 170)
    if expected <= firing():
        shot(page, f"{PROM}/alerts", "prometheus-alert-firing.png", text="AgentHighErrorRate")
    save()
    locust_a_b.wait(timeout=60)

    print("Starting C model cost fault under load")
    locust_c = locust("fault_c", 30, 5, "3m")
    started = configure({"LLM_MODEL": "mock-llm-large"})
    observations["initial_firing_at_c"] = sorted(firing())
    observations["fault_c"] = watch({"LLMCostBurnRateHigh"}, started, 170)
    save()
    locust_c.wait(timeout=60)

    print("Pausing one replica for fault D")
    configure({})
    locust_d = locust("fault_d", 30, 5, "1m20s")
    replica = compose("ps", "-q", "agent", capture_output=True).stdout.splitlines()[0]
    started = time.monotonic()
    subprocess.run(["docker", "pause", replica], check=True)
    try:
        observations["fault_d"] = watch({"AgentInstanceDown"}, started, 65)
        save()
    finally:
        subprocess.run(["docker", "unpause", replica], check=True)
    locust_d.wait(timeout=60)

    print("Observing v1 in a fresh Prometheus window (fault E)")
    configure({"AGENT_VERSION": "v1"})
    compose("rm", "-f", "-s", "-v", "prometheus")
    compose("up", "-d", "--no-deps", "prometheus")
    wait_for(lambda: read(f"{PROM}/api/v1/targets"))
    locust_e = locust("fault_e", 30, 5, "2m")
    observed = set()
    started = time.monotonic()
    while time.monotonic() - started < 120:
        observed.update(firing())
        time.sleep(5)
    locust_e.wait(timeout=60)
    observations["fault_e"] = {"seconds_observed": 120, "alerts_firing": sorted(observed)}
    observations["agent_info"] = read(f"{PROM}/api/v1/query?{urlencode({'query': 'agent_info'})}")
    save()


with sync_playwright() as browser_tool:
    browser = browser_tool.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1050}, device_scale_factor=1)
    try:
        start()
        phase = sys.argv[1]
        if phase == "scaling":
            scaling()
        elif phase == "tracing":
            tracing(page)
        elif phase == "baseline":
            baseline(page)
        else:
            alerts(page)
    finally:
        with (OUT / "compose.log").open("w") as f:
            compose("logs", "--tail=40", stdout=f, stderr=subprocess.STDOUT)
        compose("down", "-v", "--remove-orphans")
        browser.close()
