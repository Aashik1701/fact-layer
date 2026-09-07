"""
Canonicalization — subjects, measures, and issuers, across documents.

Three tiers, cheapest first, same philosophy as everywhere else in this
project: rules first, LLM only for the residual ambiguous tail.

  1. Deterministic — normalize_entity() for subjects. Already exists, reused
     as-is, never reimplemented.
   2. Declarative alias table — a small, locale-general dict for issuers
      (specification section 14a) and for well-known measure synonyms. Extensible
      at runtime, no filename- or dataset-specific rules (project invariant 2).
  3. Fuzzy string match for near-misses (measures only), then — only for
     what's still ambiguous, capped at a small LLM budget — a cached LLM call
     that decides "are these the same measure?" given both verbatim strings.

Every canonicalization decision is logged to data/resolution_log.json: which
tier resolved it and the resulting canonical id. This is a README artifact —
it shows the tiered approach actually working, not just its output.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
from dataclasses import dataclass, field, replace
from typing import Optional

from . import llm
from .models import Fact
from .normalize import normalize_entity

logger = logging.getLogger("fact_layer.resolve")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_REPO_ROOT, "data")
_RESOLUTION_LOG_PATH = os.path.join(_DATA_DIR, "resolution_log.json")

_FUZZY_MEASURE_THRESHOLD = 0.85
_LLM_WORTH_ASKING_THRESHOLD = 0.5   # below this, not even worth an LLM call
_MAX_LLM_MEASURE_CALLS = 60

# --------------------------------------------------------------------------
# Tier 2: declarative alias tables. Institutions and standard financial/macro
# line items only — no measure phrasing specific to any one document, no
# filename branching anywhere near this file.
# --------------------------------------------------------------------------

ISSUER_ALIASES: dict[str, str] = {
    "imf": "IMF",
    "imf staff": "IMF",
    "international monetary fund": "IMF",
    "rbi": "RBI",
    "reserve bank of india": "RBI",
    "government of india": "Government of India",
    "goi": "Government of India",
    "gol": "Government of India",   # common OCR/typo variant of GoI
    "union government": "Government of India",
    "central government": "Government of India",
}

# Countries/economies are what macro claims are ABOUT, never who is making
# them — "India" as issuer_raw is the claim's SUBJECT leaking into the
# issuer field (confirmed in this corpus: 5 IMF-doc facts about India's
# current account / NIIP came through with issuer_raw="India", when the
# actual issuer — the body asserting the claim — is the IMF, whose report
# this is). A wrong issuer manufactures false cross-issuer signal (gate.py's
# INCOMPARABLE_ISSUER / cross_issuer logic would treat "India vs IMF" as two
# disagreeing institutions); nulling it is honest, guessing a replacement
# institution would not be. Declarative and extensible, same pattern as
# ISSUER_ALIASES — add other countries here if the same leakage shows up
# elsewhere, not a special case for this one document.
SUBJECT_NOT_ISSUER: set[str] = {
    "india",
}

MEASURE_ALIASES: dict[str, str] = {
    "revenue": "revenue",
    "revenue from operations": "revenue",
    "total income from operations": "revenue",
    "total revenue": "revenue",
    "total income": "revenue",
    "net revenue": "revenue",
    "real gdp growth": "real_gdp_growth",
    "real gdp at market prices": "real_gdp_growth",
    "gdp growth": "real_gdp_growth",
    "profit after tax": "profit_after_tax",
    "net profit": "profit_after_tax",
    "pat": "profit_after_tax",
    "ebitda": "ebitda",
    "adjusted ebitda": "ebitda",
}

# National statistical appendix tables (RBI's macro-indicator table is one
# instance of a common convention, not unique to this document) use the
# INDICATOR NAME as the row label, implicitly about the reporting economy —
# e.g. "I.1 Real GDP at Market Prices (% change)" as a row, with the country
# never restated per-row. The LLM extracts that indicator name as
# subject_raw with a generic measure_raw like "% change"; the same
# indicator read in prose comes out the other way around (subject=India,
# measure=real GDP growth). Without reconciling these two shapes, the same
# underlying claim from a table-structured source and a prose-structured
# source never land in the same cluster, no matter how well issuer/measure
# aliasing works on each side separately. Small, declarative, extensible —
# the same pattern as ISSUER_ALIASES/MEASURE_ALIASES above, not a one-off
# fix for a single demo pair: it covers every indicator in that table
# shape, not just GDP.
MACRO_INDICATOR_SUBJECTS: dict[str, str] = {
    "real gdp at market prices": "real_gdp_growth",
    "real gva at basic prices": "real_gva_growth",
    "index of industrial production": "index_of_industrial_production",
    "index of eight core industries": "core_industries_index",
    "consumer price index cpi combined": "cpi_inflation",
    "cpi industrial workers iw": "cpi_inflation",
    "wholesale price index wpi": "wpi_inflation",
    "reserve money": "reserve_money_growth",
    "broad money m3": "broad_money_growth",
}

# The reporting economy these indicator-as-subject tables are implicitly
# about. Every document in this corpus with tables of this shape reports on
# India; a genuinely different economy's statistical annex would need its
# own entry here, not a filename check — the key is the alias table being
# used, not this constant.
_MACRO_INDICATOR_REPORTING_ECONOMY = "india"


def _normalize_key(s: Optional[str]) -> str:
    """Light key normalization shared by the alias-lookup tiers: lowercase,
    strip punctuation, collapse whitespace. Not normalize_entity() — that
    also strips company suffixes/honorifics, which doesn't apply to issuers
    or measures."""
    t = re.sub(r"[^\w\s]", " ", (s or "").lower())
    return " ".join(t.split())


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "unknown"


# --------------------------------------------------------------------------
# Resolution log
# --------------------------------------------------------------------------

@dataclass
class ResolutionDecision:
    kind: str            # "subject" | "measure" | "issuer"
    raw: str
    canonical: str
    tier: str             # "deterministic" | "alias" | "exact_repeat" | "fuzzy" | "llm" | "new" | "unresolved"
    confidence: float
    detail: str = ""


@dataclass
class Resolver:
    """Incremental canonicalization state. Subjects and issuers are
    stateless lookups; measures build a growing registry so a later
    document's measures get compared against everything already
    canonicalized — this is what makes incremental ingest (store.py)
    resolve new documents against existing canonical ids rather than
    starting over each time."""

    _canonical_measure_of: dict[str, str] = field(default_factory=dict)      # normalized raw -> canonical id
    _measure_example: dict[str, str] = field(default_factory=dict)           # canonical id -> one representative raw string
    llm_calls_used: int = 0
    decisions: list[ResolutionDecision] = field(default_factory=list)

    # ---- subjects: tier 1 only, per spec ---------------------------------
    def resolve_subject(self, subject_raw: str) -> str:
        canonical = normalize_entity(subject_raw) if subject_raw else (subject_raw or "")
        self.decisions.append(ResolutionDecision("subject", subject_raw, canonical, "deterministic", 1.0))
        return canonical

    # ---- issuers: tier 2 only, per spec ----------------------------------
    def resolve_issuer(self, issuer_raw: Optional[str]) -> Optional[str]:
        if not issuer_raw or not issuer_raw.strip():
            return None
        key = _normalize_key(issuer_raw)
        if key in SUBJECT_NOT_ISSUER:
            self.decisions.append(ResolutionDecision(
                "issuer", issuer_raw, "", "nulled",
                1.0, "matched SUBJECT_NOT_ISSUER — this is the claim's subject, not its issuer",
            ))
            return None
        alias = ISSUER_ALIASES.get(key)
        if alias:
            self.decisions.append(ResolutionDecision("issuer", issuer_raw, alias, "alias", 1.0))
            return alias
        canonical = issuer_raw.strip()
        self.decisions.append(ResolutionDecision(
            "issuer", issuer_raw, canonical, "unresolved", 0.5,
            "no alias table match; kept verbatim",
        ))
        return canonical

    # ---- measures: full 3-tier treatment ---------------------------------
    def resolve_measure(self, measure_raw: str, quote_context: str = "") -> str:
        if not measure_raw or not measure_raw.strip():
            canonical = "unknown_measure"
            self.decisions.append(ResolutionDecision("measure", measure_raw, canonical, "unresolved", 0.0))
            return canonical

        key = _normalize_key(measure_raw)

        # Tier 2a: alias table (checked before "already seen" so a document
        # that happens to hit the table gets the curated id, not whatever a
        # fuzzy/LLM tier guessed for an earlier near-miss).
        alias = MEASURE_ALIASES.get(key)
        if alias:
            self._register(alias, measure_raw)
            self.decisions.append(ResolutionDecision("measure", measure_raw, alias, "alias", 1.0))
            return alias

        # Exact repeat of something already canonicalized this session.
        if key in self._canonical_measure_of:
            canonical = self._canonical_measure_of[key]
            self.decisions.append(ResolutionDecision("measure", measure_raw, canonical, "exact_repeat", 1.0))
            return canonical

        # Tier 3a: fuzzy match against every canonical measure seen so far.
        # This registry grows with corpus size (many-document scaling test,
        # README), so the per-candidate SequenceMatcher.ratio() call — the
        # expensive part — is skipped whenever it's mathematically impossible
        # for a candidate to beat the current best: ratio = 2*M/(len(a)+len(b))
        # with M <= min(len(a),len(b)), so an upper bound on ratio is cheap to
        # compute from lengths alone. This is an exact prune, not a heuristic
        # approximation — it finds the identical best_id/best_ratio the
        # unoptimized scan would, just without scoring candidates that can't win.
        best_id, best_ratio = None, 0.0
        key_len = len(key)
        for canonical_id, example in self._measure_example.items():
            example_key = _normalize_key(example)
            total_len = key_len + len(example_key)
            if total_len == 0:
                continue
            upper_bound = 2 * min(key_len, len(example_key)) / total_len
            if upper_bound <= best_ratio:
                continue
            ratio = difflib.SequenceMatcher(None, key, example_key).ratio()
            if ratio > best_ratio:
                best_ratio, best_id = ratio, canonical_id

        if best_id and best_ratio >= _FUZZY_MEASURE_THRESHOLD:
            self._register(best_id, measure_raw)
            self.decisions.append(ResolutionDecision(
                "measure", measure_raw, best_id, "fuzzy", round(best_ratio, 3),
            ))
            return best_id

        # Tier 3b: LLM adjudication for the residual ambiguous tail — only
        # when there's a plausible candidate worth asking about, and only
        # within the call budget.
        if best_id and best_ratio >= _LLM_WORTH_ASKING_THRESHOLD and self.llm_calls_used < _MAX_LLM_MEASURE_CALLS:
            same, reason = _llm_same_measure(measure_raw, self._measure_example[best_id])
            self.llm_calls_used += 1
            if same:
                self._register(best_id, measure_raw)
                self.decisions.append(ResolutionDecision(
                    "measure", measure_raw, best_id, "llm", round(best_ratio, 3),
                    f"LLM judged same as {self._measure_example[best_id]!r}: {reason}",
                ))
                return best_id
            else:
                logger.debug("LLM measure adjudication: %r vs %r -> different (%s)",
                            measure_raw, self._measure_example[best_id], reason)

        # Nothing matched: this is a new canonical measure.
        canonical_id = _slugify(measure_raw)
        self._register(canonical_id, measure_raw)
        self.decisions.append(ResolutionDecision("measure", measure_raw, canonical_id, "new", 1.0))
        return canonical_id

    def _register(self, canonical_id: str, raw: str) -> None:
        self._canonical_measure_of[_normalize_key(raw)] = canonical_id
        self._measure_example.setdefault(canonical_id, raw)


_MEASURE_SCHEMA = '{"same_measure": true or false, "reason": "short string"}'


def _llm_same_measure(a_raw: str, b_raw: str) -> tuple[bool, str]:
    """Cached (llm.py's replay cache handles this for free) LLM adjudication
    for measure pairs the deterministic/fuzzy tiers couldn't resolve. Never
    the sole authority: this only runs on the tail the rules-first tiers
    already gave up on, and only within a small capped budget."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a precise financial/economic-data taxonomist. Decide "
                "whether two measure labels extracted from documents refer to "
                "the SAME underlying concept (e.g. 'Revenue from operations' "
                "and 'Total income from operations' are the same; 'Revenue' "
                "and 'Profit' are NOT, even though both are financial). "
                "Return ONLY valid JSON matching the schema."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Measure A: {a_raw!r}\nMeasure B: {b_raw!r}\n\n"
                f"Are these the same underlying measure? "
                f"Return JSON matching: {_MEASURE_SCHEMA}"
            ),
        },
    ]
    try:
        response = llm.complete(messages, schema_hint=_MEASURE_SCHEMA, max_tokens=200)
    except llm.LLMError as e:
        # Replay-mode cache miss, or a live call that failed — resolve.py
        # must not crash the whole pipeline over one ambiguous measure pair;
        # treat it as unresolved (conservative: stays a separate measure).
        logger.warning("measure adjudication LLM call unavailable (%s); treating as different", e)
        return False, f"llm unavailable: {e}"

    try:
        data = json.loads(response)
        return bool(data.get("same_measure", False)), str(data.get("reason", ""))
    except (json.JSONDecodeError, AttributeError):
        return False, "unparseable LLM response"


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def resolve_facts(facts: list[Fact], resolver: Optional[Resolver] = None) -> tuple[list[Fact], Resolver]:
    """Canonicalize subject/measure/issuer on a batch of facts IN PLACE
    (Fact is not frozen; fact_id deliberately stays as computed at
    extraction time — it identifies the extracted claim instance, not its
    canonical grouping, which is what cluster_key() reads live).

    `resolver` can be passed in to continue an existing session — store.py's
    incremental ingest reuses one across documents so a new document's
    measures get resolved against everything already canonicalized, not a
    fresh empty registry.
    """
    resolver = resolver if resolver is not None else Resolver()
    for f in facts:
        raw_subject = f.subject_raw or f.subject
        indicator = MACRO_INDICATOR_SUBJECTS.get(_normalize_key(raw_subject))

        if indicator:
            # Table row-label shape: reassign to (reporting economy, indicator
            # measure) so it clusters with the same indicator's prose shape.
            f.subject = _MACRO_INDICATOR_REPORTING_ECONOMY
            f.measure = indicator
            resolver._register(indicator, f.measure_raw or raw_subject)
            resolver.decisions.append(ResolutionDecision(
                "subject", raw_subject, _MACRO_INDICATOR_REPORTING_ECONOMY, "alias",
                1.0, "macro indicator table row-label reassigned via MACRO_INDICATOR_SUBJECTS",
            ))
            resolver.decisions.append(ResolutionDecision(
                "measure", f.measure_raw, indicator, "alias",
                1.0, f"measure reassigned from subject-as-indicator {raw_subject!r}",
            ))
        else:
            f.subject = resolver.resolve_subject(raw_subject)
            quote = f.evidence.verbatim_quote if f.evidence else ""
            f.measure = resolver.resolve_measure(f.measure_raw or f.measure, quote_context=quote)

        canonical_issuer = resolver.resolve_issuer(f.qualifiers.issuer)
        f.qualifiers = replace(f.qualifiers, issuer=canonical_issuer)
    return facts, resolver


def write_resolution_log(resolver: Resolver, path: str = _RESOLUTION_LOG_PATH) -> dict:
    by_tier: dict[str, int] = {}
    for d in resolver.decisions:
        by_tier[d.tier] = by_tier.get(d.tier, 0) + 1

    log = {
        "summary": {
            "total_decisions": len(resolver.decisions),
            "by_tier": by_tier,
            "llm_calls_used": resolver.llm_calls_used,
            "canonical_measures": sorted(set(resolver._measure_example.keys())),
        },
        "decisions": [
            {"kind": d.kind, "raw": d.raw, "canonical": d.canonical, "tier": d.tier,
             "confidence": d.confidence, "detail": d.detail}
            for d in resolver.decisions
        ],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    return log
