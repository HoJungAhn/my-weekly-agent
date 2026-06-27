"""CLI 진입점 테스트 — 버전 출력 + 대화형 쉘 기동/종료 (설계 #10).

task 조작 자체는 test_shell.py(dispatch)에서 검증한다.
"""

import pytest
from typer.testing import CliRunner

from relay.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RELAY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("RELAY_DB", str(tmp_path / "relay.db"))
    monkeypatch.setenv("RELAY_CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.delenv("RELAY_TEMPLATE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "relay" in result.output


def test_bare_invocation_launches_shell_and_quits() -> None:
    result = runner.invoke(app, [], input="/quit\n")
    assert result.exit_code == 0, result.output
    assert "Relay 주간보고 쉘" in result.output
    assert "종료합니다." in result.output


def test_shell_eof_exits_cleanly() -> None:
    result = runner.invoke(app, [], input="")  # 즉시 EOF
    assert result.exit_code == 0
