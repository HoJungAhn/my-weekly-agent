"""Markdown 렌더러 테스트 (T8 / 설계 #5·#9·#10).

golden output(examples/sample_report_2026-W26.md)의 코드 결정 구간을 재현하는지 검증한다.
- 헤더(기간·시스템), 카테고리 순서(order), 빈 카테고리
- 이월 마커([N주 연속 이월 ⚠]), 승격 마커((승격)), 완료·보류 접두
- detail 서브 불릿
- next_week 섹션의 carry 임계 리스크 자동 삽입
- /review, /finalize 쉘 통합
"""

from itertools import count

import pytest

from relay.db import Store, connect, init_db
from relay.llm.fake import FakeProvider
from relay.models import ReportStatus, Status, Task
from relay.services.render import render_report
from relay.shell import Session, dispatch
from relay.template import default_template_path, load_template
from relay.week import format_range


@pytest.fixture
def tmpl():
    return load_template(default_template_path())


@pytest.fixture
def store():
    conn = connect(":memory:")
    init_db(conn)
    ids = count(1)
    return Store(conn, id_factory=lambda: f"t{next(ids)}")


def _add(store: Store, **kw) -> Task:
    defaults = dict(
        week="2026-W26", system="그룹웨어", category_key="incident",
        title="테스트", status=Status.IN_PROGRESS,
    )
    defaults.update(kw)
    return store.add_task(Task(**defaults))


@pytest.fixture
def session(store, tmpl):
    ids = count(100)
    store._new_thread_id = lambda: f"th{next(ids)}"
    return Session(
        store=store,
        template=tmpl,
        week="2026-W26",
        system="그룹웨어",
        provider=FakeProvider(),
    )


# ── 헤더 ──────────────────────────────────────────────────────────────────

def test_header_contains_week_range_and_system(store, tmpl):
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    expected_range = format_range("2026-W26", tmpl.meta.week_label_format)
    assert expected_range in md  # "2026.06.22 ~ 06.26"
    assert "그룹웨어" in md
    assert "2026-W26" in md


def test_header_contains_template_title(store, tmpl):
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    assert tmpl.meta.title in md


# ── 카테고리 순서 ──────────────────────────────────────────────────────────

def test_category_order_matches_template(store, tmpl):
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    ordered_labels = [c.label for c in tmpl.ordered()]
    positions = [md.index(label) for label in ordered_labels if label in md]
    assert positions == sorted(positions), "카테고리가 order 기준으로 정렬되어야 한다"


def test_category_numbers_rendered(store, tmpl):
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    for cat in tmpl.ordered():
        assert f"## {cat.order}." in md


# ── 빈 카테고리 ────────────────────────────────────────────────────────────

def test_empty_category_shows_placeholder(store, tmpl):
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    # task 없으면 모든 카테고리가 "해당 없음" 표시
    assert md.count("*(해당 없음)*") == len(tmpl.categories)


def test_nonempty_category_no_placeholder(store, tmpl):
    _add(store, category_key="incident", title="장애A")
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    # incident 는 해당 없음이 아님
    lines = md.splitlines()
    incident_section_lines = []
    in_section = False
    for line in lines:
        if "## " in line and "장애" in line:
            in_section = True
        elif "## " in line and in_section:
            break
        if in_section:
            incident_section_lines.append(line)
    assert not any("해당 없음" in line for line in incident_section_lines)


# ── 이월 마커 ──────────────────────────────────────────────────────────────

def test_carry_count_below_threshold_no_marker(store, tmpl):
    threshold = tmpl.options.carry_warn_threshold  # 3
    _add(store, title="이월2회", carried_from="2026-W24", carry_count=threshold - 1)
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    assert "이월 ⚠" not in md


def test_carry_count_at_threshold_shows_marker(store, tmpl):
    threshold = tmpl.options.carry_warn_threshold  # 3
    _add(store, title="이월임계", carried_from="2026-W23", carry_count=threshold)
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    assert f"**[{threshold}주 연속 이월 ⚠]**" in md
    assert "이월임계" in md


def test_carry_count_above_threshold_shows_correct_count(store, tmpl):
    _add(store, title="장기이월", carried_from="2026-W20", carry_count=5)
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    assert "**[5주 연속 이월 ⚠]**" in md


# ── 승격 마커 ──────────────────────────────────────────────────────────────

def test_promoted_task_shows_promotion_marker(store, tmpl):
    # carry_count=0 + carried_from!=None → 승격
    _add(store, category_key="next_week", title="다음주예정작업",
         carried_from="2026-W25", carry_count=0, status=Status.IN_PROGRESS)
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    assert "**(승격)**" in md
    assert "다음주예정작업" in md


def test_carriedover_task_no_promotion_marker(store, tmpl):
    # carry_count >= 1 → 이월 (승격 마커 아님)
    _add(store, title="이월작업", carried_from="2026-W25", carry_count=1)
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    assert "**(승격)**" not in md


# ── 상태 접두 ──────────────────────────────────────────────────────────────

def test_done_task_shows_prefix(store, tmpl):
    _add(store, title="완료된작업", status=Status.DONE)
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    assert "(완료)" in md


