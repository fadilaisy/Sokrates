"""
rag/parse.py
────────────
Stage 2 of ingestion: parse + OCR.

* PDF text layer first (PyMuPDF). Headings are detected from font size, weight
  and numbering ("3.2 Penggantian Bearing", "BAB II"). Tables are detected with
  PyMuPDF's table finder and kept as their own blocks.
* Pages with no usable text layer are rendered and OCR'd with Tesseract
  (Indonesian + English, whatever subset is installed), grouped into
  paragraphs with bounding boxes and per-page confidence.
* Images are wrapped into a one-page PDF so they share the OCR path and the
  cockpit can render a highlight on them the same way.
* Plain text / Markdown is supported for quick demo uploads.

All bounding boxes are in PDF points (72 dpi), top-left origin, matching
PyMuPDF so the cockpit page renderer can highlight them directly.
"""

from __future__ import annotations

import io
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import config
from .text import normalize

try:  # PyMuPDF ≥ 1.24 exposes `pymupdf`; older builds only `fitz`
    import pymupdf as fitz  # type: ignore
except Exception:  # pragma: no cover
    import fitz  # type: ignore


BBox = Tuple[float, float, float, float]


@dataclass
class Block:
    page: int                     # 1-based
    kind: str                     # "text" | "heading" | "table"
    text: str
    bbox: Optional[BBox] = None
    level: int = 0                # heading level (1 = top)
    source: str = "text"          # "text" (PDF layer) | "ocr"
    confidence: float = 1.0       # OCR confidence 0..1 (1.0 for text layer)


@dataclass
class PageInfo:
    page: int
    width: float
    height: float
    source: str                   # "text" | "ocr" | "empty"
    ocr_confidence: Optional[float] = None
    low_confidence: bool = False


@dataclass
class ParseResult:
    blocks: List[Block] = field(default_factory=list)
    pages: List[PageInfo] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    pdf_bytes: Optional[bytes] = None   # set when the original was converted (image → PDF)

    @property
    def full_text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
TEXT_EXT = {".txt", ".md", ".markdown"}
SUPPORTED_EXT = PDF_EXT | IMAGE_EXT | TEXT_EXT

_NUMBERED_HEADING = re.compile(r"^\s*(\d+(?:\.\d+){0,3})[.)]?\s+[A-Za-zÀ-ɏ]")
_CHAPTER_HEADING = re.compile(r"^\s*(BAB|Bab|SECTION|Section|BAGIAN|Bagian|CHAPTER|Chapter|LAMPIRAN|Appendix)\s+[\dIVXLC]+\b")


# ─────────────────────────────────────────────────────────────────────────────
# OCR
# ─────────────────────────────────────────────────────────────────────────────

def _ocr_langs() -> Optional[str]:
    try:
        import pytesseract
        avail = set(pytesseract.get_languages(config=""))
    except Exception:
        return None
    wanted = [l for l in config.OCR_LANGS.split("+") if l]
    have = [l for l in wanted if l in avail]
    if not have:
        have = ["eng"] if "eng" in avail else sorted(a for a in avail if a != "osd")[:1]
    return "+".join(have) if have else None


def ocr_available() -> Tuple[bool, Optional[str]]:
    langs = _ocr_langs()
    return (langs is not None), langs


def _ocr_page(page, dpi: int, langs: str) -> Tuple[List[Block], Optional[float]]:
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(dpi=dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    data = pytesseract.image_to_data(img, lang=langs, output_type=pytesseract.Output.DICT)
    scale = 72.0 / dpi

    groups: dict = {}
    order: List[tuple] = []
    for i, word in enumerate(data["text"]):
        w = (word or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if not w or conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i])
        if key not in groups:
            groups[key] = {"words": [], "confs": [], "lines": {}, "box": [1e9, 1e9, 0.0, 0.0]}
            order.append(key)
        g = groups[key]
        g["words"].append(w)
        g["confs"].append(conf)
        g["lines"].setdefault(data["line_num"][i], []).append(w)
        x, y, wd, ht = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        b = g["box"]
        b[0], b[1] = min(b[0], x), min(b[1], y)
        b[2], b[3] = max(b[2], x + wd), max(b[3], y + ht)

    blocks: List[Block] = []
    all_conf, weights = [], []
    for key in order:
        g = groups[key]
        line_texts = [" ".join(ws) for _, ws in sorted(g["lines"].items())]
        # A short ALL-CAPS first line is a title/heading: keep it on its own line.
        first = line_texts[0] if line_texts else ""
        if len(line_texts) > 1 and first.isupper() and len(first.split()) <= 10:
            text = first + "\n" + " ".join(line_texts[1:])
        else:
            text = " ".join(line_texts)
        conf = sum(g["confs"]) / len(g["confs"]) / 100.0
        b = g["box"]
        blocks.append(Block(
            page=page.number + 1, kind="text", text=normalize(text),
            bbox=(b[0] * scale, b[1] * scale, b[2] * scale, b[3] * scale),
            source="ocr", confidence=round(conf, 3),
        ))
        all_conf.append(conf)
        weights.append(len(text))
    page_conf = (sum(c * w for c, w in zip(all_conf, weights)) / sum(weights)) if weights else None
    return blocks, (round(page_conf, 3) if page_conf is not None else None)


