"""
Deterministic parser/verification benchmark suite (A9/A10).

~30 small, self-contained fixtures spanning every category this
correctness-hardening pass actually built or relies on: numeric parsing
edge cases, table structure, ambiguity handling, layout, and diagnostics.
Each case is a pure function returning True/False; the runner aggregates
pass/fail counts per category.

This is a REGRESSION SIGNAL, not a claimed accuracy benchmark. 30 hand-
built fixtures cannot support a statistically meaningful "N% accurate"
claim about PDF parsing in general — report `passed/total` per category
and leave it at that. No fixture here encodes an LLM's expected output;
every assertion is about deterministic document structure or evidence
preservation, things Python computes the same way every time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .diagnostics import compute_document_diagnostics, compute_page_diagnostics, detect_repeated_edge_lines
from .layout import build_page_layout
from .normalize import parse_period, parse_quantity
from .parse import Document, Page, Table, Word
from .table_structure import find_cells_matching_value, get_cell_context, locate_cell, structure_table
from .value_verify import ValueVerificationStatus, verify_value


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    category: str
    description: str
    check: Callable[[], bool]


@dataclass(frozen=True)
class BenchmarkResult:
    case_id: str
    category: str
    description: str
    passed: bool
    error: str = ""


@dataclass
class BenchmarkReport:
    results: list[BenchmarkResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    def by_category(self) -> dict[str, tuple[int, int]]:
        """category -> (passed, total), insertion-ordered."""
        out: dict[str, list[int]] = {}
        for r in self.results:
            bucket = out.setdefault(r.category, [0, 0])
            bucket[1] += 1
            if r.passed:
                bucket[0] += 1
        return {k: tuple(v) for k, v in out.items()}


def run_benchmark(cases: list[BenchmarkCase]) -> BenchmarkReport:
    results = []
    for case in cases:
        try:
            ok = bool(case.check())
            results.append(BenchmarkResult(case.id, case.category, case.description, ok))
        except Exception as e:
            results.append(BenchmarkResult(case.id, case.category, case.description, False, str(e)))
    return BenchmarkReport(results)


def _word(text, x0, top, x1, bottom):
    return Word(text=text, char_start=0, char_end=len(text), bbox=(x0, top, x1, bottom))


def _page_from_words(words, tables=None):
    return Page(page_no=1, text=" ".join(w.text for w in words), words=words, tables=tables or [])


def _revenue_table():
    return Table(page_no=1, bbox=(0, 0, 200, 100),
                 rows=[["", "FY2024", "FY2025"], ["India", "500", "620"], ["US", "300", "350"]],
                 caption="Revenue", scale_context="USD million")


# --------------------------------------------------------------------------
# The 30 cases
# --------------------------------------------------------------------------

def _build_cases() -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []

    def add(id_, category, description, check):
        cases.append(BenchmarkCase(id_, category, description, check))

    # 1. normal paragraph
    add("01_normal_paragraph", "numeric_parsing", "plain sentence with a number",
        lambda: parse_quantity("120.4", "Revenue was 120.4 crore for the year").value == 1204000000)

    # 2. Indian number
    add("02_indian_number", "numeric_parsing", "Indian digit grouping",
        lambda: parse_quantity("1,20,41,23,456", "").value == int("1,20,41,23,456".replace(",", "")))

    # 3. negative number
    add("03_negative_number", "numeric_parsing", "ASCII negative sign",
        lambda: parse_quantity("-120.4", "").value < 0)

    # 4. percentage
    add("04_percentage", "numeric_parsing", "percent sign detected",
        lambda: parse_quantity("6.5%", "").unit == "percent")

    # 5. currency
    add("05_currency", "numeric_parsing", "currency symbol detected",
        lambda: parse_quantity("120.4", "₹120.4 crore").currency == "INR")

    # 6. unit in heading
    add("06_unit_in_heading", "unit_context", "scale phrase in a table caption",
        lambda: structure_table(_revenue_table()).unit == "USD million")

    # 7. unit in table header (column-header % marker, folded via context —
    # parse_quantity() checks for the literal '%' inside its own `text` arg
    # or the WORD "percent" in the combined blob, never a bare '%' living
    # only in context; extract.py's _ensure_percent_word() bridges that,
    # spelling the symbol out before it reaches parse_quantity)
    from .extract import _ensure_percent_word
    add("07_unit_in_table_header", "unit_context", "% marker folded into context",
        lambda: parse_quantity("6.5", _ensure_percent_word("% change")).unit == "percent")

    # 8. unit in caption (detect_scale_context direct)
    from .parse import detect_scale_context
    add("08_unit_in_caption", "unit_context", "detect_scale_context on a caption phrase",
        lambda: detect_scale_context("(Rs. in lakhs)") != "")

    # 9. period in column header
    add("09_period_in_column_header", "period_context", "FY2024 recognised as a period",
        lambda: get_cell_context(structure_table(_revenue_table()), 0, 1).period_label is not None)

    # 10. multiple periods
    def _multiple_periods():
        st = structure_table(_revenue_table())
        return (get_cell_context(st, 0, 1).period_label is not None
                and get_cell_context(st, 0, 2).period_label is not None)
    add("10_multiple_periods", "period_context", "two distinct FY columns both recognised", _multiple_periods)

    # 11. simple table
    def _simple_table():
        st = structure_table(_revenue_table())
        return st.column_headers == ["", "FY2024", "FY2025"] and st.row_labels == ["India", "US"]
    add("11_simple_table", "table_structure", "headers and row labels extracted", _simple_table)

    # 12. multi-row header
    def _multi_row_header():
        t = Table(page_no=1, bbox=(0, 0, 10, 10),
                  rows=[["", "2024", "", "2025", ""], ["", "Actual", "Forecast", "Actual", "Forecast"],
                        ["India", "500", "540", "620", "650"]])
        st = structure_table(t)
        return st.header_row_count == 2 and st.column_headers[1] == "2024 Actual"
    add("12_multi_row_header", "table_structure", "genuine 2-level header combined correctly", _multi_row_header)

    # 13. row label
    add("13_row_label", "table_structure", "row label reaches every cell in its row",
        lambda: get_cell_context(structure_table(_revenue_table()), 1, 2).row_label == "US")

    # 14. ambiguous table (numeric first column)
    def _ambiguous_table():
        t = Table(page_no=1, bbox=(0, 0, 10, 10),
                  rows=[["Year", "Revenue"], ["2024", "500"], ["2025", "620"]])
        st = structure_table(t)
        return st.row_label_confident is False
    add("14_ambiguous_table", "ambiguity", "numeric first column disables row-label confidence", _ambiguous_table)

    # 15. multiple numbers in one quote
    def _multi_number_quote():
        q = parse_quantity("120.4", "")
        r = verify_value("120.4", "Revenue 120.4 145.9 132.1", q, "")
        return r.status is ValueVerificationStatus.UNVERIFIED
    add("15_multi_number_quote", "ambiguity", "quote with 3 candidates stays unverified", _multi_number_quote)

    # 16. footnote marker (trailing marker doesn't corrupt the number)
    from decimal import Decimal
    add("16_footnote_marker", "numeric_parsing", "trailing footnote marker doesn't corrupt the number",
        lambda: parse_quantity("120.4*", "").value == Decimal("120.4"))

    # 17. repeated header
    def _repeated_header():
        pages = [Page(page_no=i, text=f"SUPER COMPANY LIMITED\nBody {i}.",
                       words=[_word("x", 0, 0, 1, 1)]) for i in range(1, 6)]
        doc = Document(doc_id="d", filename="f.pdf", n_pages=5, pages=pages)
        headers, _ = detect_repeated_edge_lines(doc)
        return any("SUPER COMPANY LIMITED" in h for h in headers)
    add("17_repeated_header", "diagnostics", "repeated header line detected across pages", _repeated_header)

    # 18. repeated footer
    def _repeated_footer():
        pages = [Page(page_no=i, text=f"Body {i}.\nSUPER CO\nPage {i}",
                       words=[_word("x", 0, 0, 1, 1)]) for i in range(1, 6)]
        doc = Document(doc_id="d", filename="f.pdf", n_pages=5, pages=pages)
        _, footers = detect_repeated_edge_lines(doc)
        return any("Page" in f for f in footers)
    add("18_repeated_footer", "diagnostics", "paginated footer pattern detected", _repeated_footer)

    # 19. sparse page
    add("19_sparse_page", "diagnostics", "few-word real page flagged sparse, not image_only",
        lambda: (lambda d: d.image_only is False and any("sparse" in w for w in d.warnings))(
            compute_page_diagnostics(_page_from_words([_word("Two", 0, 0, 1, 1), _word("words", 2, 0, 3, 1)]))))

    # 20. image-only page
    add("20_image_only_page", "diagnostics", "zero-text page flagged image_only",
        lambda: compute_page_diagnostics(Page(page_no=1, text="", image_only=True)).image_only is True)

    # 21. two-column page (confident, synthetic)
    def _two_column():
        words = []
        for i, top in enumerate(range(100, 100 + 20 * 12, 20)):
            words.append(_word(f"L{i}a", 50, top, 90, top + 10))
            words.append(_word(f"L{i}b", 95, top, 140, top + 10))
            words.append(_word(f"R{i}a", 300, top, 340, top + 10))
            words.append(_word(f"R{i}b", 345, top, 390, top + 10))
        layout = build_page_layout(_page_from_words(words))
        return layout.multi_column_detected is True
    add("21_two_column_page", "layout", "confidently repeated gutter detected as columns", _two_column)

    # 22. table + paragraph
    def _table_then_paragraph():
        table = Table(page_no=1, bbox=(40, 90, 200, 130), rows=[["a", "1"]])
        words = [_word("cell", 50, 100, 90, 110),
                 _word("Para", 50, 300, 90, 310), _word("text.", 95, 300, 130, 310)]
        layout = build_page_layout(_page_from_words(words, tables=[table]))
        types = [b.block_type for b in layout.blocks]
        return len(types) == 2 and types[0] == "table_adjacent" and types[1] != "table_adjacent"
    add("22_table_then_paragraph", "layout", "table region followed by ordinary text", _table_then_paragraph)

    # 23. paragraph + table
    def _paragraph_then_table():
        table = Table(page_no=1, bbox=(40, 290, 200, 330), rows=[["a", "1"]])
        words = [_word("Para", 50, 100, 90, 110), _word("text.", 95, 100, 130, 110),
                 _word("cell", 50, 300, 90, 310)]
        layout = build_page_layout(_page_from_words(words, tables=[table]))
        return layout.blocks[0].block_type != "table_adjacent" and layout.blocks[-1].block_type == "table_adjacent"
    add("23_paragraph_then_table", "layout", "ordinary text followed by a table region", _paragraph_then_table)

    # 24. adjacent tables with different units
    def _adjacent_units():
        a = structure_table(Table(page_no=1, bbox=(0, 0, 10, 10), rows=[["India", "500"]],
                                   caption="Revenue (₹ crore)", scale_context="₹ crore"))
        b = structure_table(Table(page_no=1, bbox=(0, 50, 10, 60), rows=[["India", "6.0"]],
                                   caption="Growth (USD million)", scale_context="USD million"))
        return a.unit != b.unit
    add("24_adjacent_table_units", "unit_context", "adjacent tables never share a unit", _adjacent_units)

    # 25. number near unrelated number (period number correctly excluded)
    def _number_near_unrelated():
        q = parse_quantity("8,141.71", "revenue from operations")
        r = verify_value("8,141.71", "Revenue from operations was Rs. 8,141.71 Cr for FY2024.", q, "revenue from operations")
        return r.status is ValueVerificationStatus.UNVERIFIED and "8141.71" in (r.extracted or "")
    add("25_number_near_unrelated", "ambiguity", "FY-year digits not mistaken for a second candidate", _number_near_unrelated)

    # 26. negative percentage with Unicode minus
    def _unicode_minus():
        q = parse_quantity("−0.4 percent", "")
        r = verify_value("−0.4 percent", "projected at −0.4 percent of GDP", q, "")
        return r.status is ValueVerificationStatus.VERIFIED
    add("26_unicode_minus_percentage", "numeric_parsing", "Unicode minus sign preserved through verification", _unicode_minus)

    # 27. table with missing cell
    def _missing_cell():
        t = Table(page_no=1, bbox=(0, 0, 10, 10), rows=[["", "FY2024", "FY2025"], ["India", None, "620"]])
        st = structure_table(t)
        return get_cell_context(st, 0, 1).text == ""
    add("27_missing_cell", "table_structure", "missing cell degrades gracefully, no crash", _missing_cell)

    # 28. merged-cell / imperfect (ragged rows) table
    def _ragged_table():
        t = Table(page_no=1, bbox=(0, 0, 10, 10),
                  rows=[["", "FY2024", "FY2025"], ["India", "500"], ["US", "300", "350", "extra"]])
        st = structure_table(t)
        return len(st.cells) == 2
    add("28_imperfect_table", "table_structure", "ragged/malformed rows do not crash structuring", _ragged_table)

    # 29. long financial table
    def _long_table():
        rows = [["", "FY2024", "FY2025"]] + [[f"Segment {i}", str(i * 10), str(i * 11)] for i in range(40)]
        st = structure_table(Table(page_no=1, bbox=(0, 0, 10, 10), rows=rows, scale_context="₹ crore"))
        return len(st.cells) == 40 and st.row_labels[39] == "Segment 39"
    add("29_long_financial_table", "table_structure", "40-row table structures correctly", _long_table)

    # 30. evidence bbox mapping
    def _bbox_mapping():
        text = "Revenue was 120.4 crore"
        words, offset = [], 0
        for token in text.split():
            start = text.find(token, offset)
            words.append(Word(text=token, char_start=start, char_end=start + len(token), bbox=(0, 0, 10, 10)))
            offset = start + len(token)
        page = Page(page_no=1, text=text, words=words)
        w = words[2]   # "120.4"
        return page.bbox_for_span(w.char_start, w.char_end) == w.bbox
    add("30_evidence_bbox_mapping", "evidence", "word offset resolves to its own bbox", _bbox_mapping)

    return cases


BENCHMARK_CASES: list[BenchmarkCase] = _build_cases()


def print_report(report: BenchmarkReport) -> None:
    print(f"Overall: {report.passed}/{report.total}")
    for category, (passed, total) in report.by_category().items():
        print(f"  {category}: {passed}/{total}")
    failures = [r for r in report.results if not r.passed]
    if failures:
        print("\nFailures:")
        for r in failures:
            print(f"  [{r.category}] {r.id} — {r.description}" + (f" ({r.error})" if r.error else ""))


if __name__ == "__main__":
    print_report(run_benchmark(BENCHMARK_CASES))
