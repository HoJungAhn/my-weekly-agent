"""SQLite 저장소 테스트 (T4 / 설계 #1·#2).

핵심: 행↔모델 매핑(JSON 필드 포함), thread_id 자동 발급/승계, note 누적, 보고서 상태 전이,
그리고 CHECK 제약으로 raw SQL 로도 '이월' 상태가 저장되지 못함(이중 방어).
"""

import sqlite3
from datetime import datetime
from itertools import count

import pytest

from relay.db import Store, connect, init_db
from relay.models import ReportStatus, Status, Task
from relay.week import KST

FIXED_NOW = datetime(2026, 6, 22, 9, 0, tzinfo=KST)


@pytest.fixture
def store() -> Store:
    conn = connect(":memory:")
    init_db(conn)
    ids = count(1)
    return Store(
        conn,
        clock=lambda: FIXED_NOW,
        id_factory=lambda: f"thread-{next(ids)}",
    )


def _task(**kw) -> Task:
    base = dict(week="2026-W26", system="그룹웨어", category_key="incident",
                title="첨부 다운로드 지연", status=Status.IN_PROGRESS)
    base.update(kw)
    return Task(**base)


def test_init_db_idempotent() -> None:
    conn = connect(":memory:")
    init_db(conn)
    init_db(conn)  # 두 번 실행해도 에러 없음(IF NOT EXISTS)


def test_add_task_fills_id_thread_and_timestamps(store: Store) -> None:
    t = store.add_task(_task())
    assert t.id == 1
    assert t.thread_id == "thread-1"  # 자동 발급
    assert t.created_at == FIXED_NOW and t.updated_at == FIXED_NOW


def test_add_task_preserves_explicit_thread_id(store: Store) -> None:
    """이월 시 전주 thread_id 를 넘기면 그대로 승계한다(같은 작업으로 잇기 — 설계 #4)."""
    t = store.add_task(_task(thread_id="inherited", carried_from="2026-W25", carry_count=1))
    assert t.thread_id == "inherited"
    assert t.carried_from == "2026-W25" and t.carry_count == 1


def test_get_task_roundtrips_json_fields(store: Store) -> None:
    store.add_task(_task(related_ids=[7, 8], metrics={"가동률": 99.95}))
    got = store.get_task(1)
    assert got is not None
    assert got.related_ids == [7, 8]
    assert got.metrics == {"가동률": 99.95}
    assert got.status is Status.IN_PROGRESS


def test_get_missing_task_returns_none(store: Store) -> None:
    assert store.get_task(999) is None


def test_list_tasks_filters_and_orders(store: Store) -> None:
    store.add_task(_task(title="A"))
    store.add_task(_task(title="B"))
    store.add_task(_task(title="C", system="포털"))  # 다른 시스템
    store.add_task(_task(title="D", week="2026-W27"))  # 다른 주차
    rows = store.list_tasks("2026-W26", "그룹웨어")
    assert [t.title for t in rows] == ["A", "B"]


def test_thread_history_orders_by_week(store: Store) -> None:
    store.add_task(_task(week="2026-W26", thread_id="t1"))
    store.add_task(_task(week="2026-W27", thread_id="t1", carried_from="2026-W26", carry_count=1))
    store.add_task(_task(week="2026-W26", thread_id="other"))
    hist = store.thread_history("t1")
    assert [t.week for t in hist] == ["2026-W26", "2026-W27"]


def test_notes_accumulate_in_order(store: Store) -> None:
    store.add_task(_task())
    store.add_note(1, "로그 수집 완료")
    store.add_note(1, "캐시 도입 검토")
    notes = store.list_notes(1)
    assert [n.body for n in notes] == ["로그 수집 완료", "캐시 도입 검토"]
    assert notes[0].created_at == FIXED_NOW


def test_note_cascade_delete(store: Store) -> None:
    """task 삭제 시 그 note 도 함께 사라진다(FK ON DELETE CASCADE)."""
    store.add_task(_task())
    store.add_note(1, "메모")
    store.conn.execute("DELETE FROM tasks WHERE id = 1")
    store.conn.commit()
    assert store.list_notes(1) == []


def test_check_constraint_rejects_carryover_status(store: Store) -> None:
    """raw SQL 로도 '이월' 상태는 저장 불가(Pydantic 우회 방어 — CHECK 제약)."""
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            """INSERT INTO tasks (week, system, category_key, title, status,
                   carry_count, thread_id, created_at, updated_at)
               VALUES ('2026-W26', 'x', 'incident', '제목', '이월', 0, 'th', 'now', 'now')"""
        )


def test_set_status_updates_task(store: Store) -> None:
    store.add_task(_task())
    updated = store.set_status(1, Status.DONE)
    assert updated is not None and updated.status is Status.DONE
    assert store.get_task(1).status is Status.DONE


def test_report_status_transitions(store: Store) -> None:
    assert store.get_report("2026-W26", "그룹웨어") is None
    r = store.set_report_status("2026-W26", "그룹웨어", ReportStatus.DRAFT)
    assert r.status is ReportStatus.DRAFT
    r = store.set_report_status("2026-W26", "그룹웨어", ReportStatus.FINALIZED)
    assert r.status is ReportStatus.FINALIZED
    assert store.get_report("2026-W26", "그룹웨어").status is ReportStatus.FINALIZED


def test_report_check_constraint(store: Store) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO reports (week, system, status, created_at, updated_at) "
            "VALUES ('2026-W26', 'x', 'bogus', 'now', 'now')"
        )
