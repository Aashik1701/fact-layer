# Project Specification & Invariants — Fact Knowledge Layer

Project constitution. Read fully before writing code. These are invariants, not
suggestions. If a task seems to require violating one, stop and say so instead
of working around it.

---

## 1. What this is

A hiring assignment for Superjoin / StackItHQ. Build a system that:

1. extracts meaningful numerical and semantic facts from arbitrary PDFs;
2. grounds every fact in verifiable evidence in its source document; and
3. decides whether facts **corroborate**, **contradict**, or only *appear* to
   conflict because of context (period, scope, unit, issuer, vintage).

**Deadline: 23 August 2026, 10:00 IST.** Scope accordingly. A smaller system
whose behaviour is clear beats a large system whose behaviour is not — this is
stated explicitly in the brief.

---

## 2. The one idea everything rests on

> **A fact is not a triple. A fact is a claim plus its qualifiers.**

`(subject, predicate, object)` is insufficient. Two revenue figures are only in
conflict if subject, measure, period, scope, unit and issuer are compatible.
The naive pipeline — extract triples, embed, cosine-similarity, "numbers differ
⇒ contradiction" — is wrong by construction and produces this:

| Fact A | Fact B | Naive | Correct |
|---|---|---|---|
| Revenue ₹120.4 Cr (FY24) | Revenue ₹32.1 Cr (Q1 FY25) | contradiction | disjoint periods |
| Revenue ₹120.4 Cr standalone | ₹145.9 Cr consolidated | contradiction | different scope, both true |
| ₹12,040 lakh | ₹120.4 Cr | different | identical |
| GDP growth 6.5% (IMF) | 7.2% (RBI) | contradiction | forecast disagreement |
| Director active (31-3-24) | resigned (12-3-25) | contradiction | supersession |

**Never compare two values until the comparability gate has passed them.**

---

## 3. Non-negotiable invariants

1. **Span verification.** Every extracted fact carries a `verbatim_quote`.
   After extraction, assert programmatically that the quote occurs in the page
   text (whitespace-normalised). Fuzzy-snap at ≥92% ratio; otherwise **drop the
   fact** and append it to `data/rejected_facts.jsonl`. Unverified facts must
   never reach the store. This file is a deliverable — it is the assignment's
   required "extraction failure" case, measured rather than anecdotal.

2. **No filename branching, ever.** No `if "delhivery" in path`, no
   per-document schemas, no hardcoded measure lists keyed to a dataset. Rules
   must be locale-general (Indian numbering, Indian fiscal years) and live in
   declarative config the resolver can extend at runtime.

3. **Rules first, LLM second.** Anything decidable deterministically is decided
   in Python: unit mismatch, period subsumption, scope difference, `as_of`
   ordering. The LLM is used only for the residual ambiguous tail, and only
   ever to reason over evidence already retrieved and verified. It is never a
   retriever and never the sole authority on a verdict.

4. **Replay mode.** Cache every LLM response at
   `cache/{sha256(model+prompt)}.json`. `LLM_MODE=replay` must run the whole
   pipeline over the starter datasets with **no API key, no network**. The
   graders have no key and the free tier is rate-limited; without this they
   cannot run the project and the demo video will not match their re-run.

5. **Provenance survives parsing.** Page text must retain char offsets that map
   back to bounding boxes, or evidence highlighting is impossible later. Do not
   lose this in the parser to save time. It is the hardest thing to retrofit.

6. **Open world.** Absence of a fact is never negation. "Director not listed in
   Doc B" ≠ "resigned". Do not generate contradictions from absence.

7. **No silent currency conversion.** INR vs USD is `INCOMPARABLE_UNIT` unless
   the document itself states a rate. Guessing an FX rate manufactures false
   contradictions. Refusing, with an explanation, is the correct behaviour.

---

## 4. Architecture

