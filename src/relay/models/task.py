"""도메인 모델 (Pydantic) — Task / Note / Report. 설계 결정 #2.

핵심: **작업 상태(status)와 이월 출처(carried_from)를 분리**한다. status 에는 절대 '이월'이
들어가지 않는다(이월은 carried_from/carry_count 로만 표현). 같은 작업을 주차를 가로질러 잇는
``thread_id`` 는 시스템이 자동 발급·승계하며 사용자에게 노출하지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from relay.week import parse_week_key


class Status(StrEnum):
    """작업 상태 — 한 task 는 이 중 정확히 하나. (의도적으로 '이월'을 포함하지 않는다.)"""

    DONE = "완료"
    IN_PROGRESS = "진행중"
    INCOMPLETE = "미완료"
    BLOCKED = "보류"  # 의존성/승인 대기로 막힘 — 단순 미완료와 리스크 의미가 다름
    CANCELED = "취소"  # 더 이상 안 함 — 안 닫으면 carry 체인이 무한히 따라옴


#: 금주 초안 생성 시 다음 주로 자동 이월하는 상태들 (설계 #4). 완료·취소는 제외.
CARRYABLE_STATUSES: frozenset[Status] = frozenset(
    {Status.IN_PROGRESS, Status.INCOMPLETE, Status.BLOCKED}
)


class ReportStatus(StrEnum):
    """보고서(주차+시스템 단위) 상태. RAG 인덱싱은 FINALIZED 만 대상(설계 #2)."""

    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    FINALIZED = "finalized"


def _validate_week_key(v: str) -> str:
    parse_week_key(v)  # 형식 틀리면 ValueError → 모델 검증 실패
    return v


class Task(BaseModel):
    """주간보고의 1급 시민. id/thread_id/타임스탬프는 저장 시 시스템이 채운다."""

    model_config = ConfigDict(use_enum_values=False)

    id: int | None = None
    week: str
    system: str
    category_key: str
    title: str
    detail: str = ""
    status: Status
    carried_from: str | None = None  # 이월 원본 주차(없으면 신규)
    carry_count: int = 0
    thread_id: str | None = None  # 저장 시 자동 발급/승계
    related_ids: list[int] = Field(default_factory=list)  # RAG 로 연결한 과거 Task id
    metrics: dict[str, float] = Field(default_factory=dict)  # 정량 지표(선택, #6)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("week")
    @classmethod
    def _check_week(cls, v: str) -> str:
        return _validate_week_key(v)

    @field_validator("carried_from")
    @classmethod
    def _check_carried_from(cls, v: str | None) -> str | None:
        return _validate_week_key(v) if v is not None else v


class Note(BaseModel):
    """task 에 누적되는 날짜별 진행 메모(진행률 % 대체 — 설계 #2)."""

    id: int | None = None
    task_id: int
    body: str
    created_at: datetime | None = None


class Report(BaseModel):
    """주차+시스템 단위 보고서 상태."""

    week: str
    system: str
    status: ReportStatus = ReportStatus.DRAFT
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("week")
    @classmethod
    def _check_week(cls, v: str) -> str:
        return _validate_week_key(v)
