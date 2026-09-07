"""
PDF parsing layer.

Turns a PDF into a Document of Pages. This is the one place in the pipeline
allowed to touch pixels: every fact created later must be traceable back
through here to a bounding box on a page, so the offset->word index is built
while the text is assembled, not by re-searching the page afterward (repeated
numbers and words make re-search ambiguous).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import warnings
from dataclasses import dataclass, field
from typing import Optional

import pdfplumber
from pdfplumber.utils import cluster_objects

# pdfminer logs a "Cannot set gray non-stroke color" warning per malformed
# content-stream operator in some of these PDFs; it is noise, not a failure —
# the page still parses. Quiet it so real errors are not lost in the flood.
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# --------------------------------------------------------------------------
# Scale-context sniffing — generic header phrase detection, not per-doc rules
# --------------------------------------------------------------------------

_SCALE_TOKEN = r"(?:lakhs?|lacs?|crores?|cr\.?|millions?|mn|mm|billions?|bn|thousands?|k)"
_CURRENCY_TOKEN = r"(?:rs\.?|inr|₹|us\$|usd|\$|eur|€|gbp|£)"
_SCALE_HEADER_RE = re.compile(
    rf"\b(?:{_CURRENCY_TOKEN}\.?\s*)?"
    rf"(?:in|figures?\s+in|amount\s+in|values?\s+in)\s+"
    rf"(?:{_CURRENCY_TOKEN}\s+)?({_SCALE_TOKEN})\b",
    re.I,
)


def detect_scale_context(text: str) -> str:
    """Pull a 'Rs. in lakhs' / '(₹ in Crore)' style phrase out of a header or
    caption. Returns the matched phrase verbatim, suitable as the `context`
    argument to normalize.parse_quantity(), or "" if none is present.

    This is locale-general phrase matching — no document- or table-specific
    rules — per the project's ban on filename/dataset branching.
    """
    if not text:
        return ""
    m = _SCALE_HEADER_RE.search(text)
    return m.group(0).strip() if m else ""


# --------------------------------------------------------------------------
# Word / bbox index
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Word:
    text: str
    char_start: int                                   # offset into Page.text
    char_end: int
    bbox: tuple[float, float, float, float]            # (x0, top, x1, bottom)


@dataclass
class Table:
    page_no: int
    bbox: tuple[float, float, float, float]
    rows: list[list[Optional[str]]]
    cell_bboxes: list[list[Optional[tuple[float, float, float, float]]]] = field(default_factory=list)
    caption: str = ""
    scale_context: str = ""

    def to_text_block(self) -> str:
        """Compact text block an LLM can read: caption, scale, then rows
        pipe-delimited. Page/bbox provenance lives on the Table object itself,
        not in this string, so nothing is lost by handing this to a prompt."""
        lines = []
        if self.caption:
            lines.append(self.caption.strip())
        if self.scale_context:
            lines.append(f"[{self.scale_context}]")
        for row in self.rows:
            lines.append(" | ".join((c or "").strip() for c in row))
        return "\n".join(lines)


@dataclass
class Page:
    page_no: int                                       # 1-indexed
    text: str
    words: list[Word] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    image_only: bool = False                            # no extractable text
    error: Optional[str] = None                          # set on degraded parse

    def bbox_for_span(self, start: int, end: int) -> Optional[tuple[float, float, float, float]]:
        """Union bbox of every word overlapping the half-open span [start, end)
        in `self.text`. Returns None if the span covers no known word (e.g. it
        falls entirely on whitespace/newlines inserted between words)."""
        boxes = [w.bbox for w in self.words if w.char_end > start and w.char_start < end]
        if not boxes:
            return None
        x0 = min(b[0] for b in boxes)
        top = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes)
        bottom = max(b[3] for b in boxes)
        return (x0, top, x1, bottom)


@dataclass
class Document:
    doc_id: str
    filename: str
    n_pages: int
    pages: list[Page] = field(default_factory=list)
    encrypted: bool = False
    error: Optional[str] = None                          # set if the file could not be opened at all
    table_strategy: str = "lines"                         # "lines" (lattice, default) | "text" (fallback)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

_LINE_TOLERANCE = 3.0   # points; words within this of each other's `top` are one line


def _doc_id(path: str) -> str:
    return hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:16]


def _looks_encrypted(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "password" in msg or "encrypt" in msg


def _build_text_and_words(raw_words: list[dict]) -> tuple[str, list[Word]]:
    """Assemble page text in reading order (top-to-bottom, left-to-right)
    while recording each word's exact char offset as it is appended — this is
    the offset index; nothing is ever re-searched for after the fact."""
    if not raw_words:
        return "", []

    lines = cluster_objects(raw_words, "top", _LINE_TOLERANCE)
    lines.sort(key=lambda line: sum(w["top"] for w in line) / len(line))

    parts: list[str] = []
    words: list[Word] = []
    offset = 0
    for line in lines:
        line_words = sorted(line, key=lambda w: w["x0"])
        for i, w in enumerate(line_words):
            if i > 0:
                parts.append(" ")
                offset += 1
            token = w["text"]
            start = offset
            parts.append(token)
            offset += len(token)
            words.append(Word(text=token, char_start=start, char_end=offset,
                               bbox=(w["x0"], w["top"], w["x1"], w["bottom"])))
        parts.append("\n")
        offset += 1

    text = "".join(parts)
    if text.endswith("\n"):
        text = text[:-1]           # offsets of real words are unaffected by trimming the tail
    return text, words


def _table_caption(page, table_bbox, window: float = 45.0) -> str:
    """Text immediately above a table — usually its caption/header line."""
    x0, top, x1, bottom = table_bbox
    top_c = max(0.0, top - window)
    if top_c >= top:
        return ""
    crop = page.crop((0, top_c, page.width, top))
    return (crop.extract_text() or "").strip()


def _parse_table(page, pt, page_no: int) -> Table:
    rows_text = pt.extract() or []
    cell_bboxes = [[cell for cell in row.cells] for row in pt.rows]
    caption = _table_caption(page, pt.bbox)
    header_blob = " ".join([caption] + [" ".join(c or "" for c in r) for r in rows_text[:2]])
    scale_context = detect_scale_context(header_blob)
    return Table(page_no=page_no, bbox=pt.bbox, rows=rows_text, cell_bboxes=cell_bboxes,
                 caption=caption, scale_context=scale_context)


def _parse_page(page, page_no: int) -> Page:
    try:
        raw_words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    except Exception as e:
        return Page(page_no=page_no, text="", image_only=True, error=f"word extraction failed: {e}")

    try:
        text, words = _build_text_and_words(raw_words)
    except Exception as e:
        return Page(page_no=page_no, text="", image_only=True, error=f"text assembly failed: {e}")

    tables: list[Table] = []
    try:
        for pt in page.find_tables():
            try:
                tables.append(_parse_table(page, pt, page_no))
            except Exception:
                continue        # one malformed table must not drop the whole page
    except Exception as e:
        return Page(page_no=page_no, text=text, words=words, tables=[],
                    image_only=not text.strip(), error=f"table extraction failed: {e}")

    image_only = not text.strip()
    return Page(page_no=page_no, text=text, words=words, tables=tables, image_only=image_only)


# --------------------------------------------------------------------------
# Table-strategy fallback
#
# pdfplumber's default table finder uses ruling lines ("lines"/lattice
# strategy). Borderless tables — common in statistical annexes — are invisible
# to it. Detecting this per-document (not per-filename) and falling back to a
# whitespace-based ("text") strategy recovers them without hardcoding which
# document needs it.
# --------------------------------------------------------------------------

_TEXT_STRATEGY_SETTINGS = {"vertical_strategy": "text", "horizontal_strategy": "text"}
_LOW_TABLE_DENSITY_THRESHOLD = 0.2   # tables/page below which the lattice pass is considered to have failed


def _find_tables_for_page(page, page_no: int, table_settings: Optional[dict] = None) -> list[Table]:
    tables: list[Table] = []
    try:
        found = page.find_tables(table_settings) if table_settings else page.find_tables()
    except Exception:
        return tables
    for pt in found:
        try:
            tables.append(_parse_table(page, pt, page_no))
        except Exception:
            continue        # one malformed table must not drop the whole page
    return tables


def _parse_pdf_uncached(path: str) -> Document:
    """Parse a single PDF into a Document. Never raises: encrypted files,
    scanned/image-only pages, and malformed pages are recorded on the
    resulting object instead of crashing the run."""
    filename = os.path.basename(path)
    doc_id = _doc_id(path)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            pdf = pdfplumber.open(path)
        except Exception as e:
            return Document(doc_id=doc_id, filename=filename, n_pages=0, pages=[],
                            encrypted=_looks_encrypted(e), error=str(e))

        try:
            with pdf:
                n = len(pdf.pages)
                pages: list[Page] = []
                for i in range(n):
                    try:
                        pages.append(_parse_page(pdf.pages[i], i + 1))
                    except Exception as e:
                        pages.append(Page(page_no=i + 1, text="", image_only=True, error=str(e)))

                table_strategy = "lines"
                lattice_total = sum(len(p.tables) for p in pages)
                density = (lattice_total / n) if n else 0.0
                if density < _LOW_TABLE_DENSITY_THRESHOLD:
                    text_tables_by_page: dict[int, list[Table]] = {}
                    text_total = 0
                    for i in range(n):
                        ft = _find_tables_for_page(pdf.pages[i], i + 1, _TEXT_STRATEGY_SETTINGS)
                        text_tables_by_page[i] = ft
                        text_total += len(ft)
                    if text_total > lattice_total:
                        for i, pg in enumerate(pages):
                            pg.tables = text_tables_by_page[i]
                        table_strategy = "text"

            return Document(doc_id=doc_id, filename=filename, n_pages=len(pages), pages=pages,
                            table_strategy=table_strategy)
        except Exception as e:
            return Document(doc_id=doc_id, filename=filename, n_pages=0, pages=[],
                            encrypted=_looks_encrypted(e), error=str(e))


# --------------------------------------------------------------------------
# Parsed-document cache
#
# A full 6-PDF parse takes ~150s. Keyed on file content (not path), so an
# edited PDF re-parses automatically while an unchanged one is instant — this
# matters as much for the demo as for iteration speed.
# --------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARSED_CACHE_DIR = os.path.join(_REPO_ROOT, "cache", "parsed")


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _word_to_dict(w: Word) -> dict:
    return {"text": w.text, "char_start": w.char_start, "char_end": w.char_end, "bbox": list(w.bbox)}


def _word_from_dict(d: dict) -> Word:
    return Word(text=d["text"], char_start=d["char_start"], char_end=d["char_end"], bbox=tuple(d["bbox"]))


def _table_to_dict(t: Table) -> dict:
    return {
        "page_no": t.page_no,
        "bbox": list(t.bbox),
        "rows": t.rows,
        "cell_bboxes": [[list(c) if c else None for c in row] for row in t.cell_bboxes],
        "caption": t.caption,
        "scale_context": t.scale_context,
    }


def _table_from_dict(d: dict) -> Table:
    return Table(
        page_no=d["page_no"], bbox=tuple(d["bbox"]), rows=d["rows"],
        cell_bboxes=[[tuple(c) if c else None for c in row] for row in d["cell_bboxes"]],
        caption=d.get("caption", ""), scale_context=d.get("scale_context", ""),
    )


def _page_to_dict(p: Page) -> dict:
    return {
        "page_no": p.page_no,
        "text": p.text,
        "words": [_word_to_dict(w) for w in p.words],
        "tables": [_table_to_dict(t) for t in p.tables],
        "image_only": p.image_only,
        "error": p.error,
    }


def _page_from_dict(d: dict) -> Page:
    return Page(
        page_no=d["page_no"], text=d["text"],
        words=[_word_from_dict(w) for w in d["words"]],
        tables=[_table_from_dict(t) for t in d["tables"]],
        image_only=d.get("image_only", False), error=d.get("error"),
    )


def _document_to_dict(doc: Document) -> dict:
    return {
        "doc_id": doc.doc_id,
        "filename": doc.filename,
        "n_pages": doc.n_pages,
        "pages": [_page_to_dict(p) for p in doc.pages],
        "encrypted": doc.encrypted,
        "error": doc.error,
        "table_strategy": doc.table_strategy,
    }


def _document_from_dict(d: dict) -> Document:
    return Document(
        doc_id=d["doc_id"], filename=d["filename"], n_pages=d["n_pages"],
        pages=[_page_from_dict(p) for p in d["pages"]],
        encrypted=d.get("encrypted", False), error=d.get("error"),
        table_strategy=d.get("table_strategy", "lines"),
    )


def _cache_path(file_hash: str) -> str:
    return os.path.join(_PARSED_CACHE_DIR, f"{file_hash}.json")


def _load_cached_document(file_hash: str) -> Optional[Document]:
    path = _cache_path(file_hash)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _document_from_dict(json.load(f))
    except Exception:
        return None     # corrupt cache entry -> fall through to a fresh parse


def _save_cached_document(file_hash: str, doc: Document) -> None:
    os.makedirs(_PARSED_CACHE_DIR, exist_ok=True)
    path = _cache_path(file_hash)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_document_to_dict(doc), f)
    os.replace(tmp, path)


def parse_pdf(path: str) -> Document:
    """Parse a single PDF into a Document, transparently cached by file
    content hash at cache/parsed/{sha256}.json. Never raises: encrypted
    files, scanned/image-only pages, and malformed pages are recorded on the
    resulting object instead of crashing the run."""
    try:
        file_hash = _file_sha256(path)
    except OSError as e:
        return Document(doc_id=_doc_id(path), filename=os.path.basename(path),
                        n_pages=0, pages=[], error=str(e))

    cached = _load_cached_document(file_hash)
    if cached is not None:
        return cached

    doc = _parse_pdf_uncached(path)
    if doc.error is None:
        _save_cached_document(file_hash, doc)
    return doc