```
fact_layer/
  models.py         # Fact, Qualifiers, Quantity, Period, Evidence, Relation   [DONE]
  normalize.py      # lakh/crore, Indian FY, entity + address canonicalisation [DONE]
  comparability.py  # THE GATE — may these two facts be compared at all?       [DONE]
  adjudicate.py     # rules-first relation classification with explanations    [DONE]
  parse.py          # pdfplumber: page text + tables + char offsets + bboxes   [DONE]
  triage.py         # deterministic, LLM-free page selection + call batching    [TODO]
  extract.py        # LLM -> Fact[], schema-constrained, then span-verified    [TODO]
  resolve.py        # canonicalise subjects + measures across documents        [TODO]
  store.py          # JSON-backed store, clusters, incremental ingest          [TODO]
  llm.py            # Groq/OpenRouter client + replay cache + retry/repair     [TODO]
api.py              # FastAPI: POST /ingest, GET /facts, /relations, /clusters [TODO]
frontend/index.html # single-file UI, no build step                            [TODO]
```

**Modules marked DONE are tested and must not be rewritten.** Extend them if
needed, but keep every existing test green.

Pipeline: `parse → triage → extract → verify → normalize → resolve → dedupe →
cluster → gate → adjudicate → serve`.

---

## 5. The datasets

Six PDFs in two unrelated domains. The split is the generalisation test — the
same pipeline must handle both with zero configuration change.

- `starter-datasets/delhivery/` — prospectus (2022), annual report (FY24), Q4
  FY24 earnings deck. One entity, three vintages, three document genres.
  Expect: standalone vs consolidated, restatements, ₹ in crore/lakh tables.
- `starter-datasets/india-macroeconomy/` — Economic Survey 2024-25, RBI Annual
  Report 2024-25, IMF Article IV 2025. Three institutions, one economy.
  Expect: GDP growth projections that genuinely disagree, real vs nominal,
  2011-12 constant prices, differing fiscal-year conventions.

**Schema consequence:** macro data forces an `issuer` qualifier.
Same issuer + later vintage + projection → `SUPERSEDES` (revised forecast).
Different issuer + same period + projection → `APPARENT_CONFLICT`, reason
`forecast_disagreement`. Different issuer + same period + asserted historical →
genuine `CONTRADICTS`. Forecast disagreement is not factual contradiction.

---

## 6. Build order and checkpoints

Do these in order. After each, run `pytest -q` and commit.

| # | Milestone | Done when |
|---|---|---|
| 1 | `parse.py` | ✅ Done. 19 tests green. Any PDF → pages with text, char offsets, bboxes, tables. |
| 1.5 | table fallback + parse cache | ✅ Done. IMF tables 15 → 89; warm parse ~2s. |
| 2 | `triage.py` + `llm.py` | ✅ Done. 88 pages → 75 calls. 30 tests green. |
| 3 | `extract.py` + span verifier | ✅ Done. 84.25% pass rate, 690/819 verified. 58 tests green. |
| 3.5 | wire `issuer` qualifier | ✅ Done. 65 tests green. Verified against specification's own worked example. |
| 4 | `resolve.py` + `store.py` | ✅ Done. 92 tests green. Real RBI/IMF pair verified end to end. |
| 4.5 | evidence contract + resolution audit | ✅ Done. 96 tests green. 471 unresolved were all issuer, not measure. |
| 5 | relation quality + `api.py` | ✅ Done. 113 tests green. All 8 CONTRADICTS correctly period-penalized; 9-endpoint FastAPI incl. /page-image. |
| 3 | `extract.py` | Facts extracted with quotes; span verifier drops hallucinations into `rejected_facts.jsonl`. |
| 4 | `resolve.py` + `store.py` | Facts from all 6 PDFs canonicalised and clustered. |
| 5 | `api.py` | `POST /ingest` accepts a new PDF and returns new facts + new relations only. |
| 6 | `frontend/index.html` | ✅ Done. Single-file dark-theme UI: stats bar, upload, relation cards, evidence viewer with bbox overlay, four-cases tab. |
| 7 | `README.md` | Four required cases with real screenshots and real numbers. |

**Stop adding features at milestone 6.** Remaining time goes to the README and
the 3-minute video, which are graded artifacts in their own right.

---

## 7. Environment

- LLM: **Groq or OpenRouter free tier.** Model name from `LLM_MODEL` env var —
  never hardcode a model string, free-tier model availability changes.
