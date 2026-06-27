"""실행 환경 경로·기본값 해석 — 설계 결정 #10(단발 CLI / 활성 컨텍스트).

DB·템플릿 위치를 환경변수로 오버라이드할 수 있게 해, 테스트가 실제 홈 디렉터리를 건드리지 않게 한다.
"""

from __future__ import annotations

import configparser
import os
import stat
from dataclasses import dataclass
from pathlib import Path

#: 활성 시스템을 못 정했을 때의 기본 시스템명(첫 사용 시).
DEFAULT_SYSTEM = "기본"

#: LLM 설정 파일의 섹션 이름.
_LLM_SECTION = "llm"


def relay_home() -> Path:
    """사용자 데이터·설정 루트. ``RELAY_HOME`` > ``XDG_CONFIG_HOME``/relay > ``~/.config/relay``."""
    if home := os.environ.get("RELAY_HOME"):
        return Path(home).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    config_dir = Path(base).expanduser() if base else Path.home() / ".config"
    return config_dir / "relay"


def db_path() -> Path:
    """SQLite 원본 DB 경로. ``RELAY_DB`` 로 오버라이드 가능(테스트용)."""
    if p := os.environ.get("RELAY_DB"):
        return Path(p).expanduser()
    return relay_home() / "relay.db"


def config_path() -> Path:
    """LLM 설정(properties) 파일 경로. ``RELAY_CONFIG`` 로 오버라이드 가능(테스트용)."""
    if p := os.environ.get("RELAY_CONFIG"):
        return Path(p).expanduser()
    return relay_home() / "config.ini"


@dataclass
class LLMConfig:
    """해석된 LLM 설정. api_key 는 (환경변수 우선 > 설정파일), model 은 설정파일 값."""

    api_key: str | None
    model: str | None


def load_llm_config() -> LLMConfig:
    """설정 파일과 환경변수에서 LLM 설정을 읽는다.

    api_key 우선순위: ``ANTHROPIC_API_KEY`` 환경변수 > 설정파일 ``[llm] api_key``.
    (CI·일시 사용은 env, 평소 보관은 파일 — 둘 다 지원.)
    """
    file_key: str | None = None
    file_model: str | None = None
    path = config_path()
    if path.exists():
        cp = configparser.ConfigParser()
        cp.read(path, encoding="utf-8")
        if cp.has_section(_LLM_SECTION):
            file_key = cp.get(_LLM_SECTION, "api_key", fallback="") or None
            file_model = cp.get(_LLM_SECTION, "model", fallback="") or None
    api_key = os.environ.get("ANTHROPIC_API_KEY") or file_key
    return LLMConfig(api_key=api_key, model=file_model)


def save_api_key(api_key: str, model: str | None = None) -> Path:
    """api_key(+선택 model)를 설정 파일에 저장하고 권한을 600으로 좁힌다(평문 비밀)."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cp = configparser.ConfigParser()
    if path.exists():
        cp.read(path, encoding="utf-8")
    if not cp.has_section(_LLM_SECTION):
        cp.add_section(_LLM_SECTION)
    cp.set(_LLM_SECTION, "api_key", api_key)
    if model:
        cp.set(_LLM_SECTION, "model", model)
    with path.open("w", encoding="utf-8") as f:
        cp.write(f)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    return path
