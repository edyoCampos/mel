"""Store policy manual, retrieved by topic (D-02, D-08, D-21).

The manual is small (10 sections), so a topic maps to fixed sections: no vector store,
no retrieval errors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

MANUAL_VERSION = "2.1 (June 2025)"


class PolicyTopic(StrEnum):
    HOURS = "hours"
    PAYMENT = "payment"
    RETURNS = "returns"
    SHIPPING = "shipping"
    PROMOTIONS = "promotions"
    WARRANTY = "warranty"
    PRIVACY = "privacy"
    CONTACT = "contact"
    SCOPE = "scope"


TOPIC_SECTIONS: dict[PolicyTopic, tuple[str, ...]] = {
    PolicyTopic.HOURS: ("2",),
    PolicyTopic.PAYMENT: ("3",),
    PolicyTopic.RETURNS: ("4",),
    PolicyTopic.SHIPPING: ("5",),
    PolicyTopic.PROMOTIONS: ("6",),
    PolicyTopic.WARRANTY: ("8",),
    PolicyTopic.PRIVACY: ("9",),
    PolicyTopic.CONTACT: ("1.2",),
    PolicyTopic.SCOPE: ("1", "7", "10"),
}

_SECTION = re.compile(r"^## (\d{1,2})\. ", re.MULTILINE)
_SUBSECTION = re.compile(r"^### (\d{1,2}\.\d) ", re.MULTILINE)


@dataclass(frozen=True)
class PolicySection:
    topic: PolicyTopic
    sections: tuple[str, ...]
    text: str  # Markdown, in Portuguese as in the manual


class PolicyLoadError(Exception):
    pass


def _split(text: str, pattern: re.Pattern[str]) -> dict[str, str]:
    matches = list(pattern.finditer(text))
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks[match.group(1)] = text[match.start() : end].strip()
    return blocks


class PolicyBook:
    EXPECTED_SECTION_COUNT = 10

    def __init__(self, sections: dict[str, str]) -> None:
        self._sections = sections
        self._subsections: dict[str, str] = {}
        for block in sections.values():
            self._subsections.update(_split(block, _SUBSECTION))

    @classmethod
    def load(cls, path: Path) -> PolicyBook:
        if not path.is_file():
            raise PolicyLoadError(f"Policy file not found: {path}")
        sections = _split(path.read_text(encoding="utf-8"), _SECTION)
        if len(sections) != cls.EXPECTED_SECTION_COUNT:
            raise PolicyLoadError(
                f"Expected {cls.EXPECTED_SECTION_COUNT} sections in {path.name}, "
                f"found {len(sections)}"
            )
        return cls(sections)

    def section_numbers(self) -> list[str]:
        return list(self._sections)

    def get(self, topic: PolicyTopic) -> PolicySection:
        numbers = TOPIC_SECTIONS[topic]
        blocks = [self._subsections[n] if "." in n else self._sections[n] for n in numbers]
        return PolicySection(topic=topic, sections=numbers, text="\n\n".join(blocks))
