#!/usr/bin/env python3
"""
research/jnj_lly_swap.py

Terminal-wealth comparison: drop JNJ (6%) → add to LLY.

Configs tested:
  Base:     LLY 15%  JNJ 6%
  Swap:     LLY 21%  JNJ 0%

Signal: floor=15%, compress=10d  (recommended config from tactical research)
IS:  2016-04-01 → 2026-03-31
OOS: 2009-01-01 → 2016-03-31

Year-by-year IS breakdown included.
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
COMPRESS_WINDOW   = 10
GOLD_FLOOR        = 0.15

GSR_T1, GSR_T2   = 83.36, 86.45
GSR_PEAK_WINDOW  = 60
GSR_FALL_PCT     = 0.05

GOLD_W   = 0.25
SILVER_W = 0.10
VRT_IPO  = pd.Timestamp("2020-02-07")
CASH_DAILY = 0.03 / 365

# Static weights shared between both configs
SHARED_W = {"WMT": 0.15, "CCJ": 0.10, "VRT": 0.10, "AVGO": 0.09}

CONFIGS = {
    "Base  (LLY 15%, JNJ 6%)": {"LLY": 0.15, "JNJ": 0.06},
    "Swap  (LLY 21%, JNJ 0%)": {"LLY": 0.21, "JNJ": 0.00},
}


# ── fetch ─────────────────────────────────────────────────────────────────────

def fetch_fred(series_id):
    if FRED_API_KEY:
        url = (f"https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&sort_order=asc")
    else:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "jnj-lly-swap/1.0"}, timeout=30)
            r.raise_for_status()
            if FRED_API_KEY:
                obs  = r.json().get("observations", [])
                rows = {o["date"]: float(o["value"]) for o in obs
                        if o.get("value") not in (".", "")}
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
            time.sleep(2 ** attempt)
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
            time.sleep(2 ** attempt)
    return None


# ── signals ───────────────────────────────────────────────────────────────────

def build_gold_signal(dfii10):
    sma      = dfii10.rolling(DFII10_SMA_WINDOW, min_periods=DFII10_SMA_WINDOW).mean()
    cvstc    = dfii10 < sma
    ry_fall  = dfii10 < dfii10.shift(COMPRESS_WINDOW)
    combined = (cvstc | ry_fall).astype(int)
    return combined.shift(1).fillna(0).rename("gs")


def compute_silver_signals(gp, sp):
    gsr  = (gp / sp).rename("gsr")
    peak = gsr.rolling(GSR_PEAK_WINDOW, min_periods=GSR_PEAK_WINDOW).max()
    fall = ((gsr - peak) / peak) <= -GSR_FALL_PCT
    t1   = ((gsr > GSR_T1) & fall).astype(int).shift(1).rename("t1")
    t2   = ((gsr > GSR_T2) & fall).astype(int).shift(1).rename("t2")
    return t1, t2


# ── simulation ────────────────────────────────────────────────────────────────

def simulate(prices_df, gs, t1, t2, lly_w, jnj_w):
    ret = prices_df.pct_change().iloc[1:]
    idx = ret.index

    def ar(sym):
        return ret[sym].fillna(0.0) if sym in ret.columns else pd.Series(0.0, index=idx)

    gs_ = gs.reindex(idx, method="ffill").fillna(0.0)
    t1_ = t1.reindex(idx, method="ffill").fillna(0.0)
    t2_ = t2.reindex(idx, method="ffill").fillna(0.0)

    gold_w   = GOLD_FLOOR + (GOLD_W - GOLD_FLOOR) * gs_
    silver_w = 0.05 * t1_ + 0.05 * t2_
    vrt_w    = pd.Series(np.where(idx >= VRT_IPO, SHARED_W["VRT"], 0.0), index=idx)
    cash_w   = (GOLD_W - gold_w) + (SILVER_W - silver_w) + (SHARED_W["VRT"] - vrt_w)

    port_ret = (
        gold_w            * ar("GC=F")  +
        silver_w          * ar("SI=F")  +
        lly_w             * ar("LLY")   +
        SHARED_W["WMT"]   * ar("WMT")   +
        jnj_w             * ar("JNJ")   +
        SHARED_W["CCJ"]   * ar("CCJ")   +
        SHARED_W["AVGO"]  * ar("AVGO")  +
        vrt_w             * ar("VRT")   +
        cash_w            * CASH_DAILY
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
            "max_dd": max_dd, "calmar": calmar, "terminal": nav.iloc[-1]}


def year_by_year(nav):
    """Annual returns from NAV series."""
    annual = {}
    for yr in range(nav.index[0].year, nav.index[-1].year + 1):
        yr_nav = nav[nav.index.year == yr]
        if len(yr_nav) < 2:
            continue
        annual[yr] = yr_nav.iloc[-1] / yr_nav.iloc[0] - 1
    return annual


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    SEP = "=" * 72
    print(SEP)
    print(f"  JNJ → LLY SWAP  |  {date.today()}")
    print(f"  Signal: floor={GOLD_FLOOR:.0%}, compress={COMPRESS_WINDOW}d")
    print(f"  IS:  {IS_START} → {IS_END}")
    print(f"  OOS: {OOS_START} → {OOS_END}")
    print(SEP)

    print("\nFetching DFII10...")
    dfii10 = fetch_fred("DFII10")
    if dfii10 is None:
        print("ERROR: could not fetch DFII10"); sys.exit(1)
    print(f"  {dfii10.index[0].date()} → {dfii10.index[-1].date()}")
    time.sleep(0.3)

    tickers = {"GC=F": "Gold", "SI=F": "Silver", "LLY": "LLY",
               "WMT": "WMT", "JNJ": "JNJ", "CCJ": "CCJ",
               "VRT": "VRT", "AVGO": "AVGO"}
    print("Fetching prices...")
    prices_raw = {}
    for sym, name in tickers.items():
        print(f"  {name}...")
        s = fetch_yahoo(sym)
        if s is not None:
            prices_raw[sym] = s
        time.sleep(0.2)

    prices_df = pd.DataFrame(prices_raw).sort_index()

    # build signals
    gs = build_gold_signal(dfii10)
    t1, t2 = compute_silver_signals(prices_raw["GC=F"], prices_raw["SI=F"])

    results_is  = {}
    results_oos = {}
    navs_is     = {}

    for label, pharma_w in CONFIGS.items():
        lly_w = pharma_w["LLY"]
        jnj_w = pharma_w["JNJ"]

        # IS
        p_is = prices_df.loc[IS_START:IS_END]
        nav_is, ret_is = simulate(p_is, gs, t1, t2, lly_w, jnj_w)
        results_is[label]  = metrics(nav_is, ret_is)
        navs_is[label]     = nav_is

        # OOS
        p_oos = prices_df.loc[OOS_START:OOS_END]
        nav_oos, ret_oos = simulate(p_oos, gs, t1, t2, lly_w, jnj_w)
        results_oos[label] = metrics(nav_oos, ret_oos)

    # ── print results ─────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  IN-SAMPLE RESULTS  (2016-04-01 → 2026-03-31)")
    print(f"  {'Config':<32}  {'Sharpe':>7}  {'Calmar':>7}  {'CAGR':>8}  {'MaxDD':>8}  {'Terminal':>9}")
    print(f"  {'-'*70}")
    for label, m in results_is.items():
        print(f"  {label:<32}  {m['sharpe']:7.3f}  {m['calmar']:7.3f}  "
              f"{m['cagr']:7.2%}  {m['max_dd']:7.2%}  {m['terminal']:9.4f}x")

    print(f"\n  DELTA (Swap − Base)")
    base_is  = results_is[list(CONFIGS.keys())[0]]
    swap_is  = results_is[list(CONFIGS.keys())[1]]
    print(f"  {'Sharpe':>10}  {'Calmar':>8}  {'CAGR':>8}  {'MaxDD':>8}  {'Terminal':>10}")
    print(f"  {swap_is['sharpe']-base_is['sharpe']:+10.3f}  "
          f"{swap_is['calmar']-base_is['calmar']:+8.3f}  "
          f"{swap_is['cagr']-base_is['cagr']:+8.2%}  "
          f"{swap_is['max_dd']-base_is['max_dd']:+8.2%}  "
          f"{swap_is['terminal']-base_is['terminal']:+10.4f}x")

    print(f"\n{SEP}")
    print("  OUT-OF-SAMPLE RESULTS  (2009-01-01 → 2016-03-31)")
    print(f"  {'Config':<32}  {'Sharpe':>7}  {'Calmar':>7}  {'CAGR':>8}  {'MaxDD':>8}  {'Terminal':>9}")
    print(f"  {'-'*70}")
    for label, m in results_oos.items():
        print(f"  {label:<32}  {m['sharpe']:7.3f}  {m['calmar']:7.3f}  "
              f"{m['cagr']:7.2%}  {m['max_dd']:7.2%}  {m['terminal']:9.4f}x")

    print(f"\n  DELTA (Swap − Base)")
    base_oos = results_oos[list(CONFIGS.keys())[0]]
    swap_oos = results_oos[list(CONFIGS.keys())[1]]
    print(f"  {'Sharpe':>10}  {'Calmar':>8}  {'CAGR':>8}  {'MaxDD':>8}  {'Terminal':>10}")
    print(f"  {swap_oos['sharpe']-base_oos['sharpe']:+10.3f}  "
          f"{swap_oos['calmar']-base_oos['calmar']:+8.3f}  "
          f"{swap_oos['cagr']-base_oos['cagr']:+8.2%}  "
          f"{swap_oos['max_dd']-base_oos['max_dd']:+8.2%}  "
          f"{swap_oos['terminal']-base_oos['terminal']:+10.4f}x")

    # ── year-by-year IS ───────────────────────────────────────────────────────
    labels = list(CONFIGS.keys())
    yby_base = year_by_year(navs_is[labels[0]])
    yby_swap = year_by_year(navs_is[labels[1]])

    print(f"\n{SEP}")
    print("  YEAR-BY-YEAR IS — Base vs Swap vs Delta")
    print(f"  {'Year':>6}  {'Base':>9}  {'Swap':>9}  {'Δ':>8}")
    print(f"  {'-'*38}")
    for yr in sorted(set(yby_base) | set(yby_swap)):
        b = yby_base.get(yr, float("nan"))
        s = yby_swap.get(yr, float("nan"))
        d = s - b if not (pd.isna(b) or pd.isna(s)) else float("nan")
        print(f"  {yr:>6}  {b:8.2%}  {s:8.2%}  {d:+7.2%}")

    print(f"\n{SEP}")


if __name__ == "__main__":
    main()
