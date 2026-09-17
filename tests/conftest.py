import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any attempt to open a socket fails the test: proof the suite is offline."""
    def guard(*args, **kwargs):
        raise RuntimeError("network access is disabled in tests")
    monkeypatch.setattr(socket, "socket", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


@pytest.fixture
def data_dir() -> Path:
    return ROOT / "sample_data"


@pytest.fixture
def out_path(tmp_path) -> Path:
    return tmp_path / "report.json"
