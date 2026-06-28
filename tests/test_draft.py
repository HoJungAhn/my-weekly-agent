"""이월(carry-over)·승격(promotion) 초안 생성 서비스 테스트 (T7 / 설계 #4).

멱등성·엣지케이스(첫 주/보고 없음/전주 미확정) 포함.
"""

from __future__ import annotations

from itertools import count

import pytest

from relay.db import Store, connect, init_db
from relay.models import CARRYABLE_STATUSES, ReportStatus, Status, Task
from relay.services.draft import create_draft
from relay.template import default_template_path, load_template
from relay.week import shift_week


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    return c


@pytest.fixture
def store(conn):
    ids = count(1)
    return Store(conn, id_factory=lambda: f"tid{next(ids)}")


@pytest.fixture
def template():
    return load_template(default_template_path())


WEEK = "2026-W26"
PREV = shift_week(WEEK, -1)
SYSTEM = "그룹웨어"


def _add(store, category_key, title, status=Status.IN_PROGRESS, week=PREV):
    return store.add_task(
        Task(
            week=week,
            system=SYSTEM,
            category_key=category_key,
            title=title,
            status=status,
        )
    )


def _finalize_prev(store):
    store.set_report_status(PREV, SYSTEM, ReportStatus.FINALIZED)


# ──────────────────────────────────────────
# 엣지케이스: 전주 보고 없음 / task 없음
# ──────────────────────────────────────────


def test_no_prev_report_returns_empty_with_warning(store, template):
    result = create_draft(store, template, WEEK, SYSTEM)
    assert result.total == 0
    assert any("보고 기록이 없습니다" in w for w in result.warnings)


def test_prev_report_exists_but_no_tasks(store, template):
    store.set_report_status(PREV, SYSTEM, ReportStatus.FINALIZED)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert result.total == 0
    assert any("task가 없습니다" in w for w in result.warnings)


def test_prev_not_finalized_produces_warning_but_still_carries(store, template):
    store.set_report_status(PREV, SYSTEM, ReportStatus.IN_PROGRESS)  # finalized 아님
    _add(store, "incident", "미완료 이슈", Status.INCOMPLETE)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert any("finalized되지 않았습니다" in w for w in result.warnings)
    assert len(result.carried) == 1  # 경고지만 이월은 됨


# ──────────────────────────────────────────
# ① 이월 — CARRYABLE_STATUSES
# ──────────────────────────────────────────


def test_carryable_statuses_are_carried(store, template):
    _finalize_prev(store)
    for s in CARRYABLE_STATUSES:
        _add(store, "incident", f"작업_{s.value}", s)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert len(result.carried) == len(CARRYABLE_STATUSES)


def test_done_and_canceled_are_not_carried(store, template):
    _finalize_prev(store)
    _add(store, "incident", "완료 작업", Status.DONE)
    _add(store, "incident", "취소 작업", Status.CANCELED)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert result.total == 0


def test_carried_task_inherits_thread_id(store, template):
    _finalize_prev(store)
    orig = _add(store, "incident", "미완료 이슈", Status.INCOMPLETE)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert result.carried[0].thread_id == orig.thread_id


def test_carried_task_carry_count_incremented(store, template):
    _finalize_prev(store)
    orig = _add(store, "incident", "미완료 이슈", Status.INCOMPLETE)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert result.carried[0].carry_count == orig.carry_count + 1


def test_carried_task_has_carried_from(store, template):
    _finalize_prev(store)
    _add(store, "incident", "미완료 이슈", Status.INCOMPLETE)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert result.carried[0].carried_from == PREV


def test_carried_task_keeps_status(store, template):
    _finalize_prev(store)
    _add(store, "incident", "보류 이슈", Status.BLOCKED)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert result.carried[0].status == Status.BLOCKED


def test_carried_task_stored_in_current_week(store, template):
    _finalize_prev(store)
    _add(store, "incident", "미완료 이슈", Status.INCOMPLETE)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert result.carried[0].week == WEEK


