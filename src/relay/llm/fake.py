"""오프라인 fake provider — API 키 없이도 캐처가 동작하게 하고, 테스트를 결정적으로 만든다.

규칙 기반(키워드)으로 role 을 고르고, 그 role 에 해당하는 템플릿 카테고리 key 를 돌려준다.
정확도가 목표가 아니라 '연결 없이 흐름이 돈다'가 목표다(설계 #8: 어댑터로 교체 가능).
"""

from __future__ import annotations

from relay.llm.base import CategoryOption, Classification, LLMProvider

# (role, 키워드) — 위에서부터 먼저 매칭. role 은 템플릿 카테고리의 role 과 맞춰 해석한다.
_RULES: list[tuple[str, list[str]]] = [
    ("incident", ["장애", "지연", "오류", "에러", "다운", "느림", "느려", "실패", "중단", "버그"]),
    ("routine", ["배포", "패치", "백업", "점검", "모니터링", "정기"]),
    ("improvement", ["요청", "개선", "정정", "its", "문의", "권한"]),
    ("next_week_plan", ["계획", "예정", "다음주", "다음 주"]),
]


def _make_title(text: str, limit: int = 40) -> str:
    t = " ".join(text.strip().split())
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


class FakeProvider(LLMProvider):
    """키워드 규칙 분류기(오프라인 폴백)."""

    def classify(self, text: str, categories: list[CategoryOption]) -> Classification:
        by_role = {c.role: c for c in categories}
        low = text.lower()
        chosen: CategoryOption | None = None
        for role, keywords in _RULES:
            if role in by_role and any(kw.lower() in low for kw in keywords):
                chosen = by_role[role]
                break
        if chosen is None:  # 못 정하면 운영현황(operation) 또는 첫 카테고리
            chosen = by_role.get("operation") or categories[0]
        return Classification(category_key=chosen.key, title=_make_title(text))
