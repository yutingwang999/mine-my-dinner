import subprocess

import pytest
from sqlalchemy import text

from tests.conftest import REPO_ROOT


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_vector_extension_available(session):
    query = text("SELECT extname FROM pg_extension WHERE extname='vector'")
    assert session.execute(query).scalar() == "vector"


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "data/x.json",
        "docs/private/x.md",
        "private/x.py",
        "frontend/node_modules/x",
        "frontend/.next/x",
    ],
)
def test_sensitive_paths_are_ignored(path):
    assert subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO_ROOT).returncode == 0


def test_env_example_is_not_ignored():
    result = subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=REPO_ROOT)
    assert result.returncode == 1
