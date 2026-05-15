"""
calibration/derive_silver_adding_trigger.py
Silver adding trigger derivation: GSR level, velocity, MAs, cycle analysis.
Output: calibration/silver_trigger_report.txt + proposed thresholds.yaml block
DO NOT commit until user confirms.
"""

import os, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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

HORIZONS          = [30, 60, 90, 180, 365]
LEVEL_PCTS        = [75, 85, 90]
VEL_CONSEC_DAYS   = [5, 10, 20]
PEAK_FALL_PCTS    = [5, 10, 15]
MA_PRIMARY        = (50, 200)
MA_ALT            = [(40, 150), (60, 250)]
SUSP_VEL_DAYS     = 5
ROLLING_PEAK_DAYS = 60


# ── Data ──────────────────────────────────────────────────
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
            print(f"  yfinance {symbol}: {e}, falling back to requests")

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


# ── Step 1a: Reference Statistics ─────────────────────────
def reference_stats(gsr):
    st = {
        "n":      len(gsr),
        "span":   f"{gsr.index[0].date()} to {gsr.index[-1].date()}",
        "mean":   float(gsr.mean()),
        "median": float(gsr.median()),
        "stdev":  float(gsr.std()),
        "min":    float(gsr.min()),
        "max":    float(gsr.max()),
    }
    for q in [10, 25, 50, 75, 85, 90]:
        st[f"p{q}"] = float(gsr.quantile(q / 100))
    return st


# ── Step 1b: Velocity Distribution ────────────────────────
def velocity_stats(gsr):
    vel = gsr.diff()   # NaN at index[0]; same index as gsr
    vst = {"series": vel}
    for q in [10, 25, 50, 75, 90]:
        vst[f"p{q}"] = float(vel.quantile(q / 100))
    return vst


# ── Step 1c: Cycle Analysis ────────────────────────────────
def _find_alt_sequence(gsr, window=20, min_move=2.0):
    arr  = gsr.values
    idx  = gsr.index
    n    = len(arr)
    pts  = []
    for i in range(window, n - window):
        seg = arr[i - window: i + window + 1]
        if arr[i] == seg.max() and arr[i] > arr[i - 1] and arr[i] > arr[i + 1]:
            pts.append((i, arr[i], "peak"))
        elif arr[i] == seg.min() and arr[i] < arr[i - 1] and arr[i] < arr[i + 1]:
            pts.append((i, arr[i], "trough"))
    pts.sort(key=lambda x: x[0])
    alt = []
    for pos, val, typ in pts:
        if not alt or alt[-1][2] != typ:
            alt.append([pos, val, typ])
        else:
            prev = alt[-1]
            if (typ == "peak" and val > prev[1]) or (typ == "trough" and val < prev[1]):
                alt[-1] = [pos, val, typ]
    comp, exp = [], []
    for i in range(len(alt) - 1):
        p1, v1, t1 = alt[i]
        p2, v2, t2 = alt[i + 1]
        mag = v2 - v1
        if abs(mag) < min_move:
            continue
        rec = {
            "start": idx[p1], "end": idx[p2],
            "duration": (idx[p2] - idx[p1]).days,
            "magnitude": round(mag, 2),
            "start_val": round(v1, 2), "end_val": round(v2, 2),
        }
        if t1 == "peak" and t2 == "trough":
            comp.append(rec)
        elif t1 == "trough" and t2 == "peak":
            exp.append(rec)
    return comp, exp


def _summarize(cycles, key):
    vals = [abs(c[key]) for c in cycles]
    if not vals:
        return {}
    return {
        "n": len(vals),
        "median": round(float(np.median(vals)), 1),
        "mean":   round(float(np.mean(vals)),   1),
        "stdev":  round(float(np.std(vals)),    1),
        "min":    round(float(np.min(vals)),    1),
        "max":    round(float(np.max(vals)),    1),
    }


def cycle_analysis(gsr, st):
    comp, exp = _find_alt_sequence(gsr)
    return {
        "compression":            comp,
        "compression_duration":   _summarize(comp, "duration"),
        "compression_magnitude":  _summarize(comp, "magnitude"),
        "expansion":              exp,
        "expansion_duration":     _summarize(exp, "duration"),
        "expansion_magnitude":    _summarize(exp, "magnitude"),
        "time_below": {
            "p75": float((gsr < st["p75"]).mean()),
            "p50": float((gsr < st["p50"]).mean()),
            "p25": float((gsr < st["p25"]).mean()),
        },
    }


