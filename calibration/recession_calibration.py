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

# (series_id, observation_start, note)
FRED_SERIES = [
    ("USREC",    None,         "NBER recession indicator"),
    ("T10Y3M",   None,         ""),
    ("T10Y2Y",   None,         ""),
    ("DFII10",   None,         ""),
    ("ICSA",     None,         ""),
    ("UMCSENT",  None,         ""),
    ("INDPRO",   None,         ""),
    ("MANEMP",   None,         "ISM PMI unavailable via FRED CSV — using manufacturing employment as proxy"),
    ("PCEPILFE", None,         ""),
    ("DFF",      None,         ""),
]

CAPE_URL = "https://multpl.com/shiller-pe/table/by-month"

HY_OAS_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2&observation_start=1996-01-01"
IG_OAS_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLC0A0CM&observation_start=1996-01-01"


# ── Fetch helpers ──────────────────────────────────────────

def fetch_fred(series_id, observation_start=None):
    if not FRED_API_KEY:
        raise ValueError("FRED_API_KEY not set")
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
    )
    if observation_start:
        url += f"&observation_start={observation_start}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "observations" not in data:
        raise ValueError(data.get("error_message", "no observations key"))
    df = pd.DataFrame(data["observations"])[["date", "value"]]
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["value"] != "."]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])
    if df.empty:
        raise ValueError("empty after cleaning")
    return df


def fetch_sp500():
    try:
        df = fetch_fred("SP500", observation_start="1956-01-01")
        if df["date"].min().year > 2000:
            raise ValueError("still truncated")
        return df, "SP500"
    except Exception:
        df = fetch_fred("SPASTT01USM661N", observation_start="1956-01-01")
        return df, "SPASTT01USM661N (fallback)"


def fetch_credit_spread():
    """CREDIT_SPREAD = HY_OAS (BAMLH0A0HYM2) minus IG_OAS (BAMLC0A0CM)."""
    def load_csv(url):
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), parse_dates=[0])
        df.columns = ["date", "value"]
        df = df[df["value"] != "."]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.dropna(subset=["value"]).set_index("date")

    hy = load_csv(HY_OAS_URL)
    ig = load_csv(IG_OAS_URL)

    aligned = hy.join(ig, how="inner", lsuffix="_hy", rsuffix="_ig")
    aligned["value"] = aligned["value_hy"] - aligned["value_ig"]
    aligned = aligned[["value"]].dropna()

    if aligned.empty:
        raise ValueError("empty after alignment")
    return aligned


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
    print(f"API key: {'set' if FRED_API_KEY else 'NOT SET'}")
    print(f"Run at:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


# ── Main ───────────────────────────────────────────────────

def main():
    rows = []

    for series_id, obs_start, note in FRED_SERIES:
        try:
            df = fetch_fred(series_id, observation_start=obs_start)
            first = df["date"].min().strftime("%Y-%m-%d")
            last  = df["date"].max().strftime("%Y-%m-%d")
            rows.append((series_id, "OK", first, last, len(df), note))
        except Exception as e:
            rows.append((series_id, "FAIL", "-", "-", 0, str(e)[:60]))

    try:
        df, source = fetch_sp500()
        first = df["date"].min().strftime("%Y-%m-%d")
        last  = df["date"].max().strftime("%Y-%m-%d")
        rows.append(("SP500", "OK", first, last, len(df), source))
    except Exception as e:
        rows.append(("SP500", "FAIL", "-", "-", 0, str(e)[:60]))

    try:
        df = fetch_credit_spread()
        first = df.index.min().strftime("%Y-%m-%d")
        last  = df.index.max().strftime("%Y-%m-%d")
        rows.append(("CREDIT_SPREAD", "OK", first, last, len(df), "HY_OAS minus IG_OAS"))
    except Exception as e:
        rows.append(("CREDIT_SPREAD", "FAIL", "-", "-", 0, str(e)[:60]))

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
