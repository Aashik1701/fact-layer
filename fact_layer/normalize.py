"""
Normalisation layer.

The brief forbids document-specific rules. These are not document-specific —
they are *locale*-general: Indian numbering (lakh/crore), Indian fiscal years
(1 April - 31 March), and company-suffix conventions. They are declarative and
config-driven, and the resolver can extend them at runtime when a new unit or
period phrasing appears. There is no branch anywhere on a filename.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from .models import Period, PeriodKind, Quantity, Scope

# --------------------------------------------------------------------------
# Numeric scales
# --------------------------------------------------------------------------

SCALES: dict[str, Decimal] = {
    "hundred": Decimal(100),
    "thousand": Decimal(1_000),
    "k": Decimal(1_000),
    "lac": Decimal(100_000),
    "lakh": Decimal(100_000),
    "lakhs": Decimal(100_000),
    "lakhs.": Decimal(100_000),
    "million": Decimal(1_000_000),
    "mn": Decimal(1_000_000),
    "mm": Decimal(1_000_000),
    "crore": Decimal(10_000_000),
    "crores": Decimal(10_000_000),
    "cr": Decimal(10_000_000),
    "cr.": Decimal(10_000_000),
    "billion": Decimal(1_000_000_000),
    "bn": Decimal(1_000_000_000),
    "trillion": Decimal(1_000_000_000_000),
}

CURRENCY_TOKENS: dict[str, str] = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "rupees": "INR",
    "$": "USD", "us$": "USD", "usd": "USD", "dollars": "USD",
    "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
}

_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _sig_figs(literal: str) -> int:
    """Significant figures as WRITTEN. '120.4' -> 4, '1,20,000' -> 6, '0.0500' -> 3."""
    s = literal.replace(",", "").replace("+", "").lstrip("-")
    if "." in s:
        whole, frac = s.split(".", 1)
        digits = (whole.lstrip("0") + frac) if whole.strip("0") else frac.lstrip("0") or frac
        return max(len(digits), 1)
    s = s.rstrip("0") or "0"
    return max(len(s.lstrip("0")), 1)


def parse_quantity(text: str, context: str = "") -> Optional[Quantity]:
    """Parse a numeric literal plus any scale/currency/unit markers around it.

    `context` is the surrounding sentence or table header — scale words often
    live in a header ("Rs. in lakhs") rather than beside the number itself.
    """
    if not text:
        return None
    raw = text.strip()
    blob = f"{raw} {context}".lower()

    m = _NUM_RE.search(raw.replace("−", "-"))
    if not m:
        return None
    literal = m.group(0)
    try:
        value = Decimal(literal.replace(",", ""))
    except InvalidOperation:
        return None

    sf = _sig_figs(literal)

    # percent
    if "%" in raw or "per cent" in blob or "percent" in blob:
        return Quantity(value=value, unit="percent", currency=None, sig_figs=sf, raw=raw)

    # scale multiplier — nearest scale token wins (in the literal first, then context)
    scale = Decimal(1)
    for source in (raw.lower(), blob):
        for token, mult in sorted(SCALES.items(), key=lambda kv: -len(kv[0])):
            if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", source):
                scale = mult
                break
        if scale != 1:
            break

    currency = None
    for token, code in CURRENCY_TOKENS.items():
        if token in blob:
            currency = code
            break

    unit = "currency" if currency else "count"
    return Quantity(value=value * scale, unit=unit, currency=currency, sig_figs=sf, raw=raw)


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})

_FY_RE = re.compile(r"\b(?:f\.?y\.?|fiscal(?:\s+year)?|financial\s+year)\s*[:\-]?\s*"
                    r"(\d{4})\s*[-–/]\s*(\d{2,4})", re.I)
_FY_SINGLE_RE = re.compile(r"\b(?:f\.?y\.?|fiscal(?:\s+year)?)\s*[:\-]?\s*(\d{4})\b", re.I)
_QTR_RE = re.compile(r"\bq([1-4])\s*(?:of\s*)?(?:f\.?y\.?)?\s*(\d{2,4})(?:\s*[-–/]\s*(\d{2,4}))?", re.I)
_ENDED_RE = re.compile(
    r"\b(year|quarter|half[\s-]?year|three\s+months|six\s+months|nine\s+months|twelve\s+months)"
    r"\s+ended\s+(?:on\s+)?(.{4,30}?)(?:[,.)]|$)", re.I)
_ASON_RE = re.compile(r"\b(?:as\s+(?:on|at|of)|w\.e\.f\.|with\s+effect\s+from|dated)\s+(.{4,30}?)(?:[,.)]|$)", re.I)

_DATE_PATTERNS = [
    re.compile(r"(\d{1,2})[\s./-](\d{1,2})[\s./-](\d{4})"),
    re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+),?\s+(\d{4})", re.I),
    re.compile(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I),
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
]


def parse_date(text: str) -> Optional[date]:
    if not text:
        return None
    t = text.strip().lower()
    for i, pat in enumerate(_DATE_PATTERNS):
        m = pat.search(t)
        if not m:
            continue
        try:
            if i == 0:
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif i == 1:
                d, mo, y = int(m.group(1)), MONTHS.get(m.group(2)[:3], 0), int(m.group(3))
            elif i == 2:
                mo, d, y = MONTHS.get(m.group(1)[:3], 0), int(m.group(2)), int(m.group(3))
            else:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if mo:
                return date(y, mo, d)
        except (ValueError, TypeError):
            continue
    return None


def _fy_window(start_year: int) -> tuple[date, date]:
    """Indian fiscal year: 1 April of start_year to 31 March of start_year+1."""
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def _expand_year(y: int, anchor: int) -> int:
    if y >= 1900:
        return y
    century = (anchor // 100) * 100
    return century + y


def parse_period(text: str) -> Optional[Period]:
    """Turn a period phrase into an explicit window. This is the single highest
    leverage function in the system: without it, every cross-period figure looks
    like a contradiction."""
    if not text:
        return None
    t = " ".join(text.split())

    # "Q1 FY25", "Q3 2023-24"
    m = _QTR_RE.search(t)
    if m:
        q = int(m.group(1))
        y = _expand_year(int(m.group(2)), 2000)
        # "Q3 FY2023-24" -> range form, first year is the FY start year.
        # "Q1 FY25" / "Q1 FY2025" -> single year names the year the FY *ends*,
        # so the FY starts in the preceding April. This is the single most
        # common period-parsing error in Indian filings.
        start_year = y if m.group(3) else y - 1
        s, _ = _fy_window(start_year)
        q_start_month = 4 + 3 * (q - 1)
        sy = start_year + (1 if q_start_month > 12 else 0)
        q_start_month = q_start_month if q_start_month <= 12 else q_start_month - 12
        start = date(sy, q_start_month, 1)
        end_month = q_start_month + 2
        ey = sy + (1 if end_month > 12 else 0)
        end_month = end_month if end_month <= 12 else end_month - 12
        last_day = 31 if end_month in (1, 3, 5, 7, 8, 10, 12) else (30 if end_month != 2 else 28)
        return Period(PeriodKind.DURATION, start, date(ey, end_month, last_day), label=m.group(0))

    # "FY2023-24"
    m = _FY_RE.search(t)
    if m:
        y1 = int(m.group(1))
        s, e = _fy_window(y1)
        return Period(PeriodKind.DURATION, s, e, label=m.group(0))

    m = _FY_SINGLE_RE.search(t)
    if m:
        y = int(m.group(1))
        s, e = _fy_window(y - 1)   # "FY2024" in India = 2023-04-01..2024-03-31
        return Period(PeriodKind.DURATION, s, e, label=m.group(0))

    # "year ended 31 March 2024" / "quarter ended 30 June 2025"
    m = _ENDED_RE.search(t)
    if m:
        span = m.group(1).lower().replace("-", " ")
        end = parse_date(m.group(2))
        if end:
            months = {"year": 12, "twelve months": 12, "quarter": 3, "three months": 3,
                      "half year": 6, "halfyear": 6, "six months": 6, "nine months": 9}.get(span, 12)
            sm = end.month - months + 1
            sy = end.year + (sm - 1) // 12
            sm = ((sm - 1) % 12) + 1
            return Period(PeriodKind.DURATION, date(sy, sm, 1), end, label=m.group(0))

    # "as on 31.03.2025" — a point-in-time stock value
    m = _ASON_RE.search(t)
    if m:
        d = parse_date(m.group(1))
        if d:
            return Period(PeriodKind.INSTANT, d, d, label=m.group(0))

    d = parse_date(t)
    if d:
        return Period(PeriodKind.INSTANT, d, d, label=t.strip())
    return None


# --------------------------------------------------------------------------
# Scope / basis detection
# --------------------------------------------------------------------------

def detect_scope(text: str) -> Scope:
    t = (text or "").lower()
    if "consolidated" in t:
        return Scope.CONSOLIDATED
    if "standalone" in t or "unconsolidated" in t or "separate financial" in t:
        return Scope.STANDALONE
    if "segment" in t:
        return Scope.SEGMENT
    return Scope.UNKNOWN


def detect_basis(text: str) -> Optional[str]:
    t = (text or "").lower()
    for b in ("unaudited", "audited", "provisional", "restated", "reviewed"):
        if b in t:
            return b
    return None


# --------------------------------------------------------------------------
# Entity + address canonicalisation
# --------------------------------------------------------------------------

_COMPANY_SUFFIXES = [
    "private limited", "pvt limited", "pvt. ltd.", "pvt ltd", "pvt.", "private",
    "limited", "ltd.", "ltd", "llp", "inc.", "inc", "corporation", "corp.", "corp",
    "company", "co.", "plc", "gmbh",
]

_HONORIFICS = ["mr.", "mr", "mrs.", "mrs", "ms.", "ms", "dr.", "dr", "shri", "smt.", "smt",
               "sri", "prof.", "prof", "cs", "ca"]


def normalize_entity(name: str) -> str:
    """Canonical key for an entity. 'Acme Technologies Pvt. Ltd.' and
    'ACME TECHNOLOGIES PRIVATE LIMITED' collapse to the same key."""
    if not name:
        return ""
    t = re.sub(r"[^\w\s.&-]", " ", name.lower())
    t = " ".join(t.split())
    for h in sorted(_HONORIFICS, key=len, reverse=True):
        t = re.sub(rf"^{re.escape(h)}\s+", "", t)
    for s in sorted(_COMPANY_SUFFIXES, key=len, reverse=True):
        t = re.sub(rf"\s+{re.escape(s)}$", "", t)
    t = re.sub(r"[.&,-]", " ", t)
    return " ".join(t.split())


_ADDRESS_ABBREV = {
    r"\bno\.?\b": "", r"\bd\.?no\.?\b": "", r"\bdoor\s+no\.?\b": "",
    r"\bst\b": "street", r"\brd\b": "road", r"\bave\b": "avenue",
    r"\bcr\b": "cross", r"\bmn\b": "main", r"\bapt\b": "apartment",
    r"\bblk\b": "block", r"\bfl\b": "floor", r"\bopp\.?\b": "opposite",
    r"\bnr\.?\b": "near", r"\bbangalore\b": "bengaluru",
    r"\bbombay\b": "mumbai", r"\bmadras\b": "chennai",
    r"\bcalcutta\b": "kolkata", r"\bpoona\b": "pune",
    r"\btrivandrum\b": "thiruvananthapuram", r"\bgurgaon\b": "gurugram",
}

_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
          "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


def normalize_address(addr: str) -> str:
    """Collapse Indian address spelling variants so that
    'No. 12, 2nd Cross, Indiranagar, Bengaluru 560038' and
    '12, II Cross Rd, Indira Nagar, Bangalore - 560 038' match."""
    if not addr:
        return ""
    t = addr.lower()
    t = re.sub(r"(\d{3})\s+(\d{3})\b", r"\1\2", t)          # 560 038 -> 560038
    t = re.sub(r"[^\w\s]", " ", t)
    t = " ".join(t.split())
    t = " ".join(_ROMAN.get(w, w) for w in t.split())
    for pat, rep in _ADDRESS_ABBREV.items():
        t = re.sub(pat, rep, t)
    t = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", t)
    t = re.sub(r"\b(indira)\s+(nagar)\b", r"\1\2", t)        # spacing variants
    tokens = sorted(set(t.split()))                          # order-insensitive
    return " ".join(tokens)


def address_similarity(a: str, b: str) -> float:
    ta, tb = set(normalize_address(a).split()), set(normalize_address(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
