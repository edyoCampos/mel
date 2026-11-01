"""CustomerVerifier and masking (R-13, A-16, NFR-03)."""

import pytest

from emporio.services import CustomerVerifier, VerificationInputError, mask_email, mask_phone
from tests.factories import make_customer

verifier = CustomerVerifier()
ANA = make_customer()


def test_verify_with_matching_email() -> None:
    assert verifier.verify(ANA, email="anacarol.ferreira@coldmail.com", phone=None)


def test_verify_email_is_case_insensitive_and_trimmed() -> None:
    assert verifier.verify(ANA, email=" AnaCarol.Ferreira@ColdMail.com ", phone=None)


def test_verify_with_phone_in_other_format() -> None:
    assert verifier.verify(ANA, email=None, phone="67996547821")


def test_verify_contact_that_does_not_match() -> None:
    assert not verifier.verify(ANA, email="ana@gmail.com", phone=None)
    assert not verifier.verify(ANA, email=None, phone="(67) 99999-0000")


def test_verify_without_contact_raises() -> None:
    with pytest.raises(VerificationInputError):
        verifier.verify(ANA, email=None, phone=None)


def test_shared_email_needs_order_pair() -> None:
    # Customers 11 and 43 share an e-mail; verification is always order owner + contact.
    bruno_11 = make_customer(
        id=11, name="Bruno Carvalho Martins", phone="(67) 98543-2109",
        email="bruno.martins@jmail.com", city="Três Lagoas",
    )  # fmt: skip
    assert verifier.verify(bruno_11, email="bruno.martins@jmail.com", phone=None)
    assert not verifier.verify(ANA, email="bruno.martins@jmail.com", phone=None)


def test_mask_email() -> None:
    assert mask_email("anacarol.ferreira@coldmail.com") == "a***@coldmail.com"


def test_mask_phone() -> None:
    assert mask_phone("(67) 99654-7821") == "(67) *****-7821"
