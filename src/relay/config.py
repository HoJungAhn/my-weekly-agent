"""실행 환경 경로·기본값 해석 — 설계 결정 #10(단발 CLI / 활성 컨텍스트).

DB·템플릿 위치를 환경변수로 오버라이드할 수 있게 해, 테스트가 실제 홈 디렉터리를 건드리지 않게 한다.
"""

from __future__ import annotations

import os
from pathlib import Path

#: 활성 시스템을 못 정했을 때의 기본 시스템명(첫 사용 시).
DEFAULT_SYSTEM = "기본"


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
