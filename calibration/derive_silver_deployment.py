"""
calibration/derive_silver_deployment.py
Silver deployment rule derivation.
Hypotheses A (GSR high+reverting), B (silver drawdown), C (both).
Output: calibration/silver_deployment_report.txt
"""

import os, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import requests
import time
from pathlib import Path
from datetime import date
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Constants (stipulated unless noted) ────────────────────
ROLLING_WINDOW_YR  = 5        # stipulated
ROLLING_WINDOW_MO  = 60
MIN_PERIODS        = 24
HORIZONS           = [6, 9, 12, 18, 24]
HIGH_PCT_LEVELS    = [75, 85, 95]
HIGH_MULT_LEVELS   = [1.2, 1.3, 1.4, 1.5]
REV_DROP_PCTS      = [5, 10, 15, 20]
REV_ROC_LAGS       = [1, 2, 3]
DD_LOOKBACKS       = [6, 12, 24]
DD_PCTS            = [25, 50, 75]


# ── Data ──────────────────────────────────────────────────
def fetch_yahoo(symbol):
    enc = symbol.replace("=", "%3D")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}?interval=1d&range=max"
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            if r.status_code != 200:
                time.sleep(2 ** attempt); continue
            d = r.json()["chart"]["result"][0]
            closes = d["indicators"]["quote"][0]["close"]
            s = pd.Series(closes, index=pd.to_datetime(d["timestamp"], unit="s"))
            s.index = s.index.normalize()
            return s.dropna().sort_index()
        except Exception as e:
            print(f"  {symbol} attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None

def to_monthly(s):
    return s.resample("ME").last().dropna()

def pit_quantile(s, window, q, min_periods=MIN_PERIODS):
    """Point-in-time trailing quantile (excludes current month)."""
    return s.shift(1).rolling(window, min_periods=min_periods).quantile(q / 100)


# ── Reference statistics ───────────────────────────────────
def reference_stats(gsr, silver):
    st = {}
    st["gsr_n"]      = len(gsr)
    st["gsr_span"]   = f"{gsr.index[0].strftime('%Y-%m')} to {gsr.index[-1].strftime('%Y-%m')}"
    st["gsr_median"] = float(gsr.median())
    st["gsr_mean"]   = float(gsr.mean())
    st["gsr_stdev"]  = float(gsr.std())
    for q in [75, 85, 95]:
        st[f"gsr_p{q}"] = float(gsr.quantile(q / 100))

    st["silver_median"] = float(silver.median())
    st["silver_mean"]   = float(silver.mean())
    st["silver_stdev"]  = float(silver.std())

    # Silver drawdown distribution (% below trailing N-month high)
    for lb in DD_LOOKBACKS:
        dds = []
        for i in range(lb, len(silver)):
            peak = silver.iloc[i - lb : i + 1].max()  # includes current for dist only
            curr = silver.iloc[i]
            if peak > 0:
                dds.append((peak - curr) / peak * 100)
        arr = np.array(dds)
        for q in [25, 50, 75, 90]:
            st[f"dd_{lb}m_p{q}"] = float(np.percentile(arr, q)) if len(arr) else 0.0

    return st


# ── Signal generation ──────────────────────────────────────
def make_high_signals(gsr, st):
    sigs = {}
    for q in HIGH_PCT_LEVELS:
        sigs[f"pct{q}"] = gsr > pit_quantile(gsr, ROLLING_WINDOW_MO, q)
    median = st["gsr_median"]
    for m in HIGH_MULT_LEVELS:
        key = f"mult{str(m).replace('.','')}"
        sigs[key] = gsr > median * m
    return sigs

def make_rev_signals(gsr):
    sigs = {}
    ma3 = gsr.rolling(3, min_periods=2).mean()
    sigs["ma_cross"] = (gsr < ma3) & (gsr.shift(1) >= ma3.shift(1))
    peak6 = gsr.shift(1).rolling(6, min_periods=3).max()
    for p in REV_DROP_PCTS:
        sigs[f"drop{p}"] = ((gsr - peak6) / peak6 * 100) <= -p
    for lag in REV_ROC_LAGS:
        sigs[f"roc{lag}m"] = gsr < gsr.shift(lag)
    return sigs

def make_dd_signals(silver, st):
    sigs = {}
    for lb in DD_LOOKBACKS:
        peak = silver.shift(1).rolling(lb, min_periods=max(3, lb // 2)).max()
        dd = (peak - silver) / peak * 100
        for q in DD_PCTS:
            thresh = st[f"dd_{lb}m_p{q}"]
            sigs[f"{lb}m_p{q}"] = dd >= thresh
    return sigs


# ── Regime-exit cool-off ───────────────────────────────────
def regime_exit(raw, gsr, exit_level):
    filtered   = pd.Series(False, index=raw.index)
    in_regime  = False
    for dt in raw.index:
        gsr_val = gsr.loc[dt] if dt in gsr.index else np.inf
        if in_regime and gsr_val < exit_level:
            in_regime = False
        if not in_regime and bool(raw.get(dt, False)):
            filtered[dt] = True
            in_regime = True
    return filtered


# ── Backtest ───────────────────────────────────────────────
def backtest(entries, silver, h):
    idx = silver.index
    recs = []
    for ed in entries:
        if ed not in idx:
            continue
        ei = idx.get_loc(ed)
        if ei + h >= len(silver):
            continue
        ep  = silver.iloc[ei]
        ep_end = silver.iloc[ei + h]
        path_ret = (silver.iloc[ei : ei + h + 1] - ep) / ep * 100
        fwd = float(path_ret.iloc[-1])
        mae = float(path_ret.min())
        ras = fwd / max(abs(mae), 1.0)
        recs.append({"ed": ed, "ep": ep, "fwd": fwd, "mae": mae, "ras": ras})
    return recs

def score(recs):
    if not recs:
        return None
    n   = len(recs)
    fwds = [r["fwd"] for r in recs]
    maes = [r["mae"] for r in recs]
    rass = [r["ras"] for r in recs]
    hit  = sum(1 for f in fwds if f > 0) / n
    conf = "INSUFFICIENT"
    if n >= 8 and hit >= 0.70: conf = "HIGH"
    elif n >= 6 and hit >= 0.60: conf = "MEDIUM"
    elif n >= 4 and hit >= 0.55: conf = "LOW"
    return {
        "n": n, "hit": hit,
        "avg_ret": np.mean(fwds),
        "avg_mae": np.mean(maes),
        "ras":     np.mean(rass),
        "conf":    conf,
    }

def dca_baseline(silver, horizons):
    out = {}
    idx = silver.index
    for h in horizons:
        recs = []
        for ei in range(len(idx) - h):
            ep = silver.iloc[ei]
            path_ret = (silver.iloc[ei : ei + h + 1] - ep) / ep * 100
            fwd = float(path_ret.iloc[-1])
            mae = float(path_ret.min())
            recs.append({"fwd": fwd, "mae": mae, "ras": fwd / max(abs(mae), 1.0)})
        if recs:
            fwds = [r["fwd"] for r in recs]
            out[h] = {
                "n": len(recs),
                "hit": sum(1 for f in fwds if f > 0) / len(recs),
                "avg_ret": np.mean(fwds),
                "avg_mae": np.mean([r["mae"] for r in recs]),
                "ras": np.mean([r["ras"] for r in recs]),
            }
    return out


# ── Sweep helpers ──────────────────────────────────────────
def sweep_A(high_sigs, rev_sigs, gsr, silver, exit_level):
    rows = []
    for hn, hs in high_sigs.items():
        for rn, rs in rev_sigs.items():
            raw = hs & rs
            filt = regime_exit(raw, gsr, exit_level)
            entries = filt[filt].index.tolist()
            for h in HORIZONS:
                recs = backtest(entries, silver, h)
                sc = score(recs) or {"n":0,"hit":0,"avg_ret":np.nan,"avg_mae":np.nan,"ras":np.nan,"conf":"INSUFFICIENT"}
                rows.append({"hyp":"A","high":hn,"rev":rn,"h":h,"entries":entries,**sc})
    return rows

def sweep_B(dd_sigs, gsr, silver, exit_level):
    rows = []
    for dn, ds in dd_sigs.items():
        filt = regime_exit(ds, gsr, exit_level)
        entries = filt[filt].index.tolist()
        for h in HORIZONS:
            recs = backtest(entries, silver, h)
            sc = score(recs) or {"n":0,"hit":0,"avg_ret":np.nan,"avg_mae":np.nan,"ras":np.nan,"conf":"INSUFFICIENT"}
            rows.append({"hyp":"B","dd":dn,"h":h,"entries":entries,**sc})
    return rows

def best_of(rows, min_n=4):
    cands = [r for r in rows if r["n"] >= min_n and not np.isnan(r.get("ras", np.nan))]
    return max(cands, key=lambda r: r["ras"]) if cands else None


# ── Sensitivity (re-run winner with alt window) ────────────
def sensitivity_check(winner, hyp, high_sigs_cache, rev_sigs, dd_sigs, gsr, silver, exit_level, st):
    results = {}
    h = winner["h"]
    for win_yr in [3, 7]:
        win_mo = win_yr * 12
        # Rebuild high signals with alt window
        alt_high = {}
        for q in HIGH_PCT_LEVELS:
            alt_high[f"pct{q}"] = gsr > pit_quantile(gsr, win_mo, q, min_periods=max(12, win_mo//4))
        for m in HIGH_MULT_LEVELS:
            key = f"mult{str(m).replace('.','')}"
            alt_high[key] = gsr > st["gsr_median"] * m

        if hyp == "A":
            hs = alt_high.get(winner["high"], high_sigs_cache.get(winner["high"]))
            rs = rev_sigs[winner["rev"]]
            raw = hs & rs
        elif hyp == "B":
            raw = dd_sigs[winner["dd"]]
        else:
            hs = alt_high.get(winner.get("a_high",""), pd.Series(False, index=gsr.index))
            rs = rev_sigs.get(winner.get("a_rev",""), pd.Series(False, index=gsr.index))
            ds = dd_sigs.get(winner.get("b_dd",""), pd.Series(False, index=gsr.index))
            raw = hs & rs & ds

        filt = regime_exit(raw, gsr, exit_level)
        entries = filt[filt].index.tolist()
        recs = backtest(entries, silver, h)
        sc = score(recs)
        results[win_yr] = sc if sc else {"n":0,"hit":0,"avg_ret":np.nan,"avg_mae":np.nan,"ras":np.nan,"conf":"INSUFFICIENT"}
    return results


# ── Report ─────────────────────────────────────────────────
def build_report(st, rows_A, rows_B, rows_C, winner, hyp,
                 dca, sensitivity, silver, gsr, exit_level):

    L_lines = []
    def L(s=""): L_lines.append(s)

    def fmt(v, d=2, suffix=""):
        return f"{v:.{d}f}{suffix}" if v is not None and not np.isnan(v) else "n/a"

    L("SILVER DEPLOYMENT RULE DERIVATION REPORT")
    L("=" * 80)
    L(f"Generated:   {date.today().isoformat()}")
    L(f"Hypotheses:  A=GSR high+reverting | B=silver drawdown | C=A AND B")
    L(f"Cool-off:    regime-exit (no re-entry until GSR < derived median)")
    L(f"Data:        GC=F / SI=F (Yahoo Finance, daily -> monthly)")
    L(f"Period:      {st['gsr_span']}  ({st['gsr_n']} months)")
    L()

    # ── Section 1 ─────────────────────────────────────────
    L("=" * 80)
    L("SECTION 1 -- REFERENCE STATISTICS (derived from full history)")
    L("=" * 80)
    L()
    L("GSR (Gold / Silver Ratio):")
    L(f"  Median:          {st['gsr_median']:.2f}   <- regime-exit cool-off level")
    L(f"  Mean:            {st['gsr_mean']:.2f}")
    L(f"  Stdev:           {st['gsr_stdev']:.2f}")
    L(f"  75th percentile: {st['gsr_p75']:.2f}")
    L(f"  85th percentile: {st['gsr_p85']:.2f}")
    L(f"  95th percentile: {st['gsr_p95']:.2f}")
    L()
    L("Silver ($/oz):")
    L(f"  Median:          ${st['silver_median']:.2f}")
    L(f"  Mean:            ${st['silver_mean']:.2f}")
    L(f"  Stdev:           ${st['silver_stdev']:.2f}")
    L()
    L("Silver drawdown distribution -- % below trailing N-month high:")
    L(f"  (Higher percentile = more severe historical drawdown)")
    L(f"  {'Lookback':<10} {'25th':>8} {'50th':>8} {'75th':>8} {'90th':>8}")
    L(f"  {'-'*42}")
    for lb in DD_LOOKBACKS:
        L(f"  {lb}m          "
          f"  {st[f'dd_{lb}m_p25']:>6.1f}%  "
          f"  {st[f'dd_{lb}m_p50']:>6.1f}%  "
          f"  {st[f'dd_{lb}m_p75']:>6.1f}%  "
          f"  {st[f'dd_{lb}m_p90']:>6.1f}%")
    L()
    L("Derived thresholds for HIGH sweep (median multiples):")
    for m in HIGH_MULT_LEVELS:
        L(f"  {m}x median -> GSR > {st['gsr_median']*m:.1f}")
    L()

    # ── Section 2: Hypothesis A ────────────────────────────
    L("=" * 80)
    L("SECTION 2 -- HYPOTHESIS A: GSR HIGH AND REVERTING")
    L("=" * 80)
    L(f"  (regime-exit cool-off at GSR < {exit_level:.2f})")
    L()
    L(f"  {'High':<18} {'Rev':<12} {'H':>3} {'N':>4}  {'Hit%':>6} {'Ret%':>7} {'MAE%':>7} {'RAS':>7} Conf")
    L(f"  {'-'*75}")
    top_A = sorted([r for r in rows_A if r["n"] >= 4], key=lambda r: r["ras"], reverse=True)[:20]
    for r in top_A:
        L(f"  {r['high']:<18} {r['rev']:<12} {r['h']:>3} {r['n']:>4}  "
          f"{r['hit']:>5.0%} {fmt(r['avg_ret']):>7} {fmt(r['avg_mae']):>7} "
          f"{fmt(r['ras']):>7} {r['conf']}")
    if not top_A:
        L("  No combinations with n >= 4.")
    L()

    # ── Section 3: Hypothesis B ────────────────────────────
    L("=" * 80)
    L("SECTION 3 -- HYPOTHESIS B: SILVER ABSOLUTE DRAWDOWN")
    L("=" * 80)
    L(f"  (regime-exit cool-off at GSR < {exit_level:.2f})")
    L()
    L(f"  {'Signal':<16} {'H':>3} {'N':>4}  {'Hit%':>6} {'Ret%':>7} {'MAE%':>7} {'RAS':>7} Conf")
    L(f"  {'-'*65}")
    top_B = sorted([r for r in rows_B if r["n"] >= 4], key=lambda r: r["ras"], reverse=True)
    for r in top_B:
        L(f"  {r['dd']:<16} {r['h']:>3} {r['n']:>4}  "
          f"{r['hit']:>5.0%} {fmt(r['avg_ret']):>7} {fmt(r['avg_mae']):>7} "
          f"{fmt(r['ras']):>7} {r['conf']}")
    if not top_B:
        L("  No combinations with n >= 4.")
    L()

    # ── Section 4: Hypothesis C ────────────────────────────
    L("=" * 80)
    L("SECTION 4 -- HYPOTHESIS C: A AND B COMBINED")
    L("=" * 80)
    L()
    if not rows_C:
        L("  No qualifying A and B winners to combine.")
    else:
        L(f"  {'H':>3} {'N':>4}  {'Hit%':>6} {'Ret%':>7} {'MAE%':>7} {'RAS':>7} Conf")
        L(f"  {'-'*50}")
        for r in rows_C:
            L(f"  {r['h']:>3} {r['n']:>4}  {r['hit']:>5.0%} {fmt(r['avg_ret']):>7} "
              f"{fmt(r['avg_mae']):>7} {fmt(r['ras']):>7} {r['conf']}")
    L()

    # ── Section 5: Winner ──────────────────────────────────
    L("=" * 80)
    L("SECTION 5 -- WINNER SELECTION AND PER-EVENT BREAKDOWN")
    L("=" * 80)
    L()

    if winner:
        L(f"  WINNER: Hypothesis {hyp}")
        if hyp == "A":
            L(f"  HIGH signal:   {winner['high']}")
            L(f"  REV signal:    {winner['rev']}")
        elif hyp == "B":
            L(f"  DD signal:     {winner['dd']}")
        elif hyp == "C":
            L(f"  A combo:       {winner.get('a_high','')} & {winner.get('a_rev','')}")
            L(f"  B combo:       {winner.get('b_dd','')}")
        L(f"  Horizon:       {winner['h']}m")
        L(f"  N events:      {winner['n']}")
        L(f"  Hit rate:      {winner['hit']:.0%}")
        L(f"  Avg return:    {fmt(winner['avg_ret'])}%")
        L(f"  Avg MAE:       {fmt(winner['avg_mae'])}%")
        L(f"  RAS:           {fmt(winner['ras'])}")
        L(f"  Confidence:    {winner['conf']}")
        L()
        L(f"  Per-event breakdown:")
        L(f"  {'Entry':<8}  {'Silver':>8}  {'GSR':>7}  {'Ret%':>8}  {'MAE%':>8}  {'RAS':>7}  Result")
        L(f"  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*8}  {'-'*8}  {'-'*7}  ------")
        h = winner["h"]
        for ed in winner["entries"]:
            if ed not in silver.index:
                continue
            ei = silver.index.get_loc(ed)
            if ei + h >= len(silver):
                L(f"  {ed.strftime('%Y-%m')}   (no forward data)")
                continue
            ep  = silver.iloc[ei]
            path_ret = (silver.iloc[ei:ei+h+1] - ep) / ep * 100
            fwd = float(path_ret.iloc[-1])
            mae = float(path_ret.min())
            ras = fwd / max(abs(mae), 1.0)
            gv  = float(gsr.loc[ed]) if ed in gsr.index else np.nan
            hit = "HIT " if fwd > 0 else "MISS"
            L(f"  {ed.strftime('%Y-%m')}   {ep:8.2f}  {gv:7.1f}  {fwd:8.2f}  {mae:8.2f}  {ras:7.3f}  {hit}")
    else:
        L("  No winner found (no combination with n >= 4).")
    L()

    # ── Section 6: Stipulated params + sensitivity ─────────
    L("=" * 80)
    L("SECTION 6 -- STIPULATED PARAMETERS AND SENSITIVITY CHECK")
    L("=" * 80)
    L()
    L("  Stipulated (not derived):")
    L(f"    Rolling window for trailing percentiles: {ROLLING_WINDOW_YR}yr  [stipulated]")
    L(f"    Resampling: monthly (month-end)  [stipulated]")
    L(f"    RAS formula: return / max(|MAE|, 1%)  [stipulated]")
    L(f"    Regime-exit level: GSR median = {exit_level:.2f}  [derived]")
    L()

    if winner and sensitivity:
        L("  Sensitivity -- re-run winner with 3yr and 7yr rolling windows:")
        L(f"  {'Window':<9} {'N':>4} {'Hit%':>6} {'Ret%':>7} {'RAS':>7} Unchanged?")
        L(f"  {'-'*50}")
        L(f"  5yr (base)  {winner['n']:>4} {winner['hit']:>5.0%} {fmt(winner['avg_ret']):>7} {fmt(winner['ras']):>7}")
        for w in [3, 7]:
            s = sensitivity.get(w, {})
            n = s.get("n", 0)
            unch = "YES" if n >= 4 and s.get("conf","INSUFFICIENT") != "INSUFFICIENT" else "NO"
            L(f"  {w}yr          {n:>4} {s.get('hit',0):>5.0%} {fmt(s.get('avg_ret',np.nan)):>7} {fmt(s.get('ras',np.nan)):>7} {unch}")
    L()

    # ── Section 7: DCA comparison ──────────────────────────
    L("=" * 80)
    L("SECTION 7 -- BASELINE DCA COMPARISON AND HONEST VERDICT")
    L("=" * 80)
    L()

    if winner:
        h = winner["h"]
        d = dca.get(h, {})
        total_mo = st["gsr_n"] - h
        deploy_rate = winner["n"] / total_mo if total_mo > 0 else 0
        idle_pct = 1 - deploy_rate
        eff_ret = winner["avg_ret"] * deploy_rate

        L(f"  Horizon: {h}m")
        L(f"  {'Metric':<32} {'Rule':>10} {'Monthly DCA':>12}")
        L(f"  {'-'*56}")
        L(f"  {'N entries':<32} {winner['n']:>10} {d.get('n',0):>12}")
        L(f"  {'Hit rate':<32} {winner['hit']:>9.0%} {d.get('hit',0):>11.0%}")
        L(f"  {'Avg return %':<32} {fmt(winner['avg_ret']):>10} {fmt(d.get('avg_ret',np.nan)):>12}")
        L(f"  {'Avg max adverse %':<32} {fmt(winner['avg_mae']):>10} {fmt(d.get('avg_mae',np.nan)):>12}")
        L(f"  {'Risk-adj score':<32} {fmt(winner['ras']):>10} {fmt(d.get('ras',np.nan)):>12}")
        L(f"  {'Idle cash periods':<32} {idle_pct:>9.0%} {'0%':>12}")
        L(f"  {'Effective return (ret x deploy%)':<32} {eff_ret:>9.2f}% {fmt(d.get('avg_ret',np.nan)):>11}%")
        L()
        L("  HONEST VERDICT:")
        dca_ret = d.get("avg_ret", np.nan)
        dca_ras = d.get("ras", np.nan)
        if not np.isnan(dca_ret):
            per_trade = winner["avg_ret"] > dca_ret
            ras_beats = winner["ras"] > dca_ras if not np.isnan(dca_ras) else False
            eff_beats = eff_ret > dca_ret

            L(f"    Per-trade return: rule {fmt(winner['avg_ret'])}% vs DCA {fmt(dca_ret)}% -> {'RULE' if per_trade else 'DCA'}")
            L(f"    Risk-adj score:   rule {fmt(winner['ras'])} vs DCA {fmt(dca_ras)} -> {'RULE' if ras_beats else 'DCA'}")
            L(f"    Effective return (after {idle_pct:.0%} idle): {eff_ret:.2f}% vs DCA {fmt(dca_ret)}% -> {'RULE' if eff_beats else 'DCA'}")
            L()
            if eff_beats and ras_beats:
                L("    CONCLUSION: Rule beats DCA on both effective return and risk-adjusted basis.")
                L("    Active deployment adds value vs systematic monthly buying.")
            elif ras_beats and not eff_beats:
                L("    CONCLUSION: Rule selects better entry points per trade but idle cash drag")
                L("    erodes total effective return vs DCA. Rule useful for limiting downside;")
                L("    DCA wins on total silver accumulation. If idle cash earns T-bill rates,")
                L("    recalculate with opportunity rate included.")
            elif per_trade and not eff_beats:
                L("    CONCLUSION: Rule picks better entry dates but is deployed too rarely.")
                L("    Consider relaxing entry threshold or using rule as a SIZING signal")
                L("    rather than a deploy/hold binary decision.")
            else:
                L("    CONCLUSION: DCA outperforms on both metrics. Systematic monthly buying")
                L("    beats the active rule. Rule should not replace DCA. May add value as")
                L("    an overweight/underweight sizing signal within an ongoing DCA program.")
    L()
    L("=" * 80)
    return "\n".join(L_lines)


# ── YAML proposal ──────────────────────────────────────────
def build_yaml(winner, hyp, st, sensitivity, dca, exit_level, best_a, best_b):
    if not winner:
        return "  # No qualifying winner found."
    h = winner["h"]
    d = dca.get(h, {})
    total_mo = st["gsr_n"] - h
    deploy_rate = winner["n"] / total_mo if total_mo > 0 else 0
    idle_pct = 1 - deploy_rate
    eff_ret = winner["avg_ret"] * deploy_rate
    beats = "Y" if eff_ret > d.get("avg_ret", 99) else "N"
    s3 = sensitivity.get(3, {})
    s7 = sensitivity.get(7, {})
    unch3 = "Y" if s3.get("n",0) >= 4 and s3.get("conf","INSUFFICIENT") != "INSUFFICIENT" else "N"
    unch7 = "Y" if s7.get("n",0) >= 4 and s7.get("conf","INSUFFICIENT") != "INSUFFICIENT" else "N"

    if hyp == "A":
        cond_h = f"      type: {'percentile' if 'pct' in winner['high'] else 'absolute_multiple'}\n      parameter: {winner['high']}"
        cond_r = f"      type: {winner['rev']}\n      parameter: see above"
        cond_d = "      lookback_months: null\n      threshold_pct: null"
    elif hyp == "B":
        parts = winner["dd"].split("_")
        lb = parts[0].replace("m","")
        pq = parts[1].replace("p","")
        thresh = st.get(f"dd_{lb}m_p{pq}", 0)
        cond_h = "      type: null\n      parameter: null"
        cond_r = "      type: null\n      parameter: null"
        cond_d = f"      lookback_months: {lb}\n      threshold_pct: {thresh:.1f}%"
    else:
        cond_h = f"      a_high: {winner.get('a_high','')}\n      a_rev: {winner.get('a_rev','')}"
        cond_r = "      # combined -- see condition_high"
        bd = winner.get("b_dd","")
        parts = bd.split("_") if bd else []
        lb = parts[0].replace("m","") if parts else "12"
        pq = parts[1].replace("p","") if len(parts)>1 else "50"
        thresh = st.get(f"dd_{lb}m_p{pq}", 0)
        cond_d = f"      lookback_months: {lb}\n      threshold_pct: {thresh:.1f}%"

    return f"""  silver_deployment:
    status: PROVISIONAL
    derivation_date: {date.today().isoformat()}

    reference_stats:
      gsr_median: {st['gsr_median']:.1f}
      gsr_mean: {st['gsr_mean']:.1f}
      gsr_stdev: {st['gsr_stdev']:.1f}
      gsr_75th_percentile: {st['gsr_p75']:.1f}
      gsr_85th_percentile: {st['gsr_p85']:.1f}
      gsr_95th_percentile: {st['gsr_p95']:.1f}
      cool_off_level: {exit_level:.1f}
      silver_typical_drawdown_50th_12m: {st['dd_12m_p50']:.1f}%
      silver_typical_drawdown_75th_12m: {st['dd_12m_p75']:.1f}%
      silver_typical_drawdown_90th_12m: {st['dd_12m_p90']:.1f}%

    winning_hypothesis: {hyp}
    condition_high:
{cond_h}
    condition_reverting:
{cond_r}
    condition_drawdown:
{cond_d}
    hold_horizon_months: {h}
    cool_off_gsr_level: {exit_level:.1f}

    performance:
      n_events: {winner['n']}
      hit_rate: {winner['hit']:.0%}
      avg_return: {winner['avg_ret']:.2f}%
      avg_max_adverse: {winner['avg_mae']:.2f}%
      risk_adjusted_score: {winner['ras']:.3f}
      confidence: {winner['conf']}

    baseline_comparison:
      dca_avg_return: {d.get('avg_ret', float('nan')):.2f}%
      rule_avg_return: {winner['avg_ret']:.2f}%
      rule_beats_dca: {beats}
      idle_cash_periods_pct: {idle_pct:.0%}

    stipulated_parameters:
      rolling_window_years: 5
      resampling: monthly
      sensitivity_3yr_winner_unchanged: {unch3}
      sensitivity_7yr_winner_unchanged: {unch7}

    notes:
      - "Regime-exit: no re-entry until GSR drops below {exit_level:.1f} (full-history median)."
      - "Winner selected by max RAS (return / max(|MAE|, 1%)) subject to n >= 4."
      - "Confidence {winner['conf']}. Status PROVISIONAL until more cycles observed."
"""


# ── Main ──────────────────────────────────────────────────
def main():
    print("Fetching GC=F...", flush=True)
    gold_d  = fetch_yahoo("GC=F")
    print("Fetching SI=F...", flush=True)
    silver_d = fetch_yahoo("SI=F")

    if gold_d is None or silver_d is None:
        print("ERROR: Data fetch failed.")
        return

    gold_m   = to_monthly(gold_d)
    silver_m = to_monthly(silver_d)
    idx      = gold_m.index.intersection(silver_m.index)
    gold_m   = gold_m.loc[idx]
    silver_m = silver_m.loc[idx]
    gsr      = gold_m / silver_m

    print(f"Monthly data: {idx[0].strftime('%Y-%m')} to {idx[-1].strftime('%Y-%m')} ({len(idx)} months)")

    st          = reference_stats(gsr, silver_m)
    exit_level  = st["gsr_median"]

    print("Generating signals...", flush=True)
    high_sigs = make_high_signals(gsr, st)
    rev_sigs  = make_rev_signals(gsr)
    dd_sigs   = make_dd_signals(silver_m, st)

    print("Sweeping Hypothesis A...", flush=True)
    rows_A = sweep_A(high_sigs, rev_sigs, gsr, silver_m, exit_level)

    print("Sweeping Hypothesis B...", flush=True)
    rows_B = sweep_B(dd_sigs, gsr, silver_m, exit_level)

    best_a = best_of(rows_A)
    best_b = best_of(rows_B)

    print("Building Hypothesis C...", flush=True)
    rows_C = []
    if best_a and best_b:
        hs = high_sigs[best_a["high"]]
        rs = rev_sigs[best_a["rev"]]
        ds = dd_sigs[best_b["dd"]]
        raw_c = hs & rs & ds
        filt_c = regime_exit(raw_c, gsr, exit_level)
        c_ent  = filt_c[filt_c].index.tolist()
        for h in HORIZONS:
            recs = backtest(c_ent, silver_m, h)
            sc   = score(recs) or {"n":0,"hit":0,"avg_ret":np.nan,"avg_mae":np.nan,"ras":np.nan,"conf":"INSUFFICIENT"}
            rows_C.append({"hyp":"C","a_high":best_a["high"],"a_rev":best_a["rev"],
                           "b_dd":best_b["dd"],"h":h,"entries":c_ent,**sc})

    best_c = best_of(rows_C)

    # Overall winner
    candidates = [(r, r["hyp"]) for r in [best_a, best_b, best_c] if r is not None]
    if candidates:
        winner, hyp = max(candidates, key=lambda x: x[0]["ras"])
    else:
        winner = hyp = None

    print("Computing DCA baseline...", flush=True)
    dca = dca_baseline(silver_m, HORIZONS)

    print("Running sensitivity check...", flush=True)
    sensitivity = {}
    if winner:
        sensitivity = sensitivity_check(winner, hyp, high_sigs, rev_sigs, dd_sigs,
                                        gsr, silver_m, exit_level, st)

    print("Building report...", flush=True)
    report = build_report(st, rows_A, rows_B, rows_C, winner, hyp,
                          dca, sensitivity, silver_m, gsr, exit_level)
    print(report)

    out = Path(__file__).parent / "silver_deployment_report.txt"
    out.write_text(report, encoding="utf-8")
    print(f"\nReport saved: {out}")

    yaml_block = build_yaml(winner, hyp, st, sensitivity, dca, exit_level, best_a, best_b)
    print("\n" + "=" * 80)
    print("PROPOSED config/thresholds.yaml ADDITION")
    print("=" * 80)
    print(yaml_block)

    return winner, hyp, st


if __name__ == "__main__":
    main()
