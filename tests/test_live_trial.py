"""Trial failures must remain failures without contacting a real bot."""

import asyncio
import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.helpers import FakeClient, configuration

spec = importlib.util.spec_from_file_location(
    "relay_live_trial", Path(__file__).resolve().parents[1] / "tools/live_trial.py"
)
assert spec is not None and spec.loader is not None
live_trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live_trial)


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"ready": True, "http_status": "sent", "mcp_status": "sent"}

    class Client(FakeClient):
        def __init__(self, token: str) -> None:
            super().__init__()

        async def close(self) -> None:
            state["client_closed"] = True

    class Service:
        def __init__(self, *args: Any) -> None:
            self.operations = object()
            self.bot_id = 123 if state["ready"] else None

        async def initialize(self) -> None:
            pass

        def close(self) -> None:
            pass

        async def run(self) -> None:
            await asyncio.Event().wait()

    class Server:
        def __init__(self, *args: Any) -> None:
            self.started = state["ready"]
            self.should_exit = False

        async def serve(self) -> None:
            while not self.should_exit:
                await asyncio.sleep(0.001)
            state["server_closed"] = True

    class Response:
        def raise_for_status(self) -> None:
            if state["http_status"] == "http_error":
                raise RuntimeError("HTTP status failure")

        def json(self) -> dict[str, str]:
            return {"operation_id": "http-operation", "status": state["http_status"]}

    class Http:
        async def __aenter__(self) -> "Http":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def post(self, *args: Any, **kwargs: Any) -> Response:
            state["http_called"] = True
            return Response()

    class Session:
        def __init__(self, *args: Any) -> None:
            pass

        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def initialize(self) -> None:
            pass

        async def call_tool(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
            state["mcp_called"] = True
            return SimpleNamespace(
                is_error=False,
                structured_content={"operation_id": "mcp-operation", "status": state["mcp_status"]},
            )

    @asynccontextmanager
    async def stream(*args: Any, **kwargs: Any) -> Any:
        yield None, None

    real_timeout = asyncio.timeout

    def bounded_timeout(delay: float | None) -> asyncio.Timeout:
        return real_timeout(0.01 if delay == 15 else delay)

    monkeypatch.setattr(live_trial, "Client", Client)
    monkeypatch.setattr(live_trial, "Service", Service)
    monkeypatch.setattr(live_trial, "create_app", lambda *args: object())
    monkeypatch.setattr(live_trial, "secret", lambda path: "fake")
    monkeypatch.setattr(live_trial.uvicorn, "Server", Server)
    monkeypatch.setattr(live_trial, "create_mcp_http_client", lambda **kwargs: Http())
    monkeypatch.setattr(live_trial, "streamable_http_client", stream)
    monkeypatch.setattr(live_trial, "ClientSession", Session)
    monkeypatch.setattr(live_trial.asyncio, "timeout", bounded_timeout)
    return state


def test_startup_timeout_is_fatal_and_still_cleans_up(
    tmp_path: Path, runtime: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    runtime["ready"] = False
    with pytest.raises(TimeoutError):
        asyncio.run(live_trial.trial(configuration(tmp_path), 101, tmp_path / "trial", 1))
    assert "No control incoming" not in capsys.readouterr().out
    assert not runtime.get("http_called")
    assert runtime["client_closed"] and runtime["server_closed"]


@pytest.mark.parametrize("status", ["denied", "failed", "uncertain", "http_error"])
def test_http_probe_cannot_pass_without_a_sent_outcome(
    tmp_path: Path, runtime: dict[str, Any], status: str
) -> None:
    runtime["http_status"] = status
    with pytest.raises(RuntimeError):
        asyncio.run(live_trial.trial(configuration(tmp_path), 101, tmp_path / "trial", 1))
    assert not runtime.get("mcp_called")
    assert json.loads((tmp_path / "trial/outcomes.json").read_text()) == []
    assert runtime["client_closed"] and runtime["server_closed"]


@pytest.mark.parametrize("status", ["denied", "failed", "uncertain"])
def test_mcp_probe_cannot_pass_from_transport_success_alone(
    tmp_path: Path, runtime: dict[str, Any], status: str
) -> None:
    runtime["mcp_status"] = status
    with pytest.raises(RuntimeError, match="not sent"):
        asyncio.run(live_trial.trial(configuration(tmp_path), 101, tmp_path / "trial", 1))
    assert len(json.loads((tmp_path / "trial/outcomes.json").read_text())) == 1
    assert runtime["client_closed"] and runtime["server_closed"]


def test_inbound_timeout_after_successful_probes_is_nonfatal(
    tmp_path: Path, runtime: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    asyncio.run(live_trial.trial(configuration(tmp_path), 101, tmp_path / "trial", 0.01))
    assert len(json.loads((tmp_path / "trial/outcomes.json").read_text())) == 4
    output = capsys.readouterr().out
    assert "REAL_BOT_READY" in output and "No control incoming" in output
    assert runtime["client_closed"] and runtime["server_closed"]


def test_trial_rejects_public_evidence_directory_before_network(
    tmp_path: Path, runtime: dict[str, Any]
) -> None:
    directory = tmp_path / "public"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    with pytest.raises(ValueError, match="Evidence directory"):
        asyncio.run(live_trial.trial(configuration(tmp_path), 101, directory, 1))
    assert not runtime.get("http_called")
    assert not list(directory.iterdir())


def test_evidence_files_are_private_and_public_existing_files_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    live_trial.write_evidence(path, b"private")
    assert path.stat().st_mode & 0o077 == 0
    live_trial.write_evidence(path, b"new")
    assert path.read_bytes() == b"new"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="Evidence files"):
        live_trial.write_evidence(path, b"overwrite")
    assert path.read_bytes() == b"new"


def test_trial_startup_validation_precedes_http_server(
    tmp_path: Path, runtime: dict[str, Any]
) -> None:
    from unittest.mock import AsyncMock, patch

    with (
        patch.object(
            live_trial.Service, "initialize", new=AsyncMock(side_effect=RuntimeError("identity"))
        ),
        patch.object(live_trial.uvicorn, "Server") as server,
    ):
        with pytest.raises(RuntimeError, match="identity"):
            asyncio.run(live_trial.trial(configuration(tmp_path), 101, tmp_path / "trial", 1))
        server.assert_not_called()
    assert runtime["client_closed"]
