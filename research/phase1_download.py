import requests
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime, timedelta
from io import StringIO

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DIR = r"C:\Users\lukas.larsson\Desktop\Privat\Project Reactor Core\data"
os.makedirs(DATA_DIR, exist_ok=True)

START_DATE = "2016-04-01"  # 10Y window
END_DATE   = datetime.today().strftime("%Y-%m-%d")

SHORT_HISTORY = {"dow.us", "vrt.us", "pstg.us", "baba.us", "jd.us", "lite.us"}

TICKERS = {
    # Commodities / Scarcity
    "xauusd":   "Gold",
    "xagusd":   "Silver",
    "cl.f":     "Crude Oil WTI",
    "hg.f":     "Copper",
    "paas.us":  "Pan American Silver",
    "xom.us":   "Exxon Mobil",
    "cvx.us":   "Chevron",
    "ccj.us":   "Cameco",
    "dow.us":   "Dow Inc",
    "lyb.us":   "LyondellBasell",
    # Semiconductors / Hardware
    "nvda.us":  "NVIDIA",
    "tsm.us":   "TSMC",
    "asml.us":  "ASML",
    "avgo.us":  "Broadcom",
    "amd.us":   "AMD",
    "lrcx.us":  "Lam Research",
    "qcom.us":  "Qualcomm",
    "adi.us":   "Analog Devices",
    "ter.us":   "Teradyne",
    "mu.us":    "Micron",
    "lite.us":  "Lumentum",
    "glw.us":   "Corning",
    # Infrastructure / Power
    "vrt.us":   "Vertiv",
    "etn.us":   "Eaton",
    # Defensive / Value
    "brk.b.us": "Berkshire Hathaway B",
    "lly.us":   "Eli Lilly",
    "jnj.us":   "J&J",
    "v.us":     "Visa",
    "ma.us":    "Mastercard",
    "wmt.us":   "Walmart",
    "cost.us":  "Costco",
    "jpm.us":   "JP Morgan",
    # China / EM
    "baba.us":  "Alibaba",
    "jd.us":    "JD.com",
    "tcehy.us": "Tencent",
    # Wildcards
    "pstg.us":  "Pure Storage",
    "tsla.us":  "Tesla",
    "aapl.us":  "Apple",
    # Benchmarks
    "smh.us":   "VanEck Semiconductor ETF",
    "acwi.us":  "iShares MSCI ACWI",
    "gld.us":   "SPDR Gold",
    "slv.us":   "iShares Silver",
    "ura.us":   "Global X Uranium ETF",
    "^spx":     "S&P 500",
}

# ── Download ──────────────────────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://stooq.com",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})
SESSION.get("https://stooq.com", timeout=15)  # seed cookies

def download_ticker(ticker):
    url = f"https://stooq.com/q/d/l/?s={ticker}&i=d"
    r = SESSION.get(url, timeout=15)
    r.raise_for_status()
    if "No data" in r.text or len(r.text.strip()) < 50:
        return None
    df = pd.read_csv(StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df

# ── Validate ──────────────────────────────────────────────────────────────────
def validate(ticker, df, name):
    issues = []
    start = pd.Timestamp(START_DATE)

    # Filter to 10Y window
    df_10y = df[df["Date"] >= start].copy()

    # History length
    if len(df_10y) == 0:
        return None, ["NO DATA in 10Y window"]

    first_date = df_10y["Date"].min()
    last_date  = df_10y["Date"].max()
    days       = len(df_10y)

    # Short history flag
    if ticker in SHORT_HISTORY:
        issues.append(f"SHORT HISTORY (starts {first_date.date()})")

    # Expected trading days ~10Y = ~2520
    if days < 1800 and ticker not in SHORT_HISTORY:
        issues.append(f"SPARSE: only {days} rows")

    # Check for gaps > 10 business days
    df_10y = df_10y.set_index("Date")
    date_diffs = df_10y.index.to_series().diff().dt.days.dropna()
    big_gaps = date_diffs[date_diffs > 14]
    if len(big_gaps) > 0:
        issues.append(f"GAPS: {len(big_gaps)} gap(s) >14 days")

    # Check for zero/null close
    nulls = df_10y["Close"].isna().sum()
    zeros = (df_10y["Close"] == 0).sum()
    if nulls > 0: issues.append(f"NULL close: {nulls}")
    if zeros > 0: issues.append(f"ZERO close: {zeros}")

    # Check for price spikes >50% single day
    pct = df_10y["Close"].pct_change().abs()
    spikes = (pct > 0.5).sum()
    if spikes > 0:
        issues.append(f"SPIKES >50%: {spikes}")

    return df_10y, issues

# ── Main ──────────────────────────────────────────────────────────────────────
results = {}
print(f"\n{'='*65}")
print(f"  Project Reactor Core — Phase 1: Data Download & Validation")
print(f"  Window: {START_DATE} → {END_DATE}")
print(f"{'='*65}\n")

for i, (ticker, name) in enumerate(TICKERS.items(), 1):
    print(f"[{i:>2}/{len(TICKERS)}] {ticker:<12} {name:<28}", end=" ", flush=True)
    try:
        df = download_ticker(ticker)
        if df is None:
            print("FAIL — no data returned")
            results[ticker] = {"name": name, "status": "FAIL", "rows": 0, "start": None, "end": None, "issues": ["no data"]}
            continue

        df_10y, issues = validate(ticker, df, name)

        # Save full CSV
        df.to_csv(os.path.join(DATA_DIR, f"{ticker.replace('^','')}.csv"), index=False)

        rows = len(df_10y) if df_10y is not None else 0
        start_d = df_10y.index.min().date() if df_10y is not None and len(df_10y) > 0 else None
        end_d   = df_10y.index.max().date() if df_10y is not None and len(df_10y) > 0 else None

        status = "WARN" if issues else "OK"
        flag = "⚠" if issues else "✓"
        print(f"{flag}  {rows} rows  {start_d} → {end_d}  {'| ' + ' | '.join(issues) if issues else ''}")

        results[ticker] = {"name": name, "status": status, "rows": rows,
                           "start": str(start_d), "end": str(end_d), "issues": issues}
    except Exception as e:
        print(f"ERROR — {e}")
        results[ticker] = {"name": name, "status": "ERROR", "rows": 0, "start": None, "end": None, "issues": [str(e)]}

    time.sleep(0.4)

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("  SUMMARY")
print(f"{'='*65}")
ok   = [t for t,v in results.items() if v["status"] == "OK"]
warn = [t for t,v in results.items() if v["status"] == "WARN"]
fail = [t for t,v in results.items() if v["status"] in ("FAIL","ERROR")]

print(f"  OK:      {len(ok)}")
print(f"  WARN:    {len(warn)}  {[results[t]['name'] for t in warn]}")
print(f"  FAIL:    {len(fail)}  {[results[t]['name'] for t in fail]}")

# ── Common date range ─────────────────────────────────────────────────────────
valid = {t: v for t, v in results.items() if v["start"] and v["status"] != "FAIL"}
if valid:
    common_start = max(v["start"] for v in valid.values())
    common_end   = min(v["end"]   for v in valid.values())
    print(f"\n  Common date range (all tickers): {common_start} → {common_end}")

# ── Save summary ──────────────────────────────────────────────────────────────
summary_df = pd.DataFrame(results).T
summary_df.index.name = "ticker"
summary_df.to_csv(os.path.join(DATA_DIR, "phase1_summary.csv"))
print(f"\n  Data saved to: {DATA_DIR}")
print(f"  Summary saved: phase1_summary.csv")
print(f"{'='*65}\n")
