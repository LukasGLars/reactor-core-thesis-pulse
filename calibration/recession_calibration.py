"""
calibration/recession_calibration.py

Data availability validation for recession indicator calibration pipeline.
Fetches all sources and prints a report. No analysis, no files saved.
"""

import io
import warnings
from datetime import datetime

import pandas as pd
import requests

warnings.filterwarnings("ignore")

FRED_SERIES = [
    ("USREC",         "https://fred.stlouisfed.org/graph/fredgraph.csv?id=USREC",         ""),
    ("T10Y3M",        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10Y3M",        ""),
    ("T10Y2Y",        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10Y2Y",        ""),
    ("DFII10",        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10",        ""),
    ("ICSA",          "https://fred.stlouisfed.org/graph/fredgraph.csv?id=ICSA",          ""),
    ("UMCSENT",       "https://fred.stlouisfed.org/graph/fredgraph.csv?id=UMCSENT",       ""),
    ("INDPRO",        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=INDPRO",        ""),
    ("MANEMP",        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=MANEMP",        "ISM PMI unavailable via FRED CSV — using manufacturing employment as proxy"),
    ("PCEPILFE",      "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PCEPILFE",      ""),
    ("DFF",           "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF",           ""),
    ("SP500",         "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500",         ""),
    ("BAMLH0A0HYM2",  "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2", "ICE BofA High Yield OAS spread — better signal than ETF price"),
    ("VIXCLS",        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS",        "CBOE VIX from FRED — same data, more reliable fetch"),
    ("DGS10",         "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",         "10Y nominal yield from FRED — same data, confirmed available"),
]

CAPE_URL = "https://multpl.com/shiller-pe/table/by-month"


# ── Fetch helpers ──────────────────────────────────────────

def fetch_fred(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), parse_dates=[0])
    df.columns = ["date", "value"]
    df = df[df["value"] != "."]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])
    if df.empty:
        raise ValueError("empty after cleaning")
    return df


def fetch_cape():
    r = requests.get(CAPE_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    tables = pd.read_html(r.text)
    if not tables:
        raise ValueError("no tables found")
    df = tables[0].copy()
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna()
    if df.empty:
        raise ValueError("empty after parsing")
    return df


# ── Report ─────────────────────────────────────────────────

def print_report(rows):
    print()
    print("DATA AVAILABILITY REPORT")
    print("=" * 80)
    print(f"{'Series':<16} {'Status':<8} {'First Date':<13} {'Last Date':<13} {'Rows':<7} Notes")
    print("=" * 80)
    for name, status, first, last, n, notes in rows:
        print(f"{name:<16} {status:<8} {first:<13} {last:<13} {str(n):<7} {notes}")
    print("=" * 80)
    ok   = sum(1 for r in rows if r[1] == "OK")
    fail = sum(1 for r in rows if r[1] == "FAIL")
    print(f"\nSummary: {ok} OK, {fail} FAIL out of {len(rows)} series")
    print(f"Run at:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


# ── Main ───────────────────────────────────────────────────

def main():
    rows = []

    for name, url, note in FRED_SERIES:
        try:
            df = fetch_fred(url)
            first = df["date"].min().strftime("%Y-%m-%d")
            last  = df["date"].max().strftime("%Y-%m-%d")
            rows.append((name, "OK", first, last, len(df), note))
        except Exception as e:
            rows.append((name, "FAIL", "-", "-", 0, str(e)[:60]))

    try:
        df = fetch_cape()
        first = df["date"].min().strftime("%Y-%m-%d")
        last  = df["date"].max().strftime("%Y-%m-%d")
        rows.append(("CAPE", "OK", first, last, len(df), "multpl.com"))
    except Exception as e:
        rows.append(("CAPE", "FAIL", "-", "-", 0, str(e)[:60]))

    print_report(rows)


if __name__ == "__main__":
    main()
