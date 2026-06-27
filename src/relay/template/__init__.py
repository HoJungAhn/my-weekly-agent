"""보고서 템플릿 로더·검증 (사용자 편집 가능한 외부 YAML).

설계 결정 #9 참조. label/role/key 분리, 로드 시 검증, 첫 실행 시 사용자 위치로 복사.
"""

from relay.template.loader import (
    TemplateError,
    default_template_path,
    ensure_user_template,
    load_active_template,
    load_template,
    user_template_path,
)
from relay.template.models import Category, Metric, Template

__all__ = [
    "Category",
    "Metric",
    "Template",
    "TemplateError",
    "default_template_path",
    "ensure_user_template",
    "load_active_template",
    "load_template",
    "user_template_path",
]
