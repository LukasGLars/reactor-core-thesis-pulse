#!/usr/bin/env python3
"""
research/optimize_gold_floor.py

Sweep GOLD_FLOOR from 0% to 25% in 2.5% increments (11 values).
For each floor, run full IS + OOS simulation and record all metrics.
Rank by: IS Sharpe, IS Calmar, combined IS+OOS Sharpe.

  floor=0.000  →  original tactical (full-or-nothing, 0–25%)
  floor=0.250  →  baseline (gold always 25%)
  floor=0.125  →  previously tested half-floor
"""

import os, sys, time
import requests
import numpy  as np
import pandas as pd
from datetime import date

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

IS_START  = "2016-04-01";  IS_END  = "2026-03-31"
OOS_START = "2009-01-01";  OOS_END = "2016-03-31"

DFII10_SMA_WINDOW = 90
GSR_T1, GSR_T2    = 83.36, 86.45
GSR_PEAK_WINDOW   = 60
GSR_FALL_PCT      = 0.05

GOLD_W   = 0.25
SILVER_W = 0.10
STATIC_W = {"LLY":0.15,"WMT":0.15,"JNJ":0.06,"CCJ":0.10,"VRT":0.10,"AVGO":0.09}
VRT_IPO    = pd.Timestamp("2020-02-07")
CASH_DAILY = 0.03 / 365

FLOOR_STEPS = [round(x * 0.025, 4) for x in range(0, 11)]  # 0.000 … 0.250


# ── fetch ─────────────────────────────────────────────────────────────────────

def fetch_fred(series_id):
    if FRED_API_KEY:
        url = (f"https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&sort_order=asc")
    else:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "floor-opt/1.0"}, timeout=30)
            r.raise_for_status()
            if FRED_API_KEY:
                obs = r.json().get("observations", [])
                rows = {o["date"]: float(o["value"]) for o in obs if o.get("value") not in (".","")}
            else:
                rows = {}
                for line in r.text.splitlines()[1:]:
                    parts = line.split(",")
                    if len(parts) == 2:
                        try: rows[parts[0].strip()] = float(parts[1].strip())
                        except ValueError: pass
            if not rows: return None
            s = pd.Series(rows, dtype=float)
            s.index = pd.to_datetime(s.index)
            return s.sort_index()
        except Exception as e:
            print(f"  FRED {series_id} attempt {attempt+1}: {e}")
            time.sleep(2**attempt)
    return None


def fetch_yahoo(symbol):
    import datetime
    p1  = int(datetime.datetime(2000, 1, 1).timestamp())
    p2  = int(datetime.datetime.now().timestamp())
    enc = symbol.replace("=", "%3D")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}"
           f"?interval=1d&period1={p1}&period2={p2}")
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            r.raise_for_status()
            res    = r.json()["chart"]["result"][0]
            ts     = pd.to_datetime(res["timestamp"], unit="s").normalize()
            closes = res["indicators"]["quote"][0]["close"]
            s = pd.Series(closes, index=ts, dtype=float)
            return s.dropna().sort_index()
        except Exception as e:
            print(f"  Yahoo {symbol} attempt {attempt+1}: {e}")
            time.sleep(2**attempt)
    return None


# ── signals ───────────────────────────────────────────────────────────────────

def compute_gold_signal(dfii10):
    sma = dfii10.rolling(DFII10_SMA_WINDOW, min_periods=DFII10_SMA_WINDOW).mean()
    return (dfii10 < sma).astype(int).shift(1).rename("gs")


def compute_silver_signals(gp, sp):
    gsr  = (gp / sp).rename("gsr")
    peak = gsr.rolling(GSR_PEAK_WINDOW, min_periods=GSR_PEAK_WINDOW).max()
    fall = ((gsr - peak) / peak) <= -GSR_FALL_PCT
    t1   = ((gsr > GSR_T1) & fall).astype(int).shift(1).rename("t1")
    t2   = ((gsr > GSR_T2) & fall).astype(int).shift(1).rename("t2")
    return t1, t2


# ── simulation ────────────────────────────────────────────────────────────────

