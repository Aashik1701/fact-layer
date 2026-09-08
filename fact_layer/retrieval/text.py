"""
Deterministic textual representation of a Fact, for both the lexical
(FTS5/BM25) index and as the embedding model's input.

Built from CANONICAL fields only (post `resolve.py`) — never raw evidence
quotes. Two reasons:
  1. Raw quotes vary with phrasing/OCR noise across documents even when the
     underlying claim is identical, which would make the lexical/semantic
     channels noisier, not more accurate, at exactly the thing they need to
     get right (matching the same claim stated two different ways).
  2. `fact_layer.models.Evidence.verbatim_quote` is canonical, span-verified
     source text (models.py, protected) — treating it as retrieval fodder
     would blur the "evidence is proof, not a search key" boundary the rest
     of this codebase is careful to keep (see Store.get_evidence()'s
     docstring on this exact point).

Determinism requirement (section 3): same Fact -> byte-identical string,
always — this is the embedding cache's namespace key and the FTS5 index's
row content, both of which must be stable across runs for the cache to
ever hit.
"""

from __future__ import annotations

from ..models import Fact, Quantity, ValueKind


def _value_kind_text(fact: Fact) -> str:
    if fact.value_kind == ValueKind.QUANTITY and isinstance(fact.value, Quantity):
        unit = fact.value.unit
        if fact.value.currency:
            unit = f"{unit} ({fact.value.currency})"
        return f"{fact.value_kind.value} ({unit})"
    return fact.value_kind.value


def fact_to_retrieval_text(fact: Fact) -> str:
    """Deterministic, canonical-field-only text for one fact. Order and
    formatting are fixed on purpose — this is a cache key input, not
    prose meant only for a human."""
    q = fact.qualifiers
    period_label = q.period.label if q.period else "none"
    lines = [
        f"subject: {fact.subject}",
        f"measure: {fact.measure}",
        f"value_kind: {_value_kind_text(fact)}",
        f"period: {period_label}",
        f"scope: {q.scope.value}",
        f"segment: {q.segment or 'none'}",
        f"geography: {q.geography or 'none'}",
        f"issuer: {q.issuer or 'none'}",
        f"basis: {q.basis or 'none'}",
        f"modality: {fact.modality.value}",
    ]
    if q.extra:
        for k in sorted(q.extra):
            lines.append(f"extra.{k}: {q.extra[k]}")
    return "\n".join(lines)


__all__ = ["fact_to_retrieval_text"]
