"""도메인 모델 검증 테스트 (T4 / 설계 #2).

핵심: status 에 '이월'이 들어가지 못한다(상태/출처 분리), week 형식 검증, 기본값.
"""

import pytest
from pydantic import ValidationError

from relay.models import CARRYABLE_STATUSES, Status, Task


def _task(**kw) -> Task:
    base = dict(week="2026-W26", system="그룹웨어", category_key="incident",
                title="첨부 다운로드 지연", status=Status.IN_PROGRESS)
    base.update(kw)
    return Task(**base)


def test_valid_task_defaults() -> None:
    t = _task()
    assert t.id is None and t.thread_id is None  # 저장 시 채워짐
    assert t.carry_count == 0
    assert t.related_ids == [] and t.metrics == {}
    assert t.carried_from is None


@pytest.mark.parametrize("status", list(Status))
def test_all_valid_statuses_accepted(status: Status) -> None:
    assert _task(status=status).status is status


def test_status_rejects_carryover_value() -> None:
    """'이월'은 작업 상태가 아니다 — status 로 들어오면 거부(설계 #2 핵심)."""
    with pytest.raises(ValidationError):
        _task(status="이월")


def test_status_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        _task(status="진행")  # 오타


def test_carryable_excludes_done_and_canceled() -> None:
    """이월 대상은 진행중/미완료/보류뿐. 완료·취소는 끌어오지 않는다(설계 #4)."""
    assert CARRYABLE_STATUSES == {Status.IN_PROGRESS, Status.INCOMPLETE, Status.BLOCKED}
    assert Status.DONE not in CARRYABLE_STATUSES
    assert Status.CANCELED not in CARRYABLE_STATUSES


def test_week_format_validated() -> None:
    with pytest.raises(ValidationError):
        _task(week="2026-26")  # 잘못된 형식


def test_carried_from_validated_when_present() -> None:
    assert _task(carried_from="2026-W25").carried_from == "2026-W25"
    with pytest.raises(ValidationError):
        _task(carried_from="bad")


def test_metrics_and_related_ids_accepted() -> None:
    t = _task(related_ids=[1, 2, 3], metrics={"가동률": 99.95, "처리건수": 14200})
    assert t.related_ids == [1, 2, 3]
    assert t.metrics["가동률"] == 99.95
