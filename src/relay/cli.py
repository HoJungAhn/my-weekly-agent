"""CLI 진입점 — 설계결정 #10(대화형 slash 쉘).

`relay` 를 인자 없이 실행하면 대화형 쉘(REPL)이 뜬다. task 조작은 모두 쉘 안의 slash 명령으로
한다(`shell.py`). 이 모듈은 진입 + 버전 출력만 담당하는 얇은 레이어다.
"""

from __future__ import annotations

import typer

from relay import __version__
from relay.shell import run_shell

app = typer.Typer(
    help="Relay — 주간보고 정리 agent (대화형 쉘)",
    add_completion=False,
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """인자 없이 실행하면 대화형 slash 쉘을 띄운다."""
    if ctx.invoked_subcommand is None:
        run_shell()


@app.command()
def version() -> None:
    """버전을 출력한다."""
    typer.echo(f"relay {__version__}")


if __name__ == "__main__":
    app()
