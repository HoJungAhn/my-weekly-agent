"""LLM provider 추상화 — 설계 결정 #8.

외부 API(Claude)와 로컬(향후)을 교체 가능하게 인터페이스를 둔다. 호출 지점은 모두 이 뒤로 숨긴다.

능력:
  - classify()         자연어 메모 → 카테고리 분류 + 제목 요약 (T10b)
  - narrate_section()  카테고리 섹션 task 목록 → Markdown 서술 (T12)
  - self_critique()    서술 품질 자가검증 → 환각/목적 부합 판단 (T12)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel

#: 분류 기본 모델 — 가벼운 작업이라 Sonnet 4.6(능력+경제성). 설정파일 model 로 덮어쓸 수 있다.
DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass(frozen=True)
class CategoryOption:
    """LLM 에 넘기는 카테고리 선택지(템플릿에서 파생)."""

    key: str
    role: str
    label: str
    hint: str = ""


class Classification(BaseModel):
    """LLM 분류 결과 — category_key 는 호출 측(services)이 템플릿과 대조해 검증한다."""

    category_key: str
    title: str
    detail: str = ""


class SelfCritiqueResult(BaseModel):
    """LLM 자가검증 결과 — narrate↔verify 루프에서 사용(설계: 검증 단계)."""

    ok: bool
    issues: list[str]  # 발견된 문제점 (ok=True 이면 빈 리스트)
    feedback: str  # 재서술 시 반영할 구체적 지침


class LLMProvider(ABC):
    """provider 인터페이스. 구현: ClaudeProvider(외부 API), FakeProvider(오프라인/테스트)."""

    @abstractmethod
    def classify(self, text: str, categories: list[CategoryOption]) -> Classification:
        """자연어 메모를 카테고리로 분류하고 간결한 제목을 만든다."""
        raise NotImplementedError

    @abstractmethod
    def narrate_section(
        self,
        label: str,
        hint: str,
        task_block: str,
        feedback: str | None = None,
    ) -> str:
        """카테고리 섹션의 task 목록을 받아 운영 서술(Markdown 불릿)을 생성한다.

        ``task_block``: task 상태·carry 등을 담은 텍스트 블록(LLM 컨텍스트).
        ``feedback``: 이전 검증 실패 사유 — 재서술 시 참고 지침. None 이면 첫 시도.
        빈 섹션이면 빈 문자열을 반환한다.
        """
        raise NotImplementedError

    @abstractmethod
    def self_critique(
        self,
        label: str,
        task_block: str,
        narrative: str,
    ) -> SelfCritiqueResult:
        """작성된 서술이 task 데이터와 일치하고 목적에 부합하는지 자가검증한다.

        환각(task 에 없는 사실) · 목적 부합(안정성 증거+리스크) · 3축 충족을 판단한다.
        """
        raise NotImplementedError
