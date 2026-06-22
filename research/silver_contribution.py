#!/usr/bin/env python3
"""
research/silver_contribution.py

Does silver add terminal wealth vs gold-only or proportional redistribution?

Three portfolios compared on IS (2016-04-01 → 2026-03-31):
  A  Current V3   : gold=25% silver=10% lly=21% wmt=15% ccj=10% vrt=10% avgo=9%
  B  Silver→Gold  : same but silver 10% moved to gold (gold=35%)
  C  Silver→Prop  : silver 10% redistributed proportionally across equities

Gold allocation is dynamic (compress/neutral/expand) for all portfolios.
"""

import os, sys, time, requests
import numpy  as np
import pandas as pd
from datetime import date

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

IS_START  = "2016-04-01"
IS_END    = "2026-03-31"
OOS_START = "2009-01-01"
OOS_END   = "2016-03-31"

# ── V3 current weights ────────────────────────────────────────────────────────
EQUITY_W = {"lly": 0.21, "wmt": 0.15, "ccj": 0.10, "vrt": 0.10, "avgo": 0.09}
GOLD_MAX     = 0.25
GOLD_NEUTRAL = 0.15
SILVER_W     = 0.10
CASH_DAILY   = 0.03 / 252

# Gold signal params
DFII10_SMA   = 90
COMPRESS_WIN = 10

