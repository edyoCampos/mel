"""The scenarios file (tests/scenarios.yaml) is well formed."""

from tests.conftest import ROOT
from tests.test_scenario_runner import runner

TOOLS = {
    "search_products",
    "get_product_details",
    "get_order_status",
    "get_store_policy",
    "escalate_to_human",
}

ITEMS = runner.load_scenarios(ROOT / "tests" / "scenarios.yaml")


def test_twenty_scenarios_with_unique_ids() -> None:
    assert len(ITEMS) == 20
    assert len({s.id for s in ITEMS}) == 20


def test_scenarios_from_the_second_review() -> None:
    by_id = {s.id: s for s in ITEMS}
    kala = by_id["kala-all-candidates"]
    assert kala.must_include_at_least is not None and kala.must_include_at_least[0] == 5
    assert "três" in kala.must_not_include
    assert {"Yamaha C40", "Yamaha FG800"} <= set(by_id["multiword-search"].must_include)
    privacy = by_id["privacy-request-asks-contact"]
    assert {"registrad", "EM-"} <= set(privacy.first_reply_must_not_include)
    assert "não tem garantia legal" in by_id["defect-legal-warranty"].must_not_include


def test_scenarios_from_the_independent_review() -> None:
    by_id = {s.id: s for s in ITEMS}
    assert "Kalani" in by_id["kala-not-kalani"].must_not_include
    assert "garantia legal" in by_id["defect-legal-warranty"].must_include


def test_v3_scenarios_are_tagged() -> None:
    assert {s.id for s in ITEMS if s.tag == "v3"} == {
        "off-topic-question",
        "complaint-out-of-hours",
    }


def test_every_clock_is_in_store_timezone() -> None:
    assert all(s.now.utcoffset().total_seconds() == -4 * 3600 for s in ITEMS)


def test_five_exports_in_examples() -> None:
    exports = [s.export_as for s in ITEMS if s.export_as]
    assert len(exports) == 5
    assert all(e.startswith("examples/") and e.endswith(".md") for e in exports)


def test_expected_tools_exist() -> None:
    for item in ITEMS:
        assert set(item.expected_tools) <= TOOLS, item.id


def test_regret_scenario_forbids_label_and_early_eligibility() -> None:
    item = next(s for s in ITEMS if s.id == "regret-within-window")
    assert "etiqueta" in item.must_not_include
    assert "pode devolver, sim" in item.first_reply_must_not_include


def test_acoustic_guitars_scenario_lists_all_twelve() -> None:
    item = next(s for s in ITEMS if s.id == "acoustic-guitars-up-to-1000")
    for name in ["Rozini RC-104", "Giannini GN-15", "Yamaha F310", "Tagima Woodstock"]:
        assert name in item.must_include
