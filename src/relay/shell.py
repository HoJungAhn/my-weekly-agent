"""대화형 slash 쉘(REPL) — 설계 결정 #10(대화형 인터페이스).

`relay` 실행 시 상주 세션이 뜨고 `/task add ...`·`/list` 같은 slash 명령을 연속 입력한다.
활성 컨텍스트(주/시스템)는 세션 메모리에 유지한다.

핵심: 명령 처리는 순수 함수 ``dispatch(session, line) -> list[str]`` 로 분리해 stdin/stdout 없이
테스트한다. slash 명령은 ``services/`` 함수를 재사용하는 한 겹 레이어일 뿐이다(의존성 정책: 표준
라이브러리 input+shlex 만 사용, 무거운 REPL 프레임워크 미사용).
"""

from __future__ import annotations

import getpass
import shlex
import sys
from dataclasses import dataclass

from relay.config import DEFAULT_SYSTEM, db_path, load_llm_config, save_api_key
from relay.db import Store, connect, init_db
from relay.llm import DEFAULT_MODEL, Classification, LLMProvider, make_provider
from relay.models import Status
from relay.services.capture import classify_capture
from relay.services.tasks import (
    create_task,
    list_tasks_numbered,
    resolve_by_number,
    resolve_category,
)
from relay.template import Template, load_active_template
from relay.week import current_week_key, parse_week_key

_STATUS_VALUES = ", ".join(s.value for s in Status)

HELP = f"""\
사용법 — slash 명령(맨 앞 / 는 생략 가능). slash 없는 일반 문장은 LLM 이 분류해 등록한다:
  <자연어 메모>            예: "어제 첨부 다운로드가 느려서 로그 봤어" → 분류+제목 제안 후 확인
  /add <카테고리> <제목>   신규 task 추가 (진행중, 카테고리 직접 지정)
  /list                    활성 주차·시스템의 task를 번호와 함께 표시
  /update <번호> <상태>    상태 변경 ({_STATUS_VALUES})
  /note <번호> <내용>      진행 메모 누적
  /history <번호>          같은 작업의 주차별 이력(thread)
  /use <시스템>            활성 시스템 전환
  /week <YYYY-Www>         활성 주차 전환
  /help                    이 도움말
  /quit                    종료"""


class QuitShell(Exception):
    """쉘 종료 신호."""


@dataclass
class Session:
    """대화형 세션 상태 — 활성 컨텍스트(주/시스템)와 분류 대기(pending)를 메모리에 유지한다."""

    store: Store
    template: Template
    week: str
    system: str
    provider: LLMProvider
    pending: Classification | None = None  # 자연어 캐처 후 사용자 확인 대기 중인 분류


def _cmd_add(s: Session, args: list[str]) -> list[str]:
    if len(args) < 2:
        return ["⚠ 사용법: /add <카테고리> <제목>"]
    category, title = args[0], " ".join(args[1:])
    try:
        category_key, label = resolve_category(s.template, category)
    except ValueError as e:
        return [f"⚠ {e}"]
    task, number = create_task(s.store, category_key, title=title, week=s.week, system=s.system)
    return [f"✓ 등록됨  [{number}] {task.title}  ({label})"]


def _cmd_list(s: Session, args: list[str]) -> list[str]:
    rows = list_tasks_numbered(s.store, s.week, s.system)
    if not rows:
        return ["  (등록된 task 없음)"]
    return [f"  [{n}] ({t.status.value}) {t.title}" for n, t in rows]


def _cmd_update(s: Session, args: list[str]) -> list[str]:
    if len(args) < 2:
        return ["⚠ 사용법: /update <번호> <상태>"]
    try:
        number = int(args[0])
    except ValueError:
        return [f"⚠ 번호는 숫자여야 합니다: {args[0]!r}"]
    try:
        new_status = Status(args[1])
    except ValueError:
        return [f"⚠ 알 수 없는 상태: {args[1]!r}. 가능한 값: {_STATUS_VALUES}"]
    try:
        task = resolve_by_number(s.store, s.week, s.system, number)
    except ValueError as e:
        return [f"⚠ {e}"]
    s.store.set_status(task.id, new_status)
    return [f"✓ [{number}] {task.title} → {new_status.value}"]