# ─────────────────────────────────────────────────────────────────────────────
# PDF text layer
# ─────────────────────────────────────────────────────────────────────────────

def _inside(inner: BBox, outer: BBox, tol: float = 2.0) -> bool:
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def _table_blocks(page) -> List[Block]:
    out: List[Block] = []
    try:
        tabs = page.find_tables()
    except Exception:
        return out
    for t in getattr(tabs, "tables", []) or []:
        try:
            rows = t.extract()
        except Exception:
            continue
        rows = [[normalize(str(c or "")).replace("\n", " ").strip() for c in r] for r in rows if r]
        rows = [r for r in rows if any(r)]
        if len(rows) < 2 or max(len(r) for r in rows) < 2:
            continue
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        out.append(Block(page=page.number + 1, kind="table", text="\n".join(lines),
                         bbox=tuple(t.bbox), source="text", confidence=1.0))
    return out


def _text_blocks(page) -> List[dict]:
    """Raw text blocks with font statistics (size, bold) for heading detection."""
    raw = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        lines, sizes, bold_chars, chars = [], [], 0, 0
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            txt = "".join(s.get("text", "") for s in spans)
            if txt.strip():
                lines.append(txt)
            for s in spans:
                n = len(s.get("text", "").strip())
                if not n:
                    continue
                sizes.append((s.get("size", 0.0), n))
                chars += n
                if "bold" in s.get("font", "").lower() or (s.get("flags", 0) & 16):
                    bold_chars += n
        # Lines inside one PDF block are wrapped prose: join with spaces, not newlines.
        text = normalize(" ".join(l.strip() for l in lines)).strip()
        if not text:
            continue
        size = max((s for s, _ in sizes), default=0.0)
        raw.append({
            "page": page.number + 1, "text": text, "bbox": tuple(b["bbox"]),
            "size": size, "bold": chars > 0 and bold_chars / chars > 0.6,
            "n_lines": len(lines), "sizes": sizes,
        })
    return raw


def _is_heading(rb: dict, body_size: float) -> bool:
    t = rb["text"]
    n_words = len(t.split())
    if n_words == 0 or n_words > 14 or rb["n_lines"] > 2:
        return False
    if t.rstrip().endswith((".", ",", ";")) and not _NUMBERED_HEADING.match(t):
        return False
    if _CHAPTER_HEADING.match(t):
        return True
    if body_size and rb["size"] >= body_size * 1.15:
        return True
    if rb["bold"] and (n_words <= 10):
        return True
    if _NUMBERED_HEADING.match(t) and n_words <= 10 and t[:1].isdigit() and not t.rstrip().endswith("."):
        return rb["bold"] or rb["size"] >= body_size * 1.05
    return False


def _heading_levels(heads: List[dict]) -> None:
    """Assign levels: numbering depth wins, otherwise rank by font size."""
    sizes = sorted({round(h["size"], 1) for h in heads}, reverse=True)
    rank = {s: i + 1 for i, s in enumerate(sizes)}
    for h in heads:
        m = _NUMBERED_HEADING.match(h["text"])
        if _CHAPTER_HEADING.match(h["text"]):
            h["level"] = 1
        elif m:
            h["level"] = min(m.group(1).count(".") + 1, 4)
        else:
            h["level"] = min(rank.get(round(h["size"], 1), 3), 4)


