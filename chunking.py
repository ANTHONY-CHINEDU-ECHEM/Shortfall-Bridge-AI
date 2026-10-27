"""Section aware chunking of label text.

Design choices
* Chunks never cross section boundaries, so every citation maps to exactly one label section
  and the section type can be used as a retrieval prior and a safety gate input.
* Splits prefer numbered subsection headings ("5.3 Renal Impairment"), then sentences, so a
  renal dosing table does not get separated from its heading.
* Token counts are approximated (1.3 tokens per word) to avoid a tokenizer dependency at
  ingestion time. The budget is deliberately small (about 220 tokens) because label sentences
  are dense and clinicians read the cited passage directly in the evidence drawer.
"""

from __future__ import annotations

import re

from ..domain.enums import SectionType
from ..domain.models import Chunk, LabelDocument

_SUBHEADING = re.compile(r"(?=(?:^|\n|\s)(?:\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)\s+[A-Z][A-Za-z])")
_SENTENCE = re.compile(r"(?<=[.;:!?])\s+(?=[A-Z0-9•(\[])|\n+")
_WS = re.compile(r"[ \t]+")


def approx_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3) + 1


def _units(text: str) -> list[str]:
    units: list[str] = []
    for block in _SUBHEADING.split(text):
        block = block.strip()
        if not block:
            continue
        for sent in _SENTENCE.split(block):
            s = _WS.sub(" ", sent).strip()
            if s:
                units.append(s)
    return units


def _hard_wrap(unit: str, budget: int) -> list[str]:
    words = unit.split()
    step = max(1, int(budget / 1.3))
    return [" ".join(words[i : i + step]) for i in range(0, len(words), step)]


def chunk_section(text: str, target_tokens: int = 220, overlap_tokens: int = 40) -> list[str]:
    units: list[str] = []
    for u in _units(text):
        units.extend(_hard_wrap(u, target_tokens) if approx_tokens(u) > target_tokens else [u])
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for u in units:
        t = approx_tokens(u)
        if current and size + t > target_tokens:
            chunks.append(" ".join(current))
            # carry trailing units as overlap so a threshold sentence keeps its lead in
            carry: list[str] = []
            carry_size = 0
            for prev in reversed(current):
                pt = approx_tokens(prev)
                if carry_size + pt > overlap_tokens:
                    break
                carry.insert(0, prev)
                carry_size += pt
            current, size = carry, carry_size
        current.append(u)
        size += t
    if current:
        tail = " ".join(current)
        if not chunks or tail not in chunks[-1]:
            chunks.append(tail)
    return chunks


def chunk_label(
    doc: LabelDocument, sections: dict[SectionType, str], target_tokens: int = 220, overlap_tokens: int = 40
) -> list[Chunk]:
    out: list[Chunk] = []
    for section, text in sections.items():
        for i, piece in enumerate(chunk_section(text, target_tokens, overlap_tokens)):
            out.append(
                Chunk(
                    chunk_id=Chunk.make_id(doc.set_id, section.value, i),
                    set_id=doc.set_id,
                    section=section,
                    ordinal=i,
                    text=piece,
                    ingredients=doc.ingredients,
                    title=doc.title,
                    effective_time=doc.effective_time,
                )
            )
    return out
