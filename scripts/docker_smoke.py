"""Smoke the packaged container in credential-free deterministic mode."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request(url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        value: dict[str, object] = json.load(response)
        return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    name = f"acv-smoke-{uuid.uuid4().hex[:10]}"
    port = _free_port()
    container_id = subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--init",
            "--name",
            name,
            "--publish",
            f"127.0.0.1:{port}:8000",
            "--env",
            "ACV_LLM__PROVIDER=fake",
            args.image,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    user = "unknown"
    exit_code = "unknown"
    try:
        user = subprocess.run(
            ["docker", "inspect", "--format", "{{.Config.User}}", container_id],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if user in {"", "0", "root"}:
            raise RuntimeError("container is running as root")
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 45
        while True:
            try:
                health = _request(f"{base_url}/health")
                break
            except (OSError, urllib.error.URLError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("container did not become healthy") from None
                time.sleep(0.2)
        result = _request(
            f"{base_url}/v1/claims/verify",
            {
                "claim": "Offer BACK159 costs $127.49.",
                "reference_id": "offer-back-to-school-back159",
                "as_of": "2026-09-10",
            },
        )
        if health.get("status") != "ok":
            raise RuntimeError("container liveness failed")
        if (
            result.get("verdict") != "SUPPORTED"
            or result.get("verification_path") != "DETERMINISTIC"
        ):
            raise RuntimeError("container deterministic verification failed")
    finally:
        stopped = subprocess.run(
            ["docker", "stop", "--time", "5", name], check=False, capture_output=True
        )
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.ExitCode}}", name],
            check=False,
            capture_output=True,
            text=True,
        )
        exit_code = inspected.stdout.strip()
        subprocess.run(["docker", "rm", "--force", name], check=False, capture_output=True)
        if stopped.returncode != 0:
            raise RuntimeError("docker stop failed")
    # Docker records 143 when the exec-form server exits directly on SIGTERM;
    # 137 would mean the grace period expired and Docker had to SIGKILL it.
    if exit_code not in {"0", "143"}:
        raise RuntimeError(f"container did not stop within its grace period: exit_code={exit_code}")
    print(f"docker_smoke=PASS image={args.image} user={user} exit_code={exit_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
