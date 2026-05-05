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
os.makedirs(OUTPUT_DIR, exist_ok=True)

TICKERS = {
    "xauusd":   "Gold",
    "xagusd":   "Silver",
    "uso.us":   "US Oil Fund ETF",
    "copx.us":  "Copper Miners ETF",
    "paas.us":  "Pan American Silver",
    "xom.us":   "Exxon Mobil",
    "cvx.us":   "Chevron",
    "ccj.us":   "Cameco",
    "dow.us":   "Dow Inc",
    "lyb.us":   "LyondellBasell",
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
    "baba.us":  "Alibaba",
    "jd.us":    "JD.com",
    "pstg.us":  "Pure Storage",
    "tsla.us":  "Tesla",
    "aapl.us":  "Apple",
    "smh.us":   "SMH ETF",
    "acwi.us":  "MSCI ACWI",
    "gld.us":   "SPDR Gold",
    "slv.us":   "iShares Silver",
    "ura.us":   "Uranium ETF",
    "^spx":     "S&P 500",
}

SHORT_HISTORY = {"dow.us", "vrt.us"}

WINDOWS = {"3Y": "2023-04-01", "5Y": "2021-04-01", "10Y": "2016-04-01"}

# ── Load data ─────────────────────────────────────────────────────────────────
def load(ticker):
    fname = ticker.replace("^", "").replace("-", "_") + ".csv"
    path = os.path.join(DATA_DIR, fname)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    return df["Close"].replace(0, np.nan).dropna()

# ── Metrics ───────────────────────────────────────────────────────────────────
def metrics(prices):
    rets = prices.pct_change().dropna()
    ann_ret = (prices.iloc[-1] / prices.iloc[0]) ** (252 / len(prices)) - 1
    ann_vol = rets.std() * np.sqrt(252)
    sharpe  = ann_ret / ann_vol if ann_vol > 0 else np.nan
    roll_max = prices.cummax()
    dd = (prices - roll_max) / roll_max
    max_dd = dd.min()
    calmar  = ann_ret / abs(max_dd) if max_dd < 0 else np.nan
    total_ret = prices.iloc[-1] / prices.iloc[0] - 1
    return {
        "ann_return":  round(ann_ret * 100, 2),
        "ann_vol":     round(ann_vol * 100, 2),
        "sharpe":      round(sharpe, 3),
        "max_dd":      round(max_dd * 100, 2),
        "calmar":      round(calmar, 3),
        "total_return":round(total_ret * 100, 2),
        "n_days":      len(prices),
    }

# ── Main ──────────────────────────────────────────────────────────────────────
print("=" * 65)
print("  Project Reactor Core — Phase 2: Individual Asset Analysis")
print("=" * 65)

all_prices = {}
results_by_window = {w: {} for w in WINDOWS}

for ticker, name in TICKERS.items():
    s = load(ticker)
    if s is None:
        print(f"  SKIP {ticker} — file not found")
        continue
    all_prices[ticker] = s

print(f"\n  Loaded {len(all_prices)} tickers\n")
print(f"  {'Ticker':<12} {'Name':<26} {'Window':<5} {'Ann Ret':>8} {'Vol':>7} {'Sharpe':>7} {'MaxDD':>8} {'Calmar':>7} {'Total Ret':>10}")
print(f"  {'-'*95}")

for ticker, name in TICKERS.items():
    if ticker not in all_prices:
        continue
    s = all_prices[ticker]
    for wname, wstart in WINDOWS.items():
        s_w = s[s.index >= wstart]
        if len(s_w) < 100:
            continue
        m = metrics(s_w)
        results_by_window[wname][ticker] = {"name": name, **m}
        if wname == "10Y":
            flag = " *" if ticker in SHORT_HISTORY else ""
            print(f"  {ticker:<12} {name:<26} {wname:<5} {m['ann_return']:>7}% {m['ann_vol']:>6}% {m['sharpe']:>7} {m['max_dd']:>7}% {m['calmar']:>7} {m['total_return']:>9}%{flag}")

# ── Save per-window metrics ───────────────────────────────────────────────────
for wname, res in results_by_window.items():
    df = pd.DataFrame(res).T
    df.index.name = "ticker"
    df.to_csv(os.path.join(OUTPUT_DIR, f"metrics_{wname}.csv"))

# ── Correlation matrix (10Y, close-to-close returns) ─────────────────────────
print(f"\n  Building correlation matrix (10Y)...")
price_df = pd.DataFrame({t: all_prices[t] for t in all_prices})
price_df = price_df[price_df.index >= "2016-04-01"].dropna(axis=1, thresh=int(0.8 * len(price_df)))
ret_df = price_df.pct_change().dropna()
corr = ret_df.corr().round(3)
corr.to_csv(os.path.join(OUTPUT_DIR, "correlation_10Y.csv"))

# Print high correlations (>0.8, excluding self)
print("\n  High correlations (>0.80):")
printed = set()
for c1 in corr.columns:
    for c2 in corr.columns:
        if c1 >= c2:
            continue
        val = corr.loc[c1, c2]
        if val > 0.80:
            n1 = TICKERS.get(c1, c1)
            n2 = TICKERS.get(c2, c2)
            print(f"    {n1:<26} x {n2:<26}  r={val:.3f}")

print(f"\n  Output saved to: {OUTPUT_DIR}")
print("=" * 65)
