"""
calibration/silver_sop_analysis.py
What should silver behavior be outside signal windows?
Analyzes silver returns across all GSR regimes to derive a full SOP.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd
import time
from pathlib import Path
from datetime import date

try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    import requests
    _HAS_YF = False

ROLLING_PEAK_DAYS = 60
HORIZONS = [30, 60, 90, 180, 365]


def fetch_yahoo(symbol):
    if _HAS_YF:
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period="max", interval="1d", auto_adjust=True)
            if hist.empty:
                raise ValueError("empty")
            s = hist["Close"].copy()
            s.index = pd.DatetimeIndex(s.index).normalize().tz_localize(None)
            return s.dropna().sort_index()
        except Exception as e:
            print(f"  yfinance {symbol}: {e}, falling back")
    import requests
    enc = symbol.replace("=", "%3D")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}?interval=1d&range=max"
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            if r.status_code != 200:
                time.sleep(2 ** attempt); continue
            d = r.json()["chart"]["result"][0]
            ts = pd.to_datetime(d["timestamp"], unit="s")
            closes = d["indicators"]["quote"][0]["close"]
            s = pd.Series(closes, index=ts, dtype=float)
            s.index = s.index.normalize()
            return s.dropna().sort_index()
        except Exception as e:
            print(f"  {symbol} attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None


def fwd_return(series, idx_pos, h_days, paired=None):
    """Forward return from position idx_pos, h_days calendar days ahead."""
    entry_date = series.index[idx_pos]
    future = series.index[series.index >= entry_date + pd.Timedelta(days=h_days - 3)]
    if len(future) == 0:
        return np.nan, np.nan
    fi  = series.index.get_loc(future[0])
    ep  = series.iloc[idx_pos]
    fp  = series.iloc[fi]
    if ep == 0:
        return np.nan, np.nan
    ret = (fp - ep) / ep * 100
    pair_ret = np.nan
    if paired is not None and paired.iloc[idx_pos] > 0:
        ep2      = paired.iloc[idx_pos]
        fp2      = paired.iloc[fi]
        pair_ret = (fp2 - ep2) / ep2 * 100
    return ret, pair_ret


def regime_analysis(gsr, silver, gold, st):
    """Returns by GSR regime (low/normal/high/extreme)."""
    p25, p75, p85, p90 = st["p25"], st["p75"], st["p85"], st["p90"]
    peak = gsr.rolling(ROLLING_PEAK_DAYS, min_periods=10).max()
    signal_active = (gsr > p90) & ((gsr - peak) / peak * 100 <= -5)

    def label(g):
        if g < p25:   return "low"
        elif g < p75: return "normal"
        elif g < p85: return "high"
        elif g < p90: return "very_high"
        else:         return "extreme"

    results = {r: {h: [] for h in HORIZONS}
               for r in ["low","normal","high","very_high","extreme",
                         "signal_on","signal_off"]}

    gc = gold.reindex(silver.index, method="ffill")

    for i in range(len(gsr)):
        g    = float(gsr.iloc[i])
        reg  = label(g)
        sig  = bool(signal_active.iloc[i]) if not pd.isna(signal_active.iloc[i]) else False
        s_key = "signal_on" if sig else "signal_off"

        for h in HORIZONS:
            si_ret, gc_ret = fwd_return(silver, i, h, gc)
            if not np.isnan(si_ret):
                exc = si_ret - gc_ret if not np.isnan(gc_ret) else np.nan
                row = {"si": si_ret, "gc": gc_ret, "exc": exc}
                results[reg][h].append(row)
                results[s_key][h].append(row)

    return results


def summarize(rows):
    if not rows:
        return None
    n       = len(rows)
    si_rets = [r["si"]  for r in rows]
    gc_rets = [r["gc"]  for r in rows if not np.isnan(r["gc"])]
    excess  = [r["exc"] for r in rows if not np.isnan(r["exc"])]
    return {
        "n":          n,
        "hit_pos":    sum(1 for x in si_rets if x > 0) / n,
        "avg_si":     float(np.mean(si_rets)),
        "median_si":  float(np.median(si_rets)),
        "avg_exc":    float(np.mean(excess))  if excess else np.nan,
        "hit_gold":   sum(1 for x in excess if x > 0) / len(excess) if excess else np.nan,
    }


def hold_vs_reduce(gsr, silver, gold, st):
    """
    Compare three strategies over full history:
    A. Hold silver always (buy once, hold)
    B. Hold silver only during signal window
    C. Hold silver always, overweight 2x during signal window
    Simulate monthly rebalancing from first available date.
    """
    p90  = st["p90"]
    peak = gsr.rolling(ROLLING_PEAK_DAYS, min_periods=10).max()
    signal = (gsr > p90) & ((gsr - peak) / peak * 100 <= -5)
    gc   = gold.reindex(silver.index, method="ffill")

    # Use month-end observations
    si_m  = silver.resample("ME").last().dropna()
    gc_m  = gc.resample("ME").last().dropna()
    sig_m = signal.resample("ME").last().reindex(si_m.index).fillna(False)

    idx = si_m.index.intersection(gc_m.index)
    si_m  = si_m.loc[idx]
    gc_m  = gc_m.loc[idx]
    sig_m = sig_m.reindex(idx).fillna(False)

    def compound(returns):
        r = 1.0
        for x in returns:
            if not np.isnan(x):
                r *= (1 + x / 100)
        return (r - 1) * 100

    # Monthly returns
    si_ret_m  = si_m.pct_change().dropna() * 100
    gc_ret_m  = gc_m.pct_change().dropna() * 100
    sig_aligned = sig_m.reindex(si_ret_m.index).fillna(False)

    # Strategy A: always hold silver
    ret_A = compound(si_ret_m)

    # Strategy B: hold silver only when signal on
    ret_B_months = [r if sig_aligned.iloc[i] else 0.0
                    for i, r in enumerate(si_ret_m)]
    ret_B = compound(ret_B_months)

    # Strategy C: 1x normally, 2x during signal
    # (simplified: base + signal bonus)
    ret_C_months = [r * 2 if sig_aligned.iloc[i] else r
                    for i, r in enumerate(si_ret_m)]
    ret_C = compound(ret_C_months)

    # Gold baseline
    ret_GC = compound(gc_ret_m)

    # Signal-on vs signal-off monthly returns
    on_rets  = [r for r, s in zip(si_ret_m, sig_aligned) if s]
    off_rets = [r for r, s in zip(si_ret_m, sig_aligned) if not s]

    return {
        "strategy_A_total":    round(ret_A,  1),
        "strategy_B_total":    round(ret_B,  1),
        "strategy_C_total":    round(ret_C,  1),
        "gold_baseline_total": round(ret_GC, 1),
        "n_months_total":      len(si_ret_m),
        "n_signal_months":     int(sig_aligned.sum()),
        "avg_si_signal_on":    round(float(np.mean(on_rets)),  2) if on_rets  else np.nan,
        "avg_si_signal_off":   round(float(np.mean(off_rets)), 2) if off_rets else np.nan,
        "hit_signal_on":       round(sum(1 for x in on_rets  if x > 0) / len(on_rets),  2) if on_rets  else np.nan,
        "hit_signal_off":      round(sum(1 for x in off_rets if x > 0) / len(off_rets), 2) if off_rets else np.nan,
    }


def main():
    print("Fetching data...", flush=True)
    gold   = fetch_yahoo("GC=F");  time.sleep(1)
    silver = fetch_yahoo("SI=F")

    idx    = gold.index.intersection(silver.index)
    gold   = gold.loc[idx]
    silver = silver.loc[idx]
    gsr    = (gold / silver).dropna()

    st = {
        "p10": float(gsr.quantile(0.10)),
        "p25": float(gsr.quantile(0.25)),
        "p50": float(gsr.quantile(0.50)),
        "p75": float(gsr.quantile(0.75)),
        "p85": float(gsr.quantile(0.85)),
        "p90": float(gsr.quantile(0.90)),
    }

    print("Regime analysis...", flush=True)
    reg = regime_analysis(gsr, silver, gold, st)

    print("Strategy comparison...", flush=True)
    strats = hold_vs_reduce(gsr, silver, gold, st)

    def fmt(v, d=1):
        return f"{v:.{d}f}" if v is not None and not np.isnan(v) else "n/a"

    L = []
    def p(s=""): L.append(s)

    p("SILVER SOP ANALYSIS")
    p("=" * 70)
    p(f"Generated: {date.today().isoformat()}")
    p(f"Period:    {gsr.index[0].date()} to {gsr.index[-1].date()}  ({len(gsr):,} obs)")
    p()

    # Section 1: Silver returns by GSR regime
    p("=" * 70)
    p("SECTION 1 -- SILVER FORWARD RETURNS BY GSR REGIME")
    p("=" * 70)
    p()
    p(f"  GSR thresholds: low<{st['p25']:.1f} | normal {st['p25']:.1f}-{st['p75']:.1f} | "
      f"high {st['p75']:.1f}-{st['p85']:.1f} | very_high {st['p85']:.1f}-{st['p90']:.1f} | extreme>{st['p90']:.1f}")
    p()
    p(f"  {'Regime':<12}  {'H':>5}  {'N':>5}  {'Hit+':>6}  {'AvgSI%':>8}  {'MedSI%':>8}  {'HitGold':>8}  {'AvgExc%':>8}")
    p(f"  {'-'*72}")
    for regime in ["low","normal","high","very_high","extreme","signal_on","signal_off"]:
        for h in HORIZONS:
            rows = reg[regime][h]
            s    = summarize(rows)
            if not s:
                continue
            sep = "  ---" if h == HORIZONS[0] and regime in ["signal_on","signal_off"] else ""
            if sep:
                p(sep)
            p(f"  {regime:<12}  {h:>5}  {s['n']:>5}  {s['hit_pos']:>6.1%}  "
              f"{fmt(s['avg_si']):>8}  {fmt(s['median_si']):>8}  "
              f"{s['hit_gold']:>8.1%}  {fmt(s['avg_exc']):>8}")
    p()

    # Section 2: Strategy comparison
    p("=" * 70)
    p("SECTION 2 -- STRATEGY COMPARISON (full history, monthly compounded)")
    p("=" * 70)
    p()
    p(f"  Total months:          {strats['n_months_total']}")
    p(f"  Signal-on months:      {strats['n_signal_months']}  ({strats['n_signal_months']/strats['n_months_total']:.1%})")
    p(f"  Signal-off months:     {strats['n_months_total'] - strats['n_signal_months']}")
    p()
    p(f"  Avg silver return (signal ON):   {fmt(strats['avg_si_signal_on'])}%/mo  hit={strats['hit_signal_on']:.0%}")
    p(f"  Avg silver return (signal OFF):  {fmt(strats['avg_si_signal_off'])}%/mo  hit={strats['hit_signal_off']:.0%}")
    p()
    p(f"  Strategy A — hold always:                    {fmt(strats['strategy_A_total'], 0)}% total")
    p(f"  Strategy B — hold only during signal:        {fmt(strats['strategy_B_total'], 0)}% total")
    p(f"  Strategy C — 1x always + 2x during signal:  {fmt(strats['strategy_C_total'], 0)}% total")
    p(f"  Gold baseline:                               {fmt(strats['gold_baseline_total'], 0)}% total")
    p()

    # Section 3: SOP derivation
    p("=" * 70)
    p("SECTION 3 -- SOP DERIVATION")
    p("=" * 70)
    p()

    # Logic: compare signal-on vs signal-off returns and determine optimal behavior
    off_365 = summarize(reg["signal_off"][365])
    on_365  = summarize(reg["signal_on"][365])
    low_365 = summarize(reg["low"][365])
    norm_365 = summarize(reg["normal"][365])

    p("  Signal-on vs signal-off (365d forward):")
    if on_365:
        p(f"    Signal ON:   avg SI={fmt(on_365['avg_si'])}%  hit={on_365['hit_pos']:.0%}  exc={fmt(on_365['avg_exc'])}%")
    if off_365:
        p(f"    Signal OFF:  avg SI={fmt(off_365['avg_si'])}%  hit={off_365['hit_pos']:.0%}  exc={fmt(off_365['avg_exc'])}%")
    p()
    p("  Low GSR regime (365d forward):")
    if low_365:
        p(f"    avg SI={fmt(low_365['avg_si'])}%  hit={low_365['hit_pos']:.0%}  exc={fmt(low_365['avg_exc'])}%")
    p("  Normal GSR regime (365d forward):")
    if norm_365:
        p(f"    avg SI={fmt(norm_365['avg_si'])}%  hit={norm_365['hit_pos']:.0%}  exc={fmt(norm_365['avg_exc'])}%")
    p()

    # Determine which strategy wins
    A = strats["strategy_A_total"]
    B = strats["strategy_B_total"]
    C = strats["strategy_C_total"]

    p("  Strategy winner:")
    best = max([("A: hold always", A), ("B: signal only", B), ("C: overweight on signal", C)],
               key=lambda x: x[1])
    p(f"    {best[0]}  ({fmt(best[1], 0)}%)")
    p()

    # Derive SOP recommendation
    p("  DATA-DERIVED SOP:")
    p()
    if off_365 and off_365["avg_exc"] > 0:
        p("    GSR LOW / NORMAL (signal off):")
        p(f"      Silver still returns {fmt(off_365['avg_si'])}% avg / yr with {off_365['hit_pos']:.0%} hit rate.")
        p("      → Hold existing silver position. Do not add. Do not reduce.")
    else:
        p("    GSR LOW / NORMAL (signal off):")
        p(f"      Silver returns {fmt(off_365['avg_si'] if off_365 else 0)}% avg / yr — underperforms gold.")
        p("      → Hold minimum target weight. No active adds.")
    p()
    p("    GSR EXTREME + COMPRESSING (signal on):")
    p(f"      Silver returns {fmt(on_365['avg_si'] if on_365 else 0)}% avg / yr, +{fmt(on_365['avg_exc'] if on_365 else 0)}% vs gold.")
    p("      → Add to silver. Each signal month is a valid entry.")
    p()
    p("    GSR BELOW P25 (cycle complete):")
    if low_365:
        p(f"      Silver returns {fmt(low_365['avg_si'])}% avg / yr, exc={fmt(low_365['avg_exc'])}% vs gold.")
        if low_365["avg_exc"] < 0:
            p("      → Silver underperforms gold. Consider trimming back to target weight.")
        else:
            p("      → Silver still holds its own. Hold target weight.")
    p()

    report = "\n".join(L)
    print(report)

    out = Path(__file__).parent / "silver_sop_report.txt"
    out.write_text(report, encoding="utf-8")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