def simulate(prices_df, gs, t1, t2, gold_floor):
    ret = prices_df.pct_change().iloc[1:]
    idx = ret.index

    def ar(sym):
        return ret[sym].fillna(0.0) if sym in ret.columns else pd.Series(0.0, index=idx)

    gs_ = gs.reindex(idx, method="ffill").fillna(0.0)
    t1_ = t1.reindex(idx, method="ffill").fillna(0.0)
    t2_ = t2.reindex(idx, method="ffill").fillna(0.0)

    # Gold: floor + signal-gated upper tranche
    gold_w   = gold_floor + (GOLD_W - gold_floor) * gs_
    silver_w = 0.05 * t1_ + 0.05 * t2_

    vrt_w = pd.Series(np.where(idx >= VRT_IPO, STATIC_W["VRT"], 0.0), index=idx)
    cash_w = (GOLD_W - gold_w) + (SILVER_W - silver_w) + (STATIC_W["VRT"] - vrt_w)

    port_ret = (
        gold_w           * ar("GC=F")  +
        silver_w         * ar("SI=F")  +
        STATIC_W["LLY"]  * ar("LLY")   +
        STATIC_W["WMT"]  * ar("WMT")   +
        STATIC_W["JNJ"]  * ar("JNJ")   +
        STATIC_W["CCJ"]  * ar("CCJ")   +
        STATIC_W["AVGO"] * ar("AVGO")  +
        vrt_w            * ar("VRT")   +
        cash_w           * CASH_DAILY
    )
    nav = (1.0 + port_ret).cumprod()
    return nav, port_ret


