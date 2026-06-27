"""Claude(외부 API) provider — 설계 결정 #8.

구조화 출력은 tool-use 로 강제한다: category_key 를 템플릿의 실제 key enum 으로 제약해, LLM 이
없는 카테고리를 지어내지 못하게 한다(코드가 구조를 검증, LLM 은 서술 — 설계 검증 원칙).
분류는 가벼운 작업이라 기본 모델은 Sonnet 4.6(능력+경제성). 필요 시 교체.

주의: 입력 메모가 외부 API 로 전송된다(설계 #8의 데이터 경계 — MVP 는 외부 API).
"""

from __future__ import annotations

from relay.llm.base import DEFAULT_MODEL, CategoryOption, Classification, LLMProvider


class ClaudeProvider(LLMProvider):
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        client: object | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key  # 설정파일에서 온 키(env 가 아닐 때)를 클라이언트에 주입
        self._client = client  # 주입 가능(테스트). None 이면 첫 호출 시 anthropic.Anthropic()

    def _ensure_client(self) -> object:
        if self._client is None:
            import anthropic  # 지연 import — 키/패키지 없이도 모듈 로드는 되게

            self._client = (
                anthropic.Anthropic(api_key=self._api_key)
                if self._api_key
                else anthropic.Anthropic()
            )
        return self._client

    def classify(self, text: str, categories: list[CategoryOption]) -> Classification:
        client = self._ensure_client()
        tool = {
            "name": "classify_task",
            "description": "운영 주간보고 메모를 카테고리로 분류하고 간결한 제목/상세를 만든다.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "category_key": {
                        "type": "string",
                        "enum": [c.key for c in categories],
                        "description": "아래 카테고리 중 하나의 key",
                    },
                    "title": {"type": "string", "description": "한 줄 제목(간결하게)"},
                    "detail": {"type": "string", "description": "보충 설명(없으면 빈 문자열)"},
                },
                "required": ["category_key", "title"],
            },
        }
        catalog = "\n".join(f"- {c.key} ({c.label}): {c.hint}" for c in categories)
        prompt = (
            "다음은 운영 시스템 주간보고에 넣을 업무 메모다. 카테고리로 분류하고 "
            "보고서에 어울리는 간결한 제목을 만들어라.\n\n"
            f"[카테고리]\n{catalog}\n\n[메모]\n{text}"
        )
        message = client.messages.create(  # type: ignore[attr-defined]
            model=self._model,
            max_tokens=512,
            tools=[tool],
            tool_choice={"type": "tool", "name": "classify_task"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in message.content:
            if getattr(block, "type", None) == "tool_use":
                return Classification(**block.input)
        raise RuntimeError("LLM 이 분류 결과(tool_use)를 반환하지 않았습니다.")
