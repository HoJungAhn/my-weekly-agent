"""대화형 쉘 dispatch 테스트 (설계 #10).

dispatch 는 순수 함수(IO 없음)라 stdin/stdout 없이 명령을 직접 검증한다.
slash 선택성·/task 접두·활성 컨텍스트 전환·에러 처리까지 포함.
"""

from itertools import count

import pytest

from relay.db import Store, connect, init_db
from relay.llm.fake import FakeProvider
from relay.shell import QuitShell, Session, dispatch
from relay.template import default_template_path, load_template


@pytest.fixture
def session() -> Session:
    conn = connect(":memory:")
    init_db(conn)
    ids = count(1)
    return Session(
        store=Store(conn, id_factory=lambda: f"t{next(ids)}"),
        template=load_template(default_template_path()),
        week="2026-W26",
        system="그룹웨어",
        provider=FakeProvider(),
    )


def test_add_and_list(session: Session) -> None:
    out = dispatch(session, "/add 장애 첨부 다운로드 지연")
    assert out == ["✓ 등록됨  [1] 첨부 다운로드 지연  (장애·이슈 대응)"]
    assert dispatch(session, "/list") == ["  [1] (진행중) 첨부 다운로드 지연"]


def test_slash_is_optional(session: Session) -> None:
    dispatch(session, "add 장애 A")
    assert dispatch(session, "list") == ["  [1] (진행중) A"]


def test_task_prefix_form(session: Session) -> None:
    """SPEC 원안 표기 '/task add ...' 도 동작한다."""
    out = dispatch(session, '/task add 정기 "보안 패치"')
    assert out[0].startswith("✓ 등록됨  [1] 보안 패치")


def test_list_empty(session: Session) -> None:
    assert dispatch(session, "/list") == ["  (등록된 task 없음)"]


def test_quoted_title(session: Session) -> None:
    out = dispatch(session, '/add 장애 "첨부 지연"')
    assert "첨부 지연" in out[0]


def test_add_unknown_category(session: Session) -> None:
    out = dispatch(session, "/add 없는것 제목")
    assert out[0].startswith("⚠") and "가능한 값" in out[0]


def test_update_changes_status(session: Session) -> None:
    dispatch(session, "/add 장애 A")
    assert dispatch(session, "/update 1 완료") == ["✓ [1] A → 완료"]
    assert dispatch(session, "/list") == ["  [1] (완료) A"]


def test_update_invalid_status(session: Session) -> None:
    dispatch(session, "/add 장애 A")
    assert "알 수 없는 상태" in dispatch(session, "/update 1 끝남")[0]


def test_update_bad_number(session: Session) -> None:
    assert "번호 9 에 해당하는 task 가 없습니다" in dispatch(session, "/update 9 완료")[0]


def test_update_non_numeric(session: Session) -> None:
    assert "숫자여야" in dispatch(session, "/update abc 완료")[0]


def test_note_then_history(session: Session) -> None:
    dispatch(session, "/add 장애 첨부 지연")
    assert dispatch(session, "/note 1 로그 수집 완료") == ["✓ 메모 추가  [1] 첨부 지연"]
    hist = dispatch(session, "/history 1")
    assert hist[0].startswith('"첨부 지연" — 1주차')
    assert any("신규 등록" in line for line in hist)
    assert any("· 로그 수집 완료" in line for line in hist)


def test_use_switches_system(session: Session) -> None:
    assert dispatch(session, "/use 포털") == ["활성 시스템 → 포털"]
    dispatch(session, "/add 장애 A")
    # 그룹웨어에는 없고 포털에 있다
    session.system = "그룹웨어"
    assert dispatch(session, "/list") == ["  (등록된 task 없음)"]
    session.system = "포털"
    assert dispatch(session, "/list") == ["  [1] (진행중) A"]


def test_week_switch_validates(session: Session) -> None:
    assert dispatch(session, "/week 2026-W27") == ["활성 주차 → 2026-W27"]
    assert session.week == "2026-W27"
    assert dispatch(session, "/week 엉터리")[0].startswith("⚠")


def test_help_lists_commands(session: Session) -> None:
    out = dispatch(session, "/help")
    assert any("/add" in line for line in out)


def test_unknown_command(session: Session) -> None:
    assert "알 수 없는 명령" in dispatch(session, "/dance")[0]


def test_empty_line_noop(session: Session) -> None:
    assert dispatch(session, "   ") == []


def test_quit_raises(session: Session) -> None:
    with pytest.raises(QuitShell):
        dispatch(session, "/quit")


# --- 자연어 캐처 (LLM 분류, FakeProvider 사용) ---

def test_capture_proposes_then_registers(session: Session) -> None:
    out = dispatch(session, "어제 첨부 다운로드가 자꾸 지연돼서 로그 봤어")
    assert any("분류:" in line and "장애·이슈 대응" in line for line in out)  # incident
    assert session.pending is not None  # 확인 대기

    confirm = dispatch(session, "y")
    assert confirm[0].startswith("✓ 등록됨")
    assert session.pending is None
    assert dispatch(session, "/list")[0].startswith("  [1] (진행중)")


