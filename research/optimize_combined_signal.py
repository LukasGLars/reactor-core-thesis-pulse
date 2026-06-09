#!/usr/bin/env python3
"""
research/optimize_combined_signal.py

Tests: gold floor (15–20%) COMBINED WITH a "compressing real yields" trigger.

Gold deployment logic:
  gold_w = floor  +  (GOLD_W - floor) * combined_signal

  combined_signal = 1  when:
    (a) CvsTC negative:  DFII10 < 90d SMA  (original signal), OR
    (b) RY compressing:  DFII10 today < DFII10 N days ago  (new trigger)
  combined_signal = 0  when both are false (RY above trend AND rising)

Sweep:
  floors             : 0.000, 0.125, 0.150, 0.175, 0.200
  compress windows   : 0 (CvsTC only), 10d, 20d, 30d

20 combinations total. IS 2016–2026 + OOS 2009–2016.

The 2025 thesis: gold rallied while DFII10 was above its 90d SMA but ACTIVELY
FALLING — the compress signal would have kept the upper tranche deployed.
"""

import os, sys, time
import requests
import numpy  as np
import pandas as pd
from datetime import date

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

IS_START  = "2016-04-01";  IS_END  = "2026-03-31"
OOS_START = "2009-01-01";  OOS_END = "2016-03-31"

DFII10_SMA_WINDOW  = 90
GSR_T1, GSR_T2     = 83.36, 86.45
GSR_PEAK_WINDOW    = 60
GSR_FALL_PCT       = 0.05

GOLD_W   = 0.25
SILVER_W = 0.10
STATIC_W = {"LLY":0.15,"WMT":0.15,"JNJ":0.06,"CCJ":0.10,"VRT":0.10,"AVGO":0.09}
VRT_IPO    = pd.Timestamp("2020-02-07")
CASH_DAILY = 0.03 / 365

FLOORS   = [0.000, 0.125, 0.150, 0.175, 0.200]
COMPRESS = [0, 10, 20, 30]    # 0 = CvsTC only (no compress trigger)


# ── fetch ─────────────────────────────────────────────────────────────────────

def fetch_fred(series_id):
    if FRED_API_KEY:
        url = (f"https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&sort_order=asc")
    else:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "combined-opt/1.0"}, timeout=30)
            r.raise_for_status()
            if FRED_API_KEY:
                obs  = r.json().get("observations", [])
                rows = {o["date"]: float(o["value"]) for o in obs
                        if o.get("value") not in (".","")}
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

def build_gold_signal(dfii10: pd.Series, compress_window: int) -> pd.Series:
    """
    combined = CvsTC_negative  OR  RY_compressing
    CvsTC_negative: DFII10 < 90d SMA
    RY_compressing: DFII10 < DFII10 N days ago  (0 = disabled)
    1-day lag applied.
    """
    sma   = dfii10.rolling(DFII10_SMA_WINDOW, min_periods=DFII10_SMA_WINDOW).mean()
    cvstc = dfii10 < sma   # True when yield below trend

    if compress_window > 0:
        ry_fall  = dfii10 < dfii10.shift(compress_window)   # True when yield is falling
        combined = (cvstc | ry_fall).astype(int)
    else:
        combined = cvstc.astype(int)

    return combined.shift(1).fillna(0).rename("gs")


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

    gold_w   = gold_floor + (GOLD_W - gold_floor) * gs_
    silver_w = 0.05 * t1_ + 0.05 * t2_
    vrt_w    = pd.Series(np.where(idx >= VRT_IPO, STATIC_W["VRT"], 0.0), index=idx)
    cash_w   = (GOLD_W - gold_w) + (SILVER_W - silver_w) + (STATIC_W["VRT"] - vrt_w)

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
            "max_dd": max_dd, "calmar": calmar}


