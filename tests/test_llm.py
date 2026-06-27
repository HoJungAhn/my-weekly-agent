"""LLM provider + 캐처 분류 서비스 테스트 (T10 / 설계 #7·#8).

FakeProvider 규칙, classify_capture 검증, ClaudeProvider 의 tool 구성·파싱(스텁 클라이언트로 망 없이).
"""

import pytest

from relay.llm import Classification, default_provider
from relay.llm.base import CategoryOption
from relay.llm.claude import ClaudeProvider
from relay.llm.fake import FakeProvider
from relay.services.capture import classify_capture
from relay.template import default_template_path, load_template

CATS = [
    CategoryOption("operation", "operation", "시스템 운영 현황", ""),
    CategoryOption("incident", "incident", "장애·이슈 대응", ""),
    CategoryOption("routine", "routine", "정기 작업", ""),
    CategoryOption("next_week", "next_week_plan", "다음 주 계획 / 리스크", ""),
]


@pytest.fixture
def template():
    return load_template(default_template_path())


def test_fake_classifies_incident() -> None:
    r = FakeProvider().classify("첨부 다운로드 지연 발생", CATS)
    assert r.category_key == "incident"
    assert r.title  # 제목 생성됨


def test_fake_classifies_routine() -> None:
    assert FakeProvider().classify("보안 패치 배포", CATS).category_key == "routine"


def test_fake_default_when_no_keyword() -> None:
    """키워드 없으면 operation 으로 폴백."""
    assert FakeProvider().classify("회의 진행", CATS).category_key == "operation"


def test_fake_title_truncated() -> None:
    long = "가" * 100
    assert FakeProvider().classify(long, CATS).title.endswith("…")


def test_default_provider_without_key_is_fake(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("RELAY_CONFIG", str(tmp_path / "none.ini"))  # 설정 파일 없음
    assert isinstance(default_provider(), FakeProvider)


def test_default_provider_with_key_is_claude(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("RELAY_CONFIG", str(tmp_path / "none.ini"))
    assert isinstance(default_provider(), ClaudeProvider)


def test_classify_capture_validates_against_template(template) -> None:
    r = classify_capture(FakeProvider(), template, "첨부 다운로드 지연")
    assert r.category_key in {c.key for c in template.categories}


def test_classify_capture_rejects_unknown_key(template) -> None:
    class BadProvider(FakeProvider):
        def classify(self, text, categories):
            return Classification(category_key="없는키", title="x")

    with pytest.raises(ValueError, match="템플릿에 없습니다"):
        classify_capture(BadProvider(), template, "메모")


def test_classify_capture_rejects_empty_text(template) -> None:
    with pytest.raises(ValueError, match="빈 입력"):
        classify_capture(FakeProvider(), template, "   ")


# --- ClaudeProvider: 망 없이 스텁 클라이언트로 tool 구성·파싱 검증 ---

class _Block:
    type = "tool_use"

    def __init__(self, inp):
        self.input = inp


class _Msg:
    def __init__(self, blocks):
        self.content = blocks


class _Messages:
    def __init__(self, payload):
        self._payload = payload
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _Msg([_Block(self._payload)])


class _Client:
    def __init__(self, payload):
        self.messages = _Messages(payload)


def test_claude_parses_tool_use_and_constrains_keys() -> None:
    client = _Client({"category_key": "incident", "title": "첨부 다운로드 지연"})
    provider = ClaudeProvider(client=client)
    result = provider.classify("첨부 느림", CATS)
    assert result.category_key == "incident"
    # category_key 가 템플릿 key enum 으로 제약됐는지(지어내기 방지)
    kwargs = client.messages.last_kwargs
    tool = kwargs["tools"][0]
    assert tool["input_schema"]["properties"]["category_key"]["enum"] == [c.key for c in CATS]
    assert kwargs["tool_choice"] == {"type": "tool", "name": "classify_task"}


def test_claude_raises_when_no_tool_use() -> None:
    class _NoToolMessages(_Messages):
        def create(self, **kwargs):
            return _Msg([])

    client = _Client({})
    client.messages = _NoToolMessages({})
    with pytest.raises(RuntimeError, match="tool_use"):
        ClaudeProvider(client=client).classify("x", CATS)