# ── Step 1d: MA Crossover Analysis ────────────────────────
def ma_crossover_analysis(gsr, gold, silver):
    ma50  = gsr.rolling(50,  min_periods=50).mean()
    ma200 = gsr.rolling(200, min_periods=200).mean()
    si    = silver.reindex(gsr.index, method="ffill")
    gc    = gold.reindex(gsr.index,   method="ffill")

    # GSR 50d crosses BELOW 200d → GSR compressing → silver bullish
    death_cross  = (ma50 < ma200) & (ma50.shift(1) >= ma200.shift(1))
    # GSR 50d crosses ABOVE 200d → GSR expanding → silver bearish
    golden_cross = (ma50 > ma200) & (ma50.shift(1) <= ma200.shift(1))

    def analyze(cross_mask):
        rows = []
        for d in cross_mask[cross_mask].index:
            if d not in si.index:
                continue
            di = si.index.get_loc(d)
            if si.iloc[di] == 0 or gc.iloc[di] == 0:
                continue
            rec = {"date": d, "gsr": float(gsr.loc[d])}
            for h in HORIZONS:
                future = si.index[si.index >= d + pd.Timedelta(days=h - 3)]
                if len(future) == 0:
                    continue
                fi = si.index.get_loc(future[0])
                if fi >= len(si):
                    continue
                si_ret = (si.iloc[fi] - si.iloc[di]) / si.iloc[di] * 100
                gc_ret = (gc.iloc[fi] - gc.iloc[di]) / gc.iloc[di] * 100
                rec[f"si_{h}d"]     = float(si_ret)
                rec[f"gc_{h}d"]     = float(gc_ret)
                rec[f"excess_{h}d"] = float(si_ret - gc_ret)
            rows.append(rec)
        return rows

    def summarize_cross(rows):
        if not rows:
            return {"n": 0}
        out = {"n": len(rows)}
        for h in HORIZONS:
            si_rets = [r[f"si_{h}d"] for r in rows if f"si_{h}d" in r]
            excess  = [r[f"excess_{h}d"] for r in rows if f"excess_{h}d" in r]
            if si_rets:
                out[f"hit_pos_{h}d"]  = round(sum(1 for x in si_rets if x > 0) / len(si_rets), 3)
                out[f"avg_si_{h}d"]   = round(float(np.mean(si_rets)), 2)
            if excess:
                out[f"hit_gold_{h}d"] = round(sum(1 for x in excess if x > 0) / len(excess), 3)
                out[f"avg_exc_{h}d"]  = round(float(np.mean(excess)), 2)
        return out

    bullish_rows = analyze(death_cross)
    bearish_rows = analyze(golden_cross)
    return {
        "bullish_rows":    bullish_rows,
        "bullish_summary": summarize_cross(bullish_rows),
        "bearish_rows":    bearish_rows,
        "bearish_summary": summarize_cross(bearish_rows),
        "ma50":  ma50,
        "ma200": ma200,
    }


# ── Step 2–3: Signal + Backtest ────────────────────────────
def build_signal(gsr, vel, st, level_pct, comp_type, ma_fast, ma_slow):
    """Returns daily pd.Series: 1=adding, 0=suspended, -1=not triggered."""
    thr     = st[f"p{level_pct}"]
    p25     = st["p25"]
    vel_p25 = float(vel.quantile(0.25))
    vel_p75 = float(vel.quantile(0.75))

    cond_level = gsr > thr

    if comp_type.startswith("vel_"):
        n = int(comp_type.split("_")[1].replace("d", ""))
        cond_comp = (vel < vel_p25).rolling(n, min_periods=n).min() == 1
    else:
        pct = int(comp_type.split("_")[1])
        peak = gsr.rolling(ROLLING_PEAK_DAYS, min_periods=20).max()
        cond_comp = (gsr - peak) / peak * 100 <= -pct

    cond_ma = ma_fast > ma_slow

    vel_susp  = (vel > vel_p75).rolling(SUSP_VEL_DAYS, min_periods=SUSP_VEL_DAYS).min() == 1
    susp_p25  = gsr < p25
    suspended = (~cond_ma) | vel_susp | susp_p25

    sig = pd.Series(-1, index=gsr.index, dtype=int)
    sig[suspended] = 0
    sig[cond_level & cond_comp & cond_ma & ~suspended] = 1
    return sig


def month_end_entries(signal):
    entries = []
    for (yr, mo), grp in signal.groupby([signal.index.year, signal.index.month]):
        last = grp.index[-1]
        if signal.loc[last] == 1:
            entries.append(last)
    return entries