- Rate limits are tight. One LLM call **per page**, batched, small schema.
  Never one call per fact or per candidate pair. Target < 100 calls for a full
  six-PDF ingest.
- `python-dotenv`; `.env` in `.gitignore`. **No credentials in the repo.**
- Deps: `pdfplumber`, `fastapi`, `uvicorn`, `python-multipart`, `httpx`,
  `python-dotenv`, `pytest`. Keep it small; every dep is a thing that can fail
  on the grader's machine.

---

## 8. Anti-patterns — refuse these

- Rewriting `models.py` / `comparability.py` / `adjudicate.py` "more simply".
- Asking an LLM "are these two facts contradictory?" without running the gate.
- Embeddings + cosine similarity as the primary linking mechanism. Similarity
  finds *related* text; it cannot decide comparability.
- A Neo4j/graph visualisation as the headline output — the brief explicitly
  says a graph or visualisation alone is not the solution.
- Extracting thousands of trivial facts. **Precision beats recall here.** Aim
  for tens of meaningful facts per document, not hundreds of noisy ones.
- Silently swallowing exceptions during ingest. Log to `rejected_facts.jsonl`;
  failures are evidence, and one of them is a required deliverable.
- Long inline explanations in chat when the change belongs in a file.

## 9. Commit discipline

One commit per milestone, imperative subject, body stating the trade-off made.
The graders read the git log.

---

## 10. Budget, measured (from the milestone-1 inventory)

Six PDFs: **511 pages, 1.68M chars (~420K tokens), 1,150 tables.**
One LLM call per page would cost 511 calls — five times over budget. Page
selection is therefore an architectural component, not an optimisation.

| Doc | Pages | chars/pg | tables/pg | Budget |
|---|---|---|---|---|
| delhivery prospectus 2022 | 100 | 3,101 | 2.06 | 15 |
| delhivery annual report FY24 | 100 | 6,011 | 3.43 | 20 |
| delhivery Q4 FY24 deck | 27 | 803 | 2.15 | all (batched) |
| economic survey 2024-25 | 89 | 2,252 | 2.42 | 12 |
| RBI annual report 2024-25 | 100 | 2,679 | 3.13 | 15 |
| IMF Article IV 2025 | 95 | 2,952 | 0.16 ⚠ | 12 |

**Target: ~101 pages selected → ~59 calls after 6K-char batching → ~73K input
tokens.** If a run projects materially more, the triage filters are not working.

Known issues from the inventory:

- **IMF tables under-detected** (0.16/page vs ~2-3 elsewhere). Borderless
  tables missed by the lattice strategy. Text-strategy fallback required —
  IMF statistical annexes are the primary source for macro Case 2.
- **No scale phrases** in the earnings deck or Economic Survey. Percentages are
  self-scaling so macro is fine; deck chart-callouts are bare numbers and must
  be marked `scale_unknown` rather than guessed. This is honest Case 4 material.
- **One image-only page** (IMF p.1, scanned cover). Skip it. No OCR needed
  anywhere in this corpus — do not add an OCR dependency.
- **Parse takes ~150s.** Cache parsed Documents to `cache/parsed/`. The demo
  cannot contain 150 seconds of dead air.

## 11. Deduplication (store invariant)

Within one document, the same figure will appear on several pages — the
Delhivery FY24 revenue will be in highlights, MD&A, the board report and the
financial statements. These are **one fact with several evidence spans**, not
four facts.

Dedupe key: `subject + measure + qualifiers + normalised value`. Merge by
appending to an `evidence: list[Evidence]` and taking max confidence.

Without this, the relation graph fills with self-corroborations from a single
source and the genuine cross-document signal is buried. Corroboration is only
meaningful across `doc_id` boundaries — weight same-document agreement at zero.

## 12. Extraction contract (binding for milestone 3)

**The model reads; Python reasons.** The LLM emits raw strings only —
`value_raw`, `period_raw`, `scope_raw`, `issuer_raw`, `verbatim_quote`. It must
never compute, convert, scale or normalise a value. All conversion runs through
`normalize.parse_quantity/parse_period/detect_scope`, which are deterministic,
tested and inspectable. A free-tier model asked to do arithmetic will
occasionally turn 120.4 crore into 120,400,000, and that error is invisible
downstream.

