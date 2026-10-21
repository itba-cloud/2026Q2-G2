"""The SEPA ingestion (ticket 09), through run-ingest's door: `make ingest`.

Without SEPA_API_URL, or with a proxy that does not answer, the ingestion
fails with a message that names the proxy. With a proxy up behind a tunnel,
it stores one file per chain in raw/ and the pipeline turns each into a job
that ends in `done`; running it again the same day stores nothing and runs no
job twice. The daily schedule invokes the same function, outside the VPC.

The proxy here is a stand-in that serves a synthetic daily archive with the
real proxy's contract (`/health` and `/v1/sepa/precios`), behind a Cloudflare
quick tunnel like the group's: the function reaches it over the internet, as
it would reach tools/sepa-proxy. The tunnel runs in Docker, which `make`
already needs for the workers' image.

Its chains (899000021 and 899000022) have no coordinates, so they never enter
a comparison.
"""
from __future__ import annotations

import http.server
import io
import os
import re
import subprocess
import threading
import time
import urllib.request
import uuid
import zipfile
from typing import Any, Callable

from archives import (FEED_DATE, JOB_TIMEOUT, delete_chain_files_at_end, job,
                      processed_after, raw_key, sepa_archive)
from cognito import owner_of_new_org
from harness import ROOT, Checks, Stack, object_versions, wait_for

CHAINS = (899000021, 899000022)
PRICES = {f"77999991{index:05d}": 200 + index + 0.5 for index in range(20)}

# The name the SEPA gives a chain's file inside the daily archive.
CHAIN_FILENAME = "sepa_1_comercio-sepa-{chain}_{date}_09-05-10.zip"

# A host that never resolves (RFC 2606): a proxy that does not answer.
UNREACHABLE_PROXY = "https://lgdc-verify.invalid"

CLOUDFLARED_IMAGE = "cloudflare/cloudflared:2025.10.0"
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
TUNNEL_TIMEOUT = 90


def daily_archive() -> bytes:
    """The SEPA's daily archive: a folder for the day, a zip per chain in it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for chain in CHAINS:
            name = CHAIN_FILENAME.format(chain=chain, date=FEED_DATE)
            archive.writestr(f"{FEED_DATE}/{name}", sepa_archive(chain, PRICES))
    return buffer.getvalue()


def chain_object_key(chain: int) -> str:
    return raw_key(chain, CHAIN_FILENAME.format(chain=chain, date=FEED_DATE))


class StandInProxy(http.server.ThreadingHTTPServer):
    """The proxy's contract, on a local port, serving one archive."""

    def __init__(self, archive: bytes) -> None:
        self.archive = archive
        self.downloads = 0
        super().__init__(("127.0.0.1", 0), StandInHandler)

    @property
    def port(self) -> int:
        return self.server_address[1]


class StandInHandler(http.server.BaseHTTPRequestHandler):
    server: StandInProxy

    def do_GET(self) -> None:  # noqa: N802, the name http.server calls
        path = self.path.split("?")[0]
        if path == "/health":
            self.reply(200, b'{"status":"ok"}', "application/json")
        elif path == "/v1/sepa/precios":
            self.server.downloads += 1
            self.reply(200, self.server.archive, "application/zip")
        else:
            self.reply(404, b'{"error":"not found"}', "application/json")

    def reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


