#!/usr/bin/env python3
"""
research/gsr_signal_check.py

1. Live GSR status — current ratio, 60d peak, pullback %, T1/T2 signal state
2. Tactical silver backtest — compare:
     Static-4%   : silver always 4% (current real position)
     Tactical T1/T2: silver 0% / 5% / 10% based on GSR triggers
     Static-10%  : silver always at full target weight
   Gold allocation is dynamic (compress/neutral) in all modes.
   Remaining equity weights fixed at current V3 (LLY=21%, WMT=15%, CCJ=10%, VRT=10%, AVGO=9%).
"""

import os, sys, time, requests
import numpy  as np
import pandas as pd
from datetime import date

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# ── thresholds (from tactical_deployment_backtest.py) ────────────────────────
GSR_T1        = 83.36   # GSR above this → consider T1
GSR_T2        = 86.45   # GSR above this → consider T2
PEAK_PULLBACK = 0.05    # silver must be >= 5% below 60d rolling peak
PEAK_WINDOW   = 60      # calendar? trading days — use trading days

# ── portfolio ─────────────────────────────────────────────────────────────────
GOLD_MAX     = 0.25
GOLD_NEUTRAL = 0.15
SILVER_T1_W  = 0.05
SILVER_T2_W  = 0.10
SILVER_FLAT4 = 0.04
EQUITY_W     = {"lly": 0.21, "wmt": 0.15, "ccj": 0.10, "vrt": 0.10, "avgo": 0.09}
CASH_DAILY   = 0.03 / 252

# ── gold signal ───────────────────────────────────────────────────────────────
DFII10_SMA   = 90
COMPRESS_WIN = 10

IS_START  = "2016-04-01"
IS_END    = "2026-03-31"

VRT_IPO = pd.Timestamp("2020-02-07")


# ── fetch ─────────────────────────────────────────────────────────────────────

