"""CLI 진입점 (단발 CLI — 설계결정 #10).

이 레이어는 '얇게' 유지한다: 인자 파싱 + 와이어링(템플릿·DB 로드) + 서비스 호출 + 출력만 담당하고,
비즈니스 로직(이월/승격/집계/검증)은 services/ 의 순수 함수에 둔다.
"""

from __future__ import annotations

import typer

from relay import __version__
from relay.config import db_path
from relay.db import Store, connect, init_db
from relay.services.tasks import (
    create_task,
    list_tasks_numbered,
    resolve_category,
    resolve_system,
)
from relay.template import load_active_template
from relay.week import current_week_key

app = typer.Typer(
    help="Relay — 주간보고 정리 agent",
    no_args_is_help=True,
    add_completion=False,
)

task_app = typer.Typer(help="task 추가·조회", no_args_is_help=True)
app.add_typer(task_app, name="task")


@app.callback()
def main() -> None:
    """Relay — 운영 시스템 주간보고서 작성·정리 도구."""


def _open_store() -> Store:
    """DB 를 열고(없으면 생성) 초기화된 Store 를 반환한다."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    init_db(conn)
    return Store(conn)


@app.command()
def version() -> None:
    """버전을 출력한다."""
    typer.echo(f"relay {__version__}")


@task_app.command("add")
def task_add(
    category: str = typer.Argument(..., help="카테고리(키 또는 이름 일부, 예: 장애)"),
    title: str = typer.Argument(..., help="task 제목"),
    system: str | None = typer.Option(None, "--system", "-s", help="대상 시스템(기본: 마지막 사용)"),
    week: str | None = typer.Option(None, "--week", "-w", help="주차 키(기본: 이번 업무주)"),
) -> None:
    """신규 task 를 추가한다 (status: 진행중)."""
    template = load_active_template()
    try:
        category_key, category_label = resolve_category(template, category)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e

    store = _open_store()
    wk = week or current_week_key()
    sys_name = resolve_system(store, system)
    task, number = create_task(store, category_key, title=title, week=wk, system=sys_name)
    typer.echo(f"✓ 등록됨  [{number}] {task.title}  ({category_label})")


@task_app.command("list")
def task_list(
    system: str | None = typer.Option(None, "--system", "-s", help="대상 시스템(기본: 마지막 사용)"),
    week: str | None = typer.Option(None, "--week", "-w", help="주차 키(기본: 이번 업무주)"),
) -> None:
    """현재 주차·시스템의 task 를 작은 번호와 함께 보여준다."""
    store = _open_store()
    wk = week or current_week_key()
    sys_name = resolve_system(store, system)
    typer.echo(f"{wk} / {sys_name}")
    rows = list_tasks_numbered(store, wk, sys_name)
    if not rows:
        typer.echo("  (등록된 task 없음)")
        return
    for number, task in rows:
        typer.echo(f"  [{number}] ({task.status.value}) {task.title}")


if __name__ == "__main__":
    app()
