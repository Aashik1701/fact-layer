"""Tests for fact_layer/layout.py (A5 — deterministic layout reconstruction).

Synthetic Word/Page fixtures, built directly with explicit bboxes (not via
_make_page()'s char-offset helper, since layout tests care about spatial
coordinates, not char offsets). No PDF I/O.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.layout import build_page_layout
from fact_layer.parse import Page, Table, Word


def _word(text, x0, top, x1, bottom):
    return Word(text=text, char_start=0, char_end=len(text), bbox=(x0, top, x1, bottom))


def _page_from_words(words, page_no=1, tables=None):
    text = " ".join(w.text for w in words)
    return Page(page_no=page_no, text=text, words=words, tables=tables or [])


# --------------------------------------------------------------------------
# Word / line grouping
# --------------------------------------------------------------------------

def test_words_on_same_baseline_group_into_one_line():
    words = [_word("Revenue", 50, 100, 100, 110), _word("was", 105, 101, 130, 111),
              _word("120.4", 135, 100, 170, 110)]
    page = _page_from_words(words)
    layout = build_page_layout(page)
    assert len(layout.lines) == 1
    assert layout.lines[0].text == "Revenue was 120.4"


def test_words_on_different_baselines_are_separate_lines():
    words = [_word("Line", 50, 100, 80, 110), _word("one", 85, 100, 110, 110),
              _word("Line", 50, 200, 80, 210), _word("two", 85, 200, 110, 210)]
    page = _page_from_words(words)
    layout = build_page_layout(page)
    assert len(layout.lines) == 2
    assert layout.lines[0].text == "Line one"
    assert layout.lines[1].text == "Line two"


def test_line_preserves_left_to_right_word_order_regardless_of_input_order():
    words = [_word("third", 200, 100, 240, 110), _word("first", 50, 100, 90, 110),
              _word("second", 100, 100, 150, 110)]
    page = _page_from_words(words)
    layout = build_page_layout(page)
    assert layout.lines[0].text == "first second third"


# --------------------------------------------------------------------------
# Block grouping
# --------------------------------------------------------------------------

def test_close_lines_form_one_paragraph_block():
    words = []
    for i, top in enumerate([100, 114, 128, 142]):
        words.append(_word(f"line{i}", 50, top, 100, top + 10))
    page = _page_from_words(words)
    layout = build_page_layout(page)
    assert len(layout.blocks) == 1
    assert layout.blocks[0].block_type == "paragraph"


def test_a_wide_vertical_gap_starts_a_new_block():
    words = [
        _word("para1a", 50, 100, 100, 110), _word("para1b", 50, 114, 100, 124),
        _word("para2a", 50, 400, 100, 410), _word("para2b", 50, 414, 100, 424),
    ]
    page = _page_from_words(words)
    layout = build_page_layout(page)
    assert len(layout.blocks) == 2


def test_single_short_line_is_classified_as_heading():
    words = [_word(w, 50 + i * 40, 100, 50 + i * 40 + 35, 110) for i, w in enumerate(["Annual", "Report"])]
    page = _page_from_words(words)
    layout = build_page_layout(page)
    assert layout.blocks[0].block_type == "heading"


def test_lines_overlapping_a_table_bbox_are_table_adjacent():
    table = Table(page_no=1, bbox=(40, 90, 200, 130), rows=[["a", "1"]])
    words = [_word("cell", 50, 100, 90, 110)]
    page = _page_from_words(words, tables=[table])
    layout = build_page_layout(page)
    assert layout.blocks[0].block_type == "table_adjacent"


# --------------------------------------------------------------------------
# Reading order: single-column default, confident columns, uncertain fallback
# --------------------------------------------------------------------------

def test_single_column_page_reports_no_columns():
    words = []
    for i, top in enumerate(range(100, 100 + 20 * 12, 20)):
        words.append(_word(f"word{i}a", 50, top, 90, top + 10))
        words.append(_word(f"word{i}b", 95, top, 140, top + 10))
        words.append(_word(f"word{i}c", 145, top, 190, top + 10))
        words.append(_word(f"word{i}d", 195, top, 240, top + 10))
    page = _page_from_words(words)
    layout = build_page_layout(page)
    assert layout.multi_column_detected is False


def test_confidently_repeated_wide_gutter_is_detected_as_columns():
    """A genuine two-column layout: the SAME wide gap recurs at the SAME
    x-position across many lines — the spatial-stability signal a single
    incidental wide gap does not have."""
    words = []
    for i, top in enumerate(range(100, 100 + 20 * 12, 20)):
        words.append(_word(f"L{i}a", 50, top, 90, top + 10))
        words.append(_word(f"L{i}b", 95, top, 140, top + 10))
        words.append(_word(f"R{i}a", 300, top, 340, top + 10))   # gap of 160pt, same x every line
        words.append(_word(f"R{i}b", 345, top, 390, top + 10))
    page = _page_from_words(words)
    layout = build_page_layout(page)
    assert layout.multi_column_detected is True
    assert layout.column_boundary_x is not None


def test_isolated_wide_gap_on_one_line_does_not_trigger_columns():
    """A single line with a wide gap (e.g. a table row leaking into prose
    text) must not, by itself, be read as a confident column layout — this
    is exactly the false-positive mode the real corpus investigation found
    with a naive single-line gap heuristic."""
    words = []
    for i, top in enumerate(range(100, 100 + 20 * 12, 20)):
        if i == 3:
            words.append(_word("label", 50, top, 90, top + 10))
            words.append(_word("value", 300, top, 340, top + 10))
        else:
            words.append(_word(f"word{i}a", 50, top, 90, top + 10))
            words.append(_word(f"word{i}b", 95, top, 140, top + 10))
            words.append(_word(f"word{i}c", 145, top, 190, top + 10))
    page = _page_from_words(words)
    layout = build_page_layout(page)
    assert layout.multi_column_detected is False


# --------------------------------------------------------------------------
# Evidence preservation — layout never touches Page.text or char offsets
# --------------------------------------------------------------------------

def test_layout_reconstruction_does_not_mutate_page_text_or_offsets():
    words = [_word("Revenue", 50, 100, 100, 110), _word("120.4", 105, 100, 140, 110)]
    page = _page_from_words(words)
    text_before = page.text
    words_before = [(w.text, w.char_start, w.char_end) for w in page.words]

    build_page_layout(page)

    assert page.text == text_before
    assert [(w.text, w.char_start, w.char_end) for w in page.words] == words_before
