"""Provision an isolated local Postgres container, run all gates' test suites, and clean up."""

import os
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    environment = os.environ | {"GODZILLA_TEST_POSTGRES_PASSWORD": secrets.token_hex(24)}
    project = "godzilla-test-" + uuid4().hex[:12]
    compose = ["docker", "compose", "-f", str(root / "docker-compose.test.yml"), "-p", project]
    try:
        subprocess.run(
            [*compose, "up", "-d", "--wait", "--wait-timeout", "120"],
            env=environment,
            cwd=root,
            check=True,
        )
        port = (
            subprocess.run(
                [*compose, "port", "postgres", "5432"],
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.strip()
            .rsplit(":", 1)[1]
        )
        environment["GODZILLA_TEST_DATABASE_URL"] = (
            "postgresql+psycopg://godzilla_test:"
            + environment["GODZILLA_TEST_POSTGRES_PASSWORD"]
            + "@127.0.0.1:"
            + port
            + "/godzilla_test"
        )
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--cov=godzilla",
                "--cov-report=term-missing",
                "--cov-fail-under=85",
                *sys.argv[1:],
            ],
            env=environment,
            cwd=root,
        ).returncode
    finally:
        subprocess.run([*compose, "down", "--volumes"], env=environment, cwd=root, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
