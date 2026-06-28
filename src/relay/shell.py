"""대화형 slash 쉘(REPL) — 설계 결정 #10(대화형 인터페이스).

`relay` 실행 시 상주 세션이 뜨고 `/task add ...`·`/list` 같은 slash 명령을 연속 입력한다.
활성 컨텍스트(주/시스템)는 세션 메모리에 유지한다.

핵심: 명령 처리는 순수 함수 ``dispatch(session, line) -> list[str]`` 로 분리해 stdin/stdout 없이
테스트한다. slash 명령은 ``services/`` 함수를 재사용하는 한 겹 레이어일 뿐이다(의존성 정책: 표준
라이브러리 input+shlex 만 사용, 무거운 REPL 프레임워크 미사용).
"""

from __future__ import annotations

import getpass
import itertools
import shlex
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator

from relay.config import DEFAULT_SYSTEM, db_path, load_llm_config, save_api_key
from relay.db import Store, connect, init_db
from relay.llm import DEFAULT_MODEL, Classification, LLMProvider, make_provider
from relay.models import ReportStatus, Status
from relay.services.capture import classify_capture
from relay.services.draft import create_draft
from relay.services.render import render_report
from relay.services.report import generate_report
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
  /draft                   전주 미완료 이월 + 다음주계획 승격으로 금주 초안 생성
  /add <카테고리> <제목>   신규 task 추가 (진행중, 카테고리 직접 지정)
  /list                    활성 주차·시스템의 task를 번호와 함께 표시
  /update <번호> <상태>    상태 변경 ({_STATUS_VALUES})
  /delete <번호>           task 삭제 (연결된 메모도 함께 삭제)
  /note <번호> <내용>      진행 메모 누적
  /history <번호>          같은 작업의 주차별 이력(thread)
  /review                  현재 주차 보고서 Markdown 미리보기
  /finalize                보고서 확정(finalized) + Markdown 출력
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


def _cmd_draft(s: Session, args: list[str]) -> list[str]:
    result = create_draft(s.store, s.template, s.week, s.system)
    out: list[str] = []

    for w in result.warnings:
        out.append(f"⚠ {w}")

    if result.total == 0 and not result.warnings:
        out.append(f"전주({result.prev_week})에서 이월·승격할 항목이 없습니다.")
        return out

    if result.total > 0:
        out.append(f"📋 초안 생성: {result.week} / {result.system}")
        out.append(
            f"  이월 {len(result.carried)}건 + 승격 {len(result.promoted)}건"
            f" = {result.total}개 task"
        )
        skipped = result.skipped_carry + result.skipped_promote
        if skipped:
            out.append(f"  (이미 존재해 스킵: {skipped}건)")
        for t in result.carried:
            label = s.template.label_of(t.category_key)
            out.append(f"  ↩ [이월#{t.carry_count}] ({t.status.value}) {t.title}  [{label}]")
        for t in result.promoted:
            label = s.template.label_of(t.category_key)
            out.append(f"  ↑ [승격] {t.title}  [{label}]")

    return out


def _cmd_review(s: Session, args: list[str]) -> list[str]:
    """현재 주차 보고서를 Markdown으로 미리본다(상태 변경 없음)."""
    return render_report(s.store, s.template, s.week, s.system).splitlines()


def _cmd_finalize(s: Session, args: list[str]) -> list[str]:
    """보고서를 finalized로 확정하고 LLM 서술이 포함된 Markdown을 출력한다."""
    s.store.set_report_status(s.week, s.system, ReportStatus.FINALIZED)
    md, warnings = generate_report(s.store, s.template, s.provider, s.week, s.system)
    out = [f"✓ 보고 확정됨  {s.week} / {s.system}  (finalized)", ""]
    if warnings:
        out.append("⚠ 검증 미통과 항목이 있습니다. 내용을 확인하세요:")
        out.extend(f"  - {w}" for w in warnings)
        out.append("")
    out.extend(md.splitlines())
    return out


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