def run_backtest(entries, silver, gold):
    gc_aligned = gold.reindex(silver.index, method="ffill")
    records = []
    for ed in entries:
        if ed not in silver.index:
            continue
        ei     = silver.index.get_loc(ed)
        ep_si  = float(silver.iloc[ei])
        ep_gc  = float(gc_aligned.iloc[ei])
        if ep_si == 0 or ep_gc == 0:
            continue
        rec = {"date": ed, "si_entry": ep_si, "gc_entry": ep_gc}
        for h in HORIZONS:
            future = silver.index[silver.index >= ed + pd.Timedelta(days=h - 3)]
            if len(future) == 0:
                continue
            fi = silver.index.get_loc(future[0])
            if fi >= len(silver):
                continue
            si_ret  = (silver.iloc[fi] - ep_si) / ep_si * 100
            gc_ret  = (gc_aligned.iloc[fi] - ep_gc) / ep_gc * 100
            path    = silver.iloc[ei: fi + 1]
            mae     = float((path.min() - ep_si) / ep_si * 100)
            rec[f"si_{h}d"]     = float(si_ret)
            rec[f"gc_{h}d"]     = float(gc_ret)
            rec[f"excess_{h}d"] = float(si_ret - gc_ret)
            rec[f"mae_{h}d"]    = mae
        records.append(rec)
    return records


def score_records(records, h):
    valid = [r for r in records if f"si_{h}d" in r]
    if len(valid) < 4:
        return None
    n        = len(valid)
    si_rets  = [r[f"si_{h}d"]     for r in valid]
    excess   = [r[f"excess_{h}d"] for r in valid]
    maes     = [r[f"mae_{h}d"]    for r in valid]
    hit_pos  = sum(1 for x in si_rets if x > 0) / n
    hit_gold = sum(1 for x in excess  if x > 0) / n
    avg_si   = float(np.mean(si_rets))
    avg_exc  = float(np.mean(excess))
    avg_mae  = float(np.mean(maes))
    ras      = avg_exc / max(abs(avg_mae), 1.0)
    conf = "INSUFFICIENT"
    if n >= 10 and hit_gold >= 0.70: conf = "HIGH"
    elif n >= 6 and hit_gold >= 0.60: conf = "MEDIUM"
    elif n >= 4 and hit_gold >= 0.55: conf = "LOW"
    return {
        "n": n, "hit_positive": hit_pos, "hit_vs_gold": hit_gold,
        "avg_si": avg_si, "avg_excess": avg_exc,
        "avg_mae": avg_mae, "ras": ras, "conf": conf,
    }


def run_sweep(gsr, gold, silver, vel, st, ma_fast, ma_slow):
    comp_types = [f"vel_{d}d" for d in VEL_CONSEC_DAYS] + \
                 [f"peak_{p}" for p in PEAK_FALL_PCTS]
    results = []
    for level_pct in LEVEL_PCTS:
        for comp in comp_types:
            sig     = build_signal(gsr, vel, st, level_pct, comp, ma_fast, ma_slow)
            entries = month_end_entries(sig)
            recs    = run_backtest(entries, silver, gold)
            for h in HORIZONS:
                sc = score_records(recs, h)
                results.append({
                    "level_pct":   level_pct,
                    "level_gsr":   round(st[f"p{level_pct}"], 2),
                    "compression": comp,
                    "horizon":     h,
                    "n":           sc["n"]           if sc else 0,
                    "hit_positive":sc["hit_positive"] if sc else np.nan,
                    "hit_vs_gold": sc["hit_vs_gold"]  if sc else np.nan,
                    "avg_si":      sc["avg_si"]        if sc else np.nan,
                    "avg_excess":  sc["avg_excess"]    if sc else np.nan,
                    "avg_mae":     sc["avg_mae"]       if sc else np.nan,
                    "ras":         sc["ras"]           if sc else np.nan,
                    "conf":        sc["conf"]          if sc else "INSUFFICIENT",
                    "entries":     entries,
                    "records":     recs,
                })
    return results


def select_winner(results):
    cands = [r for r in results
             if r["n"] >= 4 and not np.isnan(r.get("ras", np.nan))]
    if not cands:
        return None
    best_ras = max(r["ras"] for r in cands)
    tied     = [r for r in cands if r["ras"] >= best_ras * 0.95]
    valid    = [r for r in tied  if not np.isnan(r.get("hit_vs_gold", np.nan))]
    if not valid:
        valid = tied
    by_hit   = sorted(valid, key=lambda r: r.get("hit_vs_gold", 0), reverse=True)
    top_hit  = by_hit[0].get("hit_vs_gold", 0)
    still    = [r for r in by_hit
                if r.get("hit_vs_gold", 0) >= top_hit * 0.99]
    return max(still, key=lambda r: r["n"])


