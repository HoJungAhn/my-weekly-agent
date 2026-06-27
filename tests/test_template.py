"""템플릿 로더·검증 테스트 (T3 / 설계 #9).

핵심: 로드 시점에 깨진 YAML·중복 key·필수 role 누락을 명확한 에러로 차단(조용한 오동작 금지),
metric 선택 허용, 첫 실행 시 사용자 사본 복사.
"""

from pathlib import Path

import pytest
import yaml

from relay.template import (
    TemplateError,
    default_template_path,
    ensure_user_template,
    load_template,
)


def _base_template() -> dict:
    """검증을 통과하는 최소 정상 템플릿(테스트에서 변형해 쓴다)."""
    return {
        "version": 1,
        "meta": {"title": "테스트 보고", "week_label_format": "{start} ~ {end}"},
        "categories": [
            {"key": "operation", "role": "operation", "label": "운영", "order": 1,
             "metrics": [{"name": "가동률", "unit": "%", "trend": True}]},
            {"key": "next_week", "role": "next_week_plan", "label": "다음주", "order": 2},
        ],
        "options": {"carry_over_statuses": ["진행중", "미완료"], "carry_warn_threshold": 3},
    }


def _write(tmp_path: Path, data) -> Path:
    p = tmp_path / "t.yaml"
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def test_loads_repo_default_template() -> None:
    """저장소 기본 템플릿이 검증을 통과하고 필수 role 을 갖는다."""
    tpl = load_template(default_template_path())
    assert tpl.by_role("next_week_plan").key == "next_week"
    # ordered() 는 order 순
    orders = [c.order for c in tpl.ordered()]
    assert orders == sorted(orders)


def test_loads_minimal_valid(tmp_path: Path) -> None:
    tpl = load_template(_write(tmp_path, _base_template()))
    assert tpl.version == 1
    assert tpl.by_role("operation").metrics[0].name == "가동률"


def test_metrics_optional(tmp_path: Path) -> None:
    """metric 0개 카테고리 허용(#6: 선택 보조 축)."""
    data = _base_template()
    data["categories"][0]["metrics"] = []
    tpl = load_template(_write(tmp_path, data))
    assert tpl.by_role("operation").metrics == []


def test_metric_allows_future_fields(tmp_path: Path) -> None:
    """direction/agg/target 등 미래 필드를 넣어도 깨지지 않는다(forward-compatible, #6)."""
    data = _base_template()
    data["categories"][0]["metrics"][0].update({"direction": "higher_better", "target": 99.9})
    tpl = load_template(_write(tmp_path, data))
    assert tpl.by_role("operation").metrics[0].name == "가동률"


def test_duplicate_key_rejected(tmp_path: Path) -> None:
    data = _base_template()
    data["categories"][1]["key"] = "operation"  # 중복
    with pytest.raises(TemplateError, match="중복된 카테고리 key"):
        load_template(_write(tmp_path, data))


def test_missing_required_role_rejected(tmp_path: Path) -> None:
    data = _base_template()
    data["categories"][1]["role"] = "something_else"  # next_week_plan 제거
    with pytest.raises(TemplateError, match="필수 role 누락"):
        load_template(_write(tmp_path, data))


def test_empty_categories_rejected(tmp_path: Path) -> None:
    data = _base_template()
    data["categories"] = []
    with pytest.raises(TemplateError, match="categories 가 비어"):
        load_template(_write(tmp_path, data))


def test_broken_yaml_rejected(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match="YAML 파싱 실패"):
        load_template(_write(tmp_path, "version: 1\n  bad: : indent:"))


def test_non_mapping_top_level_rejected(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match="매핑"):
        load_template(_write(tmp_path, "- just\n- a\n- list"))


def test_unknown_category_field_rejected(tmp_path: Path) -> None:
    """카테고리에 오타/미지원 필드가 있으면 조용히 무시하지 않고 막는다(extra=forbid)."""
    data = _base_template()
    data["categories"][0]["roel"] = "typo"  # role 오타
    with pytest.raises(TemplateError):
        load_template(_write(tmp_path, data))


def test_missing_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match="템플릿 파일이 없습니다"):
        load_template(tmp_path / "nope.yaml")


def test_ensure_user_template_copies_on_first_run(tmp_path: Path, monkeypatch) -> None:
    """첫 실행 시 사용자 위치에 기본 템플릿을 복사 생성한다(설계 #9)."""
    home = tmp_path / "relayhome"
    monkeypatch.setenv("RELAY_HOME", str(home))
    monkeypatch.delenv("RELAY_TEMPLATE", raising=False)

    dest = ensure_user_template(default_template_path())
    assert dest.exists()
    assert dest == home / "template.yaml"
    # 복사본도 정상 로드돼야 한다
    assert load_template(dest).by_role("next_week_plan")

    # 두 번째 실행은 덮어쓰지 않는다(사용자 편집 보존)
    dest.write_text("version: 1\ncategories: []\n", encoding="utf-8")
    ensure_user_template(default_template_path())
    assert "categories: []" in dest.read_text(encoding="utf-8")
