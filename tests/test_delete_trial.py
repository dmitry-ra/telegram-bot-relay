"""Live deletion helper safety, using inert HTTP/MCP adapters only."""

import asyncio
import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.helpers import configuration

spec = importlib.util.spec_from_file_location(
    "relay_delete_trial", Path(__file__).resolve().parents[1] / "tools/delete_trial.py"
)
assert spec is not None and spec.loader is not None
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)
ORDER = ["http-send", "http-delete", "mcp-send", "mcp-delete"]


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"calls": [], "overrides": {}}
    directory = tmp_path / "evidence"

    def operation(surface: str, request: dict[str, Any]) -> dict[str, Any]:
        name = surface + ("-send" if request["method"] == "send_message" else "-delete")
        # Before every side effect, intent must already be flushed to disk.
        intent = json.loads((directory / (name + "-intent.json")).read_text())
        assert intent == {"surface": surface, "request": request}
        state["calls"].append(name)
        if request["method"] == "delete_message":
            assert request == {"method": "delete_message", "chat_id": 101, "message_id": 42}
            result: Any = True
        else:
            result = {"message_id": 42, "chat": {"id": 101}}
        value = {"operation_id": name, "status": "sent", "result": result}
        value.update(state["overrides"].get(name, {}))
        return value

    class Response:
        def __init__(self, value: dict[str, Any]) -> None:
            self.value = value

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return self.value

    class Http:
        async def __aenter__(self) -> "Http":
            return self

        async def __aexit__(self, *args: Any) -> None:
            state["http_closed"] = True

        async def get(self, url: str) -> Response:
            return Response({"status": "ok"})

        async def post(self, url: str, json: dict[str, Any]) -> Response:
            return Response(operation("http", json))

    class Session:
        def __init__(self, *args: Any) -> None:
            pass

        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *args: Any) -> None:
            state["mcp_closed"] = True

        async def initialize(self) -> None:
            pass

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="delete_message",
                        annotations=SimpleNamespace(destructive_hint=True),
                        model_dump=lambda **kwargs: {"name": "delete_message"},
                    )
                ]
            )

        async def call_tool(self, method: str, arguments: dict[str, Any]) -> SimpleNamespace:
            value = operation("mcp", arguments["request"])
            return SimpleNamespace(
                is_error=state.get("mcp_error", False),
                structured_content=None if state.get("mcp_missing") else value,
            )

    @asynccontextmanager
    async def stream(*args: Any, **kwargs: Any) -> Any:
        yield None, None

    monkeypatch.setattr(trial, "load", lambda path: configuration(tmp_path))
    monkeypatch.setattr(trial, "secret", lambda path: "fake")
    monkeypatch.setattr(trial, "create_mcp_http_client", lambda **kwargs: Http())
    monkeypatch.setattr(trial, "streamable_http_client", stream)
    monkeypatch.setattr(trial, "ClientSession", Session)
    return state


def run(tmp_path: Path) -> None:
    asyncio.run(trial.trial(tmp_path / "unused.toml", 101, tmp_path / "evidence"))


def test_success_retains_receipts_and_reused_directory_cannot_replay(
    tmp_path: Path, runtime: dict[str, Any]
) -> None:
    run(tmp_path)
    assert runtime["calls"] == ORDER
    directory = tmp_path / "evidence"
    report = json.loads((directory / "report.json").read_text())
    assert report["status"] == "passed" and len(report["deleted_test_messages"]) == 2
    for name in ORDER:
        result = json.loads((directory / (name + "-result.json")).read_text())
        assert result["operation_id"] == name and result["status"] == "sent"
    with pytest.raises(FileExistsError):
        run(tmp_path)
    assert runtime["calls"] == ORDER
    assert runtime["http_closed"] and runtime["mcp_closed"]


@pytest.mark.parametrize("name", ORDER)
@pytest.mark.parametrize("status", ["failed", "denied", "uncertain"])
def test_unsuccessful_outcome_is_retained_and_stops_without_retry(
    tmp_path: Path, runtime: dict[str, Any], name: str, status: str
) -> None:
    runtime["overrides"][name] = {"status": status}
    with pytest.raises(RuntimeError, match="not confirmed"):
        run(tmp_path)
    assert runtime["calls"] == ORDER[: ORDER.index(name) + 1]
    directory = tmp_path / "evidence"
    assert json.loads((directory / (name + "-result.json")).read_text())["status"] == status
    assert not (directory / "report.json").exists()
    assert runtime["http_closed"] and runtime["mcp_closed"]


@pytest.mark.parametrize("surface", ["http", "mcp"])
@pytest.mark.parametrize(
    "message",
    [
        None,
        {"message_id": True, "chat": {"id": 101}},
        {"message_id": 0, "chat": {"id": 101}},
        {"message_id": 42, "chat": {"id": 202}},
        {"message_id": 42},
    ],
)
def test_unconfirmed_message_identity_never_reaches_deletion(
    tmp_path: Path, runtime: dict[str, Any], surface: str, message: Any
) -> None:
    name = surface + "-send"
    runtime["overrides"][name] = {"result": message}
    with pytest.raises(ValueError):
        run(tmp_path)
    assert runtime["calls"] == ORDER[: ORDER.index(name) + 1]
    assert not (tmp_path / "evidence" / (surface + "-delete-intent.json")).exists()


@pytest.mark.parametrize("surface", ["http", "mcp"])
def test_false_delete_result_cannot_report_success(
    tmp_path: Path, runtime: dict[str, Any], surface: str
) -> None:
    name = surface + "-delete"
    runtime["overrides"][name] = {"result": False}
    with pytest.raises(ValueError, match="not confirmed"):
        run(tmp_path)
    assert runtime["calls"] == ORDER[: ORDER.index(name) + 1]
    assert not (tmp_path / "evidence/report.json").exists()


@pytest.mark.parametrize("flag", ["mcp_error", "mcp_missing"])
def test_unavailable_mcp_outcome_stops_with_intent_and_without_replay(
    tmp_path: Path, runtime: dict[str, Any], flag: str
) -> None:
    runtime[flag] = True
    with pytest.raises(RuntimeError, match="outcome unavailable"):
        run(tmp_path)
    assert runtime["calls"] == ORDER[:3]
    assert (tmp_path / "evidence/mcp-send-intent.json").exists()
    assert not (tmp_path / "evidence/mcp-delete-intent.json").exists()