class Tunnel:
    """A Cloudflare quick tunnel to a local port, in a container."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.container = f"lgdc-verify-tunnel-{uuid.uuid4().hex[:8]}"
        self.url: str | None = None

    def open(self) -> str:
        subprocess.run(
            ["docker", "run", "--detach", "--rm", "--name", self.container,
             "--add-host", "host.docker.internal:host-gateway", CLOUDFLARED_IMAGE,
             "tunnel", "--no-autoupdate", "--url", f"http://host.docker.internal:{self.port}"],
            check=True, capture_output=True, text=True)
        self.url = poll(self.announced_url, TUNNEL_TIMEOUT)
        if self.url is None:
            raise RuntimeError(f"cloudflared announced no URL in {TUNNEL_TIMEOUT}s")
        # The name takes a few seconds to resolve after cloudflared announces it.
        if not poll(lambda: answers(f"{self.url}/health"), TUNNEL_TIMEOUT):
            raise RuntimeError(f"{self.url}/health did not answer in {TUNNEL_TIMEOUT}s")
        return self.url

    def announced_url(self) -> str | None:
        logs = subprocess.run(["docker", "logs", self.container],
                              capture_output=True, text=True)
        found = TUNNEL_URL.search(logs.stdout + logs.stderr)
        return found.group(0) if found else None

    def close(self) -> None:
        subprocess.run(["docker", "rm", "--force", self.container], capture_output=True)


def poll(condition: Callable[[], Any], timeout: float, interval: float = 2) -> Any:
    deadline = time.monotonic() + timeout
    while not (result := condition()) and time.monotonic() < deadline:
        time.sleep(interval)
    return result


def answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status == 200
    except Exception:  # not yet: DNS, the tunnel's 530, a timeout
        return False


def run_ingest(sepa_api_url: str | None) -> subprocess.CompletedProcess[str]:
    """What the run-ingest workflow runs, with or without the secret."""
    environment = {k: v for k, v in os.environ.items() if k != "SEPA_API_URL"}
    if sepa_api_url is not None:
        environment["SEPA_API_URL"] = sepa_api_url
    return subprocess.run(["make", "--no-print-directory", "-C", ROOT, "ingest"],
                          env=environment, capture_output=True, text=True)


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout + result.stderr).strip()


def function_configuration(stack: Stack) -> dict[str, Any]:
    return stack.lambda_.get_function_configuration(
        FunctionName=stack.outputs["ingest_function_name"])


def check_function(checks: Checks, stack: Stack) -> dict[str, Any]:
    checks.section("Ingest · a Lambda outside the VPC, invoked by a daily cron")
    configuration = function_configuration(stack)
    checks.expect("the function runs outside the VPC",
                  not configuration.get("VpcConfig", {}).get("SubnetIds"),
                  str(configuration.get("VpcConfig")))
    rules = stack.events.list_rule_names_by_target(
        TargetArn=configuration["FunctionArn"])["RuleNames"]
    schedules = [stack.events.describe_rule(Name=name) for name in rules]
    enabled = [rule for rule in schedules
               if rule.get("ScheduleExpression", "").startswith("cron(")
               and rule["State"] == "ENABLED"]
    checks.expect("an enabled EventBridge cron invokes it", len(enabled) == 1,
                  str([(rule["Name"], rule.get("ScheduleExpression"), rule["State"])
                       for rule in schedules]))
    return configuration


def check_without_proxy(checks: Checks, stack: Stack, configuration: dict[str, Any]) -> None:
    checks.section("Ingest · without the proxy, a clear failure and nothing else")
    deployed_url = configuration.get("Environment", {}).get("Variables", {}).get(
        "SEPA_API_URL")
    if deployed_url:
        checks.note("the stack was deployed with a SEPA_API_URL: the run without one "
                    "is skipped")
    else:
        missing = run_ingest(None)
        text = output_of(missing)
        checks.expect("without SEPA_API_URL, run-ingest fails", missing.returncode != 0, text)
        checks.expect("saying that SEPA_API_URL is missing and naming the proxy",
                      "SEPA_API_URL" in text and "proxy" in text, text[-400:])

    down = run_ingest(UNREACHABLE_PROXY)
    text = output_of(down)
    checks.expect("with a proxy that does not answer, run-ingest fails",
                  down.returncode != 0, text)
    checks.expect("naming the proxy and its URL",
                  "proxy" in text and UNREACHABLE_PROXY in text, text[-400:])


def check_with_proxy(checks: Checks, stack: Stack, token: str) -> None:
    checks.section("Ingest · with the proxy up, a job per chain, each one done")
    proxy = StandInProxy(daily_archive())
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    tunnel = Tunnel(proxy.port)
    try:
        url = tunnel.open()
        checks.note(f"stand-in proxy at {url}")
        for chain in CHAINS:
            delete_chain_files_at_end(stack, chain)
        previous = {chain: job(stack, token, chain) for chain in CHAINS}

        first = run_ingest(url)
        text = output_of(first)
        checks.expect("run-ingest succeeds", first.returncode == 0, text[-600:])
        checks.expect(f"it says it stored the {len(CHAINS)} chains",
                      f'"stored": {len(CHAINS)}' in text, text[-600:])
        checks.expect("having downloaded the archive through the tunnel once",
                      proxy.downloads == 1, f"{proxy.downloads} downloads")

        done = {chain: wait_for(stack, processed_after(stack, token, chain, previous[chain],
                                                       "done"), JOB_TIMEOUT)
                for chain in CHAINS}
        checks.expect(f"each of the {len(CHAINS)} chains has its job, in done",
                      all(done.values()),
                      str({chain: job(stack, token, chain) for chain in CHAINS}))
        checks.expect(f"each one loaded the {len(PRICES)} prices",
                      all(row and row["rows_ok"] == len(PRICES) for row in done.values()),
                      str(done))

        checks.section("Ingest · twice the same day: no file and no job twice")
        second = run_ingest(url)
        text = output_of(second)
        checks.expect("the second run succeeds", second.returncode == 0, text[-600:])
        checks.expect("and stores nothing",
                      '"stored": 0' in text and f'"skipped": {len(CHAINS)}' in text,
                      text[-600:])
        versions = {chain: versions_of(stack, chain_object_key(chain)) for chain in CHAINS}
        checks.expect("each chain's file has a single version in raw/",
                      all(count == 1 for count in versions.values()), str(versions))
        # Nothing new reached the queue, but give a stray message the time a
        # worker takes to pick it up before saying no job ran again.
        time.sleep(60)
        after = {chain: job(stack, token, chain) for chain in CHAINS}
        checks.expect("no job ran again",
                      all(after[chain] and done[chain]
                          and after[chain]["updated_at"] == done[chain]["updated_at"]
                          and after[chain]["attempts"] == done[chain]["attempts"]
                          for chain in CHAINS),
                      f"before {done}, after {after}")
    finally:
        tunnel.close()
        proxy.shutdown()


def versions_of(stack: Stack, key: str) -> int:
    return len([entry for entry in object_versions(stack, key) if entry["Key"] == key])


def run(checks: Checks, stack: Stack) -> None:
    configuration = check_function(checks, stack)
    check_without_proxy(checks, stack, configuration)
    token = owner_of_new_org(stack, "LGDC verify ingest")
    check_with_proxy(checks, stack, token)
