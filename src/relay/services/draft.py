"""이월(carry-over)·승격(promotion) 초안 생성 서비스 — 설계 결정 #4.

핵심 규칙:
  ① 미완료 이월 : CARRYABLE_STATUSES task → 금주 복제, thread_id 승계, carry_count +1
  ② 계획 승격   : next_week_plan role task → 금주 신규 task, status=진행중, 새 thread_id

멱등성:
  · 이월  — 이미 이번 주에 같은 thread_id가 있으면 스킵(정확)
  · 승격  — carried_from + title 기반 스킵(MVP 휴리스틱; 나중에 source_task_id로 개선 가능)

경고는 차단이 아니라 알림 — finalize 강제 안 함(사람 책임 문서, 설계 검증 단계 참조).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from relay.db import Store
from relay.models import CARRYABLE_STATUSES, ReportStatus, Status, Task
from relay.template import Template
from relay.week import shift_week


@dataclass
class DraftResult:
    """초안 생성 결과."""

    week: str
    system: str
    prev_week: str
    carried: list[Task] = field(default_factory=list)
    promoted: list[Task] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped_carry: int = 0
    skipped_promote: int = 0

    @property
    def total(self) -> int:
        return len(self.carried) + len(self.promoted)


def create_draft(
    store: Store,
    template: Template,
    week: str,
    system: str,
) -> DraftResult:
    """전주 보고를 기반으로 금주 초안을 생성한다.

    반환값: DraftResult — 이월·승격된 task 목록 + 경고 문자열들.
    실제 저장은 이 함수 안에서 수행한다(add_task 호출).
    """
    prev_week = shift_week(week, -1)
    result = DraftResult(week=week, system=system, prev_week=prev_week)

    # 전주 보고서 상태 확인 (경고만, 차단 안 함)
    prev_report = store.get_report(prev_week, system)
    if prev_report is None:
        result.warnings.append(
            f"전주({prev_week}) 보고 기록이 없습니다. 빈 초안을 시작합니다."
        )
        return result

    if prev_report.status != ReportStatus.FINALIZED:
        result.warnings.append(
            f"전주({prev_week}) 보고가 아직 finalized되지 않았습니다"
            f" (현재 상태: {prev_report.status.value})."
            " '/finalize'를 먼저 실행하는 것을 권장합니다."
        )

    prev_tasks = store.list_tasks(prev_week, system)
    if not prev_tasks:
        result.warnings.append(f"전주({prev_week}) task가 없습니다.")
        return result

    # 현재 주 task — 멱등 체크에 사용
    current_tasks = store.list_tasks(week, system)
    existing_thread_ids: set[str | None] = {t.thread_id for t in current_tasks}

    # next_week_plan role의 category key
    next_week_cat_key: str | None
    try:
        next_week_cat_key = template.by_role("next_week_plan").key
    except ValueError:
        next_week_cat_key = None
        result.warnings.append(
            "템플릿에 next_week_plan role이 없어 계획 승격을 건너뜁니다."
        )

    for task in prev_tasks:
        # ② 승격: next_week_plan category — 이월보다 먼저 체크한다.
        # next_week_plan task가 IN_PROGRESS 상태라도 CARRYABLE 이월이 아니라 승격이 맞음.
        if next_week_cat_key and task.category_key == next_week_cat_key:
            # 중복 확인 — carried_from + title 휴리스틱 (MVP)
            already = any(
                t.carried_from == prev_week and t.title == task.title
                for t in current_tasks
            )
            if already:
                result.skipped_promote += 1
                continue
            promoted = store.add_task(
                Task(
                    week=week,
                    system=system,
                    category_key=task.category_key,
                    title=task.title,
                    detail=task.detail,
                    status=Status.IN_PROGRESS,
                    carried_from=prev_week,
                    carry_count=0,
                    thread_id=None,  # 새 thread_id 발급 (신규 작업)
                )
            )
            result.promoted.append(promoted)

        # ① 이월: CARRYABLE_STATUSES (진행중 / 미완료 / 보류)
        elif task.status in CARRYABLE_STATUSES:
            if task.thread_id in existing_thread_ids:
                result.skipped_carry += 1
                continue
            carried = store.add_task(
                Task(
                    week=week,
                    system=system,
                    category_key=task.category_key,
                    title=task.title,
                    detail=task.detail,
                    status=task.status,
                    carried_from=prev_week,
                    carry_count=task.carry_count + 1,
                    thread_id=task.thread_id,  # 같은 작업 체인 유지
                )
            )
            result.carried.append(carried)
            existing_thread_ids.add(carried.thread_id)

    return result
