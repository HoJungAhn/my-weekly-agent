"""보고서 Markdown 렌더러 — 설계 결정 #5, #9, #10.

LLM 없이 코드로만 결정론적으로 렌더한다(T8). 나중에 T12의 LLM 서술(narrate) 레이어가
각 섹션의 task 목록 뒤에 서술 문단을 추가한다.

렌더 규칙(코드가 결정하는 것):
  - 헤더(제목/기간/시스템), 카테고리 순서(order 기준)
  - 이월 마커: carry_count >= threshold → "**[N주 연속 이월 ⚠]**"
  - 승격 마커: carried_from != None and carry_count == 0 → "**(승격)**"
  - 완료·보류 상태 접두(LLM 서술 없을 때 상태 가시성)
  - carry 임계 task → next_week 섹션에 리스크 항목 자동 삽입
  - 빈 카테고리: "*(해당 없음)*"
  - 지표(metrics) 표: 데이터 있을 때만(#6 미입력 허용 — T15 이전은 생략)
"""

from __future__ import annotations

from collections import defaultdict

from relay.db import Store
from relay.models import Status, Task
from relay.template import Template
from relay.week import format_range


def _task_lines(task: Task, threshold: int) -> list[str]:
    """task 한 건을 Markdown 줄들로 변환한다."""
    if task.carry_count >= threshold:
        prefix = f"**[{task.carry_count}주 연속 이월 ⚠]** "
    elif task.carried_from is not None and task.carry_count == 0:
        # carry_count=0 이지만 carried_from 있음 → 승격(next_week_plan → 금주 신규)
        prefix = "**(승격)** "
    elif task.status is Status.DONE:
        prefix = "(완료) "
    elif task.status is Status.BLOCKED:
        prefix = "(보류) "
    else:
        prefix = ""

    lines = [f"- {prefix}{task.title}"]

    if task.detail:
        for dl in task.detail.splitlines():
            stripped = dl.strip()
            if stripped:
                lines.append(f"  - {stripped}")

    return lines


def render_report(store: Store, template: Template, week: str, system: str) -> str:
    """현재 주차·시스템 task를 Markdown 보고서로 렌더한다(LLM 없이 결정론적).

    carry_count >= threshold 인 task는 자동으로 리스크 항목을 next_week 섹션에 삽입한다.
    지표 표는 T15(/metric set) 구현 전까지 데이터가 없으면 생략한다(설계 #6).
    """
    tasks = store.list_tasks(week, system)
    threshold = template.options.carry_warn_threshold

    by_cat: dict[str, list[Task]] = defaultdict(list)
    for t in tasks:
        by_cat[t.category_key].append(t)

    # carry 임계 도달 task → next_week 섹션에 리스크 자동 삽입용
    risk_tasks = [t for t in tasks if t.carry_count >= threshold]

    parts: list[str] = []

    # ── 헤더 ──────────────────────────────────────────────────────────────
    week_range = format_range(week, template.meta.week_label_format)
    parts.append(f"# {template.meta.title}")
    parts.append(f"**기간:** {week_range} ({week})  |  **대상 시스템:** {system}")
    parts.append("")
    parts.append("---")
    parts.append("")

    # ── 카테고리별 섹션 ───────────────────────────────────────────────────
    for cat in template.ordered():
        parts.append(f"## {cat.order}. {cat.label}")
        parts.append("")

        cat_tasks = by_cat.get(cat.key, [])

        if not cat_tasks:
            parts.append("*(해당 없음)*")
        else:
            for task in cat_tasks:
                parts.extend(_task_lines(task, threshold))

        # next_week 섹션: carry 임계 task 리스크를 코드로 자동 삽입
        if cat.role == "next_week_plan" and risk_tasks:
            for t in risk_tasks:
                parts.append(
                    f"- ⚠ **리스크**: {t.title}이(가) "
                    f"**{t.carry_count}주째 이월**(임계치 {threshold} 도달) → 점검 필요."
                )

        parts.append("")

    return "\n".join(parts).rstrip("\n") + "\n"
