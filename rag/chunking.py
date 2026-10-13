"""
rag/chunking.py
───────────────
Stage 3 of ingestion: structure-aware chunking.

* Chunks follow the section hierarchy and never cross a heading.
* Target size 300–800 tokens; long paragraphs are split on sentence boundaries.
* Tables are always their own chunk.
* Chunks stay on one page so `chunk_id = doc_id:page:ordinal` and the bounding
  box the cockpit highlights are exact.
* Every chunk points at its parent section, so retrieval can return the small
  chunk and the generator can expand to the full section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import config
from .parse import BBox, Block
from .text import est_tokens


@dataclass
class Section:
    index: int
    path: str
    level: int
    texts: List[str] = field(default_factory=list)
    page_start: Optional[int] = None
    page_end: Optional[int] = None

    @property
    def text(self) -> str:
        return "\n\n".join(t for t in self.texts if t.strip())


@dataclass
class Chunk:
    page: int
    ordinal: int
    section_index: int
    section_path: str
    kind: str                                   # "text" | "table"
    text: str
    bbox: Optional[BBox]
    ocr_confidence: Optional[float]             # None when from the text layer
    source: str = "text"


_SENT_SPLIT = re.compile(r"(?<=[.!?;:])\s+(?=[A-Z0-9À-ɏ•\-–(])")


def _union(a: Optional[BBox], b: Optional[BBox]) -> Optional[BBox]:
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _split_long(text: str, max_tokens: int) -> List[str]:
    if est_tokens(text) <= max_tokens:
        return [text]
    sents = _SENT_SPLIT.split(text)
    out, cur = [], ""
    for s in sents:
        cand = (cur + " " + s).strip() if cur else s
        if cur and est_tokens(cand) > max_tokens:
            out.append(cur)
            cur = s
        else:
            cur = cand
    if cur:
        out.append(cur)
    # A single monster "sentence" (e.g. OCR without punctuation): hard-split on words.
    final: List[str] = []
    for piece in out:
        if est_tokens(piece) <= max_tokens:
            final.append(piece)
            continue
        ws = piece.split()
        step = max(int(max_tokens / 1.3), 50)
        final.extend(" ".join(ws[i:i + step]) for i in range(0, len(ws), step))
    return final


def chunk_blocks(blocks: List[Block], title: str,
                 max_tokens: Optional[int] = None) -> Tuple[List[Section], List[Chunk]]:
    max_tokens = max_tokens or config.CHUNK_MAX_TOKENS
    sections: List[Section] = [Section(index=0, path="(Pendahuluan)", level=0)]
    stack: List[Tuple[int, str]] = []
    chunks: List[Chunk] = []
    ordinals: dict = {}

    buf: List[Block] = []

    def cur_section() -> Section:
        return sections[-1]

    def emit(text: str, page: int, kind: str, bbox, conf, source):
        o = ordinals.get(page, 0) + 1
        ordinals[page] = o
        sec = cur_section()
        chunks.append(Chunk(page=page, ordinal=o, section_index=sec.index, section_path=sec.path,
                            kind=kind, text=text.strip(), bbox=bbox,
                            ocr_confidence=conf, source=source))

    def flush():
        if not buf:
            return
        text = "\n".join(b.text for b in buf).strip()
        bbox = None
        for b in buf:
            bbox = _union(bbox, b.bbox)
        ocr = [b for b in buf if b.source == "ocr"]
        conf = None
        if ocr:
            w = sum(len(b.text) for b in ocr) or 1
            conf = round(sum(b.confidence * len(b.text) for b in ocr) / w, 3)
        src = "ocr" if ocr else "text"
        page = buf[0].page
        buf.clear()
        if text:
            emit(text, page, "text", bbox, conf, src)

    def note_page(sec: Section, page: int):
        sec.page_start = page if sec.page_start is None else min(sec.page_start, page)
        sec.page_end = page if sec.page_end is None else max(sec.page_end, page)

    for b in blocks:
        if b.kind == "heading":
            flush()
            lvl = max(b.level, 1)
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, b.text.replace("\n", " ").strip()))
            path = " > ".join(t for _, t in stack)
            sections.append(Section(index=len(sections), path=path, level=lvl, texts=[b.text.strip()]))
            note_page(cur_section(), b.page)
            continue

        sec = cur_section()
        sec.texts.append(b.text)
        note_page(sec, b.page)

        if b.kind == "table":
            flush()
            conf = b.confidence if b.source == "ocr" else None
            for piece in _split_long(b.text, max_tokens):
                emit(piece, b.page, "table", b.bbox, conf, b.source)
            continue

        if buf and buf[0].page != b.page:
            flush()
        if buf and est_tokens("\n".join(x.text for x in buf) + "\n" + b.text) > max_tokens:
            flush()
        if est_tokens(b.text) > max_tokens:
            flush()
            conf = b.confidence if b.source == "ocr" else None
            for piece in _split_long(b.text, max_tokens):
                emit(piece, b.page, "text", b.bbox, conf, b.source)
            continue
        buf.append(b)
    flush()

    # Keep only sections that own content (drop empty intro / heading-only parents).
    used = {c.section_index for c in chunks}
    sections = [s for s in sections if s.index in used]
    return sections, chunks
