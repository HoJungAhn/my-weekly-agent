"""LLM 설정(properties) 로드/저장 테스트 (설계 #8).

api_key 우선순위(env > 파일), 모델 읽기, 저장 후 재로드, 파일 권한(600).
"""

import stat

import pytest

from relay.config import config_path, load_llm_config, save_api_key


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("RELAY_CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_no_file_no_env_is_empty() -> None:
    cfg = load_llm_config()
    assert cfg.api_key is None and cfg.model is None


def test_env_provides_key(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert load_llm_config().api_key == "sk-env"


def test_save_then_load_roundtrip() -> None:
    save_api_key("sk-file", model="claude-sonnet-4-6")
    cfg = load_llm_config()
    assert cfg.api_key == "sk-file"
    assert cfg.model == "claude-sonnet-4-6"


def test_env_overrides_file(monkeypatch) -> None:
    save_api_key("sk-file")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert load_llm_config().api_key == "sk-env"  # env 우선


def test_saved_file_is_owner_only() -> None:
    path = save_api_key("sk-secret")
    mode = stat.S_IMODE(config_path().stat().st_mode)
    assert mode == 0o600, oct(mode)
    assert path == config_path()


def test_model_persisted_separately() -> None:
    save_api_key("sk-1", model="claude-opus-4-8")
    save_api_key("sk-2")  # model 미지정 → 기존 model 유지
    cfg = load_llm_config()
    assert cfg.api_key == "sk-2"
    assert cfg.model == "claude-opus-4-8"
