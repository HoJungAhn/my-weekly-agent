"""task 서비스 단위 테스트 (T5 / 설계 #7·#10).

카테고리 해석(키/이름/부분일치/모호/없음), 활성 시스템 결정, 생성·번호 매김.
"""

from itertools import count

import pytest

from relay.config import DEFAULT_SYSTEM
from relay.db import Store, connect, init_db
from relay.models import Status
from relay.services.tasks import (
    create_task,
    list_tasks_numbered,
    resolve_by_number,
    resolve_category,
    resolve_system,
)
from relay.template import default_template_path, load_template
from relay.template.models import Category, Template


@pytest.fixture
def template() -> Template:
    return load_template(default_template_path())


@pytest.fixture
def store() -> Store:
    conn = connect(":memory:")
    init_db(conn)
    ids = count(1)
    return Store(conn, id_factory=lambda: f"t{next(ids)}")


def test_resolve_category_by_key(template: Template) -> None:
    assert resolve_category(template, "incident") == ("incident", "장애·이슈 대응")


def test_resolve_category_by_exact_label(template: Template) -> None:
    assert resolve_category(template, "장애·이슈 대응")[0] == "incident"


def test_resolve_category_by_partial_label(template: Template) -> None:
    assert resolve_category(template, "장애")[0] == "incident"


def test_resolve_category_unknown_raises(template: Template) -> None:
    with pytest.raises(ValueError, match="찾을 수 없"):
        resolve_category(template, "없는카테고리")


def test_resolve_category_ambiguous_raises() -> None:
    tpl = Template(
        version=1,
        categories=[
            Category(key="a", role="operation", label="공통 작업", order=1),
            Category(key="b", role="next_week_plan", label="긴급 작업", order=2),
        ],
    )
    with pytest.raises(ValueError, match="모호"):
        resolve_category(tpl, "작업")


def test_resolve_system_priority(store: Store) -> None:
    assert resolve_system(store, None) == DEFAULT_SYSTEM  # 비었을 때 기본값
    assert resolve_system(store, "그룹웨어") == "그룹웨어"  # 명시값 우선
    create_task(store, "incident", title="x", week="2026-W26", system="포털")
    assert resolve_system(store, None) == "포털"  # 마지막 사용 시스템


def test_create_task_defaults_in_progress(store: Store) -> None:
    task, number = create_task(store, "incident", title="장애A", week="2026-W26", system="그룹웨어")
    assert task.status is Status.IN_PROGRESS
    assert task.category_key == "incident"
    assert number == 1


def test_numbering_increments_per_week_system(store: Store) -> None:
    create_task(store, "incident", title="A", week="2026-W26", system="그룹웨어")
    _, n2 = create_task(store, "incident", title="B", week="2026-W26", system="그룹웨어")
    assert n2 == 2
    rows = list_tasks_numbered(store, "2026-W26", "그룹웨어")
    assert [(n, t.title) for n, t in rows] == [(1, "A"), (2, "B")]


def test_numbering_isolated_by_system(store: Store) -> None:
    create_task(store, "incident", title="A", week="2026-W26", system="그룹웨어")
    _, n = create_task(store, "incident", title="B", week="2026-W26", system="포털")
    assert n == 1  # 다른 시스템은 번호가 다시 1부터


def test_resolve_by_number_found(store: Store) -> None:
    create_task(store, "incident", title="A", week="2026-W26", system="그룹웨어")
    create_task(store, "incident", title="B", week="2026-W26", system="그룹웨어")
    assert resolve_by_number(store, "2026-W26", "그룹웨어", 2).title == "B"


def test_resolve_by_number_out_of_range(store: Store) -> None:
    create_task(store, "incident", title="A", week="2026-W26", system="그룹웨어")
    with pytest.raises(ValueError, match="번호 5 에 해당하는 task 가 없습니다"):
        resolve_by_number(store, "2026-W26", "그룹웨어", 5)