def signal_on_pct(gs, prices_df):
    idx = prices_df.pct_change().iloc[1:].index
    return gs.reindex(idx, method="ffill").fillna(0).mean() * 100


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    SEP = "=" * 88

    print(SEP)
    print(f"  COMBINED SIGNAL OPTIMISATION  |  {date.today()}")
    print(f"  Signal:  (DFII10 < 90d SMA)  OR  (DFII10 < DFII10[N days ago])")
    print(f"  Floors:  {[f'{f:.1%}' for f in FLOORS]}")
    print(f"  Windows: {COMPRESS}  (0 = CvsTC only)")
    print(SEP)

    # fetch
    print("\nFetching DFII10...")
    dfii10 = fetch_fred("DFII10")
    if dfii10 is None: print("ERROR"); sys.exit(1)
    print(f"  {dfii10.index[0].date()} → {dfii10.index[-1].date()}")
    time.sleep(0.3)

    tickers = {"GC=F":"Gold","SI=F":"Silver","LLY":"LLY","WMT":"WMT",
               "JNJ":"JNJ","CCJ":"CCJ","VRT":"VRT","AVGO":"AVGO"}
    print("Fetching prices...")
    prices_raw = {}
    for sym, name in tickers.items():
        print(f"  {name}...")
        p = fetch_yahoo(sym)
        if p is None: print(f"ERROR: {sym}"); sys.exit(1)
        prices_raw[sym] = p
        time.sleep(0.3)

    t1, t2 = compute_silver_signals(prices_raw["GC=F"], prices_raw["SI=F"])

    prices_is  = pd.DataFrame(prices_raw).sort_index().loc[IS_START:IS_END].ffill()
    prices_oos = pd.DataFrame(prices_raw).sort_index().loc[OOS_START:OOS_END].ffill()

    # pre-build all signals (floor doesn't affect signal, only compress window does)
    signals = {cw: build_gold_signal(dfii10, cw) for cw in COMPRESS}

    # print signal ON% diagnostics
    print(f"\nGold signal ON% (IS period, across compress windows):")
    for cw, gs in signals.items():
        on_pct = signal_on_pct(gs, prices_is)
        label  = f"CvsTC only" if cw == 0 else f"CvsTC OR compress {cw}d"
        print(f"  {label:<30}: {on_pct:.1f}% ON")

    # sweep
    print(f"\nSweeping {len(FLOORS) * len(COMPRESS)} combinations...")
    results = []
    for cw in COMPRESS:
        gs = signals[cw]
        for fl in FLOORS:
            nav_is,  ret_is  = simulate(prices_is,  gs, t1, t2, fl)
            nav_oos, ret_oos = simulate(prices_oos, gs, t1, t2, fl)
            m_is  = metrics(nav_is,  ret_is)
            m_oos = metrics(nav_oos, ret_oos)
            on_is  = signal_on_pct(gs, prices_is)
            combined = m_is["sharpe"] + m_oos["sharpe"]
            tag = (f"CvsTC+{cw}d" if cw > 0 else "CvsTC") + f" / fl={fl:.1%}"
            results.append({
                "compress":   cw,
                "floor":      fl,
                "tag":        tag,
                "is_sharpe":  m_is["sharpe"],
                "is_calmar":  m_is["calmar"],
                "is_cagr":    m_is["cagr"],
                "is_maxdd":   m_is["max_dd"],
                "is_vol":     m_is["vol"],
                "oos_sharpe": m_oos["sharpe"],
                "oos_calmar": m_oos["calmar"],
                "oos_cagr":   m_oos["cagr"],
                "oos_maxdd":  m_oos["max_dd"],
                "on_is_pct":  on_is,
                "combined":   combined,
            })

    df = pd.DataFrame(results)

    # ── Full table ────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  FULL RESULTS — grouped by compress window")
    HDR = (f"  {'Config':<28}  {'OnIS%':>6}  {'ISSh':>6}  {'ISCal':>7}  "
           f"{'ISCAGR':>8}  {'ISMaxDD':>8}  {'OOSSh':>6}  {'OOSCal':>7}  {'Comb':>6}")
    DIV = "  " + "-" * 84
    print(HDR); print(DIV)

    for cw in COMPRESS:
        sub = df[df["compress"] == cw].sort_values("floor")
        label = "— CvsTC only (no compress) —" if cw == 0 else f"— CvsTC OR compress {cw}d —"
        print(f"\n  {label}")
        for _, r in sub.iterrows():
            fl_label = (f"fl={r['floor']:.1%}"
                        + (" [ref-0%]"  if r['floor']==0.000 else
                           " [ref-12.5%]" if r['floor']==0.125 else ""))
            print(f"  {fl_label:<28}  {r['on_is_pct']:>5.1f}%  "
                  f"{r['is_sharpe']:>6.3f}  {r['is_calmar']:>7.3f}  "
                  f"{r['is_cagr']:>8.2%}  {r['is_maxdd']:>8.2%}  "
                  f"{r['oos_sharpe']:>6.3f}  {r['oos_calmar']:>7.3f}  "
                  f"{r['combined']:>6.3f}")

    # ── Rankings ──────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  RANKINGS  (top 5 by each metric)")
    print(DIV)

    for metric, label in [
        ("combined",   "Combined IS+OOS Sharpe"),
        ("is_sharpe",  "IS Sharpe"),
        ("oos_sharpe", "OOS Sharpe"),
        ("is_calmar",  "IS Calmar"),
    ]:
        show_cols = ["tag", metric, "is_sharpe", "oos_sharpe", "is_cagr", "is_maxdd", "oos_cagr"]
        show_cols = list(dict.fromkeys(show_cols))  # drop duplicate when metric is one of the base cols
        top5 = df.nlargest(5, metric)[show_cols]
        print(f"\n  Top 5 by {label}:")
        print(f"  {'Config':<35}  {metric:>8}  {'ISSh':>6}  {'OOSSh':>6}  "
              f"{'ISCAGR':>8}  {'ISMaxDD':>8}  {'OOScagr':>8}")
        for _, r in top5.iterrows():
            print(f"  {r['tag']:<35}  {r[metric]:>8.3f}  {r['is_sharpe']:>6.3f}  "
                  f"{r['oos_sharpe']:>6.3f}  {r['is_cagr']:>8.2%}  "
                  f"{r['is_maxdd']:>8.2%}  {r['oos_cagr']:>8.2%}")

    # ── Focus: 15–20% floor, all compress windows ─────────────────────────────
    print(f"\n{SEP}")
    print("  FOCUS: floors 15–20%  vs  compress window")
    print(DIV)
    focus = df[df["floor"].isin([0.150, 0.175, 0.200])].sort_values(
        ["floor","compress"])
    print(HDR); print(DIV)
    for _, r in focus.iterrows():
        print(f"  {r['tag']:<28}  {r['on_is_pct']:>5.1f}%  "
              f"{r['is_sharpe']:>6.3f}  {r['is_calmar']:>7.3f}  "
              f"{r['is_cagr']:>8.2%}  {r['is_maxdd']:>8.2%}  "
              f"{r['oos_sharpe']:>6.3f}  {r['oos_calmar']:>7.3f}  "
              f"{r['combined']:>6.3f}")

    # ── 2025 year-by-year for focus configs ──────────────────────────────────
    print(f"\n{SEP}")
    print("  YEAR-BY-YEAR IS — focus configs vs baseline  (how 2025 looks)")
    print(DIV)

    # baseline: floor=25% (fully deployed), no compress
    gs_base = signals[0]
    nav_base_is, _ = simulate(prices_is, gs_base, t1, t2, 0.25)

    focus_configs = [(0.150, 0), (0.150, 20), (0.175, 0), (0.175, 20),
                     (0.200, 0), (0.200, 20)]

    navs = {"Baseline": nav_base_is}
    for fl, cw in focus_configs:
        nav_f, _ = simulate(prices_is, signals[cw], t1, t2, fl)
        label = f"fl={fl:.1%}/cw={cw}d" if cw > 0 else f"fl={fl:.1%}/CvsTC"
        navs[label] = nav_f

    all_nav = pd.DataFrame(navs)
    print(f"  {'Year':<6}  " + "  ".join(f"{c:>18}" for c in all_nav.columns))
    print(f"  {'-' * (6 + 20*len(all_nav.columns))}")

    rets_df = all_nav.pct_change()
    for year, grp in rets_df.groupby(rets_df.index.year):
        annual = {c: (1 + grp[c].fillna(0)).prod() - 1 for c in all_nav.columns}
        row = f"  {year:<6}  " + "  ".join(f"{annual[c]:>18.2%}" for c in all_nav.columns)
        print(row)

    # ── Best single config summary ────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  RECOMMENDED CONFIG  (best combined IS+OOS Sharpe with floor 15–20%)")
    print(DIV)
    focus_top = df[df["floor"].isin([0.150, 0.175, 0.200])].nlargest(1, "combined").iloc[0]
    print(f"  Floor:            {focus_top['floor']:.1%}")
    print(f"  Compress window:  {int(focus_top['compress'])}d  "
          f"({'disabled' if focus_top['compress']==0 else 'DFII10 < DFII10[N days ago]'})")
    print(f"  IS   Sharpe: {focus_top['is_sharpe']:.3f}  Calmar: {focus_top['is_calmar']:.3f}  "
          f"CAGR: {focus_top['is_cagr']:.2%}  MaxDD: {focus_top['is_maxdd']:.2%}")
    print(f"  OOS  Sharpe: {focus_top['oos_sharpe']:.3f}  Calmar: {focus_top['oos_calmar']:.3f}  "
          f"CAGR: {focus_top['oos_cagr']:.2%}  MaxDD: {focus_top['oos_maxdd']:.2%}")
    print(f"  Combined:    {focus_top['combined']:.3f}")
    print(f"  Gold signal ON: {focus_top['on_is_pct']:.1f}% of IS days")
    print(SEP)


if __name__ == "__main__":
    main()
