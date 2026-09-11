"""Build and smoke an installed wheel with no checkout on ``sys.path``."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _json_request(url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        value: dict[str, object] = json.load(response)
        return value


def _wait_for_health(base_url: str, process: subprocess.Popen[bytes]) -> dict[str, object]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"installed service exited early with code {process.returncode}")
        try:
            return _json_request(f"{base_url}/health")
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise RuntimeError("installed service did not become healthy")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="acv-wheel-smoke-") as temporary:
        temp = Path(temporary)
        dist = temp / "dist"
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
            cwd=ROOT,
            check=True,
        )
        wheel = next(dist.glob("*.whl"))
        venv = temp / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        python = venv / "bin" / "python"
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)],
            check=True,
        )
        run_dir = temp / "outside-checkout"
        run_dir.mkdir()
        clean_environment = {
            key: value
            for key, value in os.environ.items()
            if key != "PYTHONPATH" and not key.startswith("ACV_")
        }
        imported_from = subprocess.run(
            [
                str(python),
                "-c",
                (
                    "import app; "
                    "from app.core.config import Settings; "
                    "from app.raters.prompts import load_prompt; "
                    "s=Settings(_env_file=None); "
                    "assert s.pipeline_config_path.is_file(); "
                    "assert s.data.reference_catalog_path.is_file(); "
                    "assert load_prompt('rater', 'v1'); "
                    "print(app.__file__)"
                ),
            ],
            cwd=run_dir,
            check=True,
            capture_output=True,
            text=True,
            env=clean_environment,
        ).stdout.strip()
        if str(ROOT) in imported_from:
            raise RuntimeError("wheel smoke imported app from the source checkout")

        port = _free_port()
        clean_environment.update(
            {
                "ACV_ENVIRONMENT": "test",
                "ACV_LLM__PROVIDER": "fake",
                "ACV_OBSERVABILITY__METRICS_ENABLED": "false",
            }
        )
        process = subprocess.Popen(
            [
                str(python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--loop",
                "asyncio",
            ],
            cwd=run_dir,
            env=clean_environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        try:
            base_url = f"http://127.0.0.1:{port}"
            health = _wait_for_health(base_url, process)
            readiness = _json_request(f"{base_url}/ready")
            result = _json_request(
                f"{base_url}/v1/claims/verify",
                {
                    "claim": "Offer BACK159 costs $127.49.",
                    "reference_id": "offer-back-to-school-back159",
                    "as_of": "2026-09-10",
                },
            )
            if health.get("status") != "ok" or readiness.get("status") != "ready":
                raise RuntimeError("installed service health/readiness failed")
            if (
                result.get("verdict") != "SUPPORTED"
                or result.get("verification_path") != "DETERMINISTIC"
            ):
                raise RuntimeError("installed deterministic verification failed")
        finally:
            # Let the final response drain before exercising Uvicorn's TERM
            # handler; otherwise a very fast local run can signal while the
            # connection teardown callback is still active.
            time.sleep(0.2)
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.returncode != 0:
            raise RuntimeError(f"installed service shutdown returned {process.returncode}")
        print(f"installed_wheel_smoke=PASS wheel={wheel.name} imported_from={imported_from}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
