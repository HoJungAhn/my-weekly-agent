"""Claude(외부 API) provider — 설계 결정 #8.

구조화 출력은 tool-use 로 강제한다: 코드가 구조를 검증하고 LLM 은 서술만 담당한다.
``dry_run=True`` 이면 프롬프트를 stdout 에 출력하고 API 를 호출하지 않는다(데이터 경계 확인용).

주의: 입력 메모·task 데이터가 외부 API 로 전송된다(설계 #8: MVP 는 외부 API).
"""

from __future__ import annotations

from relay.llm.base import (
    DEFAULT_MODEL,
    CategoryOption,
    Classification,
    LLMProvider,
    SelfCritiqueResult,
)

# narrate_section 에서 반환할 placeholder (dry_run 전용)
_DRY_RUN_NARRATIVE = "- (dry-run: 실제 LLM 호출 없이 서술 생략)"
_DRY_RUN_CRITIQUE = SelfCritiqueResult(ok=True, issues=[], feedback="(dry-run)")


class ClaudeProvider(LLMProvider):
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        client: object | None = None,
        api_key: str | None = None,
        dry_run: bool = False,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._client = client
        self._dry_run = dry_run

    def _ensure_client(self) -> object:
        if self._client is None:
            import anthropic

            self._client = (
                anthropic.Anthropic(api_key=self._api_key)
                if self._api_key
                else anthropic.Anthropic()
            )
        return self._client

    def _call(self, prompt: str, tool: dict, tool_name: str, max_tokens: int = 1024) -> dict:
        """tool-use 구조화 출력 호출 — dry_run 이면 prompt 출력 후 즉시 반환."""
        if self._dry_run:
            print(f"\n[dry-run] 도구: {tool_name}\n프롬프트:\n{prompt}\n")
            return {}
        client = self._ensure_client()
        message = client.messages.create(  # type: ignore[attr-defined]
            model=self._model,
            max_tokens=max_tokens,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in message.content:
            if getattr(block, "type", None) == "tool_use":
                return block.input  # type: ignore[return-value]
        raise RuntimeError(f"LLM 이 {tool_name}(tool_use)를 반환하지 않았습니다.")

    # ── classify ────────────────────────────────────────────────────────────

    def classify(self, text: str, categories: list[CategoryOption]) -> Classification:
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
                    "detail": {
                        "type": "string",
                        "description": "보충 설명 — 멀티라인 입력이면 요점을 2-3문장으로 정리(없으면 빈 문자열)",
                    },
                },
                "required": ["category_key", "title"],
            },
        }
        catalog = "\n".join(f"- {c.key} ({c.label}): {c.hint}" for c in categories)
        prompt = (
            "다음은 운영 시스템 주간보고에 넣을 업무 메모다. 카테고리로 분류하고 "
            "보고서에 어울리는 간결한 제목을 만들어라. "
            "여러 줄 입력이면 detail 에 핵심 내용을 2-3문장으로 요약하라.\n\n"
            f"[카테고리]\n{catalog}\n\n[메모]\n{text}"
        )
        if self._dry_run:
            print(f"\n[dry-run] classify\n프롬프트:\n{prompt}\n")
            return Classification(category_key=categories[0].key, title=text[:40], detail="")
        raw = self._call(prompt, tool, "classify_task", max_tokens=512)
        return Classification(**raw)

    # ── narrate_section ─────────────────────────────────────────────────────

    def narrate_section(
        self,
        label: str,
        hint: str,
        task_block: str,
        feedback: str | None = None,
    ) -> str:
        if not task_block.strip():
            return ""

        tool = {
            "name": "write_narrative",
            "description": "주간보고 섹션의 운영 서술(Markdown 불릿)을 작성한다.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "narrative": {
                        "type": "string",
                        "description": "Markdown bullet(- 내용) 형식의 운영 서술. 2-4개 항목.",
                    }
                },
                "required": ["narrative"],
            },
        }
        feedback_block = ""
        if feedback:
            feedback_block = f"\n[이전 검증 실패 — 재작성 시 반영]\n{feedback}\n"

        prompt = (
            f"당신은 시스템 운영 주간보고 작성 어시스턴트입니다.\n\n"
            f'"{label}" 섹션의 이번 주 task 목록을 바탕으로 운영 서술을 작성하세요.\n'
            f"섹션 힌트: {hint}\n\n"
            f"[Task 목록]\n{task_block}\n{feedback_block}\n"
            "작성 규칙:\n"
            "- task 에 없는 수치나 사실을 만들어내지 마세요(환각 금지).\n"
            "- '단순 나열'이 아닌 '시스템 안정성 증거 + 리스크 관리' 관점으로 서술하세요.\n"
            "- Markdown bullet(`- 내용`) 2-4개로 작성하세요.\n"
        )

        if self._dry_run:
            print(f"\n[dry-run] narrate_section({label})\n프롬프트:\n{prompt}\n")
            return _DRY_RUN_NARRATIVE

        raw = self._call(prompt, tool, "write_narrative", max_tokens=600)
        return raw.get("narrative", "")

    # ── self_critique ────────────────────────────────────────────────────────

    def self_critique(
        self,
        label: str,
        task_block: str,
        narrative: str,
    ) -> SelfCritiqueResult:
        if not narrative.strip():
            return SelfCritiqueResult(ok=True, issues=[], feedback="")

        tool = {
            "name": "critique_narrative",
            "description": "주간보고 서술의 품질을 검토한다.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ok": {
                        "type": "boolean",
                        "description": "서술이 적절하면 true",
                    },
                    "issues": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "발견된 문제점 목록 (ok=true 이면 빈 배열)",
                    },
                    "feedback": {
                        "type": "string",
                        "description": "재작성 시 반영해야 할 구체적 지침 (ok=true 이면 빈 문자열)",
                    },
                },
                "required": ["ok", "issues", "feedback"],
            },
        }
        prompt = (
            f'다음 "{label}" 섹션 주간보고 서술을 검토하세요.\n\n'
            f"[Task 데이터 (사실의 원천)]\n{task_block}\n\n"
            f"[작성된 서술]\n{narrative}\n\n"
            "검토 항목:\n"
            "1. task 에 없는 수치나 사실을 지어냈는가? (환각)\n"
            "2. '단순 나열'이 아닌 '안정성 증거 + 리스크' 관점인가?\n"
            "3. 서술 내용이 task 데이터와 일치하는가?\n"
        )

        if self._dry_run:
            print(f"\n[dry-run] self_critique({label})\n프롬프트:\n{prompt}\n")
            return _DRY_RUN_CRITIQUE

        raw = self._call(prompt, tool, "critique_narrative", max_tokens=512)
        return SelfCritiqueResult(**raw)
