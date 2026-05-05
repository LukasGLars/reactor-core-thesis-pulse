import pandas as pd
import numpy as np
import os
import sys
import io
import warnings
from scipy.optimize import minimize
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
}

WINDOWS = {"3Y": "2023-04-01", "5Y": "2021-04-01", "10Y": "2016-04-01"}

# Constraints
MIN_W = 0.01   # min 1% per position
MAX_W = 0.25   # max 25% per position

# ── Load ──────────────────────────────────────────────────────────────────────
def load(ticker):
    fname = ticker.replace("^", "").replace("-", "_") + ".csv"
    path = os.path.join(DATA_DIR, fname)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    return df["Close"].replace(0, np.nan).dropna()

# ── Portfolio metrics ─────────────────────────────────────────────────────────
def portfolio_metrics(weights, mean_rets, cov, prices_df):
    w = np.array(weights)
    ann_ret = np.dot(w, mean_rets) * 252
    ann_vol = np.sqrt(w @ cov @ w) * np.sqrt(252)
    sharpe  = ann_ret / ann_vol if ann_vol > 0 else 0

    # Calmar: use actual portfolio drawdown
    port_prices = prices_df.dot(w)
    port_prices = port_prices / port_prices.iloc[0]
    roll_max = port_prices.cummax()
    dd = (port_prices - roll_max) / roll_max
    max_dd = dd.min()
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0

    total_ret = port_prices.iloc[-1] - 1
    return ann_ret, ann_vol, sharpe, max_dd, calmar, total_ret

# ── Optimizer ─────────────────────────────────────────────────────────────────
def optimize(prices_df, objective="sharpe"):
    rets = prices_df.pct_change().dropna()
    mean_rets = rets.mean()
    cov = rets.cov()
    n = len(prices_df.columns)

    def neg_sharpe(w):
        ann_ret = np.dot(w, mean_rets) * 252
        ann_vol = np.sqrt(w @ cov @ w) * np.sqrt(252)
        return -ann_ret / ann_vol if ann_vol > 0 else 0

    def neg_calmar(w):
        port = prices_df.dot(w)
        port = port / port.iloc[0]
        roll_max = port.cummax()
        dd = (port - roll_max) / roll_max
        max_dd = dd.min()
        ann_ret = np.dot(w, mean_rets) * 252
        return ann_ret / abs(max_dd) if max_dd < 0 else 0

    def neg_blended(w):
        return 0.5 * neg_sharpe(w) + 0.5 * neg_calmar(w)

    obj_fn = {"sharpe": neg_sharpe, "calmar": neg_calmar, "blended": neg_blended}[objective]

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(MIN_W, MAX_W)] * n
    w0 = np.array([1 / n] * n)

    best = None
    for _ in range(50):
        w0 = np.random.dirichlet(np.ones(n))
        w0 = np.clip(w0, MIN_W, MAX_W)
        w0 /= w0.sum()
        res = minimize(obj_fn, w0, method="SLSQP", bounds=bounds, constraints=constraints,
                       options={"maxiter": 1000, "ftol": 1e-12})
        if res.success and (best is None or res.fun < best.fun):
            best = res

    return best.x if best else None

# ── Main ──────────────────────────────────────────────────────────────────────
print("=" * 70)
print("  Project Reactor Core — Phase 3: Portfolio Optimization")
print(f"  Constraints: min {MIN_W*100:.0f}% / max {MAX_W*100:.0f}% per position")
print("=" * 70)

all_results = {}

for wname, wstart in WINDOWS.items():
    print(f"\n  === Window: {wname} (from {wstart}) ===\n")

    # Build aligned price matrix
    price_dict = {}
    for ticker in TICKERS:
        s = load(ticker)
        if s is None:
            continue
        s_w = s[s.index >= wstart]
        if len(s_w) < 100:
            continue
        price_dict[ticker] = s_w

    prices_df = pd.DataFrame(price_dict).dropna()
    tickers_used = list(prices_df.columns)
    n = len(tickers_used)
    print(f"  Tickers in this window: {n}")
    print(f"  Date range: {prices_df.index[0].date()} -> {prices_df.index[-1].date()}")

    rets = prices_df.pct_change().dropna()
    mean_rets = rets.mean()
    cov = rets.cov()

    window_results = {}

    for obj in ["sharpe", "calmar", "blended"]:
        print(f"\n  Optimizing: {obj.upper()}...", end=" ", flush=True)
        weights = optimize(prices_df, objective=obj)
        if weights is None:
            print("FAILED")
            continue

        ann_ret, ann_vol, sharpe, max_dd, calmar, total_ret = portfolio_metrics(
            weights, mean_rets, cov, prices_df
        )

        print(f"done")
        print(f"    Ann Return: {ann_ret*100:.2f}%  |  Vol: {ann_vol*100:.2f}%  |  Sharpe: {sharpe:.3f}  |  MaxDD: {max_dd*100:.2f}%  |  Calmar: {calmar:.3f}  |  Total: {total_ret*100:.2f}%")

        # Top holdings
        w_series = pd.Series(weights, index=tickers_used).sort_values(ascending=False)
        top = w_series[w_series > 0.02]
        print(f"    Top holdings:")
        for t, w in top.head(10).items():
            print(f"      {TICKERS.get(t, t):<28}  {w*100:.1f}%")

        window_results[obj] = {
            "weights": dict(zip(tickers_used, weights.round(4))),
            "ann_return": round(ann_ret * 100, 2),
            "ann_vol":    round(ann_vol * 100, 2),
            "sharpe":     round(sharpe, 3),
            "max_dd":     round(max_dd * 100, 2),
            "calmar":     round(calmar, 3),
            "total_ret":  round(total_ret * 100, 2),
        }

    all_results[wname] = window_results

    # Save weights for this window
    rows = []
    for obj, res in window_results.items():
        for t, w in res["weights"].items():
            rows.append({"window": wname, "objective": obj, "ticker": t,
                         "name": TICKERS.get(t, t), "weight": w})
    pd.DataFrame(rows).to_csv(os.path.join(OUTPUT_DIR, f"weights_{wname}.csv"), index=False)

# ── Cross-window consistency ──────────────────────────────────────────────────
print("\n\n  === Cross-window consistency (names appearing in all 3 windows, all 3 objectives) ===\n")
from collections import defaultdict
ticker_counts = defaultdict(int)
for wname, wres in all_results.items():
    for obj, res in wres.items():
        for t, w in res["weights"].items():
            if w > 0.03:
                ticker_counts[t] += 1

consistent = {t: c for t, c in ticker_counts.items() if c == 9}  # 3 windows x 3 objectives
print(f"  Consistent across all 3 windows x 3 objectives (weight >3%):")
for t in sorted(consistent):
    print(f"    {TICKERS.get(t, t)}")

print(f"\n  Output saved to: {OUTPUT_DIR}")
print("=" * 70)
