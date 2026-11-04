import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from telegram_bot_relay import cli
from telegram_bot_relay.config import Gateway


@pytest.mark.parametrize(
    "url",
    [
        "http://user:example@localhost",
        "http://user@localhost",
        "http://@localhost",
        "http://localhost?token=example",
        "http://localhost?",
        "http://localhost#example",
        "http://localhost#",
        "ftp://localhost",
        "http:///missing",
        "http://localhost:invalid",
    ],
)
def test_gateway_rejects_unsafe_or_invalid_base_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        Gateway(url=url, target="work", start="oldest")


@pytest.mark.parametrize(
    "url", ["http://localhost:8090", "https://example.invalid/base/", "http://[::1]:8090"]
)
def test_gateway_accepts_http_base_urls_without_changing_destination(url: str) -> None:
    assert Gateway(url=url, target="work", start="oldest").url == url


def test_url_credentials_fail_before_journal_or_network_and_are_not_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'token_file="unused"\napi_token_file="unused"\ndata_dir="unused"\n[gateway]\nurl="http://user:example-secret@localhost"\ntarget="work"\nstart="oldest"\n'
    )
    previous = os.umask(0o077)
    try:
        with (
            patch("sys.argv", ["relay", "--config", str(config)]),
            patch.object(cli, "Journal") as journal,
            patch.object(cli, "Client") as client,
        ):
            with pytest.raises(SystemExit) as result:
                cli.main()
            assert result.value.code == 1
            journal.assert_not_called()
            client.assert_not_called()
    finally:
        os.umask(previous)
    assert "example-secret" not in capsys.readouterr().err
