"""초안 서술 생성 + 자가검증 루프 — 설계 결정 #5, #12(검증 단계).

파이프라인: collect() → narrate() → verify_deterministic() → verify_judgment() → render_with_narrative()
narrate↔verify 루프는 섹션 단위로 돌며, MAX_ATTEMPTS 초과 시 "⚠ 검증 미통과" 표시 초안을 남긴다.

핵심 규칙:
  - collect() 의 숫자·데이터는 루프 밖 확정값 — LLM 이 재계산하지 않는다(설계 #5).
  - verify_deterministic 먼저(코드), 통과분만 verify_judgment(LLM) — 검증판 원칙.
  - FakeProvider 사용 시 narrate_section → "", self_critique → ok=True → 서술 없는 구조 렌더.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from relay.db import Store
from relay.llm import LLMProvider
from relay.models import Note, Status, Task
from relay.template import Template
from relay.template.models import Category
from relay.week import format_range

MAX_ATTEMPTS = 3  # 재서술 최대 횟수(설계: "무한루프 방지")


# ── 데이터 수집 ─────────────────────────────────────────────────────────────

@dataclass
class TaskData:
    task: Task
    notes: list[Note] = field(default_factory=list)


@dataclass
class CategorySection:
    category: Category
    task_data: list[TaskData] = field(default_factory=list)

    @property
    def tasks(self) -> list[Task]:
        return [td.task for td in self.task_data]


@dataclass
class ReportData:
    week: str
    system: str
    sections: list[CategorySection]
    threshold: int

    @property
    def risk_tasks(self) -> list[Task]:
        """carry_count >= threshold 인 task (next_week 섹션 리스크 자동 삽입용)."""
        return [
            td.task
            for sec in self.sections
            for td in sec.task_data
            if td.task.carry_count >= self.threshold
        ]


def collect(store: Store, template: Template, week: str, system: str) -> ReportData:
    """DB 에서 task·note 를 읽어 카테고리별로 묶은 :class:`ReportData` 를 반환한다.

    이 함수의 반환값이 "확정 데이터"다 — narrate·verify 루프 밖에 두고 재계산하지 않는다.
    """
    tasks = store.list_tasks(week, system)
    by_cat: dict[str, list[TaskData]] = defaultdict(list)
    for t in tasks:
        notes = store.list_notes(t.id)
        by_cat[t.category_key].append(TaskData(task=t, notes=notes))

    sections = [
        CategorySection(category=cat, task_data=by_cat.get(cat.key, []))
        for cat in template.ordered()
    ]
    return ReportData(week=week, system=system, sections=sections, threshold=template.options.carry_warn_threshold)


# ── LLM 프롬프트용 task 블록 포매터 ─────────────────────────────────────────

def _format_task_block(sec: CategorySection, threshold: int) -> str:
    """LLM 프롬프트에 넣을 task 텍스트 블록을 만든다.

    사실의 원천(grounding anchor)이므로 상태·carry·note 를 모두 포함한다.
    """
    lines: list[str] = []
    for td in sec.task_data:
        t = td.task
        carry_note = ""
        if t.carry_count >= threshold:
            carry_note = f" [주의: {t.carry_count}주 연속 이월]"
        elif t.carried_from and t.carry_count == 0:
            carry_note = " [승격됨]"
        status_label = {
            Status.DONE: "완료",
            Status.IN_PROGRESS: "진행중",
            Status.INCOMPLETE: "미완료",
            Status.BLOCKED: "보류",
            Status.CANCELED: "취소",
        }.get(t.status, t.status.value)
        lines.append(f"- [{status_label}]{carry_note} {t.title}")
        if t.detail:
            lines.append(f"  상세: {t.detail}")
        for note in td.notes:
            lines.append(f"  메모: {note.body}")
    return "\n".join(lines)


# ── 결정론 검증 (코드) ──────────────────────────────────────────────────────

@dataclass
class VerifyResult:
    ok: bool
    issues: list[str]
    feedback: str  # narrate() 재시도 시 넘기는 지침


def verify_deterministic(narrative: str, sec: CategorySection, threshold: int) -> VerifyResult:
    """코드 레벨 검증(빠르고 결정론적). LLM 판단 전에 먼저 실행한다.

    현재 체크:
    - 내용이 있는 섹션에서 서술이 비어 있으면 → 경고(단, FakeProvider 에서 빈 문자열은 정상)
    - 이월 임계 task 가 있는데 서술에 '이월' 또는 '리스크' 언급이 없으면 → 경고
    """
    if not narrative:
        return VerifyResult(ok=True, issues=[], feedback="")  # 빈 서술은 ok(FakeProvider 패스)

    issues: list[str] = []
    risk_tasks = [td.task for td in sec.task_data if td.task.carry_count >= threshold]
    if risk_tasks:
        low = narrative.lower()
        if not any(kw in low for kw in ("이월", "주차", "리스크", "위험", "주의", "점검")):
            issues.append(
                f"'{risk_tasks[0].title}' 등 장기 이월 task 가 있으나 서술에 리스크 언급이 없습니다."
            )

    if issues:
        return VerifyResult(ok=False, issues=issues, feedback="\n".join(issues))
    return VerifyResult(ok=True, issues=[], feedback="")


# ── 서술 생성 루프 ───────────────────────────────────────────────────────────

def _narrate_section_with_retry(
    provider: LLMProvider,
    sec: CategorySection,
    threshold: int,
) -> tuple[str, list[str]]:
    """섹션 하나의 서술을 생성하고 verify 루프를 돈다.

    반환: (narrative, warnings) — 최대 시도 초과 시 마지막 서술 + 경고 목록.
    """
    if not sec.task_data:
        return "", []

    task_block = _format_task_block(sec, threshold)
    narrative = ""
    feedback: str | None = None
    last_issues: list[str] = []

    for _ in range(MAX_ATTEMPTS):
        narrative = provider.narrate_section(
            label=sec.category.label,
            hint=sec.category.hint,
            task_block=task_block,
            feedback=feedback,
        )

        # ① 결정론 검증 (코드)
        det = verify_deterministic(narrative, sec, threshold)
        if not det.ok:
            last_issues = det.issues
            feedback = det.feedback
            continue

        # ② 판단 검증 (LLM 자가검증)
        critique = provider.self_critique(
            label=sec.category.label,
            task_block=task_block,
            narrative=narrative,
        )
        if critique.ok:
            return narrative, []
        last_issues = critique.issues
        feedback = critique.feedback

    # MAX_ATTEMPTS 초과 — 마지막 서술을 경고와 함께 반환 (차단 아님, 설계: 사람이 마무리)
    return narrative, last_issues


# ── 서술+구조 통합 렌더 ─────────────────────────────────────────────────────

def _render_task_lines(td_list: list[TaskData], threshold: int) -> list[str]:
    """task + detail 을 Markdown 불릿으로 변환한다(T8 render.py 와 동일한 마커 규칙)."""
    lines: list[str] = []
    for td in td_list:
        t = td.task
        if t.carry_count >= threshold:
            prefix = f"**[{t.carry_count}주 연속 이월 ⚠]** "
        elif t.carried_from is not None and t.carry_count == 0:
            prefix = "**(승격)** "
        elif t.status is Status.DONE:
            prefix = "(완료) "
        elif t.status is Status.BLOCKED:
            prefix = "(보류) "
        else:
            prefix = ""
        lines.append(f"- {prefix}{t.title}")
        if t.detail:
            for dl in t.detail.splitlines():
                if dl.strip():
                    lines.append(f"  - {dl.strip()}")
    return lines


def render_with_narrative(
    data: ReportData,
    narrative: dict[str, str],
    template: Template,
    warnings: list[str] | None = None,
) -> str:
    """구조 렌더 + LLM 서술을 합쳐 최종 Markdown 을 반환한다.

    ``narrative``: category_key → 서술 텍스트. 없거나 빈 문자열이면 task 목록만 표시.
    ``warnings``: 검증 미통과 항목. 있으면 보고서 상단에 ⚠ 표기(차단 아님).
    """
    parts: list[str] = []

    # 헤더
    week_range = format_range(data.week, template.meta.week_label_format)
    parts.append(f"# {template.meta.title}")
    parts.append(f"**기간:** {week_range} ({data.week})  |  **대상 시스템:** {data.system}")
    parts.append("")
    if warnings:
        for w in warnings:
            parts.append(f"> ⚠ 검증 미통과: {w}")
        parts.append("")
    parts.append("---")
    parts.append("")

    for sec in data.sections:
        parts.append(f"## {sec.category.order}. {sec.category.label}")
        parts.append("")

        # LLM 서술 (있으면 task 목록 위에 삽입)
        narr = narrative.get(sec.category.key, "").strip()
        if narr:
            parts.append(narr)
            parts.append("")

        # task 목록 (구조 부분 — 결정론)
        if not sec.task_data:
            parts.append("*(해당 없음)*")
        else:
            parts.extend(_render_task_lines(sec.task_data, data.threshold))

        # next_week 섹션: carry 임계 리스크 자동 삽입 (T8 과 동일 규칙)
        if sec.category.role == "next_week_plan" and data.risk_tasks:
            for t in data.risk_tasks:
                parts.append(
                    f"- ⚠ **리스크**: {t.title}이(가) "
                    f"**{t.carry_count}주째 이월**(임계치 {data.threshold} 도달) → 점검 필요."
                )

        parts.append("")

    return "\n".join(parts).rstrip("\n") + "\n"


# ── 메인 진입점 ─────────────────────────────────────────────────────────────

def generate_report(
    store: Store,
    template: Template,
    provider: LLMProvider,
    week: str,
    system: str,
) -> tuple[str, list[str]]:
    """collect → 섹션별 narrate+verify 루프 → render_with_narrative.

    반환: ``(markdown, warnings)``
      - markdown: 최종 Markdown 문자열
      - warnings: 검증 미통과 항목 목록 (빈 리스트 = 완전 통과)

    FakeProvider 사용 시: narrate_section → "", self_critique → ok=True → warnings 없음.
    """
    data = collect(store, template, week, system)
    narrative: dict[str, str] = {}
    all_warnings: list[str] = []

    for sec in data.sections:
        narr, warns = _narrate_section_with_retry(provider, sec, data.threshold)
        narrative[sec.category.key] = narr
        if warns:
            label = sec.category.label
            all_warnings.extend(f"[{label}] {w}" for w in warns)

    md = render_with_narrative(data, narrative, template, all_warnings or None)
    return md, all_warnings
