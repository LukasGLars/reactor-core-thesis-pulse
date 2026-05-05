import pandas as pd
import numpy as np
import os
import sys
import io
import warnings
warnings.filterwarnings("ignore")

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DATA_DIR   = r"C:\Users\lukas.larsson\Desktop\Privat\Project Reactor Core\data"
OUTPUT_DIR = r"C:\Users\lukas.larsson\Desktop\Privat\Project Reactor Core\output"

TICKERS = {
    "xauusd":   "Gold",
    "xagusd":   "Silver",
    "copx.us":  "Copper Miners ETF",
    "paas.us":  "Pan American Silver",
    "xom.us":   "Exxon Mobil",
    "cvx.us":   "Chevron",
    "ccj.us":   "Cameco",
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
    "vrt.us":   "Vertiv",
    "etn.us":   "Eaton",
    "brk-b.us": "Berkshire Hathaway B",
    "lly.us":   "Eli Lilly",
    "jnj.us":   "J&J",
    "v.us":     "Visa",
    "ma.us":    "Mastercard",
    "wmt.us":   "Walmart",
    "cost.us":  "Costco",
    "jpm.us":   "JP Morgan",
    "pstg.us":  "Pure Storage",
    "tsla.us":  "Tesla",
    "aapl.us":  "Apple",
    "^spx":     "S&P 500",
}

# Regime definitions
REGIMES = {
    "Pre-COVID Bull":      ("2016-04-01", "2020-01-31", "Low rates, equity bull, commodity bear"),
    "COVID Crash":         ("2020-02-01", "2020-03-31", "Credit stress, liquidity crisis"),
    "COVID Recovery":      ("2020-04-01", "2021-12-31", "Stimulus, commodity bull, reflation"),
    "Rate Hike / Inflation":("2022-01-01", "2023-07-31", "Fastest Fed hike cycle since 1980s"),
    "Post-Hike / AI Bull": ("2023-08-01", "2024-08-31", "Rates plateau, AI theme dominates"),
    "Rate Cut":            ("2024-09-01", "2026-04-01", "Fed cutting, macro uncertainty"),
}

# Sharpe-optimal weights from Phase 3 (10Y window)
SHARPE_10Y_WEIGHTS = {
    "xauusd":   0.25,
    "lly.us":   0.16,
    "wmt.us":   0.153,
    "vrt.us":   0.053,
    "nvda.us":  0.035,
    "tsla.us":  0.033,
    "xom.us":   0.01,
    "cvx.us":   0.01,
    "tsm.us":   0.01,
    "asml.us":  0.01,
    "avgo.us":  0.01,
    "amd.us":   0.01,
    "lrcx.us":  0.01,
    "qcom.us":  0.01,
    "adi.us":   0.01,
    "ter.us":   0.01,
    "mu.us":    0.01,
    "etn.us":   0.01,
    "brk-b.us": 0.01,
    "jnj.us":   0.01,
    "v.us":     0.01,
    "ma.us":    0.01,
    "cost.us":  0.01,
    "jpm.us":   0.01,
    "aapl.us":  0.01,
}

def load(ticker):
    fname = ticker.replace("^", "").replace("-", "_") + ".csv"
    path = os.path.join(DATA_DIR, fname)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    return df["Close"].replace(0, np.nan).dropna()

def regime_metrics(prices, label=""):
    if len(prices) < 5:
        return None
    ret = prices.iloc[-1] / prices.iloc[0] - 1
    daily_rets = prices.pct_change().dropna()
    ann_vol = daily_rets.std() * np.sqrt(252)
    roll_max = prices.cummax()
    max_dd = ((prices - roll_max) / roll_max).min()
    return {"total_ret": round(ret * 100, 2), "max_dd": round(max_dd * 100, 2), "vol": round(ann_vol * 100, 2)}

# ── Load all prices ───────────────────────────────────────────────────────────
all_prices = {}
for ticker in TICKERS:
    s = load(ticker)
    if s is not None:
        all_prices[ticker] = s

# Build full price df
price_df = pd.DataFrame(all_prices).ffill()

# ── Main ──────────────────────────────────────────────────────────────────────
print("=" * 72)
print("  Project Reactor Core — Phase 4: Regime Testing")
print("=" * 72)

# Normalize weights to available tickers
avail_w = {t: w for t, w in SHARPE_10Y_WEIGHTS.items() if t in price_df.columns}
total_w = sum(avail_w.values())
avail_w = {t: w / total_w for t, w in avail_w.items()}

spx = all_prices.get("^spx")

regime_summary = []

