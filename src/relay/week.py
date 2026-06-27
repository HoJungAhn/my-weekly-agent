"""업무주(week) 변환 유틸 — 설계 결정 #3.

한 주는 **월요일 시작, 금요일 마감의 업무주**다. 주말(토·일)에 발생한 작업은
직전 업무주(그 주 월~금)에 귀속한다.

ISO 8601 주(월~일)는 토·일을 '직전 월요일'과 같은 주로 묶으므로, ISO 주번호가
그대로 위 '직전 업무주' 정의와 일치한다 — 주말을 위한 별도 보정이 필요 없다.
단, 연말 경계에서는 ISO '주 연도'가 달력 연도와 다를 수 있으므로(예: 2027-01-01 → 2026-W53)
키 생성 시 반드시 ``isocalendar().year`` 를 쓴다(``date.year`` 아님).

식별 키는 ``YYYY-Www``(예: ``2026-W26``). 모든 이월·집계 조회가 이 유틸에 의존하므로
날짜→week 변환은 반드시 여기서만 수행한다(설계 #3: "한 곳에 두고 재사용").
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

#: 활성 컨텍스트의 '현재 주'는 KST 기준으로 판단한다.
KST = ZoneInfo("Asia/Seoul")

_WEEK_KEY_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def week_key(d: date) -> str:
    """날짜를 업무주 키(``YYYY-Www``)로 변환한다.

    토·일은 ISO 주 묶음에 의해 직전 월~금과 같은 키가 된다(직전 업무주 귀속).
    """
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def parse_week_key(key: str) -> tuple[int, int]:
    """``YYYY-Www`` 키를 ``(iso_year, iso_week)`` 로 파싱한다. 형식이 틀리면 ValueError."""
    m = _WEEK_KEY_RE.match(key)
    if not m:
        raise ValueError(f"잘못된 주차 키 형식: {key!r} (기대: 'YYYY-Www', 예: '2026-W26')")
    return int(m.group(1)), int(m.group(2))


def week_monday(key: str) -> date:
    """업무주의 시작일(월요일)을 반환한다. 존재하지 않는 주차면 ValueError."""
    year, week = parse_week_key(key)
    try:
        return date.fromisocalendar(year, week, 1)  # 1 = 월요일
    except ValueError as e:
        raise ValueError(f"존재하지 않는 주차: {key!r} ({e})") from e


def week_bounds(key: str) -> tuple[date, date]:
    """업무주의 ``(월요일, 금요일)`` 경계 날짜를 반환한다."""
    monday = week_monday(key)
    return monday, monday + timedelta(days=4)


def shift_week(key: str, n: int) -> str:
    """``n`` 주만큼 이동한 업무주 키. ``-1`` = 직전 주, ``+1`` = 다음 주."""
    return week_key(week_monday(key) + timedelta(days=7 * n))


def current_week_key(today: date | None = None) -> str:
    """현재(KST) 업무주 키. ``today`` 를 주면 그 날짜 기준(테스트용 주입)."""
    if today is None:
        today = datetime.now(KST).date()
    return week_key(today)


def format_range(key: str, fmt: str = "{start} ~ {end}") -> str:
    """업무주 기간 표시 문자열(예: ``2026.06.22 ~ 06.26``).

    ``fmt`` 는 템플릿의 ``meta.week_label_format`` 을 그대로 받는다(설계 #9).
    """
    start, end = week_bounds(key)
    return fmt.format(start=start.strftime("%Y.%m.%d"), end=end.strftime("%m.%d"))