def metrics(nav, rets):
    cal_days = (nav.index[-1] - nav.index[0]).days
    years    = max(cal_days / 365.25, 1e-6)
    gaps     = pd.Series(nav.index).diff().dt.days.dropna()
    ppy      = 365.25 / gaps.median() if gaps.median() > 0 else 252
    cagr     = nav.iloc[-1] ** (1.0 / years) - 1
    ann_ret  = rets.mean() * ppy
    ann_vol  = rets.std()  * np.sqrt(ppy)
    sharpe   = ann_ret / ann_vol if ann_vol > 0 else np.nan
    peak     = nav.expanding().max()
    dd       = (nav - peak) / peak
    max_dd   = dd.min()
    calmar   = cagr / abs(max_dd) if max_dd < 0 else np.nan
    return {"cagr": cagr, "vol": ann_vol, "sharpe": sharpe,
            "max_dd": max_dd, "calmar": calmar, "total": nav.iloc[-1]-1}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    SEP = "=" * 84

    print(SEP)
    print(f"  GOLD FLOOR OPTIMISATION  |  {date.today()}")
    print(f"  Sweeping floor: {[f'{f:.1%}' for f in FLOOR_STEPS]}")
    print(SEP)

    # fetch
    print("\nFetching DFII10...")
    dfii10 = fetch_fred("DFII10")
    if dfii10 is None: print("ERROR: DFII10"); sys.exit(1)
    print(f"  {dfii10.index[0].date()} → {dfii10.index[-1].date()}")
    time.sleep(0.3)

    tickers = {"GC=F":"Gold","SI=F":"Silver","LLY":"LLY","WMT":"WMT",
               "JNJ":"JNJ","CCJ":"CCJ","VRT":"VRT","AVGO":"AVGO"}
    print("Fetching prices...")
    prices_raw = {}
    for sym, name in tickers.items():
        print(f"  {name} ({sym})...")
        p = fetch_yahoo(sym)
        if p is None: print(f"ERROR: {sym}"); sys.exit(1)
        prices_raw[sym] = p
        time.sleep(0.3)

    gs = compute_gold_signal(dfii10)
    t1, t2 = compute_silver_signals(prices_raw["GC=F"], prices_raw["SI=F"])

    prices_is  = pd.DataFrame(prices_raw).sort_index().loc[IS_START:IS_END].ffill()
    prices_oos = pd.DataFrame(prices_raw).sort_index().loc[OOS_START:OOS_END].ffill()

    # sweep
    results = []
    print(f"\nSweeping {len(FLOOR_STEPS)} floor values...")
    for fl in FLOOR_STEPS:
        nav_is,  ret_is  = simulate(prices_is,  gs, t1, t2, fl)
        nav_oos, ret_oos = simulate(prices_oos, gs, t1, t2, fl)
        m_is  = metrics(nav_is,  ret_is)
        m_oos = metrics(nav_oos, ret_oos)
        label = ("baseline" if fl == GOLD_W
                 else ("orig-tact" if fl == 0.0
                       else f"floor {fl:.1%}"))
        results.append({
            "floor":      fl,
            "label":      label,
            "is_sharpe":  m_is["sharpe"],
            "is_calmar":  m_is["calmar"],
            "is_cagr":    m_is["cagr"],
            "is_vol":     m_is["vol"],
            "is_maxdd":   m_is["max_dd"],
            "is_total":   m_is["total"],
            "oos_sharpe": m_oos["sharpe"],
            "oos_calmar": m_oos["calmar"],
            "oos_cagr":   m_oos["cagr"],
            "oos_maxdd":  m_oos["max_dd"],
            "combined":   m_is["sharpe"] + m_oos["sharpe"],
        })
        print(f"  floor={fl:.1%}  IS Sharpe={m_is['sharpe']:.3f}  "
              f"OOS Sharpe={m_oos['sharpe']:.3f}  "
              f"IS CAGR={m_is['cagr']:.2%}  IS MaxDD={m_is['max_dd']:.2%}")

    df = pd.DataFrame(results)

    # ── Full table ────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  FULL SWEEP RESULTS  (sorted by floor value)")
    print(f"  {'-'*80}")
    print(f"  {'Floor':<12}  {'IS Sharpe':>10}  {'IS Calmar':>10}  {'IS CAGR':>9}  "
          f"{'IS MaxDD':>9}  {'OOS Sharpe':>11}  {'OOS Calmar':>11}  {'Combined':>9}")
    print(f"  {'-'*80}")
    for _, r in df.iterrows():
        print(f"  {r['label']:<12}  {r['is_sharpe']:>10.3f}  {r['is_calmar']:>10.3f}  "
              f"{r['is_cagr']:>9.2%}  {r['is_maxdd']:>9.2%}  "
              f"{r['oos_sharpe']:>11.3f}  {r['oos_calmar']:>11.3f}  {r['combined']:>9.3f}")

    # ── Rankings ──────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  RANKINGS")
    print(f"  {'-'*80}")

    for sort_key, label in [
        ("is_sharpe",  "IS Sharpe"),
        ("is_calmar",  "IS Calmar"),
        ("combined",   "Combined IS+OOS Sharpe"),
    ]:
        ranked = df.sort_values(sort_key, ascending=False).reset_index(drop=True)
        best   = ranked.iloc[0]
        print(f"\n  Best by {label}:")
        print(f"    Floor = {best['floor']:.1%}  ({best['label']})")
        print(f"    IS   → Sharpe {best['is_sharpe']:.3f}  Calmar {best['is_calmar']:.3f}  "
              f"CAGR {best['is_cagr']:.2%}  MaxDD {best['is_maxdd']:.2%}")
        print(f"    OOS  → Sharpe {best['oos_sharpe']:.3f}  Calmar {best['oos_calmar']:.3f}  "
              f"CAGR {best['oos_cagr']:.2%}  MaxDD {best['oos_maxdd']:.2%}")
        print(f"    Top 3: ", end="")
        for i in range(min(3, len(ranked))):
            r = ranked.iloc[i]
            print(f"{r['floor']:.1%} ({r[sort_key]:.3f})", end="  ")
        print()

    # ── IS Sharpe curve (ASCII) ───────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  IS SHARPE vs FLOOR  (ASCII curve)")
    print(f"  {'-'*60}")
    sharpes   = df["is_sharpe"].values
    floors    = df["floor"].values
    s_min, s_max = sharpes.min(), sharpes.max()
    s_range   = s_max - s_min or 1.0
    HEIGHT    = 8
    WIDTH     = len(FLOOR_STEPS)

    grid = [[" "] * WIDTH for _ in range(HEIGHT)]
    for j, s in enumerate(sharpes):
        row = HEIGHT - 1 - int((s - s_min) / s_range * (HEIGHT - 1))
        row = max(0, min(HEIGHT - 1, row))
        grid[row][j] = "●"

    for i, row in enumerate(grid):
        val = s_max - i * s_range / (HEIGHT - 1)
        print(f"  {val:5.3f} │ {'  '.join(row)}")
    print(f"         └{'───' * WIDTH}")

    floor_labels = [f"{f:.0%}" for f in floors]
    label_line   = "  ".join(f"{l:>3}" for l in floor_labels)
    print(f"          {label_line}")
    print(f"          floor →")

    # ── IS MaxDD curve ────────────────────────────────────────────────────────
    print(f"\n  IS MAX DRAWDOWN vs FLOOR  (smaller magnitude = better)")
    print(f"  {'-'*60}")
    maxdds  = df["is_maxdd"].values   # negative numbers, less negative = better
    d_min, d_max = maxdds.min(), maxdds.max()
    d_range = d_max - d_min or 1.0

    grid2 = [[" "] * WIDTH for _ in range(HEIGHT)]
    for j, d in enumerate(maxdds):
        row = HEIGHT - 1 - int((d - d_min) / d_range * (HEIGHT - 1))
        row = max(0, min(HEIGHT - 1, row))
        grid2[row][j] = "●"

    for i, row in enumerate(grid2):
        val = d_max - i * d_range / (HEIGHT - 1)
        print(f"  {val:6.2%} │ {'  '.join(row)}")
    print(f"          └{'───' * WIDTH}")
    print(f"          {label_line}")
    print(f"          floor →  (top = least drawdown / best)")

    print(f"\n{SEP}")
    print("  NOTES")
    print(f"  {'-'*60}")
    print("  floor=0.0%  →  original tactical (0% or 25% gold)")
    print("  floor=25.0% →  equivalent to fully deployed baseline")
    print("  silver signal unchanged across all floor values")
    print("  combined = IS_Sharpe + OOS_Sharpe (equal-weight stability metric)")
    print(SEP)


if __name__ == "__main__":
    main()