# ──────────────────────────────────────────
# ② 승격 — next_week_plan
# ──────────────────────────────────────────


def test_next_week_plan_is_promoted(store, template):
    _finalize_prev(store)
    next_key = template.by_role("next_week_plan").key
    _add(store, next_key, "DB 점검 예정", Status.IN_PROGRESS)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert len(result.promoted) == 1


def test_promoted_task_status_is_in_progress(store, template):
    _finalize_prev(store)
    next_key = template.by_role("next_week_plan").key
    _add(store, next_key, "DB 점검 예정", Status.INCOMPLETE)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert result.promoted[0].status == Status.IN_PROGRESS


def test_promoted_task_carry_count_is_zero(store, template):
    _finalize_prev(store)
    next_key = template.by_role("next_week_plan").key
    _add(store, next_key, "DB 점검 예정", Status.IN_PROGRESS)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert result.promoted[0].carry_count == 0


def test_promoted_task_has_carried_from(store, template):
    _finalize_prev(store)
    next_key = template.by_role("next_week_plan").key
    _add(store, next_key, "DB 점검 예정", Status.IN_PROGRESS)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert result.promoted[0].carried_from == PREV


# ──────────────────────────────────────────
# 멱등성 — 두 번 실행해도 중복 없음
# ──────────────────────────────────────────


def test_idempotent_carry(store, template):
    _finalize_prev(store)
    _add(store, "incident", "미완료 이슈", Status.INCOMPLETE)
    create_draft(store, template, WEEK, SYSTEM)  # 1회
    r2 = create_draft(store, template, WEEK, SYSTEM)  # 2회
    assert len(r2.carried) == 0
    assert r2.skipped_carry == 1
    # 실제 이번 주 task 수 — 중복 없이 1개
    tasks = store.list_tasks(WEEK, SYSTEM)
    assert len(tasks) == 1


def test_idempotent_promote(store, template):
    _finalize_prev(store)
    next_key = template.by_role("next_week_plan").key
    _add(store, next_key, "DB 점검 예정", Status.IN_PROGRESS)
    create_draft(store, template, WEEK, SYSTEM)
    r2 = create_draft(store, template, WEEK, SYSTEM)
    assert len(r2.promoted) == 0
    assert r2.skipped_promote == 1
    tasks = store.list_tasks(WEEK, SYSTEM)
    assert len(tasks) == 1


# ──────────────────────────────────────────
# 복합: 이월 + 승격 함께
# ──────────────────────────────────────────


def test_carry_and_promote_together(store, template):
    _finalize_prev(store)
    next_key = template.by_role("next_week_plan").key
    _add(store, "incident", "진행중 이슈", Status.IN_PROGRESS)
    _add(store, "incident", "완료 작업", Status.DONE)
    _add(store, next_key, "다음 주 패치", Status.IN_PROGRESS)
    result = create_draft(store, template, WEEK, SYSTEM)
    assert len(result.carried) == 1   # 진행중만
    assert len(result.promoted) == 1  # 다음주계획 승격
    assert result.total == 2


# ──────────────────────────────────────────
# 쉘 /draft 명령 통합
# ──────────────────────────────────────────


def test_shell_draft_command(store, template):
    from relay.llm.fake import FakeProvider
    from relay.shell import Session, dispatch

    _finalize_prev(store)
    _add(store, "incident", "미완료 이슈", Status.INCOMPLETE)

    session = Session(
        store=store,
        template=template,
        week=WEEK,
        system=SYSTEM,
        provider=FakeProvider(),
    )
    out = dispatch(session, "/draft")
    combined = "\n".join(out)
    assert "이월 1건" in combined
    assert "↩" in combined  # 이월 항목 접두


def test_shell_draft_no_prev_report(store, template):
    from relay.llm.fake import FakeProvider
    from relay.shell import Session, dispatch

    session = Session(
        store=store,
        template=template,
        week=WEEK,
        system=SYSTEM,
        provider=FakeProvider(),
    )
    out = dispatch(session, "/draft")
    assert any("⚠" in line for line in out)
