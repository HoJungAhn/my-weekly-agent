"""업무주 변환 유틸 테스트 (T2 / 설계 #3).

핵심 검증: ① 월~금 같은 키, ② 주말은 직전 업무주에 귀속, ③ 연말 ISO 경계,
④ 경계일·주차 이동·왕복(round-trip), ⑤ 잘못된 키 에러.
"""

from datetime import date

import pytest

from relay.week import (
    current_week_key,
    format_range,
    parse_week_key,
    shift_week,
    week_bounds,
    week_key,
    week_monday,
)


@pytest.mark.parametrize(
    "d",
    [
        date(2026, 6, 22),  # 월
        date(2026, 6, 23),  # 화
        date(2026, 6, 24),  # 수
        date(2026, 6, 25),  # 목
        date(2026, 6, 26),  # 금
    ],
)
def test_weekdays_same_key(d: date) -> None:
    """같은 업무주의 월~금은 모두 같은 키."""
    assert week_key(d) == "2026-W26"


@pytest.mark.parametrize("d", [date(2026, 6, 27), date(2026, 6, 28)])  # 토, 일
def test_weekend_belongs_to_preceding_workweek(d: date) -> None:
    """주말(토·일)은 직전 업무주(같은 ISO 주)에 귀속한다 — 설계 #3 핵심 규칙."""
    assert week_key(d) == "2026-W26"


def test_next_monday_rolls_over() -> None:
    """다음 주 월요일은 새 주차."""
    assert week_key(date(2026, 6, 29)) == "2026-W27"


def test_year_boundary_uses_iso_year() -> None:
    """연말 경계: ISO 주 연도가 달력 연도와 다를 수 있다(date.year 쓰면 버그)."""
    assert week_key(date(2027, 1, 1)) == "2026-W53"  # 금요일, ISO로는 2026년 53주
    assert week_key(date(2021, 1, 1)) == "2020-W53"
    assert week_key(date(2026, 12, 31)) == "2026-W53"


def test_week_bounds() -> None:
    assert week_bounds("2026-W26") == (date(2026, 6, 22), date(2026, 6, 26))


def test_week_monday() -> None:
    assert week_monday("2026-W26") == date(2026, 6, 22)


def test_shift_week() -> None:
    assert shift_week("2026-W26", -1) == "2026-W25"
    assert shift_week("2026-W26", 1) == "2026-W27"
    assert shift_week("2026-W26", 0) == "2026-W26"


def test_shift_week_crosses_year_boundary() -> None:
    """주차 이동이 연도 경계를 넘어도 ISO 키가 정확해야 한다."""
    assert shift_week("2026-W53", 1) == "2027-W01"
    assert shift_week("2027-W01", -1) == "2026-W53"


def test_round_trip() -> None:
    """week_key → week_monday → week_key 왕복이 보존돼야 한다(임의 날짜들)."""
    for d in [date(2026, 1, 5), date(2026, 6, 27), date(2027, 1, 1), date(2020, 2, 29)]:
        key = week_key(d)
        assert week_key(week_monday(key)) == key


def test_current_week_key_with_injected_today() -> None:
    """today 주입 시 그 날짜 기준(주말이면 직전 업무주)."""
    assert current_week_key(date(2026, 6, 27)) == "2026-W26"  # 토요일


def test_parse_week_key_ok() -> None:
    assert parse_week_key("2026-W26") == (2026, 26)


@pytest.mark.parametrize("bad", ["2026-26", "2026W26", "26-W26", "2026-W2", "", "abc"])
def test_parse_week_key_rejects_bad_format(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_week_key(bad)


def test_week_monday_rejects_nonexistent_week() -> None:
    with pytest.raises(ValueError):
        week_monday("2026-W54")  # 2026년은 53주까지만 존재


def test_format_range_default() -> None:
    assert format_range("2026-W26") == "2026.06.22 ~ 06.26"
