"""템플릿 로드·검증·배포 — 설계 결정 #9.

- 저장소의 기본 템플릿은 원본(읽기 전용). 첫 실행 시 사용자 위치로 복사해 사용자는 사본을 편집한다.
- 로드 시점에 깨진 YAML·중복 key·필수 role 누락을 명확한 에러로 차단한다(조용한 오동작 금지).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import yaml
from pydantic import ValidationError

from relay.template.models import Template


class TemplateError(Exception):
    """템플릿 로드/검증 실패. 사용자에게 그대로 보여줄 만한 한국어 메시지를 담는다."""


def load_template(path: str | Path) -> Template:
    """경로의 YAML 템플릿을 읽어 검증된 :class:`Template` 로 반환한다.

    실패(파일 없음/깨진 YAML/구조 오류)는 모두 :class:`TemplateError` 로 변환한다.
    """
    path = Path(path)
    if not path.exists():
        raise TemplateError(f"템플릿 파일이 없습니다: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise TemplateError(f"템플릿 YAML 파싱 실패: {path}\n{e}") from e

    if not isinstance(raw, dict):
        raise TemplateError(f"템플릿 최상위가 매핑(dict)이 아닙니다: {path}")

    try:
        return Template.model_validate(raw)
    except ValidationError as e:
        raise TemplateError(f"템플릿 구조 오류: {path}\n{e}") from e


def default_template_path() -> Path:
    """저장소의 기본 템플릿(읽기 전용 원본) 경로.

    MVP(개발 중)에는 이 파일에서 위로 올라가며 ``templates/weekly_template.yaml`` 을 찾는다.
    (패키징 후에는 package data 로 대체 예정 — T3 한계로 기록.)
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "templates" / "weekly_template.yaml"
        if candidate.exists():
            return candidate
    raise TemplateError("기본 템플릿(templates/weekly_template.yaml)을 찾을 수 없습니다.")


def user_template_path() -> Path:
    """사용자가 편집하는 템플릿 사본 경로.

    우선순위: ``RELAY_TEMPLATE`` > ``RELAY_HOME``/template.yaml >
    ``XDG_CONFIG_HOME``/relay/template.yaml > ``~/.config/relay/template.yaml``.
    """
    if env := os.environ.get("RELAY_TEMPLATE"):
        return Path(env).expanduser()
    if home := os.environ.get("RELAY_HOME"):
        return Path(home).expanduser() / "template.yaml"
    base = os.environ.get("XDG_CONFIG_HOME")
    config_dir = Path(base).expanduser() if base else Path.home() / ".config"
    return config_dir / "relay" / "template.yaml"


def ensure_user_template(default_src: str | Path | None = None) -> Path:
    """사용자 템플릿 사본이 없으면 기본 템플릿을 복사 생성하고, 경로를 반환한다(설계 #9)."""
    dest = user_template_path()
    if not dest.exists():
        src = Path(default_src) if default_src is not None else default_template_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    return dest


def load_active_template(default_src: str | Path | None = None) -> Template:
    """첫 실행 시 사본을 보장하고, 사용자 사본을 로드·검증해 반환한다."""
    return load_template(ensure_user_template(default_src))
