"""자연어 캐처 서비스 — 자연어 메모를 LLM 으로 분류(설계 #7 보강 / #8 어댑터).

LLM 은 제안만 한다. category_key 가 템플릿의 실제 key 인지 **코드가 검증**하고, 통과한 것만
호출 측(shell)이 사람 확인을 거쳐 저장한다.
"""

from __future__ import annotations

from relay.llm.base import CategoryOption, Classification, LLMProvider
from relay.template import Template


def classify_capture(provider: LLMProvider, template: Template, text: str) -> Classification:
    """자연어 ``text`` 를 분류해 검증된 :class:`Classification` 으로 반환한다.

    LLM 이 템플릿에 없는 카테고리나 빈 제목을 주면 ValueError(조용한 오동작 금지).
    """
    if not text.strip():
        raise ValueError("빈 입력은 분류할 수 없습니다.")

    options = [CategoryOption(c.key, c.role, c.label, c.hint) for c in template.categories]
    result = provider.classify(text, options)

    valid_keys = {c.key for c in template.categories}
    if result.category_key not in valid_keys:
        raise ValueError(f"분류된 카테고리가 템플릿에 없습니다: {result.category_key!r}")
    if not result.title.strip():
        raise ValueError("분류 결과 제목이 비어 있습니다.")
    return result