def _cmd_delete(s: Session, args: list[str]) -> list[str]:
    if not args:
        return ["⚠ 사용법: /delete <번호>"]
    try:
        number = int(args[0])
    except ValueError:
        return [f"⚠ 번호는 숫자여야 합니다: {args[0]!r}"]
    try:
        task = resolve_by_number(s.store, s.week, s.system, number)
    except ValueError as e:
        return [f"⚠ {e}"]

    # finalized 보고의 task 삭제는 경고 표시(차단 안 함 — 설계: 사람 책임 문서)
    report = s.store.get_report(s.week, s.system)
    warning = ""
    if report and report.status.value == "finalized":
        warning = "⚠ 이 주차 보고가 finalized 상태입니다. 삭제하면 원본이 변경됩니다.\n"

    s.store.delete_task(task.id)
    return [f"{warning}✓ 삭제됨  [{number}] {task.title}"]


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
    "draft": _cmd_draft,
    "review": _cmd_review,
    "finalize": _cmd_finalize,
    "add": _cmd_add,
    "delete": _cmd_delete,
    "del": _cmd_delete,
    "rm": _cmd_delete,
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


@contextmanager
def _spinner(msg: str = "LLM 분류 중") -> Generator[None, None, None]:
    """LLM 호출 중 터미널에 스피너를 표시하는 컨텍스트 매니저.

    tty 에서만 동작한다 — 파이프·테스트(비대화형)에서는 스레드를 시작하지 않아
    완전히 결정론적이다.
    """
    if not sys.stdout.isatty():
        yield
        return

    stop = threading.Event()
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _spin() -> None:
        for frame in itertools.cycle(frames):
            if stop.is_set():
                break
            sys.stdout.write(f"\r  {frame} {msg}...")
            sys.stdout.flush()
            time.sleep(0.08)
        sys.stdout.write("\r" + " " * (len(msg) + 10) + "\r")
        sys.stdout.flush()

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=0.3)


def _capture(session: Session, text: str) -> list[str]:
    """자연어 메모를 LLM 으로 분류해 사용자 확인 대기(pending) 상태로 둔다."""
    try:
        with _spinner():
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
    """입력을 실행하고 출력 줄들을 반환한다(순수 — IO 없음).

    ``line`` 은 단일 줄 또는 멀티라인(자연어 캐처)일 수 있다. 멀티라인이 오면
    명령 파싱은 첫 줄만, 자연어 캐처는 전체 텍스트를 LLM 에 넘긴다.

    우선순위: 분류 확인 대기(pending) → slash/명령 → 자연어 캐처.
    종료 명령은 :class:`QuitShell` 을 올린다. slash(``/``) 접두는 선택이며,
    ``/task add`` 처럼 ``task`` 접두어도 허용한다(SPEC 원안 표기).
    """
    if session.pending is not None:
        # pending 응답(y/n/새 제목)은 첫 줄만 사용
        return _resolve_pending(session, line.split("\n")[0].strip())

    # 명령 파싱은 항상 첫 줄만(멀티라인 입력에서도 명령어는 첫 줄에 있음)
    first_line = line.split("\n")[0]
    try:
        tokens = shlex.split(first_line)
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

    if first_line.lstrip().startswith("/"):  # 명시적 명령인데 알 수 없음
        return [f"알 수 없는 명령: {tokens[0]!r} (/help 로 목록 확인)"]

    return _capture(session, line.strip())  # slash 없는 문장(멀티라인 포함) → 자연어 캐처


def _collect_multiline(first_line: str) -> str:
    """자연어 입력의 첫 줄 이후를 ``... > `` 프롬프트로 계속 읽는다.

    빈 줄(Enter)이 오면 수집 종료. EOF·Ctrl+C 도 즉시 종료.
    수집된 줄들을 ``\\n`` 으로 이어 반환한다.
    """
    lines = [first_line]
    while True:
        try:
            cont = input("... > ")
        except (EOFError, KeyboardInterrupt):
            break
        if not cont.strip():
            break
        lines.append(cont)
    return "\n".join(lines)


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
    print("  · 자연어는 여러 줄 입력 가능 — 빈 줄(Enter)로 완료합니다.")
    if api_key:
        print(f"  · LLM: Claude ({cfg.model or DEFAULT_MODEL})")
    else:
        print("  · LLM: 오프라인 규칙 분류 (API 키 미설정)")
    while True:
        try:
            first = input(f"[{session.week} / {session.system}] > ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        stripped = first.strip()
        # 자연어 입력(pending 응답·slash 명령·빈 줄 아님) → 빈 줄까지 멀티라인 수집
        if stripped and session.pending is None and not stripped.startswith("/"):
            line = _collect_multiline(first)
        else:
            line = first

        try:
            for out in dispatch(session, line):
                print(out)
        except QuitShell:
            print("종료합니다.")
            break
