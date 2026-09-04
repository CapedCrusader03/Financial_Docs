from decimal import Decimal

from app.narrative import extract_sections, header_aware_chunks
from app.pdf_fallback import _parse_cell, _unit_from_caption
from app.query import AGGREGATE_EXPRESSIONS


def test_narrative_excludes_html_tables_before_chunking() -> None:
    html = (
        "<html><body><div>Item 1A. Risk Factors</div><p>"
        + "Supply concentration and competition may materially affect our results. " * 12
        + "</p><table><tr><td>Revenue</td><td>999999</td></tr></table></body></html>"
    )
    sections = extract_sections(html)
    chunks = [chunk for section in sections for chunk in header_aware_chunks(section, max_chars=500)]
    assert chunks
    assert all("999999" not in chunk.text for chunk in chunks)


def test_pdf_cell_uses_caption_scale_and_strips_footnote_marker() -> None:
    unit, multiplier = _unit_from_caption("Amounts in millions, except per share data")
    assert unit == "USD (millions)"
    assert _parse_cell("(1,234)*", multiplier) == Decimal("-1234000000")


def test_all_supported_numeric_operations_are_sql_templates() -> None:
    assert {"value", "sum", "average", "minimum", "maximum", "change", "percent_change"} == set(AGGREGATE_EXPRESSIONS)
    assert "SUM(value)" in AGGREGATE_EXPRESSIONS["sum"]
