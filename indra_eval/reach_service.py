"""Manage the local REACH NLP web services running in docker.

Two variants coexist (see docs/SCORING.md):

* ``v14``    -- pathwaycommons/reach-docker:latest, amd64 under emulation, port 8080
* ``modern`` -- reach-modern:latest, native arm64 built from clulab/reach master, port 8081

stdlib-only on purpose: this module runs before any venv exists.

    python3 indra_eval/reach_service.py start [--no-wait] [--modern]
    python3 indra_eval/reach_service.py stop [--modern]
    python3 indra_eval/reach_service.py status [--modern]
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROBE_TEXT = "MEK phosphorylates ERK."

PROBE_TIMEOUT = 10  # seconds per readiness HTTP request
LONG_PROBE_TIMEOUT = 15 * 60  # one in-flight request may block while models load
POLL_INTERVAL = 15  # seconds between readiness polls
POLL_CAP = 20 * 60  # give up waiting after 20 minutes

# status codes returned in result["state"]
NO_DOCKER = "no-docker"
NO_IMAGE = "no-image"
NO_CONTAINER = "no-container"
BOOTING = "booting"
READY = "ready"
PORT_CONFLICT = "port-conflict"
EXITED = "exited"


# --------------------------------------------------------------------------- #
# variants
# --------------------------------------------------------------------------- #

class Variant:
    """Everything that differs between the two REACH deployments."""

    def __init__(
        self,
        name: str,
        image: str,
        container: str,
        host_port: int,
        container_port: int,
        platform: str | None,
        boot_note: str,
        missing_image_verb: str,
        missing_image_hint: str,
        env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.image = image
        self.container = container
        self.host_port = host_port
        self.container_port = container_port
        self.platform = platform
        self.boot_note = boot_note
        self.missing_image_verb = missing_image_verb
        self.missing_image_hint = missing_image_hint
        self.env = dict(env or {})

    @property
    def text_url(self) -> str:
        return f"http://localhost:{self.host_port}/api/text"


V14 = Variant(
    name="v14",
    image="pathwaycommons/reach-docker:latest",
    container="indra-reach",
    host_port=8080,
    container_port=8080,
    platform="linux/amd64",
    boot_note="JVM boot under emulation takes 5-15 minutes",
    missing_image_verb="pulled",
    missing_image_hint=(
        "pull it first (docker pull --platform linux/amd64 "
        "pathwaycommons/reach-docker:latest)"
    ),
)

MODERN = Variant(
    name="modern",
    image="reach-modern:latest",
    container="indra-reach-modern",
    host_port=8081,
    container_port=8080,
    # clulab master loads DyNet via an x86_64-only libdynet_swig.so, so the modern
    # image is amd64 and runs under emulation just like v14.
    platform="linux/amd64",
    boot_note=(
        "sbt compile plus model loading under emulation takes 10-20+ minutes on first boot"
    ),
    missing_image_verb="built",
    missing_image_hint=(
        "it has not been built yet; build it from clulab/reach master (docker build "
        "--platform linux/amd64 -t reach-modern:latest "
        "'https://github.com/clulab/reach.git#master:docker')"
    ),
    # sbt's default heap OOM-killed the container (exit 137) on large papers; v14 never did.
    env={"SBT_OPTS": "-Xmx10g -Xms2g"},
)

VARIANTS = {V14.name: V14, MODERN.name: MODERN}
DEFAULT_VARIANT = V14.name


def _variant(variant: str | Variant = DEFAULT_VARIANT) -> Variant:
    if isinstance(variant, Variant):
        return variant
    try:
        return VARIANTS[variant]
    except KeyError:
        raise ValueError(
            f"unknown REACH variant {variant!r}; expected one of {sorted(VARIANTS)}"
        ) from None


def text_url(variant: str = DEFAULT_VARIANT) -> str:
    """Public helper: the /api/text endpoint for a variant."""
    return _variant(variant).text_url


# --------------------------------------------------------------------------- #
# docker helpers
# --------------------------------------------------------------------------- #

def _docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Run a docker command. Never raises: failures come back as returncode != 0."""
    try:
        return subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess[str](
            list(args), 124, "", f"docker {' '.join(args)} timed out after {timeout}s"
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess[str](
            list(args), 127, "", "docker CLI not found on PATH"
        )


def _err(proc: subprocess.CompletedProcess[str], fallback: str) -> str:
    """Last informative line of a failed docker command, trimmed to one readable line.

    docker often ends with a bare "See 'docker run --help'." that carries no
    information, so those trailing pointer lines are skipped.
    """
    lines = [ln.strip() for ln in (proc.stderr or proc.stdout).strip().splitlines() if ln.strip()]
    while len(lines) > 1 and lines[-1].startswith("See 'docker"):
        lines.pop()
    msg = lines[-1] if lines else fallback
    return msg if len(msg) <= 160 else msg[:157] + "..."


def _docker_available() -> tuple[bool, str]:
    proc = _docker("info", "--format", "{{.ServerVersion}}", timeout=30)
    if proc.returncode != 0:
        return False, _err(proc, "docker daemon not reachable")
    return True, proc.stdout.strip()


def _image_present(var: Variant) -> tuple[bool, str]:
    """Return (usable, note): is the image present *for the platform we will run*?

    A tag can be held locally by an image of the wrong architecture (e.g. an
    arm64 build left over before a rebuild for linux/amd64). `docker image
    inspect` still succeeds, but `docker run --platform linux/amd64` cannot use
    it and falls back to a registry pull, so that counts as not present here.
    The note explains the mismatch; it is empty when there is nothing to add.
    """
    proc = _docker("image", "inspect", var.image, "--format", "{{.Architecture}}")
    if proc.returncode != 0:
        return False, ""
    arch = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    if not var.platform:
        return True, ""
    want = var.platform.rsplit("/", 1)[-1]
    if arch == want:
        return True, ""
    return False, f" for {var.platform}; the tag currently holds arch={arch or 'unknown'}"


def _container_state(var: Variant) -> tuple[bool, str | None]:
    """Return (docker_ok, State). State is "running"/"exited"/... or None if absent."""
    proc = _docker(
        "ps", "-a", "--filter", f"name=^/{var.container}$", "--format", "{{.State}}"
    )
    if proc.returncode != 0:
        return False, _err(proc, "docker ps failed")
    out = proc.stdout.strip()
    return True, (out.splitlines()[0] if out else None)


# --------------------------------------------------------------------------- #
# readiness
# --------------------------------------------------------------------------- #

def _port_open(var: Variant) -> bool:
    with socket.socket() as sock:
        sock.settimeout(2)
        return sock.connect_ex(("127.0.0.1", var.host_port)) == 0


def probe(timeout: int = PROBE_TIMEOUT, variant: str = DEFAULT_VARIANT) -> tuple[bool, str]:
    """POST the probe sentence; READY means HTTP 200 with a JSON body.

    REACH's ApiServer takes `text` and `output` as QUERY parameters on
    POST /api/text, with an empty request body -- not as form fields. A form
    body gets a 404 from both variants.
    """
    var = _variant(variant)
    query = urllib.parse.urlencode({"text": PROBE_TEXT, "output": "fries"})
    req = urllib.request.Request(f"{var.text_url}?{query}", data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            try:
                json.loads(body.decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                return False, "HTTP 200 but body is not JSON"
            return True, "HTTP 200 with JSON body"
    except urllib.error.HTTPError as err:
        return False, f"HTTP {err.code}"
    except urllib.error.URLError as err:
        return False, f"no response ({err.reason})"
    except TimeoutError:
        return False, f"no response within {timeout}s"
    except OSError as err:
        return False, f"no response ({err})"


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #

def status(variant: str = DEFAULT_VARIANT) -> dict:
    var = _variant(variant)

    ok, info = _docker_available()
    if not ok:
        return {"state": NO_DOCKER, "ready": False, "message": f"docker unavailable: {info}"}

    ok, state = _container_state(var)
    if not ok:
        return {"state": NO_DOCKER, "ready": False, "message": f"docker unavailable: {state}"}
    if state is None:
        if _port_open(var):
            return {
                "state": PORT_CONFLICT,
                "ready": False,
                "message": (
                    f"no '{var.container}' container, but port {var.host_port} is already in "
                    "use by another process — free it before starting REACH"
                ),
            }
        usable, note = _image_present(var)
        extra = "" if usable else f" (image {var.image} not {var.missing_image_verb} yet{note})"
        return {
            "state": NO_CONTAINER,
            "ready": False,
            "message": f"REACH is not running: no '{var.container}' container{extra}",
        }

    if state.lower() != "running":
        return {
            "state": EXITED,
            "ready": False,
            "message": (
                f"container '{var.container}' exists but is {state} — run 'stop' then 'start'"
            ),
        }

    ready, detail = probe(variant=var.name)
    if ready:
        return {
            "state": READY,
            "ready": True,
            "message": f"REACH is READY at {var.text_url} ({detail})",
        }
    return {
        "state": BOOTING,
        "ready": False,
        "message": (
            f"container '{var.container}' is running but the service is not ready yet "
            f"({detail}) — {var.boot_note}"
        ),
    }


def stop(variant: str = DEFAULT_VARIANT) -> dict:
    var = _variant(variant)

    ok, info = _docker_available()
    if not ok:
        return {"state": NO_DOCKER, "removed": False, "message": f"docker unavailable: {info}"}
    ok, state = _container_state(var)
    if not ok:
        return {"state": NO_DOCKER, "removed": False, "message": f"docker unavailable: {state}"}
    if state is None:
        return {
            "state": NO_CONTAINER,
            "removed": False,
            "message": f"nothing to stop: no '{var.container}' container",
        }
    proc = _docker("rm", "-f", var.container, timeout=120)
    if proc.returncode != 0:
        return {
            "state": "error",
            "removed": False,
            "message": f"docker rm -f {var.container} failed: {_err(proc, 'unknown error')}",
        }
    return {
        "state": NO_CONTAINER,
        "removed": True,
        "message": f"removed container '{var.container}'",
    }


def start(wait: bool = True, variant: str = DEFAULT_VARIANT) -> dict:
    var = _variant(variant)

    ok, info = _docker_available()
    if not ok:
        return {"state": NO_DOCKER, "ready": False, "message": f"docker unavailable: {info}"}

    ok, state = _container_state(var)
    if not ok:
        return {"state": NO_DOCKER, "ready": False, "message": f"docker unavailable: {state}"}
    if state is not None and state.lower() == "running":
        cur = status(var.name)
        if cur["ready"]:
            return cur
        if not wait:
            return cur
        return _poll(started_at=time.monotonic(), var=var)
    if state is not None:
        removed = stop(var.name)
        if not removed.get("removed"):
            return {"state": "error", "ready": False, "message": removed["message"]}

    usable, note = _image_present(var)
    if not usable:
        return {
            "state": NO_IMAGE,
            "ready": False,
            "message": (
                f"image {var.image} is not present locally{note} — {var.missing_image_hint}; "
                "no container was created"
            ),
        }

    if _port_open(var):
        return {
            "state": PORT_CONFLICT,
            "ready": False,
            "message": (
                f"port {var.host_port} is already in use by another process — free it before "
                "starting REACH; no container was created"
            ),
        }

    run_args = ["run", "-d", "--name", var.container]
    if var.platform:
        run_args += ["--platform", var.platform]
    for key, value in var.env.items():
        run_args += ["-e", f"{key}={value}"]
    run_args += ["-p", f"{var.host_port}:{var.container_port}", var.image]

    proc = _docker(*run_args, timeout=300)
    if proc.returncode != 0:
        err = _err(proc, "unknown error")
        _docker("rm", "-f", var.container)  # leave nothing half-created
        return {"state": "error", "ready": False, "message": f"docker run failed: {err}"}

    cid = proc.stdout.strip()[:12]
    print(f"started container '{var.container}' ({cid}) on port {var.host_port}")
    if not wait:
        return {
            "state": BOOTING,
            "ready": False,
            "message": (
                f"container '{var.container}' started; not waiting for readiness "
                f"({var.boot_note})"
            ),
        }
    return _poll(started_at=time.monotonic(), var=var)


def _poll(started_at: float, var: Variant = V14) -> dict:
    """Single documented readiness poll: every POLL_INTERVAL up to POLL_CAP.

    REACH loads its models lazily, when the first request arrives, so once the
    server accepts a request but does not answer within PROBE_TIMEOUT we switch
    to one long-timeout request (LONG_PROBE_TIMEOUT) and wait it out instead of
    hammering the loading server with more short polls.
    """
    print(f"waiting for REACH readiness (poll every {POLL_INTERVAL}s, cap {POLL_CAP // 60} min)...")
    long_wait_done = False
    while True:
        elapsed = time.monotonic() - started_at
        ready, detail = probe(variant=var.name)
        if not long_wait_done and not ready:
            if detail == f"no response within {PROBE_TIMEOUT}s":
                print(
                    f"  [{int(elapsed):5d}s] request accepted but unanswered — REACH is loading "
                    f"models; waiting up to {LONG_PROBE_TIMEOUT // 60} min on a single request"
                )
                long_wait_done = True
                ready, detail = probe(timeout=LONG_PROBE_TIMEOUT, variant=var.name)
                elapsed = time.monotonic() - started_at
        if ready:
            return {
                "state": READY,
                "ready": True,
                "message": f"REACH is READY at {var.text_url} after {int(elapsed)}s ({detail})",
            }
        cok, cstate = _container_state(var)
        if cok and (cstate is None or cstate.lower() != "running"):
            return {
                "state": EXITED,
                "ready": False,
                "message": (
                    f"container '{var.container}' is no longer running ({cstate}) — "
                    f"check 'docker logs {var.container}'"
                ),
            }
        if elapsed >= POLL_CAP:
            return {
                "state": BOOTING,
                "ready": False,
                "message": (
                    f"REACH still not ready after {int(elapsed)}s ({detail}); container is "
                    f"running — check 'docker logs {var.container}'"
                ),
            }
        print(f"  [{int(elapsed):5d}s] not ready yet: {detail}")
        time.sleep(POLL_INTERVAL)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _add_modern_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--modern",
        action="store_true",
        help=f"act on the modern variant ({MODERN.container}, port {MODERN.host_port})",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reach_service", description="Manage the local REACH docker services."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_start = sub.add_parser("start", help="start the REACH container and wait for readiness")
    p_start.add_argument(
        "--no-wait", action="store_true", help="do not poll for readiness after starting"
    )
    _add_modern_flag(p_start)
    p_stop = sub.add_parser("stop", help="remove the REACH container")
    _add_modern_flag(p_stop)
    p_status = sub.add_parser(
        "status", help="report whether REACH is absent, booting, or ready"
    )
    _add_modern_flag(p_status)

    args = parser.parse_args(argv)
    variant = MODERN.name if args.modern else V14.name

    match args.command:
        case "start":
            result = start(wait=not args.no_wait, variant=variant)
            code = 0 if result["state"] in (READY, BOOTING) else 1
        case "stop":
            result = stop(variant)
            code = 0 if result["state"] == NO_CONTAINER else 1
        case "status":
            result = status(variant)
            code = 0
        case _:  # pragma: no cover - argparse guards this
            parser.error(f"unknown command {args.command!r}")

    print(result["message"])
    return code


if __name__ == "__main__":
    sys.exit(main())