def fetch_fred(series_id):
    url = (f"https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
           f"&observation_start=2007-01-01")
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            obs = r.json()["observations"]
            s = pd.Series(
                {o["date"]: float(o["value"]) for o in obs if o["value"] != "."},
                dtype=float)
            s.index = pd.to_datetime(s.index)
            return s.sort_index()
        except Exception as e:
            print(f"  FRED {series_id} attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None


def fetch_yahoo(symbol, label):
    import datetime as dt
    p1  = int(dt.datetime(2007, 1, 1).timestamp())
    p2  = int(dt.datetime.now().timestamp())
    enc = symbol.replace("=", "%3D").replace("^", "%5E")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}"
           f"?interval=1d&period1={p1}&period2={p2}")
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            ts  = pd.to_datetime(res["timestamp"], unit="s").normalize()
            adj = res["indicators"].get("adjclose", [{}])
            closes = (adj[0].get("adjclose") if adj and adj[0].get("adjclose")
                      else res["indicators"]["quote"][0]["close"])
            s = pd.Series(closes, index=ts, dtype=float).dropna().sort_index()
            return s
        except Exception as e:
            print(f"  {label} attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None


# ── GSR signal ────────────────────────────────────────────────────────────────

def build_gsr_signals(gold, silver):
    gsr         = gold / silver
    peak_60d    = silver.rolling(PEAK_WINDOW, min_periods=PEAK_WINDOW // 2).max()
    pullback    = (silver - peak_60d) / peak_60d      # negative when below peak
    pulled_back = pullback <= -PEAK_PULLBACK

    t1 = ((gsr > GSR_T1) & pulled_back).astype(int).shift(1).fillna(0)
    t2 = ((gsr > GSR_T2) & pulled_back).astype(int).shift(1).fillna(0)
    return gsr, peak_60d, pullback, t1, t2


def build_gold_signal(dfii10):
    sma        = dfii10.rolling(DFII10_SMA, min_periods=DFII10_SMA // 2).mean()
    below_sma  = dfii10 < sma
    ry_falling = dfii10 < dfii10.shift(COMPRESS_WIN)
    compress   = (below_sma | ry_falling).astype(int).shift(1).fillna(0)
    return compress


# ── simulate ──────────────────────────────────────────────────────────────────

def simulate(prices, dfii10, silver_mode, t1_sig, t2_sig, start, end, label):
    idx      = prices.loc[start:end].index
    compress = build_gold_signal(dfii10).reindex(idx, method="ffill").fillna(0)
    gold_w_  = np.where(compress.astype(bool), GOLD_MAX, GOLD_NEUTRAL)

    t1_ = t1_sig.reindex(idx, method="ffill").fillna(0)
    t2_ = t2_sig.reindex(idx, method="ffill").fillna(0)

    eq_names = list(EQUITY_W.keys())

    rets = pd.DataFrame(index=idx)
    rets["gold"]   = prices["gold"].pct_change().reindex(idx)
    rets["silver"] = prices["silver"].pct_change().reindex(idx)
    for k in eq_names:
        rets[k] = prices[k].pct_change().reindex(idx)

    port_ret = pd.Series(0.0, index=idx)
    for i, dt_ in enumerate(idx):
        gw = gold_w_[i]

        if silver_mode == "tactical":
            if t2_.iloc[i]:
                sw = SILVER_T2_W
            elif t1_.iloc[i]:
                sw = SILVER_T1_W
            else:
                sw = 0.0
        elif silver_mode == "flat4":
            sw = SILVER_FLAT4
        elif silver_mode == "flat10":
            sw = SILVER_T2_W
        else:
            sw = 0.0

        vrt_w = EQUITY_W.get("vrt", 0.0) if dt_ >= VRT_IPO else 0.0
        eq_w  = {k: (vrt_w if k == "vrt" else EQUITY_W[k]) for k in eq_names}
        cash_w = max(0.0, 1.0 - gw - sw - sum(eq_w.values()))

        day_ret = cash_w * CASH_DAILY
        for sym_, w_ in [("gold", gw), ("silver", sw)] + list(eq_w.items()):
            if w_ == 0.0:
                continue
            r_ = rets[sym_].iloc[i]
            day_ret += w_ * (CASH_DAILY if np.isnan(r_) else r_)
        port_ret.iloc[i] = day_ret

    cum     = (1 + port_ret).cumprod()
    ann_vol = port_ret.std() * np.sqrt(252)
    n_years = len(idx) / 252
    cagr    = cum.iloc[-1] ** (1 / n_years) - 1
    sharpe  = (port_ret.mean() * 252) / ann_vol if ann_vol > 0 else 0
    max_dd  = ((cum - cum.cummax()) / cum.cummax()).min()

    print(f"  {label:<35}  CAGR {cagr*100:+.2f}%  Sharpe {sharpe:.3f}"
          f"  MaxDD {max_dd*100:.2f}%  Terminal {cum.iloc[-1]:.3f}x")
    return {"cagr": cagr, "sharpe": sharpe, "max_dd": max_dd,
            "terminal": cum.iloc[-1], "cum": cum}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"GSR Signal Check  |  {date.today()}")

    if not FRED_API_KEY:
        print("ERROR: FRED_API_KEY not set"); sys.exit(1)

    print("\nFetching prices...")
    gold_s   = fetch_yahoo("GC=F",  "Gold")
    silver_s = fetch_yahoo("SI=F",  "Silver")
    if gold_s is None or silver_s is None:
        print("ERROR: gold/silver fetch failed"); sys.exit(1)

    raw = {
        "gold":  gold_s,
        "silver": silver_s,
        "lly":   fetch_yahoo("LLY",  "LLY"),
        "wmt":   fetch_yahoo("WMT",  "WMT"),
        "ccj":   fetch_yahoo("CCJ",  "CCJ"),
        "vrt":   fetch_yahoo("VRT",  "VRT"),
        "avgo":  fetch_yahoo("AVGO", "AVGO"),
    }
    failed = [k for k, v in raw.items() if v is None]
    if failed:
        print(f"ERROR: missing {failed}"); sys.exit(1)

    prices = pd.DataFrame(raw).sort_index().ffill(limit=3)

    print("\nFetching DFII10...")
    dfii10 = fetch_fred("DFII10")
    if dfii10 is None:
        print("ERROR: DFII10 unavailable"); sys.exit(1)

    # ── live GSR status ───────────────────────────────────────────────────────
    gsr, peak_60d, pullback, t1_sig, t2_sig = build_gsr_signals(
        prices["gold"], prices["silver"])

    latest      = prices.index[-1]
    gsr_now     = gsr.iloc[-1]
    peak_now    = peak_60d.iloc[-1]
    pullback_pct= pullback.iloc[-1] * 100
    t1_now      = bool(t1_sig.iloc[-1])
    t2_now      = bool(t2_sig.iloc[-1])
    gold_px     = prices["gold"].iloc[-1]
    silver_px   = prices["silver"].iloc[-1]

    print(f"\n{'='*60}")
    print(f"  LIVE GSR STATUS  ({latest.date()})")
    print(f"{'='*60}")
    print(f"  Gold price       : ${gold_px:,.2f}")
    print(f"  Silver price     : ${silver_px:,.2f}")
    print(f"  GSR (gold/silver): {gsr_now:.2f}")
    print(f"  T1 threshold     : {GSR_T1:.2f}  {'✓ ABOVE' if gsr_now > GSR_T1 else f'▲ {GSR_T1 - gsr_now:.2f} pts away'}")
    print(f"  T2 threshold     : {GSR_T2:.2f}  {'✓ ABOVE' if gsr_now > GSR_T2 else f'▲ {GSR_T2 - gsr_now:.2f} pts away'}")
    print(f"  Silver 60d peak  : ${peak_now:,.2f}")
    print(f"  Pullback from pk : {pullback_pct:+.2f}%  (need <= -{PEAK_PULLBACK*100:.0f}%)")
    print(f"  T1 signal        : {'ON  ← deploy to 5%' if t1_now else 'OFF'}")
    print(f"  T2 signal        : {'ON  ← deploy to 10%' if t2_now else 'OFF'}")

    # recent signal history
    cutoff = prices.index[-1] - pd.Timedelta(days=90)
    recent = pd.DataFrame({
        "GSR": gsr, "Pullback%": pullback*100,
        "T1": t1_sig, "T2": t2_sig
    }).loc[cutoff:]
    t1_days = int(recent["T1"].sum())
    t2_days = int(recent["T2"].sum())
    print(f"\n  Last 90 days: T1 ON {t1_days} days  |  T2 ON {t2_days} days")

    # ── tactical backtest ─────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  TACTICAL SILVER BACKTEST  {IS_START} → {IS_END}")
    print(f"{'='*60}")
    print(f"  Signal stats (IS):")
    t1_is = t1_sig.loc[IS_START:IS_END]
    t2_is = t2_sig.loc[IS_START:IS_END]
    n     = len(t1_is)
    print(f"    T1 ON: {t1_is.mean()*100:.1f}% of days  |  T2 ON: {t2_is.mean()*100:.1f}% of days")

    print(f"\n  Mode definitions:")
    print(f"    Static-4%    : silver always 4% (current real position)")
    print(f"    Tactical T1/2: silver 0% / 5% / 10% on GSR signals")
    print(f"    Static-10%   : silver always at full 10% target")
    print(f"\n  Results:")

    r_flat4   = simulate(prices, dfii10, "flat4",    t1_sig, t2_sig, IS_START, IS_END, "Static-4%  (current)")
    r_tactical= simulate(prices, dfii10, "tactical", t1_sig, t2_sig, IS_START, IS_END, "Tactical T1/T2")
    r_flat10  = simulate(prices, dfii10, "flat10",   t1_sig, t2_sig, IS_START, IS_END, "Static-10% (full target)")
    r_none    = simulate(prices, dfii10, "none",     t1_sig, t2_sig, IS_START, IS_END, "No silver  (0%)")

    print(f"\n  Delta vs Static-4% (current):")
    for lbl, r in [("Tactical T1/T2", r_tactical), ("Static-10%", r_flat10), ("No silver", r_none)]:
        dc = r["cagr"]    - r_flat4["cagr"]
        ds = r["sharpe"]  - r_flat4["sharpe"]
        dt = r["terminal"]- r_flat4["terminal"]
        dd = r["max_dd"]  - r_flat4["max_dd"]
        print(f"    {lbl:<20}  CAGR {dc*100:+.2f}pp  Sharpe {ds:+.3f}"
              f"  Terminal {dt:+.3f}x  MaxDD {dd*100:+.2f}pp")


if __name__ == "__main__":
    main()