for rname, (rstart, rend, rdesc) in REGIMES.items():
    print(f"\n  {'='*68}")
    print(f"  {rname}  [{rstart} -> {rend}]")
    print(f"  {rdesc}")
    print(f"  {'-'*68}")

    slice_df = price_df.loc[rstart:rend].dropna(how="all")
    if len(slice_df) < 5:
        print("  Insufficient data")
        continue

    # Portfolio performance
    port_cols = [t for t in avail_w if t in slice_df.columns]
    port_prices_raw = slice_df[port_cols].dropna()
    if len(port_prices_raw) < 5:
        continue

    # Normalize each asset to 1 at start, then weight
    normed = port_prices_raw / port_prices_raw.iloc[0]
    weights_arr = np.array([avail_w[t] for t in port_cols])
    port_series = normed.dot(weights_arr)

    pm = regime_metrics(port_series)

    # S&P 500 benchmark
    if spx is not None:
        spx_slice = spx.loc[rstart:rend].dropna()
        if len(spx_slice) > 5:
            spx_m = regime_metrics(spx_slice)
            vs_spx = round(pm["total_ret"] - spx_m["total_ret"], 2)
            spx_str = f"  S&P 500:    {spx_m['total_ret']:>8.1f}%  |  MaxDD: {spx_m['max_dd']:>7.1f}%"
        else:
            spx_m = None
            vs_spx = None
            spx_str = ""
    else:
        spx_str = ""
        vs_spx = None

    print(f"  Portfolio:  {pm['total_ret']:>8.1f}%  |  MaxDD: {pm['max_dd']:>7.1f}%  |  Vol: {pm['vol']:>6.1f}%")
    if spx_str:
        print(spx_str)
    if vs_spx is not None:
        sign = "+" if vs_spx >= 0 else ""
        print(f"  vs S&P 500: {sign}{vs_spx:.1f}%")

    # Top 5 / Bottom 5 individual assets
    asset_rets = {}
    for ticker in TICKERS:
        if ticker == "^spx" or ticker not in slice_df.columns:
            continue
        s = slice_df[ticker].dropna()
        if len(s) < 5:
            continue
        r = s.iloc[-1] / s.iloc[0] - 1
        asset_rets[ticker] = round(r * 100, 2)

    sorted_rets = sorted(asset_rets.items(), key=lambda x: x[1], reverse=True)
    print(f"\n  Best 5:")
    for t, r in sorted_rets[:5]:
        print(f"    {TICKERS.get(t, t):<28}  {r:>+.1f}%")
    print(f"  Worst 5:")
    for t, r in sorted_rets[-5:]:
        print(f"    {TICKERS.get(t, t):<28}  {r:>+.1f}%")

    regime_summary.append({
        "regime": rname,
        "start": rstart,
        "end": rend,
        "portfolio_ret": pm["total_ret"],
        "portfolio_maxdd": pm["max_dd"],
        "portfolio_vol": pm["vol"],
        "spx_ret": spx_m["total_ret"] if spx_m else None,
        "vs_spx": vs_spx,
    })

# ── All-weather analysis ──────────────────────────────────────────────────────
print(f"\n\n  {'='*68}")
print(f"  ALL-WEATHER ANALYSIS — asset returns across all regimes")
print(f"  {'='*68}")
print(f"  {'Name':<28}", end="")
for rname in REGIMES:
    short = rname[:8]
    print(f"  {short:>9}", end="")
print()
print(f"  {'-'*28}", end="")
for _ in REGIMES:
    print(f"  {'-'*9}", end="")
print()

asset_regime_matrix = {}
for ticker, name in TICKERS.items():
    if ticker == "^spx":
        continue
    row = {}
    for rname, (rstart, rend, _) in REGIMES.items():
        if ticker not in price_df.columns:
            row[rname] = None
            continue
        s = price_df[ticker].loc[rstart:rend].dropna()
        if len(s) < 5:
            row[rname] = None
            continue
        row[rname] = round((s.iloc[-1] / s.iloc[0] - 1) * 100, 1)
    asset_regime_matrix[ticker] = row

# Score: count regimes with positive return
scored = []
for ticker, row in asset_regime_matrix.items():
    vals = [v for v in row.values() if v is not None]
    pos = sum(1 for v in vals if v > 0)
    avg = round(np.mean(vals), 1) if vals else None
    scored.append((ticker, pos, avg, row))

scored.sort(key=lambda x: (x[1], x[2] or 0), reverse=True)

for ticker, pos, avg, row in scored:
    name = TICKERS.get(ticker, ticker)
    print(f"  {name:<28}", end="")
    for rname in REGIMES:
        v = row.get(rname)
        if v is None:
            print(f"  {'N/A':>9}", end="")
        else:
            print(f"  {v:>+8.1f}%", end="")
    print(f"  | {pos}/6 positive  avg {avg:>+.1f}%")

# Save
pd.DataFrame(regime_summary).to_csv(os.path.join(OUTPUT_DIR, "regime_summary.csv"), index=False)
rows = []
for ticker, pos, avg, row in scored:
    r = {"ticker": ticker, "name": TICKERS.get(ticker, ticker), "positive_regimes": pos, "avg_return": avg}
    r.update(row)
    rows.append(r)
pd.DataFrame(rows).to_csv(os.path.join(OUTPUT_DIR, "asset_regime_matrix.csv"), index=False)

print(f"\n  Output saved to: {OUTPUT_DIR}")
print("=" * 72)
