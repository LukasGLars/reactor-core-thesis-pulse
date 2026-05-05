import pandas as pd
import numpy as np
import os, sys, io, warnings
warnings.filterwarnings("ignore")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DATA_DIR   = r"C:\Users\lukas.larsson\Desktop\Privat\Project Reactor Core\data"
OUTPUT_DIR = r"C:\Users\lukas.larsson\Desktop\Privat\Project Reactor Core\output"

TICKERS = {
    "xauusd":  "Gold",
    "ccj.us":  "Cameco",
    "avgo.us": "Broadcom",
    "lite.us": "Lumentum",
    "vrt.us":  "Vertiv",
    "lly.us":  "Eli Lilly",
    "jnj.us":  "J&J",
    "wmt.us":  "Walmart",
    "cost.us": "Costco",
    "tsla.us": "Tesla",
    "^spx":    "S&P 500",
}

REGIMES = {
    "Pre-COVID Bull":       ("2016-04-01", "2020-01-31", "Low rates, equity bull"),
    "COVID Crash":          ("2020-02-01", "2020-03-31", "Credit stress"),
    "COVID Recovery":       ("2020-04-01", "2021-12-31", "Stimulus, reflation"),
    "Rate Hike/Inflation":  ("2022-01-01", "2023-07-31", "Fastest hike cycle since 1980s"),
    "Post-Hike/AI Bull":    ("2023-08-01", "2024-08-31", "AI theme, rates plateau"),
    "Rate Cut":             ("2024-09-01", "2026-04-01", "Fed cutting"),
}

# 10Y Sharpe weights
WEIGHTS = {
    "xauusd":  0.379, "lly.us":  0.156, "wmt.us":  0.114,
    "avgo.us": 0.050, "lite.us": 0.050, "cost.us": 0.050,
    "tsla.us": 0.050, "ccj.us":  0.050, "vrt.us":  0.050,
    "jnj.us":  0.050,
}

def load(ticker):
    fname = ticker.replace("^","").replace("-","_") + ".csv"
    path = os.path.join(DATA_DIR, fname)
    if not os.path.exists(path): return None
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    return df["Close"].replace(0, np.nan).dropna()

all_prices = {t: load(t) for t in TICKERS if load(t) is not None}
price_df = pd.DataFrame(all_prices).ffill()
spx = all_prices.get("^spx")

print("=" * 68)
print("  Phase 4 — Regime Testing: 10-position portfolio")
print("=" * 68)

regime_summary = []

for rname, (rstart, rend, rdesc) in REGIMES.items():
    print(f"\n  {rname}  [{rstart} -> {rend}]  {rdesc}")
    slice_df = price_df.loc[rstart:rend].dropna(how="all")
    if len(slice_df) < 5: continue

    port_tickers = [t for t in WEIGHTS if t in slice_df.columns]
    sub = slice_df[port_tickers].dropna()
    normed = sub / sub.iloc[0]
    w_arr = np.array([WEIGHTS[t] for t in port_tickers])
    w_arr /= w_arr.sum()
    port = normed.dot(w_arr)

    ret    = port.iloc[-1] - 1
    max_dd = ((port - port.cummax()) / port.cummax()).min()
    vol    = port.pct_change().std() * np.sqrt(252)

    spx_slice = spx.loc[rstart:rend].dropna() if spx is not None else None
    spx_ret = (spx_slice.iloc[-1]/spx_slice.iloc[0]-1) if spx_slice is not None and len(spx_slice)>5 else None

    vs = round((ret - spx_ret)*100, 1) if spx_ret is not None else None
    sign = "+" if vs and vs >= 0 else ""
    print(f"  Portfolio  {ret*100:>+6.1f}%  MaxDD {max_dd*100:>6.1f}%  Vol {vol*100:>5.1f}%")
    if spx_ret is not None:
        print(f"  S&P 500    {spx_ret*100:>+6.1f}%")
        print(f"  vs S&P     {sign}{vs}%")

    asset_rets = {}
    for t, name in TICKERS.items():
        if t == "^spx" or t not in slice_df.columns: continue
        s = slice_df[t].dropna()
        if len(s) < 5: continue
        asset_rets[t] = round((s.iloc[-1]/s.iloc[0]-1)*100, 1)

    srt = sorted(asset_rets.items(), key=lambda x: x[1], reverse=True)
    print(f"  Best:  " + "  |  ".join(f"{TICKERS[t]} {v:+.1f}%" for t,v in srt[:3]))
    print(f"  Worst: " + "  |  ".join(f"{TICKERS[t]} {v:+.1f}%" for t,v in srt[-3:]))

    regime_summary.append({
        "regime": rname, "start": rstart, "end": rend,
        "portfolio_ret": round(ret*100,2), "portfolio_maxdd": round(max_dd*100,2),
        "portfolio_vol": round(vol*100,2),
        "spx_ret": round(spx_ret*100,2) if spx_ret else None,
        "vs_spx": vs,
    })

# Asset regime matrix
rows = []
for t, name in TICKERS.items():
    if t == "^spx": continue
    row = {"ticker": t, "name": name, "positive_regimes": 0, "avg_return": 0}
    rets = []
    for rname, (rstart, rend, _) in REGIMES.items():
        s = price_df[t].loc[rstart:rend].dropna() if t in price_df else pd.Series()
        v = round((s.iloc[-1]/s.iloc[0]-1)*100, 1) if len(s) > 5 else None
        row[rname] = v
        if v is not None: rets.append(v)
    row["positive_regimes"] = sum(1 for v in rets if v > 0)
    row["avg_return"] = round(np.mean(rets), 1) if rets else None
    rows.append(row)

regime_df = pd.DataFrame(rows)
regime_df.to_csv(os.path.join(OUTPUT_DIR, "asset_regime_matrix10.csv"), index=False)
pd.DataFrame(regime_summary).to_csv(os.path.join(OUTPUT_DIR, "regime_summary10.csv"), index=False)
print(f"\n  Saved to {OUTPUT_DIR}")
print("=" * 68)
