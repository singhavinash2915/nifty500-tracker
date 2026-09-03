"""Benchmark index archive parsing."""

from datetime import date

import pytest

from n500.sources import nse_index
from n500.sources.nse_index import IndexArchiveError

HEADER = (
    "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,"
    "Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),"
    "P/E,P/B,Div Yield\n"
)


def line(name, close="23222.8", open_="23176.95", pe="22.5"):
    return f"{name},02-09-2026,{open_},23226.2,23058.3,{close},-117.1,-.5,100,5000.5,{pe},3.9,1.2\n"


def make_file(n=60, include_benchmark=True):
    body = line("Nifty 500") if include_benchmark else ""
    body += "".join(line(f"Nifty Filler {i}") for i in range(n))
    return HEADER + body


def test_extracts_the_benchmark():
    quotes = nse_index.parse(make_file())
    q = quotes["Nifty 500"]
    assert q.close == pytest.approx(23222.8)
    assert q.date == date(2026, 9, 2)
    assert q.pe == pytest.approx(22.5)


def test_only_the_benchmark_is_kept_by_default():
    quotes = nse_index.parse(make_file())
    assert list(quotes) == ["Nifty 500"]


def test_rejects_a_file_without_the_benchmark():
    with pytest.raises(IndexArchiveError, match="not present"):
        nse_index.parse(make_file(include_benchmark=False))


def test_rejects_a_changed_layout():
    with pytest.raises(IndexArchiveError, match="missing columns"):
        nse_index.parse("Name,Value\nNifty 500,100\n")


def test_rejects_a_suspiciously_short_file():
    with pytest.raises(IndexArchiveError, match="expected at least"):
        nse_index.parse(HEADER + line("Nifty 500"))


def test_handles_nses_placeholder_dash_and_leading_dot_numbers():
    """NSE writes '-' for not-applicable and '.34' rather than '0.34'."""
    assert nse_index._number("-") is None
    assert nse_index._number("") is None
    assert nse_index._number(None) is None
    assert nse_index._number(".34") == pytest.approx(0.34)
    assert nse_index._number(" 1,234.5 ") == pytest.approx(1234.5)


def test_an_index_with_no_open_still_parses():
    text = HEADER + line("Nifty 500", open_="-") + "".join(
        line(f"F{i}") for i in range(60)
    )
    q = nse_index.parse(text)["Nifty 500"]
    assert q.open is None
    assert q.close == pytest.approx(23222.8)


@pytest.mark.parametrize("raw,expected", [
    ("02-09-2026", date(2026, 9, 2)),
    ("2026-09-02", date(2026, 9, 2)),
])
def test_date_formats(raw, expected):
    assert nse_index._parse_date(raw) == expected


def test_unparsable_date_raises():
    with pytest.raises(IndexArchiveError, match="unparsable"):
        nse_index._parse_date("not a date")