def _cmd_note(s: Session, args: list[str]) -> list[str]:
    if len(args) < 2:
        return ["⚠ 사용법: /note <번호> <내용>"]
    try:
        number = int(args[0])
    except ValueError:
        return [f"⚠ 번호는 숫자여야 합니다: {args[0]!r}"]
    body = " ".join(args[1:])
    try:
        task = resolve_by_number(s.store, s.week, s.system, number)
    except ValueError as e:
        return [f"⚠ {e}"]
    s.store.add_note(task.id, body)
    return [f"✓ 메모 추가  [{number}] {task.title}"]


def _cmd_history(s: Session, args: list[str]) -> list[str]:
    if not args:
        return ["⚠ 사용법: /history <번호>"]
    try:
        number = int(args[0])
    except ValueError:
        return [f"⚠ 번호는 숫자여야 합니다: {args[0]!r}"]
    try:
        task = resolve_by_number(s.store, s.week, s.system, number)
    except ValueError as e:
        return [f"⚠ {e}"]
    entries = s.store.thread_history(task.thread_id)
    current = entries[-1].status.value if entries else "-"
    out = [f'"{task.title}" — {len(entries)}주차 / 현재: {current}']
    for entry in entries:
        origin = "신규 등록" if entry.carried_from is None else f"이월({entry.carry_count})"
        label = s.template.label_of(entry.category_key)
        out.append(f"  {entry.week}  {origin}  ({entry.status.value}, {label})")
        out.extend(f"           · {n.body}" for n in s.store.list_notes(entry.id))
    return out


def _cmd_use(s: Session, args: list[str]) -> list[str]:
    if not args:
        return ["⚠ 사용법: /use <시스템>"]
    s.system = " ".join(args)
    return [f"활성 시스템 → {s.system}"]


def _cmd_week(s: Session, args: list[str]) -> list[str]:
    if not args:
        return ["⚠ 사용법: /week <YYYY-Www>"]
    try:
        parse_week_key(args[0])
    except ValueError as e:
        return [f"⚠ {e}"]
    s.week = args[0]
    return [f"활성 주차 → {s.week}"]


def _cmd_help(s: Session, args: list[str]) -> list[str]:
    return HELP.splitlines()


def _cmd_quit(s: Session, args: list[str]) -> list[str]:
    raise QuitShell


_COMMANDS = {
    "add": _cmd_add,
    "list": _cmd_list,
    "update": _cmd_update,
    "note": _cmd_note,
    "history": _cmd_history,
    "use": _cmd_use,
    "week": _cmd_week,
    "help": _cmd_help,
    "quit": _cmd_quit,
    "exit": _cmd_quit,
    "q": _cmd_quit,
}


def _register_pending(session: Session, title: str) -> list[str]:
    pending = session.pending
    assert pending is not None
    session.pending = None
    task, number = create_task(
        session.store,
        pending.category_key,
        title=title,
        detail=pending.detail,
        week=session.week,
        system=session.system,
    )
    label = session.template.label_of(pending.category_key)
    return [f"✓ 등록됨  [{number}] {task.title}  ({label})"]


def _resolve_pending(session: Session, text: str) -> list[str]:
    """분류 확인 대기 상태에서의 응답 처리: y=등록 / n=취소 / 그 외=그 제목으로 등록."""
    if text in ("y", "Y", "yes", "Yes", "등록", "ㅇ"):
        return _register_pending(session, session.pending.title)
    if text in ("n", "N", "no", "No", "취소"):
        session.pending = None
        return ["취소됨"]
    if not text:
        return ["먼저 y(등록) / n(취소) 또는 새 제목을 입력하세요."]
    return _register_pending(session, text)  # 입력한 텍스트를 새 제목으로 등록


