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
import yfinance as yf

warnings.filterwarnings("ignore")

FRED_SERIES = [
    ("USREC",    "https://fred.stlouisfed.org/graph/fredgraph.csv?id=USREC"),
    ("T10Y3M",   "https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10Y3M"),
    ("T10Y2Y",   "https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10Y2Y"),
    ("DFII10",   "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"),
    ("ICSA",     "https://fred.stlouisfed.org/graph/fredgraph.csv?id=ICSA"),
    ("UMCSENT",  "https://fred.stlouisfed.org/graph/fredgraph.csv?id=UMCSENT"),
    ("INDPRO",   "https://fred.stlouisfed.org/graph/fredgraph.csv?id=INDPRO"),
    ("NAPM",     "https://fred.stlouisfed.org/graph/fredgraph.csv?id=NAPM"),
    ("PCEPILFE", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PCEPILFE"),
    ("DFF",      "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"),
    ("SP500",    "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500"),
]

YAHOO_TICKERS = ["HYG", "^VIX", "^TNX"]

CAPE_PRIMARY  = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"
CAPE_FALLBACK = "https://multpl.com/shiller-pe/table/by-month"


# ── Fetch helpers ──────────────────────────────────────────

def fetch_fred(name, url):
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), parse_dates=[0])
        df.columns = ["date", "value"]
        df = df.dropna(subset=["value"])
        df = df[df["value"] != "."]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])
        if df.empty:
            return None, "empty after cleaning"
        return df, None
    except Exception as e:
        return None, str(e)[:80]


def fetch_yahoo(ticker):
    try:
        df = yf.download(ticker, period="max", auto_adjust=True, progress=False)
        if df.empty:
            return None, "empty response"
        df = df[["Close"]].rename(columns={"Close": "value"})
        df.index.name = "date"
        df = df.dropna()
        return df, None
    except Exception as e:
        return None, str(e)[:80]


def fetch_cape():
    try:
        r = requests.get(CAPE_PRIMARY, timeout=30)
        r.raise_for_status()
        xls = pd.ExcelFile(io.BytesIO(r.content))
        sheet = xls.parse("Data", header=7)
        sheet.columns = [str(c).strip() for c in sheet.columns]
        cape_col = next((c for c in sheet.columns if "cape" in c.lower() or "p/e10" in c.lower()), None)
        if cape_col is None:
            raise ValueError(f"CAPE column not found. Columns: {list(sheet.columns)[:10]}")
        date_col = sheet.columns[0]
        df = sheet[[date_col, cape_col]].copy()
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"].astype(str).str[:7], format="%Y.%m", errors="coerce")
        df = df.dropna()
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])
        if df.empty:
            raise ValueError("empty after parsing")
        return df, None, "Yale ie_data.xls"
    except Exception as primary_err:
        pass

    try:
        r = requests.get(CAPE_FALLBACK, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0"})
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
        return df, None, "multpl.com (fallback)"
    except Exception as fallback_err:
        return None, f"primary: {str(primary_err)[:40]} | fallback: {str(fallback_err)[:40]}", None


# ── Report ─────────────────────────────────────────────────

def fmt_date(df):
    try:
        dates = df.index if hasattr(df.index, 'min') and df.index.dtype == 'datetime64[ns]' else df["date"]
        return dates.min().strftime("%Y-%m-%d"), dates.max().strftime("%Y-%m-%d")
    except Exception:
        return "?", "?"


def print_report(rows):
    header = f"{'Series':<10} {'Status':<8} {'First Date':<13} {'Last Date':<13} {'Rows':<8} Notes"
    divider = "=" * 68
    print()
    print("DATA AVAILABILITY REPORT")
    print(divider)
    print(header)
    print(divider)
    for r in rows:
        name, status, first, last, n, notes = r
        print(f"{name:<10} {status:<8} {first:<13} {last:<13} {str(n):<8} {notes}")
    print(divider)
    ok  = sum(1 for r in rows if r[1] == "OK")
    fail = sum(1 for r in rows if r[1] == "FAIL")
    print(f"\nSummary: {ok} OK, {fail} FAIL out of {len(rows)} series")
    print(f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


# ── Main ───────────────────────────────────────────────────

def main():
    rows = []

    for name, url in FRED_SERIES:
        df, err = fetch_fred(name, url)
        if df is not None:
            first, last = fmt_date(df)
            rows.append((name, "OK", first, last, len(df), ""))
        else:
            rows.append((name, "FAIL", "-", "-", 0, err or "unknown error"))

    for ticker in YAHOO_TICKERS:
        df, err = fetch_yahoo(ticker)
        if df is not None:
            first = df.index.min().strftime("%Y-%m-%d")
            last  = df.index.max().strftime("%Y-%m-%d")
            rows.append((ticker, "OK", first, last, len(df), ""))
        else:
            rows.append((ticker, "FAIL", "-", "-", 0, err or "unknown error"))

    df, err, source = fetch_cape()
    if df is not None:
        first, last = fmt_date(df)
        rows.append(("CAPE", "OK", first, last, len(df), source))
    else:
        rows.append(("CAPE", "FAIL", "-", "-", 0, err or "unknown error"))

    print_report(rows)


if __name__ == "__main__":
    main()
