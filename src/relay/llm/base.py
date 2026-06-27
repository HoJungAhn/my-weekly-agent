"""LLM provider 추상화 — 설계 결정 #8.

외부 API(Claude)와 로컬(향후)을 교체 가능하게 인터페이스를 둔다. 호출 지점은 모두 이 뒤로 숨긴다.
지금 필요한 능력은 "자연어 메모 → 카테고리 분류 + 제목 요약" 하나다(설계 #7 보강).
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


class LLMProvider(ABC):
    """provider 인터페이스. 구현: ClaudeProvider(외부 API), FakeProvider(오프라인/테스트)."""

    @abstractmethod
    def classify(self, text: str, categories: list[CategoryOption]) -> Classification:
        """자연어 메모를 카테고리로 분류하고 간결한 제목을 만든다."""
        raise NotImplementedError