def parse_pdf_bytes(data: bytes, force_ocr: bool = False) -> ParseResult:
    res = ParseResult()
    doc = fitz.open(stream=data, filetype="pdf")
    ocr_ok, langs = ocr_available()

    per_page_raw: List[Tuple[int, List[dict], List[Block]]] = []
    needs_ocr: List[int] = []
    for page in doc:
        raw = [] if force_ocr else _text_blocks(page)
        chars = sum(len(r["text"]) for r in raw)
        if chars < config.TEXT_LAYER_MIN_CHARS:
            needs_ocr.append(page.number)
            per_page_raw.append((page.number, [], []))
            continue
        tables = _table_blocks(page)
        if tables:
            raw = [r for r in raw if not any(_inside(r["bbox"], t.bbox) for t in tables)]
        per_page_raw.append((page.number, raw, tables))

    # Body font size = most common size by character count across the document.
    size_hist: dict = {}
    for _, raw, _ in per_page_raw:
        for r in raw:
            for s, n in r["sizes"]:
                size_hist[round(s, 1)] = size_hist.get(round(s, 1), 0) + n
    body_size = max(size_hist, key=lambda k: size_hist[k]) if size_hist else 0.0

    heads = [r for _, raw, _ in per_page_raw for r in raw if _is_heading(r, body_size)]
    _heading_levels(heads)
    head_ids = {id(h) for h in heads}

    for pno, raw, tables in per_page_raw:
        page = doc[pno]
        info = PageInfo(page=pno + 1, width=page.rect.width, height=page.rect.height, source="text")
        if pno in needs_ocr:
            if ocr_ok and langs:
                try:
                    blocks, conf = _ocr_page(page, config.OCR_DPI, langs)
                except Exception as exc:  # tesseract crash on one page must not kill the doc
                    blocks, conf = [], None
                    res.warnings.append(f"OCR gagal di halaman {pno + 1}: {exc}")
                info.source = "ocr" if blocks else "empty"
                info.ocr_confidence = conf
                info.low_confidence = conf is not None and conf < config.OCR_LOW_CONFIDENCE
                for b in blocks:
                    # OCR has no font info: only numbered / chapter-style short lines become headings.
                    short = len(b.text.split()) <= 10 and "\n" not in b.text.strip()
                    m = _NUMBERED_HEADING.match(b.text)
                    if short and (_CHAPTER_HEADING.match(b.text) or (m and not b.text.rstrip().endswith("."))):
                        b.kind, b.level = "heading", (1 if not m else min(m.group(1).count(".") + 1, 4))
                res.blocks.extend(blocks)
            else:
                info.source = "empty"
                res.warnings.append(f"Halaman {pno + 1} tidak punya text layer dan OCR tidak tersedia.")
            res.pages.append(info)
            continue

        items: List[Block] = []
        for r in raw:
            is_h = id(r) in head_ids
            items.append(Block(page=pno + 1, kind="heading" if is_h else "text", text=r["text"],
                               bbox=r["bbox"], level=r.get("level", 0) if is_h else 0))
        items.extend(tables)
        # Reading order: top-to-bottom, then left-to-right.
        items.sort(key=lambda b: (round((b.bbox or (0, 0, 0, 0))[1] / 3), (b.bbox or (0, 0, 0, 0))[0]))
        res.blocks.extend(items)
        res.pages.append(info)

    if langs and "ind" not in langs and any(p.source == "ocr" for p in res.pages):
        res.warnings.append("Model OCR Bahasa Indonesia (tesseract 'ind') belum terpasang; memakai: " + langs)
    doc.close()
    return res


def image_to_pdf_bytes(data: bytes) -> bytes:
    img_doc = fitz.open(stream=data, filetype=None)
    try:
        return img_doc.convert_to_pdf()
    finally:
        img_doc.close()


def parse_text(data: bytes) -> ParseResult:
    res = ParseResult()
    text = normalize(data.decode("utf-8", errors="replace"))
    para: List[str] = []

    def flush():
        if para:
            res.blocks.append(Block(page=1, kind="text", text="\n".join(para).strip()))
            para.clear()

    for line in text.splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            flush()
            res.blocks.append(Block(page=1, kind="heading", text=m.group(2).strip(), level=len(m.group(1))))
        elif line.strip().startswith("|"):
            if res.blocks and res.blocks[-1].kind == "table" and not para:
                res.blocks[-1].text += "\n" + line.strip()
            else:
                flush()
                res.blocks.append(Block(page=1, kind="table", text=line.strip()))
        elif not line.strip():
            flush()
        else:
            para.append(line)
    flush()
    res.pages.append(PageInfo(page=1, width=0, height=0, source="text"))
    return res


def parse_file(path: Path, force_ocr: bool = False) -> ParseResult:
    ext = path.suffix.lower()
    data = path.read_bytes()
    if ext in PDF_EXT:
        return parse_pdf_bytes(data, force_ocr=force_ocr)
    if ext in IMAGE_EXT:
        pdf = image_to_pdf_bytes(data)
        res = parse_pdf_bytes(pdf, force_ocr=True)
        res.pdf_bytes = pdf
        return res
    if ext in TEXT_EXT:
        return parse_text(data)
    raise ValueError(f"Format file tidak didukung: {ext}")