**Document metadata pass.** One call per document extracts its default
reporting context — issuer, reporting entity, document type, default currency,
scale, scope and period. Facts inherit these and override per-fact. Real
filings establish a reporting context once and omit it thereafter; this mirrors
that, and it recovers the scale for the Q4 earnings deck whose slides state
bare numbers with no scale phrase anywhere.

**`issuer` is a first-class qualifier.** Same issuer + later vintage +
projection → `SUPERSEDES`. Different issuer + same period + projection →
`APPARENT_CONFLICT`, reason `forecast_disagreement`. Different issuer + same
period + asserted historical → genuine `CONTRADICTS`. Forecast disagreement
between the IMF and the RBI is not a factual contradiction, and a system that
reports it as one has misunderstood the documents.

**Payload cap: 8,000 chars per call.** Over that, split into prose and table
calls. Never truncate — truncation drops facts with no trace, which is the one
failure mode the rejected-facts log cannot catch.

## 13. Issuer wiring — what actually happened (record, not aspiration)

M3's extraction correctly computed `issuer` per fact (with document-default
fallback from the metadata pass) but had nowhere in the schema to attach it —
`Qualifiers` had no `issuer` field, and neither `comparability.py` nor
`adjudicate.py` referenced it. The gate would have let an IMF projection and an
RBI projection for the same period through as fully comparable and diffed them
as a raw mismatch -> CONTRADICTS. That is precisely the failure the section 2
worked example exists to prevent, and it would have broken silently — no test
failure, just a wrong verdict on the strongest demo case.

Fixed in milestone 3.5, narrowly: `Qualifiers.issuer`, an
`INCOMPARABLE_ISSUER` verdict in the gate (only when at least one side is
`ESTIMATED`/`PROJECTED` — two differing ASSERTED historical claims from
different issuers are a genuine contradiction candidate and must NOT be
short-circuited), and a `forecast_disagreement` reason code in the adjudicator.
`GateResult.cross_issuer` records the fact for the explanation text even when
the verdict proceeds to normal value comparison.

**Lesson for future milestones:** when a prompt says a module computes a value
"with fallback," verify it is actually attached to the object that survives —
not merely present in a local variable at extraction time. Re-check this class
of gap before M4, since resolve.py/store.py will introduce more derived fields
(canonical subject/measure ids) that are equally easy to compute and lose.

## 14. Two real gaps surfaced by 3.5 verification — required in milestone 4

Milestone 3.5 tried to verify its fix against the actual extracted RBI-vs-IMF
GDP pair, not a synthetic stand-in, and found two pre-existing gaps this
exposed rather than caused:

**(a) Issuer strings are not canonicalized.** The real facts carry
`"International Monetary Fund"` and `"IMF staff"` as distinct issuer strings —
same institution, no resolver between them. A bulk scan for cross-issuer pairs
found 9 candidates that were all this single collapsed-string problem, not 9
real disagreements. `resolve.py` must canonicalize issuer the same way
`normalize_entity()` canonicalizes company names, via a small declarative alias
table (extend at runtime, no filename-specific rules) covering at minimum: IMF
/ IMF staff / International Monetary Fund; RBI / Reserve Bank of India;
Government of India / GoI / Union Government.

**(b) Column-header units are not threaded into value parsing.** RBI's GDP
figure extracted as `value_raw="6.5"` with the `%` living only in the table's
column header, never folded into the row's context — so `parse_quantity`
returned `unit="count"` instead of `unit="percent"`. This is the same class of
problem `scale_context` already solves for currency scale, just not yet applied
to per-column units. Fix in extract.py before resolve.py: when serialising a
table block, thread the column header text into the context passed to
`parse_quantity` for every cell in that column, not just the table-level
`scale_context`.

**Milestone 4 is not complete until the real RBI-vs-IMF real-GDP-growth pair
resolves correctly end-to-end** — through resolve.py's clustering, the gate,
and the adjudicator — with matching issuer strings and a `percent` unit on
both sides. The clean synthetic example in section 2 passing is not sufficient
proof; the real pair is the actual bar.

