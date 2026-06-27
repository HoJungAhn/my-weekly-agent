"""LLM provider 어댑터 — 설계 결정 #8.

호출 지점은 모두 :class:`LLMProvider` 뒤로 숨긴다. 기본 provider 는 API 키 유무로 결정한다:
키가 있으면 Claude(외부 API), 없으면 오프라인 FakeProvider(키워드 규칙).
"""

from __future__ import annotations

from relay.llm.base import DEFAULT_MODEL, CategoryOption, Classification, LLMProvider


def make_provider(api_key: str | None, model: str | None = None) -> LLMProvider:
    """키가 있으면 Claude(외부 API), 없으면 오프라인 FakeProvider 를 만든다."""
    if api_key:
        from relay.llm.claude import ClaudeProvider

        return ClaudeProvider(model=model or DEFAULT_MODEL, api_key=api_key)
    from relay.llm.fake import FakeProvider

    return FakeProvider()


def default_provider() -> LLMProvider:
    """설정(파일+환경변수)에서 키/모델을 읽어 기본 provider 를 만든다."""
    from relay.config import load_llm_config

    cfg = load_llm_config()
    return make_provider(cfg.api_key, cfg.model)


__all__ = [
    "DEFAULT_MODEL",
    "CategoryOption",
    "Classification",
    "LLMProvider",
    "default_provider",
    "make_provider",
]
