"""
calibration/recession_calibration.py

Data availability validation for recession indicator calibration pipeline.
Fetches all sources and prints a report. No analysis, no files saved.
"""

import io
import os
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

warnings.filterwarnings("ignore")

FRED_API_KEY = os.getenv("FRED_API_KEY", "")

FRED_SERIES = [
    ("USREC",         "ISM PMI — NBER recession indicator"),
    ("T10Y3M",        ""),
    ("T10Y2Y",        ""),
    ("DFII10",        ""),
    ("ICSA",          ""),
    ("UMCSENT",       ""),
    ("INDPRO",        ""),
    ("MANEMP",        "ISM PMI unavailable via FRED CSV — using manufacturing employment as proxy"),
    ("PCEPILFE",      ""),
    ("DFF",           ""),
    ("SP500",         ""),
    ("BAMLH0A0HYM2",  "ICE BofA High Yield OAS spread — better signal than ETF price"),
    ("VIXCLS",        "CBOE VIX from FRED — same data, more reliable fetch"),
    ("DGS10",         "10Y nominal yield from FRED — same data, confirmed available"),
]

CAPE_URL = "https://multpl.com/shiller-pe/table/by-month"


# ── Fetch helpers ──────────────────────────────────────────

def fetch_fred(series_id):
    if not FRED_API_KEY:
        raise ValueError("FRED_API_KEY not set")
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "observations" not in data:
        raise ValueError(data.get("error_message", "no observations key"))
    obs = data["observations"]
    df = pd.DataFrame(obs)[["date", "value"]]
    df["date"] = pd.to_datetime(df["date"])
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
    print("=" * 90)
    print(f"{'Series':<16} {'Status':<8} {'First Date':<13} {'Last Date':<13} {'Rows':<7} Notes")
    print("=" * 90)
    for name, status, first, last, n, notes in rows:
        print(f"{name:<16} {status:<8} {first:<13} {last:<13} {str(n):<7} {notes}")
    print("=" * 90)
    ok   = sum(1 for r in rows if r[1] == "OK")
    fail = sum(1 for r in rows if r[1] == "FAIL")
    print(f"\nSummary: {ok} OK, {fail} FAIL out of {len(rows)} series")
    print(f"API key: {'set' if FRED_API_KEY else 'NOT SET — full history unavailable'}")
    print(f"Run at:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


# ── Main ───────────────────────────────────────────────────

def main():
    rows = []

    for series_id, note in FRED_SERIES:
        try:
            df = fetch_fred(series_id)
            first = df["date"].min().strftime("%Y-%m-%d")
            last  = df["date"].max().strftime("%Y-%m-%d")
            rows.append((series_id, "OK", first, last, len(df), note))
        except Exception as e:
            rows.append((series_id, "FAIL", "-", "-", 0, str(e)[:60]))

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
