"""Convert the store policy manual (PDF) to a Markdown draft.

One-off helper (D-08). The PDF is not versioned (D-23): pass its local path.
The output is a draft; `data/policies.md` is this draft reviewed by hand.

Usage:
    uv run python scripts/extract_policies.py path/to/politicas_da_loja.pdf > draft.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from pypdf import PdfReader

PAGE_HEADER = "Empório da Música Manual de Políticas e Procedimentos"
SECTION = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")  # "4. Política de Trocas e Devoluções"
SUBSECTION = re.compile(r"^(\d{1,2}\.\d)\s+(\S.*)$")  # "4.1 Direito de Arrependimento"
PAGE_FOOTER = re.compile(r"^Página\s+\d+$")


def to_markdown(pdf_path: Path) -> str:
    reader = PdfReader(pdf_path)
    lines: list[str] = []
    for page in reader.pages[1:]:  # page 1 is the cover
        for raw in page.extract_text(extraction_mode="layout").splitlines():
            line = raw.strip()
            if not line or line == PAGE_HEADER or PAGE_FOOTER.match(line):
                continue
            if match := SUBSECTION.match(line):
                lines += ["", f"### {match.group(1)} {match.group(2)}", ""]
            elif match := SECTION.match(line):
                lines += ["", f"## {match.group(1)}. {match.group(2)}", ""]
            elif line.startswith("•"):
                lines.append(f"- {line.lstrip('•').strip()}")
            else:
                lines.append(re.sub(r"\s{3,}", " | ", line))
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    sys.stdout.write(to_markdown(Path(sys.argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
