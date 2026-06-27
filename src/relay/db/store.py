"""SQLite 접근 레이어 — 연결/초기화 + Task·Note·Report CRUD. 설계 결정 #1·#2.

타임스탬프와 thread_id 생성은 ``Store`` 내부에서 한다. 테스트 결정성을 위해 시계(``clock``)와
id 생성기(``id_factory``)를 주입할 수 있다(테스트 정책: 시간·랜덤 의존 제거).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from relay.db.schema import SCHEMA_SQL
from relay.models import Note, Report, ReportStatus, Status, Task
from relay.week import KST


def connect(path: str | Path) -> sqlite3.Connection:
    """SQLite 연결을 연다(행은 dict 형 접근, 외래키 ON). ``:memory:`` 도 허용."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """스키마를 생성한다(멱등 — IF NOT EXISTS)."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        week=row["week"],
        system=row["system"],
        category_key=row["category_key"],
        title=row["title"],
        detail=row["detail"],
        status=row["status"],
        carried_from=row["carried_from"],
        carry_count=row["carry_count"],
        thread_id=row["thread_id"],
        related_ids=json.loads(row["related_ids"]),
        metrics=json.loads(row["metrics"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class Store:
    """원본 데이터 저장소. 비즈니스 로직(이월/승격/집계)은 services 레이어가 이 위에 올린다."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.conn = conn
        self._clock = clock or (lambda: datetime.now(KST))
        self._new_thread_id = id_factory or (lambda: uuid.uuid4().hex)

    # ---- tasks ---------------------------------------------------------
    def add_task(self, task: Task) -> Task:
        """task 를 저장하고 id/thread_id/타임스탬프가 채워진 사본을 반환한다.

        ``thread_id`` 가 비어 있으면 새로 발급한다(신규 작업). 이월 시에는 호출자가 전주
        task 의 ``thread_id`` 를 채워 넘겨 같은 작업으로 잇는다(설계 #4).
        """
        now = self._clock().isoformat()
        thread_id = task.thread_id or self._new_thread_id()
        cur = self.conn.execute(
            """
            INSERT INTO tasks
                (week, system, category_key, title, detail, status, carried_from,
                 carry_count, thread_id, related_ids, metrics, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.week,
                task.system,
                task.category_key,
                task.title,
                task.detail,
                task.status.value,
                task.carried_from,
                task.carry_count,
                thread_id,
                json.dumps(task.related_ids),
                json.dumps(task.metrics),
                now,
                now,
            ),
        )
        self.conn.commit()
        stored = self.get_task(cur.lastrowid)
        assert stored is not None  # 방금 INSERT 했으므로 존재
        return stored

    def set_status(self, task_id: int, status: Status) -> Task | None:
        """task 의 작업 상태를 변경한다(updated_at 갱신). 없으면 None."""
        now = self._clock().isoformat()
        self.conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, now, task_id),
        )
        self.conn.commit()
        return self.get_task(task_id)

    def get_task(self, task_id: int) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def list_tasks(self, week: str, system: str) -> list[Task]:
        """특정 주차·시스템의 task 를 id 순으로 반환(결정적 조회 — 설계 #1)."""
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE week = ? AND system = ? ORDER BY id",
            (week, system),
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    def last_used_system(self) -> str | None:
        """가장 최근에 추가된 task 의 시스템(활성 컨텍스트 기본값 — 설계 #10). 없으면 None."""
        row = self.conn.execute(
            "SELECT system FROM tasks ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["system"] if row else None

    def thread_history(self, thread_id: str) -> list[Task]:
        """같은 작업(thread)의 주차별 이력을 week 순으로 반환(설계 #2 — task history)."""
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE thread_id = ? ORDER BY week, id",
            (thread_id,),
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    # ---- notes ---------------------------------------------------------
    def add_note(self, task_id: int, body: str) -> Note:
        now = self._clock().isoformat()
        cur = self.conn.execute(
            "INSERT INTO notes (task_id, body, created_at) VALUES (?, ?, ?)",
            (task_id, body, now),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
        return Note(id=row["id"], task_id=row["task_id"], body=row["body"], created_at=row["created_at"])

    def list_notes(self, task_id: int) -> list[Note]:
        rows = self.conn.execute(
            "SELECT * FROM notes WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()
        return [
            Note(id=r["id"], task_id=r["task_id"], body=r["body"], created_at=r["created_at"])
            for r in rows
        ]

    # ---- reports -------------------------------------------------------
    def get_report(self, week: str, system: str) -> Report | None:
        row = self.conn.execute(
            "SELECT * FROM reports WHERE week = ? AND system = ?", (week, system)
        ).fetchone()
        if not row:
            return None
        return Report(
            week=row["week"],
            system=row["system"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def set_report_status(self, week: str, system: str, status: ReportStatus) -> Report:
        """보고서 상태를 upsert 한다(없으면 생성). draft → in_progress → finalized."""
        now = self._clock().isoformat()
        if self.get_report(week, system) is None:
            self.conn.execute(
                "INSERT INTO reports (week, system, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (week, system, status.value, now, now),
            )
        else:
            self.conn.execute(
                "UPDATE reports SET status = ?, updated_at = ? WHERE week = ? AND system = ?",
                (status.value, now, week, system),
            )
        self.conn.commit()
        report = self.get_report(week, system)
        assert report is not None
        return report
