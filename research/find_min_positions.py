import pandas as pd
import numpy as np
import os
import sys
import io
import warnings
from scipy.optimize import minimize
warnings.filterwarnings("ignore")

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DATA_DIR = r"C:\Users\lukas.larsson\Desktop\Privat\Project Reactor Core\data"

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

def load(ticker):
    fname = ticker.replace("^", "").replace("-", "_") + ".csv"
    path = os.path.join(DATA_DIR, fname)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    return df["Close"].replace(0, np.nan).dropna()

def optimize_sharpe(prices_df, min_w=0.01, max_w=0.25):
    rets = prices_df.pct_change().dropna()
    mean_rets = rets.mean()
    cov = rets.cov()
    n = len(prices_df.columns)

    def neg_sharpe(w):
        r = np.dot(w, mean_rets) * 252
        v = np.sqrt(w @ cov @ w) * np.sqrt(252)
        return -r / v if v > 0 else 0

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(min_w, max_w)] * n

    best = None
    for _ in range(60):
        w0 = np.random.dirichlet(np.ones(n))
        w0 = np.clip(w0, min_w, max_w)
        w0 /= w0.sum()
        res = minimize(neg_sharpe, w0, method="SLSQP", bounds=bounds,
                       constraints=constraints, options={"maxiter": 1000, "ftol": 1e-12})
        if res.success and (best is None or res.fun < best.fun):
            best = res

    if best is None:
        return None, None
    w = best.x
    r = np.dot(w, mean_rets) * 252
    v = np.sqrt(w @ cov @ w) * np.sqrt(252)
    return w, -best.fun

# ── Build price matrix (10Y window) ──────────────────────────────────────────
price_dict = {}
for ticker in TICKERS:
    s = load(ticker)
    if s is not None:
        s_w = s[s.index >= "2016-04-01"]
        if len(s_w) >= 100:
            price_dict[ticker] = s_w

prices_df = pd.DataFrame(price_dict).dropna()
active = list(prices_df.columns)

print("=" * 65)
print("  Minimum positions for maximum Sharpe (10Y window)")
print("=" * 65)
print(f"\n  {'N':>3}  {'Sharpe':>7}  {'Dropped'}  {'Remaining holdings'}")
print(f"  {'-'*62}")

results = []
iteration = 0

while len(active) >= 2:
    subset = prices_df[active]
    weights, sharpe = optimize_sharpe(subset)
    if weights is None:
        break

    w_series = pd.Series(weights, index=active)
    holdings = w_series[w_series >= 0.015].sort_values(ascending=False)
    names = [TICKERS.get(t, t) for t in holdings.index]

    results.append({
        "n": len(active),
        "sharpe": round(sharpe, 3),
        "weights": dict(zip(active, weights.round(4))),
        "holdings": names,
    })

    if iteration == 0:
        print(f"  {len(active):>3}  {sharpe:>7.3f}  {'(start)':20}  {', '.join(names[:6])}{'...' if len(names)>6 else ''}")
    else:
        print(f"  {len(active):>3}  {sharpe:>7.3f}  {dropped_name:20}  {', '.join(names[:6])}{'...' if len(names)>6 else ''}")

    # Drop the lowest-weight ticker
    drop_ticker = w_series.idxmin()
    dropped_name = TICKERS.get(drop_ticker, drop_ticker)
    active.remove(drop_ticker)
    iteration += 1

# ── Find knee — biggest Sharpe drop ──────────────────────────────────────────
print(f"\n  {'='*65}")
print(f"  Sharpe by number of positions:")
print(f"  {'N':>3}  {'Sharpe':>7}  {'Delta':>8}")
print(f"  {'-'*25}")

best_sharpe = max(r["sharpe"] for r in results)
knee = None
prev = None
for r in results:
    delta = f"{r['sharpe']-prev['sharpe']:+.3f}" if prev else "    —"
    flag = ""
    # Mark knee: first point where removing a position costs >0.05 Sharpe
    if prev and knee is None and (prev["sharpe"] - r["sharpe"]) > 0.05:
        knee = prev
        flag = "  <-- knee"
    print(f"  {r['n']:>3}  {r['sharpe']:>7.3f}  {delta:>8}{flag}")
    prev = r

print(f"\n  Best Sharpe:  {best_sharpe:.3f} (at {max(results, key=lambda x: x['sharpe'])['n']} positions)")
if knee:
    print(f"  Knee point:   {knee['sharpe']:.3f} at {knee['n']} positions")
    print(f"\n  Optimal holdings at knee ({knee['n']} positions):")
    for t, w in sorted(knee["weights"].items(), key=lambda x: x[1], reverse=True):
        if w >= 0.01:
            print(f"    {TICKERS.get(t,t):<28}  {w*100:.1f}%")
