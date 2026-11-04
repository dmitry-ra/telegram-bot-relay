import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import JsonValue

from telegram_bot_relay.application.models import Media, Operation, SendMedia
from telegram_bot_relay.application.operations import Operations
from telegram_bot_relay.journal import writer
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.telegram.attachments import prepare
from tests.helpers import FakeClient, configuration


@pytest.mark.parametrize("kind", ["incoming", "upload"])
@pytest.mark.parametrize("failure", [False, True])
def test_parent_entry_is_durable_before_download_or_send(
    tmp_path: Path, kind: str, failure: bool
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        period = journal.root / journal.period
        synced: list[Path] = []
        real_sync = writer.sync_directory

        def sync(path: Path) -> None:
            if failure and path == period:
                raise OSError("parent sync failed")
            real_sync(path)
            synced.append(path)

        class Client(FakeClient):
            async def download(self, file_id: str, path: Path, limit: int, timeout: int) -> None:
                assert period in synced
                await super().download(file_id, path, limit, timeout)

            async def execute(self, operation: Operation) -> JsonValue:
                assert period in synced
                assert any(record.type == "operation_artifacts" for record in journal.records())
                return await super().execute(operation)

        client = Client()
        with patch.object(writer, "sync_directory", side_effect=sync):
            if kind == "upload":
                source = tmp_path / "source"
                source.write_bytes(b"upload")
                config.attachments.upload_roots = [str(tmp_path)]
                result = await Operations(config, journal, client).execute(
                    SendMedia(chat_id=101, media=[Media(kind="document", path=str(source))]), "test"
                )
                assert result.status == ("failed" if failure else "sent")
            elif failure:
                with pytest.raises(OSError, match="parent sync failed"):
                    await prepare(
                        client,
                        {"document": {"file_id": "one"}},
                        period / "attachments",
                        config.attachments,
                        True,
                    )
            else:
                downloads = await prepare(
                    client,
                    {"document": {"file_id": "one"}},
                    period / "attachments",
                    config.attachments,
                    True,
                )
                assert downloads[0]["status"] == "saved"
        if failure:
            assert not client.downloaded and not client.sent
        else:
            assert period in synced
        journal.close()

    asyncio.run(run())