def _capture(session: Session, text: str) -> list[str]:
    """자연어 메모를 LLM 으로 분류해 사용자 확인 대기(pending) 상태로 둔다."""
    try:
        result = classify_capture(session.provider, session.template, text)
    except Exception as e:  # 분류 실패·API 오류로 쉘이 죽지 않게
        return [f"⚠ 분류 실패: {e}"]
    session.pending = result
    label = session.template.label_of(result.category_key)
    return [
        f"🤖 분류: {label} ({result.category_key})",
        f"   제목: {result.title}",
        "   [y=등록 / n=취소 / 새 제목 입력=그 제목으로 등록]",
    ]


def dispatch(session: Session, line: str) -> list[str]:
    """한 줄 입력을 실행하고 출력 줄들을 반환한다(순수 — IO 없음).

    우선순위: 분류 확인 대기(pending) → slash/명령 → 자연어 캐처.
    종료 명령은 :class:`QuitShell` 을 올린다. slash(``/``) 접두는 선택이며,
    ``/task add`` 처럼 ``task`` 접두어도 허용한다(SPEC 원안 표기).
    """
    if session.pending is not None:
        return _resolve_pending(session, line.strip())

    try:
        tokens = shlex.split(line)
    except ValueError as e:  # 따옴표 안 닫힘 등
        return [f"⚠ 입력 파싱 실패: {e}"]
    if not tokens:
        return []

    cmd = tokens[0].lstrip("/").lower()
    args = tokens[1:]
    if cmd == "task" and args:  # '/task add ...' 형태 지원
        cmd, args = args[0].lower(), args[1:]

    handler = _COMMANDS.get(cmd)
    if handler is not None:
        return handler(session, args)

    if line.lstrip().startswith("/"):  # 명시적 명령인데 알 수 없음
        return [f"알 수 없는 명령: {tokens[0]!r} (/help 로 목록 확인)"]

    return _capture(session, line.strip())  # slash 없는 일반 문장 → 자연어 캐처


def _open_store() -> Store:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    init_db(conn)
    return Store(conn)


def _prompt_and_store_api_key(model: str | None) -> str | None:
    """첫 실행 시 API 키를 한 번 입력받아 설정 파일에 저장한다. 비우면 오프라인(None)."""
    print("Anthropic API 키가 설정돼 있지 않습니다.")
    print("키를 입력하면 설정 파일에 저장하고 Claude 분류를 사용합니다.")
    print("(키 없이 Enter → 오프라인 규칙 분류로 진행)")
    try:
        key = getpass.getpass("ANTHROPIC_API_KEY> ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not key:
        return None
    path = save_api_key(key, model)
    print(f"  저장됨: {path}")
    return key


def run_shell() -> None:
    """대화형 쉘 진입점. DB·템플릿을 로드하고 활성 컨텍스트를 세팅한 뒤 루프를 돈다."""
    store = _open_store()
    cfg = load_llm_config()
    api_key = cfg.api_key
    # 설정에 키가 없고 대화형(tty)이면 첫 실행 시 한 번 입력받아 저장한다.
    if api_key is None and sys.stdin.isatty():
        api_key = _prompt_and_store_api_key(cfg.model)

    provider = make_provider(api_key, cfg.model)
    session = Session(
        store=store,
        template=load_active_template(),
        week=current_week_key(),
        system=store.last_used_system() or DEFAULT_SYSTEM,
        provider=provider,
    )
    print("Relay 주간보고 쉘 — /help 로 명령 목록, /quit 로 종료")
    print("  · 자연어로 입력하면 LLM 이 카테고리를 분류해 등록합니다(확인 후).")
    if api_key:
        print(f"  · LLM: Claude ({cfg.model or DEFAULT_MODEL})")
    else:
        print("  · LLM: 오프라인 규칙 분류 (API 키 미설정)")
    while True:
        try:
            line = input(f"[{session.week} / {session.system}] > ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        try:
            for out in dispatch(session, line):
                print(out)
        except QuitShell:
            print("종료합니다.")
            break