# ── Step 4: Exit Signal Analysis ──────────────────────────
def exit_signal_analysis(gsr, silver, vel, vel_p75, ma_fast, ma_slow):
    si_idx = silver.index

    def fwd_stats(dates, horizons=(30, 60, 90)):
        out = {}
        for h in horizons:
            fwds = []
            for d in dates:
                if d not in si_idx:
                    continue
                di = si_idx.get_loc(d)
                future = si_idx[si_idx >= d + pd.Timedelta(days=h - 3)]
                if len(future) == 0:
                    continue
                fi = si_idx.get_loc(future[0])
                if fi >= len(silver):
                    continue
                ret = (silver.iloc[fi] - silver.iloc[di]) / silver.iloc[di] * 100
                fwds.append(float(ret))
            out[h] = fwds
        return out

    results = []

    # Velocity above 75th for N consecutive days
    for n in [5, 10, 20]:
        triggered = (vel > vel_p75).rolling(n, min_periods=n).min() == 1
        dates = triggered[triggered].index
        fwd = fwd_stats(dates)
        rec = {"exit_type": f"vel_above_75th_{n}d", "n_signals": len(dates)}
        for h in [30, 60, 90]:
            rec[f"median_si_{h}d"]   = round(float(np.median(fwd[h])), 2) if fwd[h] else np.nan
            rec[f"pct_pos_{h}d"]     = round(sum(1 for x in fwd[h] if x > 0) / len(fwd[h]), 2) if fwd[h] else np.nan
        results.append(rec)

    # GSR risen from trough by X%
    for pct in [5, 10, 15]:
        trough = gsr.rolling(ROLLING_PEAK_DAYS, min_periods=20).min()
        risen  = (gsr - trough) / trough * 100 >= pct
        dates  = risen[risen].index
        fwd    = fwd_stats(dates)
        rec = {"exit_type": f"risen_from_trough_{pct}pct", "n_signals": len(dates)}
        for h in [30, 60, 90]:
            rec[f"median_si_{h}d"] = round(float(np.median(fwd[h])), 2) if fwd[h] else np.nan
            rec[f"pct_pos_{h}d"]   = round(sum(1 for x in fwd[h] if x > 0) / len(fwd[h]), 2) if fwd[h] else np.nan
        results.append(rec)

    # MA 50d crosses below 200d on GSR (cycle complete — silver has caught up)
    cross = (ma_fast < ma_slow) & (ma_fast.shift(1) >= ma_slow.shift(1))
    dates = cross[cross].index
    fwd   = fwd_stats(dates)
    rec   = {"exit_type": "ma50_crosses_below_200d", "n_signals": len(dates)}
    for h in [30, 60, 90]:
        rec[f"median_si_{h}d"] = round(float(np.median(fwd[h])), 2) if fwd[h] else np.nan
        rec[f"pct_pos_{h}d"]   = round(sum(1 for x in fwd[h] if x > 0) / len(fwd[h]), 2) if fwd[h] else np.nan
    results.append(rec)

    return results


# ── Step 5: Current State ──────────────────────────────────
def current_state(gsr, vel, st, winner, ma_fast, ma_slow):
    today_gsr  = float(gsr.iloc[-1])
    today_vel  = float(vel.iloc[-1]) if not np.isnan(vel.iloc[-1]) else 0.0
    today_maf  = float(ma_fast.dropna().iloc[-1])
    today_mas  = float(ma_slow.dropna().iloc[-1])
    vel_p25    = float(vel.quantile(0.25))
    vel_p75    = float(vel.quantile(0.75))

    if today_vel < vel_p25:   vel_band = "compression"
    elif today_vel > vel_p75: vel_band = "expansion"
    else:                     vel_band = "normal"

    susp_ma   = today_maf < today_mas
    susp_p25  = today_gsr < st["p25"]

    if susp_p25:
        signal = "expansion"
    elif susp_ma:
        signal = "adding suspended"
    elif winner:
        level_ok = today_gsr > st[f"p{winner['level_pct']}"]
        if winner["compression"].startswith("vel_"):
            n  = int(winner["compression"].split("_")[1].replace("d", ""))
            rv = vel.iloc[-n:]
            comp_ok = bool((rv < vel_p25).all())
        else:
            pct  = int(winner["compression"].split("_")[1])
            peak = float(gsr.rolling(ROLLING_PEAK_DAYS, min_periods=20).max().iloc[-1])
            comp_ok = (today_gsr - peak) / peak * 100 <= -pct
        signal = "adding allowed" if (level_ok and comp_ok and not susp_ma) else "adding suspended"
    else:
        signal = "adding suspended"

    return {
        "date":      gsr.index[-1].strftime("%Y-%m-%d"),
        "gsr":       round(today_gsr, 2),
        "vel_today": round(today_vel, 4),
        "vel_band":  vel_band,
        "ma50":      round(today_maf, 2),
        "ma200":     round(today_mas, 2),
        "ma_ok":     today_maf > today_mas,
        "signal":    signal,
    }