def test_capture_cancel(session: Session) -> None:
    dispatch(session, "정기 백업 점검 했어")
    assert dispatch(session, "n") == ["취소됨"]
    assert session.pending is None
    assert dispatch(session, "/list") == ["  (등록된 task 없음)"]


def test_capture_edit_title_on_confirm(session: Session) -> None:
    dispatch(session, "장애 지연 발생")
    dispatch(session, "첨부 다운로드 지연(수정본)")  # 새 제목으로 등록
    assert session.pending is None
    out = dispatch(session, "/list")
    assert "첨부 다운로드 지연(수정본)" in out[0]


def test_capture_classifies_routine(session: Session) -> None:
    out = dispatch(session, "보안 패치 2건 배포함")
    assert any("정기 작업" in line for line in out)


def test_pending_blocks_other_commands(session: Session) -> None:
    """pending 중에는 입력이 확인 응답으로 해석된다(임의 텍스트=새 제목 등록)."""
    dispatch(session, "장애 지연")
    dispatch(session, "list")  # 명령이 아니라 '제목'으로 해석되어 등록됨
    assert session.pending is None
    assert any("list" in line for line in dispatch(session, "/list"))


# --- 멀티라인 자연어 입력 ---

def test_multiline_capture_uses_full_text(session: Session) -> None:
    """멀티라인 텍스트 전체가 LLM 에 전달되고, 첫 줄이 제목으로 쓰인다."""
    multiline = "어제 첨부 다운로드가 자꾸 지연돼서 로그 봤어\n캐시 이슈인 것 같고 스토리지도 확인 중"
    out = dispatch(session, multiline)
    assert any("분류:" in line for line in out)
    assert session.pending is not None
    # FakeProvider 는 첫 줄을 제목으로 — 제목이 두 번째 줄 내용을 포함하지 않음
    assert "캐시" not in session.pending.title


def test_multiline_capture_stores_detail(session: Session) -> None:
    """멀티라인 입력 시 첫 줄 이후가 detail 로 저장된다."""
    multiline = "장애 발생\n13:00 경 다운. 스토리지 로그 점검 중.\n임시 재시작으로 복구."
    dispatch(session, multiline)
    assert session.pending is not None
    assert "스토리지" in session.pending.detail or "13:00" in session.pending.detail


def test_multiline_pending_response_uses_first_line_only(session: Session) -> None:
    """pending 응답이 멀티라인이어도 첫 줄만 제목으로 처리한다."""
    dispatch(session, "장애 지연")
    assert session.pending is not None
    # 멀티라인 응답 → 첫 줄만 새 제목으로 등록
    dispatch(session, "새 제목\n두 번째 줄은 무시됨")
    assert session.pending is None
    out = dispatch(session, "/list")
    assert "새 제목" in out[0]
    assert "두 번째" not in out[0]


# --- /delete ---

def test_delete_removes_task(session: Session) -> None:
    dispatch(session, "/add 장애 첨부 다운로드 지연")
    dispatch(session, "/add 정기 보안 패치")
    out = dispatch(session, "/delete 1")
    assert "✓ 삭제됨  [1]" in out[-1]
    # 삭제 후 [1]이 보안 패치로 당겨짐
    remaining = dispatch(session, "/list")
    assert len(remaining) == 1
    assert "보안 패치" in remaining[0]


def test_delete_unknown_number(session: Session) -> None:
    out = dispatch(session, "/delete 99")
    assert out[0].startswith("⚠")


def test_delete_no_args(session: Session) -> None:
    assert dispatch(session, "/delete") == ["⚠ 사용법: /delete <번호>"]


def test_delete_aliases(session: Session) -> None:
    dispatch(session, "/add 장애 A")
    assert dispatch(session, "/del 1")[-1].startswith("✓ 삭제됨")
    dispatch(session, "/add 장애 B")
    assert dispatch(session, "/rm 1")[-1].startswith("✓ 삭제됨")


def test_delete_cascades_notes(session: Session) -> None:
    """삭제 시 연결된 메모도 함께 사라진다."""
    dispatch(session, "/add 장애 A")
    dispatch(session, "/note 1 메모")
    dispatch(session, "/delete 1")
    # 삭제 후 list 는 비어 있어야 함
    assert dispatch(session, "/list") == ["  (등록된 task 없음)"]


def test_delete_finalized_shows_warning(session: Session) -> None:
    from relay.models import ReportStatus
    dispatch(session, "/add 장애 A")
    session.store.set_report_status(session.week, session.system, ReportStatus.FINALIZED)
    out = dispatch(session, "/delete 1")
    assert any("finalized" in line for line in out)
    assert any("✓ 삭제됨" in line for line in out)


def test_fake_provider_multiline_title_from_first_line(session: Session) -> None:
    """FakeProvider 의 제목은 첫 줄만, 키워드 탐색은 전체 텍스트."""
    # 키워드("장애")가 두 번째 줄에 있어도 incident 로 분류돼야 함
    multiline = "오늘 작업 내용 정리\n첨부 다운로드 장애 발생 및 로그 점검"
    dispatch(session, multiline)
    assert session.pending is not None
    assert session.pending.category_key == "incident"
    # 제목은 첫 줄("오늘 작업 내용 정리")
    assert "장애" not in session.pending.title
