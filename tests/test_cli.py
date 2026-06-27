"""CLI end-to-end 테스트 (T5 최소 슬라이스). typer CliRunner 로 add/list 를 실제 실행한다.

결정성: RELAY_HOME(템플릿 사본)·RELAY_DB(임시 DB)를 임시 경로로 돌리고, 주차는 --week 로 고정해
'오늘'에 의존하지 않게 한다(테스트 정책).
"""

import pytest
from typer.testing import CliRunner

from relay.cli import app

runner = CliRunner()
WK = "2026-W26"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RELAY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("RELAY_DB", str(tmp_path / "relay.db"))
    monkeypatch.delenv("RELAY_TEMPLATE", raising=False)


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "relay" in result.output


def test_add_then_list() -> None:
    r = runner.invoke(app, ["task", "add", "장애", "첨부 다운로드 지연", "-s", "그룹웨어", "-w", WK])
    assert r.exit_code == 0, r.output
    assert "✓ 등록됨  [1] 첨부 다운로드 지연" in r.output

    r = runner.invoke(app, ["task", "list", "-s", "그룹웨어", "-w", WK])
    assert r.exit_code == 0, r.output
    assert f"{WK} / 그룹웨어" in r.output
    assert "[1] (진행중) 첨부 다운로드 지연" in r.output


def test_list_empty() -> None:
    r = runner.invoke(app, ["task", "list", "-s", "그룹웨어", "-w", WK])
    assert r.exit_code == 0
    assert "(등록된 task 없음)" in r.output


def test_add_unknown_category_fails() -> None:
    r = runner.invoke(app, ["task", "add", "없는것", "제목", "-s", "그룹웨어", "-w", WK])
    assert r.exit_code != 0
    assert "가능한 값" in r.output


def test_numbering_increments() -> None:
    runner.invoke(app, ["task", "add", "장애", "A", "-s", "그룹웨어", "-w", WK])
    r = runner.invoke(app, ["task", "add", "정기", "B", "-s", "그룹웨어", "-w", WK])
    assert "[2] B" in r.output


def test_last_used_system_defaults() -> None:
    """--system 없이 list 하면 마지막 사용 시스템으로 기본 설정된다(설계 #10)."""
    runner.invoke(app, ["task", "add", "장애", "A", "-s", "포털", "-w", WK])
    r = runner.invoke(app, ["task", "list", "-w", WK])  # --system 생략
    assert r.exit_code == 0, r.output
    assert f"{WK} / 포털" in r.output
