import pandas as pd
import numpy as np
import os, sys, io, warnings
from scipy.optimize import minimize
warnings.filterwarnings("ignore")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DATA_DIR   = r"C:\Users\lukas.larsson\Desktop\Privat\Project Reactor Core\data"
OUTPUT_DIR = r"C:\Users\lukas.larsson\Desktop\Privat\Project Reactor Core\output"

TICKERS = {
    "xauusd":   "Gold",
    "ccj.us":   "Cameco",
    "avgo.us":  "Broadcom",
    "lite.us":  "Lumentum",
    "vrt.us":   "Vertiv",
    "lly.us":   "Eli Lilly",
    "jnj.us":   "J&J",
    "wmt.us":   "Walmart",
    "cost.us":  "Costco",
    "tsla.us":  "Tesla",
}

WINDOWS = {"3Y": "2023-04-01", "5Y": "2021-04-01", "10Y": "2016-04-01"}
MIN_W, MAX_W = 0.05, 0.40  # wider bounds for concentrated portfolio

def load(ticker):
    fname = ticker.replace("^","").replace("-","_") + ".csv"
    path = os.path.join(DATA_DIR, fname)
    if not os.path.exists(path): return None
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    return df["Close"].replace(0, np.nan).dropna()

def portfolio_metrics(weights, mean_rets, cov, prices_df):
    w = np.array(weights)
    ann_ret = np.dot(w, mean_rets) * 252
    ann_vol = np.sqrt(w @ cov @ w) * np.sqrt(252)
    sharpe  = ann_ret / ann_vol if ann_vol > 0 else 0
    port    = prices_df.dot(w)
    port    = port / port.iloc[0]
    roll_max = port.cummax()
    max_dd  = ((port - roll_max) / roll_max).min()
    calmar  = ann_ret / abs(max_dd) if max_dd < 0 else 0
    total   = port.iloc[-1] - 1
    return ann_ret, ann_vol, sharpe, max_dd, calmar, total

def optimize(prices_df, objective="sharpe"):
    rets = prices_df.pct_change().dropna()
    mean_rets = rets.mean()
    cov = rets.cov()
    n = len(prices_df.columns)

    def neg_sharpe(w):
        r = np.dot(w, mean_rets) * 252
        v = np.sqrt(w @ cov @ w) * np.sqrt(252)
        return -r / v if v > 0 else 0

    def neg_calmar(w):
        port = prices_df.dot(w) / prices_df.iloc[0].dot(w)
        max_dd = ((port - port.cummax()) / port.cummax()).min()
        r = np.dot(w, mean_rets) * 252
        return r / abs(max_dd) if max_dd < 0 else 0

    def neg_blended(w):
        return 0.5 * neg_sharpe(w) + 0.5 * neg_calmar(w)

    obj_fn = {"sharpe": neg_sharpe, "calmar": neg_calmar, "blended": neg_blended}[objective]
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(MIN_W, MAX_W)] * n
    best = None
    for _ in range(80):
        w0 = np.random.dirichlet(np.ones(n))
        w0 = np.clip(w0, MIN_W, MAX_W); w0 /= w0.sum()
        res = minimize(obj_fn, w0, method="SLSQP", bounds=bounds,
                       constraints=constraints, options={"maxiter": 1000, "ftol": 1e-12})
        if res.success and (best is None or res.fun < best.fun):
            best = res
    return best.x if best else None

print("=" * 65)
print("  Phase 3 — Optimization: 10-position portfolio")
print(f"  Constraints: min {MIN_W*100:.0f}% / max {MAX_W*100:.0f}% per position")
print("=" * 65)

all_results = {}

for wname, wstart in WINDOWS.items():
    print(f"\n  === Window: {wname} ===")
    price_dict = {t: load(t) for t in TICKERS if load(t) is not None}
    prices_df = pd.DataFrame({t: s[s.index >= wstart] for t, s in price_dict.items()}).dropna()
    rets = prices_df.pct_change().dropna()
    mean_rets = rets.mean()
    cov = rets.cov()
    window_results = {}

    for obj in ["sharpe", "calmar", "blended"]:
        print(f"  Optimizing {obj.upper()}...", end=" ", flush=True)
        weights = optimize(prices_df, objective=obj)
        if weights is None: print("FAILED"); continue
        ann_ret, ann_vol, sharpe, max_dd, calmar, total = portfolio_metrics(weights, mean_rets, cov, prices_df)
        print(f"done  |  Sharpe {sharpe:.3f}  Ann {ann_ret*100:.1f}%  MaxDD {max_dd*100:.1f}%  Calmar {calmar:.3f}  Total {total*100:.1f}%")
        w_series = pd.Series(weights, index=prices_df.columns).sort_values(ascending=False)
        for t, w in w_series.items():
            print(f"    {TICKERS[t]:<28}  {w*100:.1f}%")
        window_results[obj] = {
            "weights": dict(zip(prices_df.columns, weights.round(4))),
            "ann_return": round(ann_ret*100,2), "ann_vol": round(ann_vol*100,2),
            "sharpe": round(sharpe,3), "max_dd": round(max_dd*100,2),
            "calmar": round(calmar,3), "total_ret": round(total*100,2),
        }

    all_results[wname] = window_results
    rows = [{"window":wname,"objective":obj,"ticker":t,"name":TICKERS.get(t,t),"weight":w}
            for obj,res in window_results.items() for t,w in res["weights"].items()]
    pd.DataFrame(rows).to_csv(os.path.join(OUTPUT_DIR, f"weights10_{wname}.csv"), index=False)

print(f"\n  Saved to {OUTPUT_DIR}")
print("=" * 65)