# ── Report ─────────────────────────────────────────────────
def build_report(st, vst, cycles, ma_anal, sweep_res, winner,
                 exit_res, curr, sensitivity):
    lines = []
    def L(s=""): lines.append(s)
    def fmt(v, d=2):
        return f"{v:.{d}f}" if v is not None and not (isinstance(v, float) and np.isnan(v)) else "n/a"

    L("SILVER ADDING TRIGGER DERIVATION REPORT")
    L("=" * 80)
    L(f"Generated:  {date.today().isoformat()}")
    L(f"Data:       GC=F / SI=F (Yahoo Finance, daily, full history)")
    L(f"Period:     {st['span']}  ({st['n']:,} trading days)")
    L()

    # Section 1
    L("=" * 80)
    L("SECTION 1 -- GSR REFERENCE STATISTICS")
    L("=" * 80)
    L()
    L(f"  Mean:      {fmt(st['mean'])}")
    L(f"  Median:    {fmt(st['median'])}")
    L(f"  Stdev:     {fmt(st['stdev'])}")
    L(f"  Min:       {fmt(st['min'])}")
    L(f"  Max:       {fmt(st['max'])}")
    L()
    L("  Percentiles:")
    for q in [10, 25, 50, 75, 85, 90]:
        L(f"    p{q:>2}: {fmt(st[f'p{q}'])}")
    L()

    # Section 2
    L("=" * 80)
    L("SECTION 2 -- VELOCITY BAND DEFINITIONS")
    L("=" * 80)
    L()
    L("  Daily velocity = GSR[t] - GSR[t-1]")
    L()
    L("  Percentiles:")
    for q in [10, 25, 50, 75, 90]:
        L(f"    p{q:>2}: {fmt(vst[f'p{q}'], 4)}")
    L()
    L(f"  Active compression: velocity < {fmt(vst['p25'], 4)}  (below 25th pct)")
    L(f"  Normal:             {fmt(vst['p25'], 4)} to {fmt(vst['p75'], 4)}")
    L(f"  Active expansion:   velocity > {fmt(vst['p75'], 4)}  (above 75th pct)")
    L()

    # Section 3
    L("=" * 80)
    L("SECTION 3 -- CYCLE ANALYSIS")
    L("=" * 80)
    L()
    def print_summary(label, dur, mag):
        L(f"  {label}")
        if dur:
            L(f"    n:         {dur['n']}")
            L(f"    Duration:  median {dur['median']}d  mean {dur['mean']}d  stdev {dur['stdev']}d  range [{dur['min']}, {dur['max']}]d")
            L(f"    Magnitude: median {mag['median']}pts  mean {mag['mean']}pts  stdev {mag['stdev']}pts  range [{mag['min']}, {mag['max']}]pts")
        else:
            L("    (insufficient cycles detected)")
    print_summary("Compression cycles (peak → trough):",
                  cycles["compression_duration"], cycles["compression_magnitude"])
    L()
    print_summary("Expansion cycles (trough → peak):",
                  cycles["expansion_duration"], cycles["expansion_magnitude"])
    L()
    L("  Time spent below key GSR levels (full history):")
    L(f"    < p75 ({fmt(st['p75'])}): {cycles['time_below']['p75']:.1%}")
    L(f"    < p50 ({fmt(st['p50'])}): {cycles['time_below']['p50']:.1%}")
    L(f"    < p25 ({fmt(st['p25'])}): {cycles['time_below']['p25']:.1%}")
    L()

    # Section 4
    L("=" * 80)
    L("SECTION 4 -- MA CROSSOVER ANALYSIS  (50d / 200d on daily GSR)")
    L("=" * 80)
    L()
    for label, summ in [
        ("Bullish crosses: GSR 50d crosses BELOW 200d (GSR compressing → silver bullish)",
         ma_anal["bullish_summary"]),
        ("Bearish crosses: GSR 50d crosses ABOVE 200d (GSR expanding → silver bearish)",
         ma_anal["bearish_summary"]),
    ]:
        L(f"  {label}")
        L(f"  n = {summ.get('n', 0)}")
        L(f"  {'Horizon':>8}  {'Hit+':>6}  {'AvgSI%':>8}  {'HitGold':>8}  {'AvgExc%':>9}")
        L(f"  {'-'*48}")
        for h in HORIZONS:
            L(f"  {h:>6}d  "
              f"  {summ.get(f'hit_pos_{h}d', np.nan):>5.1%}"
              f"  {fmt(summ.get(f'avg_si_{h}d',  np.nan)):>8}"
              f"  {summ.get(f'hit_gold_{h}d', np.nan):>7.1%}"
              f"  {fmt(summ.get(f'avg_exc_{h}d', np.nan)):>9}")
        L()

    # Section 5
    L("=" * 80)
    L("SECTION 5 -- ADDING SIGNAL SWEEP RESULTS")
    L("=" * 80)
    L()
    L("  Conditions: (1) GSR > level  (2) compression_type  (3) MA50 > MA200")
    L("  Suspension: MA50 < MA200 | vel > p75 for 5d | GSR < p25")
    L("  Entry: last trading day of month when signal = 1")
    L()
    hdr = f"  {'Lvl':>4} {'GSR':>6}  {'Compression':>18}  {'H':>5}  {'N':>4}  {'Hit+':>6}  {'HitG':>6}  {'SI%':>7}  {'Exc%':>7}  {'MAE%':>7}  {'RAS':>7}  Conf"
    L(hdr)
    L("  " + "-" * (len(hdr) - 2))

    top = sorted(
        [r for r in sweep_res if r["n"] >= 4 and not np.isnan(r.get("ras", np.nan))],
        key=lambda r: r["ras"], reverse=True
    )[:30]
    for r in top:
        L(f"  p{r['level_pct']:>2} {r['level_gsr']:>6.1f}  {r['compression']:>18}  {r['horizon']:>5}"
          f"  {r['n']:>4}  {r['hit_positive']:>6.1%}  {r['hit_vs_gold']:>6.1%}"
          f"  {fmt(r['avg_si']):>7}  {fmt(r['avg_excess']):>7}"
          f"  {fmt(r['avg_mae']):>7}  {fmt(r['ras']):>7}  {r['conf']}")
    L()
    if winner:
        L(f"  WINNER: level=p{winner['level_pct']} ({winner['level_gsr']:.1f}), "
          f"compression={winner['compression']}, horizon={winner['horizon']}d")
        L(f"    n={winner['n']}, hit+={winner['hit_positive']:.1%}, "
          f"hit_vs_gold={winner['hit_vs_gold']:.1%}, "
          f"avg_excess={fmt(winner['avg_excess'])}%, RAS={fmt(winner['ras'])}, "
          f"conf={winner['conf']}")
        L()
        L("  Per-event breakdown (winner combo):")
        L(f"  {'Date':<10}  {'SI Entry':>9}  {'SI Ret%':>8}  {'GC Ret%':>8}  {'Exc%':>7}  {'MAE%':>7}  Result")
        L(f"  {'-'*66}")
        h = winner["horizon"]
        for rec in winner["records"]:
            si_ret = rec.get(f"si_{h}d", np.nan)
            gc_ret = rec.get(f"gc_{h}d", np.nan)
            exc    = rec.get(f"excess_{h}d", np.nan)
            mae    = rec.get(f"mae_{h}d", np.nan)
            res    = "HIT " if not np.isnan(si_ret) and si_ret > 0 else "MISS"
            L(f"  {rec['date'].strftime('%Y-%m-%d')}  {rec['si_entry']:>9.2f}"
              f"  {fmt(si_ret):>8}  {fmt(gc_ret):>8}  {fmt(exc):>7}  {fmt(mae):>7}  {res}")
    else:
        L("  No winner found (no combination with n >= 4).")
    L()

    # Section 6
    L("=" * 80)
    L("SECTION 6 -- EXIT SIGNAL ANALYSIS")
    L("=" * 80)
    L()
    L("  Forward silver return AFTER exit signal fires.")
    L("  Negative 90d median = signal captures peak.  Positive = exit too early.")
    L()
    L(f"  {'Exit Definition':>35}  {'N':>6}  {'Med30':>7}  {'Hit+30':>7}  {'Med60':>7}  {'Hit+60':>7}  {'Med90':>7}  {'Hit+90':>7}")
    L(f"  {'-'*92}")
    for r in exit_res:
        L(f"  {r['exit_type']:>35}  {r['n_signals']:>6}"
          f"  {fmt(r.get('median_si_30d', np.nan)):>7}  {r.get('pct_pos_30d', np.nan):>6.0%} "
          f"  {fmt(r.get('median_si_60d', np.nan)):>7}  {r.get('pct_pos_60d', np.nan):>6.0%} "
          f"  {fmt(r.get('median_si_90d', np.nan)):>7}  {r.get('pct_pos_90d', np.nan):>6.0%}")
    L()
    # Recommendation: prefer exit with most negative 90d median (captured most of move)
    scored = [(r, r.get("median_si_90d", 0) or 0) for r in exit_res if r.get("n_signals", 0) >= 4]
    if scored:
        best_exit = min(scored, key=lambda x: x[1])
        L(f"  Recommended exit: {best_exit[0]['exit_type']}")
        L(f"  (most negative 90d median = {fmt(best_exit[1])}% — captures most of move)")
    L()

    # Section 7
    L("=" * 80)
    L("SECTION 7 -- CURRENT STATE ASSESSMENT")
    L("=" * 80)
    L()
    L(f"  As of: {curr['date']}")
    L()
    L(f"  GSR:              {curr['gsr']:.2f}")
    L(f"    vs p25 ({st['p25']:.1f}): {curr['gsr'] - st['p25']:+.2f}")
    L(f"    vs p75 ({st['p75']:.1f}): {curr['gsr'] - st['p75']:+.2f}")
    L(f"    vs p85 ({st['p85']:.1f}): {curr['gsr'] - st['p85']:+.2f}")
    L(f"    vs p90 ({st['p90']:.1f}): {curr['gsr'] - st['p90']:+.2f}")
    L()
    L(f"  Velocity (today): {curr['vel_today']:+.4f}")
    L(f"  Velocity band:    {curr['vel_band']}")
    L()
    L(f"  MA 50d:           {curr['ma50']:.2f}")
    L(f"  MA 200d:          {curr['ma200']:.2f}")
    L(f"  MA 50d > 200d:    {curr['ma_ok']}")
    L()
    L(f"  SIGNAL:           {curr['signal'].upper()}")
    L()

    # Section 8
    L("=" * 80)
    L("SECTION 8 -- STIPULATED PARAMETERS + SENSITIVITY CHECK")
    L("=" * 80)
    L()
    L("  Primary MA windows:         50d / 200d  [stipulated]")
    L("  Level thresholds:           full-history percentiles  [stipulated]")
    L("  Velocity bands:             full-history percentiles  [stipulated]")
    L("  Suspension velocity days:   5  [stipulated]")
    L("  Rolling peak lookback:      60d  [stipulated]")
    L("  Entry timing:               month-end  [stipulated]")
    L()
    if winner and sensitivity:
        L("  Sensitivity — re-run winner with alternative MA windows:")
        L(f"  {'Windows':>12}  {'N':>4}  {'Hit+':>6}  {'HitG':>6}  {'RAS':>7}  {'Conf':>12}  Unchanged?")
        L(f"  {'-'*65}")
        L(f"  {'50/200':>12}  {winner['n']:>4}  {winner['hit_positive']:>6.1%}  "
          f"{winner['hit_vs_gold']:>6.1%}  {fmt(winner['ras']):>7}  {winner['conf']:>12}  (base)")
        for label, sr in sensitivity.items():
            if sr:
                unch = "YES" if sr["n"] >= 4 and sr["conf"] != "INSUFFICIENT" else "NO"
                L(f"  {label:>12}  {sr['n']:>4}  {sr.get('hit_positive', 0):>6.1%}  "
                  f"{sr.get('hit_vs_gold', 0):>6.1%}  "
                  f"{fmt(sr.get('ras', np.nan)):>7}  {sr.get('conf', 'n/a'):>12}  {unch}")
            else:
                L(f"  {label:>12}   n/a  (no qualifying winner)")
    L()

    return "\n".join(lines)