## 15. Milestone 4 results (record)

Real, verified end-to-end (not synthetic): RBI real GDP growth 6.5% and IMF
real GDP growth 6.2% for the same period land in cluster `india::real_gdp_growth`
and correctly resolve to `apparent_conflict / forecast_disagreement`. A
same-measure pair within tolerance (6.6 vs 6.5) correctly resolves to
`corroborates` instead — the system distinguishes real disagreement from
noise on the same real data, not just on a clean synthetic pair.

Getting RBI and IMF into the same cluster needed one more alias layer beyond
issuer/measure: RBI's statistical-annex rows carry the indicator name as the
row label with a generic measure ("% change"), while IMF's prose gives an
explicit subject and measure. `MACRO_INDICATOR_SUBJECTS` is a declarative
table (9 indicators) bridging that shape — same alias-table pattern as
company-suffix and issuer normalisation, not a one-off special case.

Final corpus numbers: 401 canonical subjects, 328 canonical measures, 39
canonical issuers, 686 facts, 552 clusters (89 with ≥2 facts), 27 relations
(18 apparent_conflict, 6 contradicts, 2 corroborates, 1 aggregates_into).

**Known limitation, accepted, not worked around:** `Fact.compute_id()` hashes
subject/measure/value/doc_id/page/char_start but not qualifiers. Two facts at
the same span differing only in qualifiers collide (~0.3% loss, 688→686
facts). `models.py` is protected and the loss is small — documented as a
README limitation rather than patched around.

**Evidence access contract (binding from milestone 4.5 on):**
`Fact.evidence` is a single required field; merged evidence from deduplication
lives in `Store.extra_evidence`. `Store.get_evidence(fact_id)` is the only
sanctioned read path for a fact's full evidence — api.py and the frontend must
use it, never read `fact.evidence` directly expecting completeness.

## 16. Measure-resolution budget: settled, do not revisit

Raising the measure-canonicalization LLM budget from 15 to 60 produced 10
genuine merges but only **+1 net relation** (27 → 28). The reason is
structural, not tunable: most merged measures are phrasing variants *within a
single document* (prospectus line items), and same-document corroboration is
weighted zero by design. Additional budget buys merges that cannot produce
cross-document relations.

Settled at 60. This is a measured diminishing-return result, not an untested
assumption — report it as such.

## 17. Unverifiable periods (milestone 5, step 0a)

`compare_periods()` returns `UNKNOWN` when either fact has no period, and the
gate previously fell through to `COMPARABLE`. That is wrong on this system's
own terms: unknown is not compatible, it is *unverifiable*. Asserting a
like-for-like contradiction without having established like-for-like is the
precise overreach section 2 exists to prevent.

Handled as a confidence signal rather than a filter: relation type is
preserved, confidence is halved, `reason_code` gains a `_period_unverified`
suffix, and the explanation states plainly that one source gives no reporting
period, so the difference may reflect an extraction gap rather than a real
disagreement. Relations sort by confidence descending everywhere, so
genuinely-qualified contradictions rank above unverifiable ones.

4 of the 5 CONTRADICTS relations in the corpus fell in this bucket at the time
this was written. After milestone 5 fully landed (section 18), the corpus
regenerated with 8 CONTRADICTS relations, and **all 8** fall in this bucket —
none of the pipeline's raw CONTRADICTS calls, in this corpus, currently rest
on a period verified on both sides. Surfacing that honestly, ranked by
confidence, is a stronger result than either suppressing them or presenting
all eight as equally solid.

---

## 18. Milestone 5 results (record)

Three relation-quality fixes landed in `adjudicate.py`/`resolve.py`, verified
against a full offline re-ingest of all six PDFs (`network_calls=0`), then
`api.py` was built on top. 113 tests green (96 pre-existing + 17 new in
`tests/test_api.py`).

