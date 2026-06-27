"""SQLite 스키마 DDL — 설계 결정 #1·#2.

SQLite 가 원본(source of truth). status 컬럼에 CHECK 제약을 둬, Pydantic 검증을 우회하는
raw SQL 로도 잘못된 상태('이월' 등)가 저장되지 못하게 한다(이중 방어).
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reports (
    week       TEXT NOT NULL,
    system     TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'draft'
               CHECK (status IN ('draft', 'in_progress', 'finalized')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (week, system)
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    week         TEXT NOT NULL,
    system       TEXT NOT NULL,
    category_key TEXT NOT NULL,
    title        TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT '',
    -- 작업 상태만. '이월'은 여기 들어오지 않는다(출처는 carried_from 으로 표현).
    status       TEXT NOT NULL
                 CHECK (status IN ('완료', '진행중', '미완료', '보류', '취소')),
    carried_from TEXT,                       -- 이월 원본 주차(NULL=신규)
    carry_count  INTEGER NOT NULL DEFAULT 0,
    thread_id    TEXT NOT NULL,              -- 주차를 가로질러 같은 작업을 잇는 키
    related_ids  TEXT NOT NULL DEFAULT '[]', -- JSON array
    metrics      TEXT NOT NULL DEFAULT '{}', -- JSON object
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_week_system ON tasks (week, system);
CREATE INDEX IF NOT EXISTS idx_tasks_thread      ON tasks (thread_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status      ON tasks (status);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL REFERENCES tasks (id) ON DELETE CASCADE,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_task ON notes (task_id);
"""
