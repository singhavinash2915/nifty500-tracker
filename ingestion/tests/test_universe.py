"""Parser assertions matter more than usual here: a silent layout change on
NSE's side would otherwise write 500 rows of nulls into the universe table."""

import pytest

from n500.sources.universe import UniverseParseError, parse_csv, week_start
from datetime import date

HEADER = "Company Name,Industry,Symbol,Series,ISIN Code\n"
GOOD_ROW = "HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034\n"


def make_csv(rows: int = 500, *, body: str = GOOD_ROW) -> str:
    lines = []
    for i in range(rows):
        lines.append(body.replace("HDFCBANK", f"SYM{i:04d}").replace(
            "INE040A01034", f"INE040A{i:05d}"))
    return HEADER + "".join(lines)


def test_parses_a_full_list():
    parsed = parse_csv(make_csv())
    assert len(parsed) == 500
    assert parsed[0].sector == "Financial Services"
    assert parsed[0].series == "EQ"


def test_quoted_company_names_with_commas_survive():
    text = HEADER + '"Bajaj Finance, Ltd.",Financial Services,BAJFINANCE,EQ,INE296A01024\n'
    text += make_csv(499)[len(HEADER):]
    parsed = parse_csv(text)
    assert parsed[0].company_name == "Bajaj Finance, Ltd."


def test_rejects_a_changed_header():
    text = "Name,Sector,Ticker\nfoo,bar,baz\n"
    with pytest.raises(UniverseParseError, match="unexpected header"):
        parse_csv(text)


def test_rejects_a_truncated_list():
    with pytest.raises(UniverseParseError, match="expected 450-520"):
        parse_csv(make_csv(12))


def test_rejects_a_bad_isin():
    text = HEADER + "HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,NOTANISIN\n"
    with pytest.raises(UniverseParseError, match="bad ISIN"):
        parse_csv(text)


def test_rejects_a_missing_industry():
    text = HEADER + "HDFC Bank Ltd.,,HDFCBANK,EQ,INE040A01034\n"
    with pytest.raises(UniverseParseError, match="no industry"):
        parse_csv(text)


def test_rejects_duplicate_symbols():
    text = HEADER + GOOD_ROW + GOOD_ROW + make_csv(498)[len(HEADER):]
    with pytest.raises(UniverseParseError, match="duplicate symbol"):
        parse_csv(text)


@pytest.mark.parametrize(
    "given,expected",
    [
        (date(2026, 9, 3), date(2026, 8, 31)),   # Thursday -> Monday
        (date(2026, 8, 31), date(2026, 8, 31)),  # Monday -> itself
        (date(2026, 9, 6), date(2026, 8, 31)),   # Sunday -> that week's Monday
    ],
)
def test_week_start(given, expected):
    assert week_start(given) == expected