**0a — period-unverified penalty (section 17).** Confirmed on the real,
regenerated corpus: all 8 CONTRADICTS relations carry a missing period on at
least one side and are correctly flagged `..._period_unverified` with
confidence roughly halved (0.455–0.475). The genuine cross-issuer forecast
disagreements (RBI 6.5% vs IMF 7.8%/6.2% real GDP growth) are unaffected —
they resolve via the `INCOMPARABLE_ISSUER` gate branch to
`APPARENT_CONFLICT`/`forecast_disagreement` at confidence 0.85, a different
code path from the period-unverified check.

**0b — reason_code mislabel.** A `CORROBORATES` relation whose gate verdict
was `INCOMPARABLE_*` used to inherit that verdict's `reason_code` verbatim —
e.g. a values-match relation labelled `forecast_disagreement`, which reads as
self-contradictory. Now prefixed `value_match_despite_<original_code>`.
Confirmed on the real RBI/IMF value-match pair (both read
`value_match_despite_forecast_disagreement`).

**0c — subject/issuer leakage.** 5 facts in the corpus carried
`issuer="India"` — all IMF Article IV claims *about* India's current account
deficit or NIIP, not claims made *by* India (a country cannot issue an IMF
report about itself). This was subject-field leakage into the issuer slot,
not a genuine second institution. Added `SUBJECT_NOT_ISSUER` to `resolve.py`
— a small declarative guard set (same pattern as `ISSUER_ALIASES`), checked
in `resolve_issuer()` before the alias table, that **nulls** the issuer
rather than canonicalizing it: a null issuer is honest, a wrong one is not.
After the fix, the flagged CA-deficit pair (IMF 0.2% vs 0.6% of GDP) shows
`qualifier_diff: {"issuer": ["IMF", null]}` — the false cross-issuer signal
is gone, and the pair now resolves purely on the normal COMPARABLE path
(still period-unverified, since one side has no parsed period; genuinely
CONTRADICTS, not an artifact of the old issuer bug).

**`api.py`** — thin FastAPI layer, zero new domain logic, all 9 required
endpoints: `POST /ingest`, `GET /documents`, `GET /facts`,
`GET /facts/{id}`, `GET /clusters`, `GET /relations` (default-sorted by
confidence descending), `GET /relations/{id}`, `GET /stats`,
`GET /page-image/{doc_id}/{page}`. Two design decisions worth recording:

- `Relation` (models.py, protected) has no id field, so `/relations/{id}`
  synthesizes a stable one as `sha1(source_fact_id|target_fact_id|relation)`
  rather than an array index, which would break across ingests.
- `doc_id` (parse.py, protected) hashes the file's *absolute path*, not its
  bytes, so re-uploading a byte-identical PDF through a different path (e.g.
  re-uploading a corpus PDF via the API) would otherwise mint a fresh doc_id
  and silently double-ingest it. `/ingest` checks the upload's content hash
  against every already-ingested document's source file *before* writing it
  or calling `Store.ingest()`, and short-circuits with
  `already_ingested: true` on a match — no LLM calls, no new facts, verified
  by `test_ingest_already_ingested_doc_is_idempotent_not_double_counted`.

`GET /page-image` renders via `pdfplumber`'s `page.to_image(resolution=110)`,
cached to `cache/pages/{doc_id}_{page}.png`; `GET /relations/{id}` calls the
pure `comparability.gate()` a second time on read (cheap, side-effect-free)
to surface the gate verdict, since `Relation` doesn't persist it.

Frontend explicitly not built this milestone, per instruction.

---

## 19. Period inheritance — investigated, fix built and verified, reverted (timeboxed)

Hypothesis going in: facts inherit `default_scale`/`default_scope`/`default_currency`
from the document metadata pass but not `default_period`, which is why all 8
CONTRADICTS relations were `period_unverified`. **Verified false as literally
stated** — `extract.py` already inherits `default_period` (the code existed
before this investigation). The real cause is one level deeper:
`normalize.parse_period()` (protected) requires an explicit `"FY"`/`"financial
year"` marker, and two of three documents whose facts drive the 8 CONTRADICTS
state their default period as a bare year-range or a non-period string:
RBI's is `"2024-25"` (no `FY` marker — silently fails to parse), and IMF's is
`"2025 Article IV Consultation"` — not a period at all, it's the report title,
so nothing *should* be inherited there.

