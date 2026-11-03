"""System prompt template contract (D-18, R-14)."""

from string import Formatter

from emporio.agent import load_system_prompt_template

TEMPLATE = load_system_prompt_template()


def test_template_has_exactly_the_known_placeholders() -> None:
    fields = {name for _, name, _, _ in Formatter().parse(TEMPLATE) if name is not None}
    assert fields == {"now_local", "weekday", "store_status"}


def test_template_renders_without_error() -> None:
    rendered = TEMPLATE.format(
        now_local="01/11/2026 10:00", weekday="Sunday", store_status="Store is CLOSED."
    )
    assert "Sunday, 01/11/2026 10:00" in rendered
    assert "Store is CLOSED." in rendered


def test_template_has_all_blocks() -> None:
    for heading in [
        "# 1. Identity and tone",
        "# 2. Inviolable rules",
        "# 3. When to use each tool",
        "# 4. Conversation flow and special situations",
        "# 5. Answer format",
        "# 6. Current context",
    ]:
        assert heading in TEMPLATE


def test_template_names_all_five_tools() -> None:
    tools_block = TEMPLATE.split("# 3. When to use each tool")[1].split("# 4.")[0]
    for tool in [
        "search_products",
        "get_product_details",
        "get_order_status",
        "get_store_policy",
        "escalate_to_human",
    ]:
        assert tool in tools_block


def test_template_forbids_computing_prices() -> None:
    assert "Never calculate, round or estimate amounts yourself" in TEMPLATE


def test_template_shows_pix_as_label_and_value() -> None:
    """pix_display is only the amount; the prompt adds the "PIX:" label once."""
    assert "PIX: <pix_display>" in TEMPLATE


def test_template_presents_alternatives_without_asking_first() -> None:
    """The model asked before showing the alternatives the tool returned."""
    assert (
        "When the product is unavailable, present the alternatives the tool returned, "
        "without asking first." in " ".join(TEMPLATE.split())
    )


def test_template_asks_contact_before_escalating_without_a_verified_order() -> None:
    flat = " ".join(TEMPLATE.split())
    assert "Without a verified order, ask for the customer's name and e-mail or phone" in flat
    assert "never say that anything was registered" in flat
