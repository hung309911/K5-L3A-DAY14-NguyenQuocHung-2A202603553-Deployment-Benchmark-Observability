"""Run the Docker lab and capture observations from actual request traffic."""

import csv
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
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
                *COMPOSE, "run", "--rm", "locust", "-f", "/mnt/locust/locustfile.py",
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


def trace_query(params):
    return read(f"{JAEGER}/api/traces?{urlencode(params)}").get("data", [])


def trace_duration(trace):
    spans = trace["spans"]
    return max(s["startTime"] + s["duration"] for s in spans) - min(s["startTime"] for s in spans)


def save_trace(page, trace, kind):
    trace_id = trace["traceID"]
    model_span = max(
        (span for span in trace["spans"] if span["operationName"].startswith("chat ")),
        key=lambda span: span["duration"],
    )
    logs = compose("logs", "--no-color", "--no-log-prefix", "agent", capture_output=True).stdout
    event = "llm_error" if kind == "error" else "llm_call"
    entries = []
    for line in logs.splitlines():
        if not line.startswith("{") or trace_id not in line:
            continue
        row = json.loads(line)
        if row.get("trace_id") == trace_id:
            entries.append(row)
    match = next((row for row in entries if row.get("event") == event), None)
    if match is None and entries:
        match = entries[0]
    trace_url = f"{JAEGER}/trace/{trace_id}"
    page.goto(trace_url, wait_until="domcontentloaded")
    model = page.get_by_text(model_span["operationName"], exact=True).first
    try:
        model.wait_for(timeout=20_000)
        model.click()
        if kind == "error":
            page.get_by_text("Logs", exact=False).last.click(timeout=3000)
    except BrowserTimeout:
        pass
    page.wait_for_timeout(1000)
    page.screenshot(path=SCREENSHOTS / f"jaeger-{kind}-trace.png", full_page=True)
    observations[f"{kind}_trace"] = {
        "trace_id": trace_id,
        "duration_ms": trace_duration(trace) / 1000,
        "span": model_span["operationName"],
        "span_ms": model_span["duration"] / 1000,
        "span_events": model_span.get("logs", []),
        "matching_log": match,
    }
    save()


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


def main(page):
    print("Starting the observability stack with one replica")
    compose("up", "-d", "--build", "--scale", "agent=1")
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

    print("Locust 100 users, scale 1")
    first = locust("scale_1", 100, 10, "2m")
    time.sleep(65)
    dash = f"{GRAFANA}/d/day14-agent-overview/agent-overview?from=now-5m&to=now&refresh=5s"
    shot(page, dash, "grafana-dashboard.png", text="Traffic (req/s)")
    finish_scaling(first, "scale_1")

    print("Locust 100 users, scale 3")
    compose("up", "-d", "--scale", "agent=3")
    time.sleep(18)
    finish_scaling(locust("scale_3", 100, 10, "2m"), "scale_3")
    observations["agent_targets_at_scale_3"] = [
        target["scrapeUrl"] for target in read(f"{PROM}/api/v1/targets")["data"]["activeTargets"]
        if target["labels"]["job"] == "agent" and target["health"] == "up"
    ]
    save()

    services = read(f"{JAEGER}/api/services")["data"]
    service = "observable-agent" if "observable-agent" in services else next(
        name for name in services if name != "jaeger"
    )
    params = {"service": service, "limit": 100, "lookback": "1h"}
    slow = trace_query({**params, "minDuration": "1s"})
    save_trace(page, max(slow, key=trace_duration), "slow")
    errors = trace_query({**params, "tags": json.dumps({"error": "true"})})
    save_trace(page, next(t for t in errors if any(
        span["operationName"].startswith("chat ") for span in t["spans"]
    )), "error")

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
        main(page)
    finally:
        with (OUT / "compose.log").open("w") as f:
            compose("logs", "--tail=40", stdout=f, stderr=subprocess.STDOUT)
        compose("down", "-v", "--remove-orphans")
        browser.close()