# ── YAML Proposal ──────────────────────────────────────────
def build_yaml(winner, st, vst, curr, sensitivity, exit_res):
    if not winner:
        return "  # No qualifying winner found."

    comp = winner["compression"]
    if comp.startswith("vel_"):
        n = comp.split("_")[1]
        comp_block = (
            f"    condition_compression:\n"
            f"      type: velocity_below_p25\n"
            f"      consecutive_days: {n}\n"
            f"      velocity_p25_value: {vst['p25']:.4f}"
        )
    else:
        pct = comp.split("_")[1]
        comp_block = (
            f"    condition_compression:\n"
            f"      type: fallen_from_recent_peak\n"
            f"      fallen_pct: {pct}%\n"
            f"      lookback_days: {ROLLING_PEAK_DAYS}"
        )

    scored = [(r, r.get("median_si_90d", 0) or 0) for r in exit_res if r.get("n_signals", 0) >= 4]
    rec_exit = min(scored, key=lambda x: x[1])[0]["exit_type"] if scored else "TBD"

    unch = {}
    for label, sr in (sensitivity or {}).items():
        unch[label] = "Y" if sr and sr["n"] >= 4 and sr["conf"] != "INSUFFICIENT" else "N"

    return f"""silver_adding_trigger:
  status: PROVISIONAL
  derivation_date: {date.today().isoformat()}

  reference_stats:
    gsr_mean: {st['mean']:.2f}
    gsr_median: {st['median']:.2f}
    gsr_stdev: {st['stdev']:.2f}
    gsr_10th_pct: {st['p10']:.2f}
    gsr_25th_pct: {st['p25']:.2f}
    gsr_75th_pct: {st['p75']:.2f}
    gsr_85th_pct: {st['p85']:.2f}
    gsr_90th_pct: {st['p90']:.2f}
    velocity_25th_pct: {vst['p25']:.4f}
    velocity_75th_pct: {vst['p75']:.4f}

  adding_signal:
    condition_level:
      type: percentile
      threshold: p{winner['level_pct']}
      gsr_value: {winner['level_gsr']:.2f}
{comp_block}
    condition_not_expanding:
      ma_50d_above_200d: true

  suspension_rules:
    ma_50d_crosses_below_200d: true
    velocity_above_75th_for_days: {SUSP_VEL_DAYS}
    gsr_below_25th_percentile: true
    gsr_25th_value: {st['p25']:.2f}

  exit_signal:
    definition: {rec_exit}
    parameter: derived_from_section_6

  performance:
    n_signal_months: {winner['n']}
    horizon_days: {winner['horizon']}
    hit_rate_positive: {winner['hit_positive']:.1%}
    hit_rate_vs_gold: {winner['hit_vs_gold']:.1%}
    avg_silver_return: {winner['avg_si']:.2f}%
    avg_excess_return: {winner['avg_excess']:.2f}%
    avg_max_adverse: {winner['avg_mae']:.2f}%
    risk_adjusted_score: {winner['ras']:.3f}
    confidence: {winner['conf']}

  current_state:
    gsr: {curr['gsr']:.2f}
    velocity_today: {curr['vel_today']:.4f}
    velocity_band: {curr['vel_band']}
    ma_50d: {curr['ma50']:.2f}
    ma_200d: {curr['ma200']:.2f}
    signal: {curr['signal']}

  stipulated_parameters:
    ma_windows: [50, 200]
    sensitivity_40_150_unchanged: {unch.get('40/150', 'N')}
    sensitivity_60_250_unchanged: {unch.get('60/250', 'N')}

  notes: []
"""