def test_blocked_task_shows_prefix(store, tmpl):
    _add(store, title="보류작업", status=Status.BLOCKED)
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    assert "(보류)" in md


def test_in_progress_task_no_status_prefix(store, tmpl):
    _add(store, title="진행작업", status=Status.IN_PROGRESS)
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    # 진행중은 접두 없이 제목만
    assert "- 진행작업" in md


# ── detail 서브 불릿 ────────────────────────────────────────────────────────

def test_detail_renders_as_subbullets(store, tmpl):
    _add(store, title="장애A", detail="원인: 스토리지 부하\n조치: 재시작")
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    assert "  - 원인: 스토리지 부하" in md
    assert "  - 조치: 재시작" in md


def test_empty_detail_no_subbullets(store, tmpl):
    _add(store, title="장애B", detail="")
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    lines = md.splitlines()
    task_idx = next(i for i, ln in enumerate(lines) if "장애B" in ln)
    # 바로 다음 줄이 서브 불릿이면 안 됨
    if task_idx + 1 < len(lines):
        assert not lines[task_idx + 1].startswith("  - ")


# ── next_week 리스크 자동 삽입 ─────────────────────────────────────────────

def test_carry_risk_injected_into_next_week_section(store, tmpl):
    threshold = tmpl.options.carry_warn_threshold
    _add(store, category_key="incident", title="오래된장애",
         carried_from="2026-W23", carry_count=threshold)
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    # next_week 섹션에 리스크 항목이 삽입되어야 함
    assert "⚠ **리스크**" in md
    assert "오래된장애" in md.split("⚠ **리스크**")[1]  # 리스크 항목에 제목 포함


def test_no_carry_risk_when_below_threshold(store, tmpl):
    threshold = tmpl.options.carry_warn_threshold
    _add(store, title="짧은이월", carried_from="2026-W25", carry_count=threshold - 1)
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    assert "⚠ **리스크**" not in md


def test_carry_risk_in_next_week_even_when_empty(store, tmpl):
    """next_week 카테고리에 task가 없어도 carry 리스크는 삽입된다."""
    threshold = tmpl.options.carry_warn_threshold
    _add(store, category_key="incident", title="이월작업임계",
         carried_from="2026-W23", carry_count=threshold)
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    # next_week 섹션에 "해당 없음"과 리스크가 함께 있어야 함
    # (next_week 자체 task는 없으므로 "해당 없음" 뒤에 리스크 삽입)
    next_week_label = tmpl.by_role("next_week_plan").label
    nw_pos = md.index(next_week_label)
    nw_section = md[nw_pos:]
    assert "*(해당 없음)*" in nw_section
    assert "⚠ **리스크**" in nw_section


# ── 다른 주차·시스템 격리 ───────────────────────────────────────────────────

def test_render_only_current_week_and_system(store, tmpl):
    _add(store, week="2026-W25", title="이전주작업")
    _add(store, system="포털", title="다른시스템")
    _add(store, title="정확한작업")
    md = render_report(store, tmpl, "2026-W26", "그룹웨어")
    assert "이전주작업" not in md
    assert "다른시스템" not in md
    assert "정확한작업" in md


# ── /review 쉘 통합 ───────────────────────────────────────────────────────

def test_review_command_renders_markdown(session):
    dispatch(session, "/add 장애 테스트장애")
    out = dispatch(session, "/review")
    full = "\n".join(out)
    assert "시스템 운영 주간업무 보고" in full
    assert "테스트장애" in full


def test_review_does_not_change_report_status(session):
    dispatch(session, "/add 장애 테스트")
    dispatch(session, "/review")
    report = session.store.get_report(session.week, session.system)
    assert report is None  # review는 report 상태를 만들지 않음


# ── /finalize 쉘 통합 ─────────────────────────────────────────────────────

def test_finalize_sets_status_finalized(session):
    dispatch(session, "/add 장애 테스트")
    dispatch(session, "/finalize")
    report = session.store.get_report(session.week, session.system)
    assert report is not None
    assert report.status is ReportStatus.FINALIZED


def test_finalize_output_includes_confirmation_and_markdown(session):
    dispatch(session, "/add 장애 테스트")
    out = dispatch(session, "/finalize")
    full = "\n".join(out)
    assert "finalized" in out[0]  # 첫 줄에 확정 메시지
    assert "시스템 운영 주간업무 보고" in full  # Markdown 포함


def test_finalize_idempotent(session):
    """두 번 finalize해도 오류 없이 동일한 결과."""
    dispatch(session, "/add 장애 테스트")
    dispatch(session, "/finalize")
    out = dispatch(session, "/finalize")
    assert any("finalized" in line for line in out)


def test_review_and_finalize_same_markdown(session):
    """review와 finalize의 Markdown 본문이 동일해야 한다."""
    dispatch(session, "/add 장애 테스트")
    dispatch(session, "/add 정기 패치")
    review_md = "\n".join(dispatch(session, "/review"))
    finalize_out = dispatch(session, "/finalize")
    finalize_md = "\n".join(finalize_out[2:])  # 첫 두 줄(확정 메시지, 빈 줄) 제외
    assert review_md == finalize_md
