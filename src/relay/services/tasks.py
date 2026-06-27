"""task 추가·조회 서비스 (T5 최소 슬라이스). CLI 는 이 순수 함수들을 호출만 한다.

핵심: 사용자가 보는 "주차별 작은 번호(1,2,3…)"와 내부 DB id 의 변환을 여기서 담당한다(설계 #10).
번호는 ``list_tasks(week, system)`` 의 1-based 위치이며, 매 명령 전 ``list`` 로 확인하는 흐름을 전제한다.
"""

from __future__ import annotations

from relay.config import DEFAULT_SYSTEM
from relay.db import Store
from relay.models import Status, Task
from relay.template import Template


def resolve_category(template: Template, text: str) -> tuple[str, str]:
    """사용자 입력을 카테고리 ``(key, label)`` 로 해석한다.

    우선순위: key/role/label 정확 일치 → label 부분 일치(유일할 때). 0개/다수면 명확한 에러.
    """
    text = text.strip()
    for cat in template.categories:
        if text in (cat.key, cat.role, cat.label):
            return cat.key, cat.label

    partial = [c for c in template.categories if text and text in c.label]
    if len(partial) == 1:
        return partial[0].key, partial[0].label

    options = ", ".join(f"{c.label}({c.key})" for c in template.ordered())
    if not partial:
        raise ValueError(f"카테고리를 찾을 수 없습니다: {text!r}. 가능한 값: {options}")
    raise ValueError(
        f"카테고리가 모호합니다: {text!r} → {[c.label for c in partial]}. 더 구체적으로 지정하세요."
    )


def resolve_system(store: Store, system: str | None) -> str:
    """활성 시스템 결정: 명시값 > 마지막 사용 시스템 > 기본값(설계 #10)."""
    if system:
        return system
    return store.last_used_system() or DEFAULT_SYSTEM


def display_number(store: Store, task: Task) -> int:
    """task 의 주차별 표시 번호(1-based)를 계산한다."""
    for i, t in enumerate(store.list_tasks(task.week, task.system), start=1):
        if t.id == task.id:
            return i
    raise AssertionError("방금 저장한 task 가 목록에 없습니다 — 데이터 일관성 오류")


def create_task(
    store: Store,
    category_key: str,
    *,
    title: str,
    week: str,
    system: str,
    detail: str = "",
    status: Status = Status.IN_PROGRESS,
) -> tuple[Task, int]:
    """신규 task 를 저장하고 ``(task, 표시번호)`` 를 반환한다. 신규는 기본 '진행중'(작성 워크플로우)."""
    task = store.add_task(
        Task(
            week=week,
            system=system,
            category_key=category_key,
            title=title,
            detail=detail,
            status=status,
        )
    )
    return task, display_number(store, task)


def list_tasks_numbered(store: Store, week: str, system: str) -> list[tuple[int, Task]]:
    """주차·시스템 task 를 표시 번호와 함께 반환한다."""
    return list(enumerate(store.list_tasks(week, system), start=1))