# ── Main ──────────────────────────────────────────────────
def main():
    print("Fetching GC=F...", flush=True)
    gold = fetch_yahoo("GC=F")
    time.sleep(1)
    print("Fetching SI=F...", flush=True)
    silver = fetch_yahoo("SI=F")

    if gold is None or silver is None:
        print("ERROR: data fetch failed"); return

    idx    = gold.index.intersection(silver.index)
    gold   = gold.loc[idx]
    silver = silver.loc[idx]
    gsr    = (gold / silver).dropna()
    print(f"Data loaded: {gsr.index[0].date()} to {gsr.index[-1].date()} ({len(gsr):,} days)")

    print("Step 1: reference statistics...", flush=True)
    st  = reference_stats(gsr)
    vst = velocity_stats(gsr)
    vel = vst["series"]

    print("Step 1c: cycle analysis...", flush=True)
    cycles = cycle_analysis(gsr, st)

    print("Step 1d: MA crossover analysis...", flush=True)
    ma_anal = ma_crossover_analysis(gsr, gold, silver)
    ma50    = ma_anal["ma50"]
    ma200   = ma_anal["ma200"]

    print("Step 2-3: sweep (18 combos × 5 horizons)...", flush=True)
    sweep_res = run_sweep(gsr, gold, silver, vel, st, ma50, ma200)
    winner    = select_winner(sweep_res)

    print("Step 3: sensitivity check (40/150, 60/250)...", flush=True)
    sensitivity = {}
    for fast, slow in MA_ALT:
        label = f"{fast}/{slow}"
        maf   = gsr.rolling(fast, min_periods=fast).mean()
        mas   = gsr.rolling(slow, min_periods=slow).mean()
        alt   = run_sweep(gsr, gold, silver, vel, st, maf, mas)
        aw    = select_winner(alt)
        if aw and winner:
            sensitivity[label] = {
                "n":            aw["n"],
                "hit_positive": aw["hit_positive"],
                "hit_vs_gold":  aw["hit_vs_gold"],
                "ras":          aw["ras"],
                "conf":         aw["conf"],
                "same_combo":   (aw["level_pct"] == winner["level_pct"] and
                                 aw["compression"] == winner["compression"]),
            }
        else:
            sensitivity[label] = None

    print("Step 4: exit signal analysis...", flush=True)
    exit_res = exit_signal_analysis(gsr, silver, vel, vst["p75"], ma50, ma200)

    print("Step 5: current state...", flush=True)
    curr = current_state(gsr, vel, st, winner, ma50, ma200)

    print("Building report...", flush=True)
    report = build_report(st, vst, cycles, ma_anal, sweep_res, winner,
                          exit_res, curr, sensitivity)
    print(report)

    out = Path(__file__).parent / "silver_trigger_report.txt"
    out.write_text(report, encoding="utf-8")
    print(f"\nReport saved: {out}")

    yaml_block = build_yaml(winner, st, vst, curr, sensitivity, exit_res)
    print("\n" + "=" * 80)
    print("PROPOSED config/thresholds.yaml ADDITION")
    print("=" * 80)
    print(yaml_block)


if __name__ == "__main__":
    main()