# ── data fetch ────────────────────────────────────────────────────────────────

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
                dtype=float,
            )
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
            print(f"  {label}: {s.index[0].date()} to {s.index[-1].date()}  ({len(s):,} days)")
            return s
        except Exception as e:
            print(f"  {label} attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None


# ── signals ───────────────────────────────────────────────────────────────────

def build_gold_signal(dfii10):
    sma        = dfii10.rolling(DFII10_SMA, min_periods=DFII10_SMA // 2).mean()
    below_sma  = dfii10 < sma
    ry_falling = dfii10 < dfii10.shift(COMPRESS_WIN)
    compress   = (below_sma | ry_falling).astype(int).shift(1).fillna(0)
    return compress


# ── simulation ────────────────────────────────────────────────────────────────

def simulate(prices, dfii10, start, end, gold_max, silver_w, equity_weights, label):
    idx = prices.loc[start:end].index
    compress = build_gold_signal(dfii10).reindex(idx, method="ffill").fillna(0)

    gold_w   = np.where(compress.astype(bool), gold_max, GOLD_NEUTRAL)
    silver_w_ = silver_w  # scalar

    # equity weights (static)
    eq_names = list(equity_weights.keys())
    total_w  = gold_w + silver_w_ + sum(equity_weights.values())
    # cash = leftover (includes pre-VRT-IPO gap handled below)

    rets = pd.DataFrame(index=idx)
    rets["gold"]   = prices["gold"].pct_change().reindex(idx)
    rets["silver"] = prices["silver"].pct_change().reindex(idx)
    for k in eq_names:
        rets[k] = prices[k].pct_change().reindex(idx)

    VRT_IPO = pd.Timestamp("2020-02-07")

    port_ret = pd.Series(0.0, index=idx)
    for i, dt_ in enumerate(idx):
        gw = gold_w[i]
        sw = silver_w_
        vrt_w = equity_weights.get("vrt", 0.0) if dt_ >= VRT_IPO else 0.0
        eq_w = {k: (vrt_w if k == "vrt" else equity_weights[k]) for k in eq_names}
        cash_w = max(0.0, 1.0 - gw - sw - sum(eq_w.values()))

        day_ret = cash_w * CASH_DAILY
        for sym_, w_ in [("gold", gw), ("silver", sw)] + list(eq_w.items()):
            if w_ == 0.0:
                continue
            r_ = rets[sym_].iloc[i]
            if np.isnan(r_):
                day_ret += w_ * CASH_DAILY  # no price data → treat as cash
            else:
                day_ret += w_ * r_
        port_ret.iloc[i] = day_ret

    port_ret = port_ret.fillna(0.0)
    cum      = (1 + port_ret).cumprod()
    ann_vol  = port_ret.std() * np.sqrt(252)
    n_years  = len(idx) / 252
    cagr     = cum.iloc[-1] ** (1 / n_years) - 1
    sharpe   = (port_ret.mean() * 252) / ann_vol if ann_vol > 0 else 0
    roll_max = cum.cummax()
    dd       = (cum - roll_max) / roll_max
    max_dd   = dd.min()
    terminal = cum.iloc[-1]

    print(f"\n  {label}")
    print(f"    CAGR          {cagr*100:+.2f}%")
    print(f"    Ann Volatility {ann_vol*100:.2f}%")
    print(f"    Sharpe         {sharpe:.3f}")
    print(f"    Max Drawdown   {max_dd*100:.2f}%")
    print(f"    Terminal Wealth (×100 start) {terminal:.3f}x")

    return {"cagr": cagr, "vol": ann_vol, "sharpe": sharpe,
            "max_dd": max_dd, "terminal": terminal, "cum": cum}


def year_by_year(cum, start, end):
    rets_ = cum.pct_change().fillna(0)
    years = range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1)
    rows  = []
    for y in years:
        mask = rets_.index.year == y
        yr   = (1 + rets_[mask]).prod() - 1
        rows.append((y, yr))
    return rows


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Silver Contribution Analysis  |  {date.today()}")

    if not FRED_API_KEY:
        print("ERROR: FRED_API_KEY not set"); sys.exit(1)

    print("\nFetching DFII10...")
    dfii10 = fetch_fred("DFII10")
    if dfii10 is None:
        print("ERROR: DFII10 unavailable"); sys.exit(1)
    print(f"  {dfii10.index[0].date()} to {dfii10.index[-1].date()}")

    print("\nFetching prices...")
    raw = {
        "gold":  fetch_yahoo("GC=F",  "Gold (GC=F)"),
        "silver": fetch_yahoo("SI=F", "Silver (SI=F)"),
        "lly":   fetch_yahoo("LLY",   "LLY"),
        "wmt":   fetch_yahoo("WMT",   "WMT"),
        "ccj":   fetch_yahoo("CCJ",   "CCJ"),
        "vrt":   fetch_yahoo("VRT",   "VRT"),
        "avgo":  fetch_yahoo("AVGO",  "AVGO"),
    }
    failed = [k for k, v in raw.items() if v is None]
    if failed:
        print(f"ERROR: missing data for {failed}"); sys.exit(1)

    prices = pd.DataFrame(raw).sort_index().ffill(limit=3)

    # Equity weights for each scenario
    eq_base  = EQUITY_W.copy()  # lly=0.21, wmt=0.15, ccj=0.10, vrt=0.10, avgo=0.09

    # C: redistribute silver 10% proportionally to equities
    eq_total = sum(eq_base.values())  # 0.65
    scale    = (eq_total + SILVER_W) / eq_total
    eq_prop  = {k: round(v * scale, 6) for k, v in eq_base.items()}

    print(f"\nPortfolio configurations:")
    print(f"  A  Current V3    : gold={GOLD_MAX:.0%}(max) silver={SILVER_W:.0%} equities={sum(eq_base.values()):.0%}")
    print(f"  B  Silver→Gold   : gold={GOLD_MAX+SILVER_W:.0%}(max) silver=0% equities={sum(eq_base.values()):.0%}")
    print(f"  C  Silver→Prop   : gold={GOLD_MAX:.0%}(max) silver=0% equities={sum(eq_prop.values()):.1%}")

    for period_label, start, end in [
        ("IN-SAMPLE  2016-04-01 → 2026-03-31", IS_START, IS_END),
        ("OUT-OF-SAMPLE  2009-01-01 → 2016-03-31", OOS_START, OOS_END),
    ]:
        print(f"\n{'='*70}")
        print(f"  {period_label}")
        print(f"{'='*70}")

        rA = simulate(prices, dfii10, start, end,
                      GOLD_MAX, SILVER_W, eq_base,  "A  Current V3 (with silver)")
        rB = simulate(prices, dfii10, start, end,
                      GOLD_MAX + SILVER_W, 0.0, eq_base, "B  Silver→Gold (gold=35%)")
        rC = simulate(prices, dfii10, start, end,
                      GOLD_MAX, 0.0, eq_prop,  "C  Silver→Proportional equities")

        print(f"\n  Delta vs A (silver vs no-silver):")
        for label_, r_ in [("B (→Gold)", rB), ("C (→Prop)", rC)]:
            print(f"    {label_}:  CAGR {(r_['cagr']-rA['cagr'])*100:+.2f}pp  "
                  f"Sharpe {r_['sharpe']-rA['sharpe']:+.3f}  "
                  f"Terminal {r_['terminal']-rA['terminal']:+.3f}x  "
                  f"MaxDD {(r_['max_dd']-rA['max_dd'])*100:+.2f}pp")

        print(f"\n  Year-by-year  (A vs B vs C):")
        yA = year_by_year(rA["cum"], start, end)
        yB = year_by_year(rB["cum"], start, end)
        yC = year_by_year(rC["cum"], start, end)
        print(f"  {'Year':<6}  {'A (silver)':>12}  {'B (→gold)':>12}  {'C (→prop)':>12}  {'ΔB-A':>8}  {'ΔC-A':>8}")
        print(f"  {'-'*66}")
        for (yr, a), (_, b), (_, c) in zip(yA, yB, yC):
            print(f"  {yr:<6}  {a*100:>+11.2f}%  {b*100:>+11.2f}%  {c*100:>+11.2f}%  {(b-a)*100:>+7.2f}pp  {(c-a)*100:>+7.2f}pp")


if __name__ == "__main__":
    main()
