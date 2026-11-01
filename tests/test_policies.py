"""PolicyBook (D-02, D-08, A-01, A-02)."""

from pathlib import Path

import pytest

from emporio.policies import PolicyBook, PolicyLoadError, PolicyTopic

POLICIES = Path(__file__).resolve().parents[1] / "data" / "policies.md"


@pytest.fixture(scope="module")
def book() -> PolicyBook:
    return PolicyBook.load(POLICIES)


def test_load_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PolicyLoadError):
        PolicyBook.load(tmp_path / "missing.md")


def test_load_rejects_wrong_section_count(tmp_path: Path) -> None:
    text = "\n".join(f"## {n}. Section {n}\n\ntext" for n in range(1, 10))
    path = tmp_path / "nine.md"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PolicyLoadError, match="10"):
        PolicyBook.load(path)


def test_policies_md_has_ten_sections(book: PolicyBook) -> None:
    assert book.section_numbers() == [str(n) for n in range(1, 11)]


@pytest.mark.parametrize("topic", list(PolicyTopic))
def test_every_topic_returns_non_empty_text(book: PolicyBook, topic: PolicyTopic) -> None:
    section = book.get(topic)
    assert section.topic is topic
    assert section.sections
    assert len(section.text) > 100


def test_contact_has_address(book: PolicyBook) -> None:
    text = book.get(PolicyTopic.CONTACT).text
    assert "Rua 14 de Maio, 3200" in text
    assert "79202-333" in text


def test_contact_uses_single_phone_and_email_without_accent(book: PolicyBook) -> None:
    text = book.get(PolicyTopic.CONTACT).text
    assert "(67) 3341-4444" in text
    assert "contato@emporiodamusica.com.br" in text
    assert "empóriodamusica" not in text


def test_payment_has_combination_threshold(book: PolicyBook) -> None:
    assert "R$ 2.000,00" in book.get(PolicyTopic.PAYMENT).text


def test_shipping_mentions_special_quote(book: PolicyBook) -> None:
    assert "cotação individual" in book.get(PolicyTopic.SHIPPING).text


def test_returns_has_regret_and_non_eligible_items(book: PolicyBook) -> None:
    text = book.get(PolicyTopic.RETURNS).text
    assert "7 (sete) dias" in text
    assert "Boquilhas" in text


def test_warranty_has_exclusions(book: PolicyBook) -> None:
    assert "Desgaste natural" in book.get(PolicyTopic.WARRANTY).text


def test_promotions_has_no_rain_check(book: PolicyBook) -> None:
    assert "rain check" in book.get(PolicyTopic.PROMOTIONS).text


def test_scope_has_accessories_rule(book: PolicyBook) -> None:
    text = book.get(PolicyTopic.SCOPE).text
    assert "exclusivamente com instrumentos musicais" in text
    assert "não comercializamos acessórios" in text


def test_contact_section_does_not_include_section_2(book: PolicyBook) -> None:
    assert "Horário de Funcionamento" not in book.get(PolicyTopic.CONTACT).text
