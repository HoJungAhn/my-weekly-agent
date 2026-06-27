"""보고서 템플릿 구조 모델 (Pydantic) — 설계 결정 #9.

코드는 사용자가 보는 ``label`` 이 아니라 변하지 않는 ``key``/``role`` 에 의존한다.
metric 은 선택 보조 축(#6)이라 0개를 허용하고, 미래 필드(direction/agg/target)를
위해 추가 필드를 허용한다(forward-compatible).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: '다음주 계획 → 금주 신규 Task 승격' 자동화가 의존하는 필수 role (설계 #4·#9).
REQUIRED_ROLES = frozenset({"next_week_plan"})


class Metric(BaseModel):
    """카테고리에서 수동 입력받을 정량 지표 정의(선택, #6).

    direction/agg/target 등 미래 메타데이터를 사용자가 미리 넣어도 깨지지 않도록
    추가 필드를 허용한다.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    unit: str = ""
    trend: bool = False


class Category(BaseModel):
    """보고서 카테고리. ``key``/``role`` 은 로직이 의존하는 식별자(변경 금지)."""

    model_config = ConfigDict(extra="forbid")

    key: str
    role: str
    label: str
    order: int = 0
    hint: str = ""
    metrics: list[Metric] = Field(default_factory=list)


class Meta(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = "주간업무 보고"
    week_label_format: str = "{start} ~ {end}"


class Options(BaseModel):
    model_config = ConfigDict(extra="allow")

    carry_over_statuses: list[str] = Field(default_factory=lambda: ["진행중", "미완료"])
    carry_warn_threshold: int = 3


class Template(BaseModel):
    """검증된 보고서 템플릿. 로드 시점에 무결성을 보장한다(설계 #9: 조용한 오동작 금지)."""

    model_config = ConfigDict(extra="forbid")

    version: int
    meta: Meta = Field(default_factory=Meta)
    categories: list[Category]
    options: Options = Field(default_factory=Options)

    @model_validator(mode="after")
    def _check_integrity(self) -> Template:
        if not self.categories:
            raise ValueError("categories 가 비어 있습니다 — 최소 1개의 카테고리가 필요합니다.")

        seen: dict[str, int] = {}
        for i, cat in enumerate(self.categories):
            if cat.key in seen:
                raise ValueError(
                    f"중복된 카테고리 key: {cat.key!r} "
                    f"(categories[{seen[cat.key]}] 와 categories[{i}])"
                )
            seen[cat.key] = i

        roles = {cat.role for cat in self.categories}
        missing = REQUIRED_ROLES - roles
        if missing:
            raise ValueError(
                f"필수 role 누락: {sorted(missing)} — "
                "이 role 이 없으면 '다음주 계획 승격' 자동화가 동작하지 않습니다(설계 #4)."
            )
        return self

    def by_role(self, role: str) -> Category:
        """role 로 카테고리를 찾는다(label 의존 금지 — 설계 #9)."""
        for cat in self.categories:
            if cat.role == role:
                return cat
        raise KeyError(f"role 에 해당하는 카테고리가 없습니다: {role!r}")

    def ordered(self) -> list[Category]:
        """``order`` 순으로 정렬된 카테고리(보고서 렌더 순서)."""
        return sorted(self.categories, key=lambda c: c.order)