Built and verified end-to-end: `Qualifiers.period_inherited` (models.py,
additive, same pattern as `issuer` in 3.5), a `_coerce_default_period()`
fallback in extract.py that retries a bare `\d{4}-\d{2,4}` range with an `FY`
prefix (same protected-file-workaround pattern as `_ensure_percent_word`),
and a 0.75× confidence penalty + `_period_inherited` reason_code suffix in
adjudicate.py. Result on the real corpus: 208 facts gained an honestly-flagged
inherited period (mostly the Delhivery prospectus, whose default period parses
directly). **Zero of the 8 CONTRADICTS relations gained a period on both
sides** — IMF's default period correctly stays unparseable (it isn't one),
and the one case that did improve (the ESOPs pair, via the Delhivery annual
report's `"Annual Report 2023-24"` default) reclassified out of CONTRADICTS
entirely, into `AGGREGATES_INTO`/`period_subsumption` — a *better* outcome
architecturally, but not the literal bar set for this experiment.

Per the explicit hard-abort rule, fully reverted (all four touched files:
models.py, extract.py, adjudicate.py, store.py) rather than tuned further.
Confirmed clean via `grep` (zero remaining references) and a full offline
re-ingest reproducing the exact pre-experiment state: 686 facts, 28 relations,
8 CONTRADICTS. **The corpus's honest state stands**: no CONTRADICTS relation
currently rests on a period verified on both sides — that is itself the
Case 2 result, ranked below the corroboration/apparent-conflict/supersession
cases the corpus does support cleanly.

## 20. Frontend (milestone 6) — evidence-viewer bbox scaling, verified

Single file, vanilla JS, dark theme, served by `api.py`'s static mount —
`uvicorn api:app` is the only command a grader runs. Three api.py additions
were needed to support it (extending api.py, not a fact_layer module):
`page_width`/`page_height` (PDF points) on every evidence span, a
`clusters_touched` count on `/ingest`'s response for the incrementality
line, and `GET /rejected-facts` for the Case 4 tab.

The evidence-bbox overlay is positioned as a **percentage** of the rendered
image (`bbox / page_dimension`), not fixed pixels — this is mathematically
identical to scaling by `image_width / page_width` (confirmed: a real
evidence span's rendered PNG was 1819×1287px against an expected
1819×1286px at `resolution=110`, i.e. exactly `page_dimension × 110/72`) but
stays correct regardless of how large the `<img>` is displayed at, with no
`naturalWidth`-load race. Verified pixel-perfect on a real fact (ESOPs,
`f_8116d4364169`, delhivery annual report p.43): cropping the rendered page
at the bbox's exact coordinates shows the highlight landing precisely on the
table row `"No. of ESOPs vested as on - 676,000 - 250,000"`, zero offset.

Upload flow verified two ways, both offline: (1) re-uploading an
already-ingested corpus PDF correctly short-circuits via content-hash match
(`already_ingested: true`, zero LLM calls, zero new facts — added in this
step, since `doc_id` hashes the upload's *path*, not bytes, so a byte-
identical re-upload through `data/uploads/` would otherwise silently mint a
fresh doc_id); (2) a genuinely new synthetic PDF (hand-built, not from the
corpus) correctly runs the full parse → triage → extract pipeline and fails
*cleanly* at the LLM call with an actionable message, since this environment
has no live key by design (`LLM_MODE=replay`) — added `except LLMError`
handling to `/ingest` (previously an unhandled exception → opaque 500) and a
matching `r.ok` check in the frontend, so the failure surfaces as a readable
error in the upload status line, not a crash. No `location.reload()` /
`location.href=` / form-submit-navigation anywhere in the frontend JS
(confirmed via grep) — new facts/relations from a successful ingest are
spliced into the DOM in place.

**Known gap, not closed this milestone:** a live, network-enabled "upload a
genuinely new 7th document and see new facts render" pass was not run — doing
so would spend real LLM API quota, which wasn't authorized for this step. The
mechanism is proven correct up to and including the LLM call; only the live
call itself is unverified.