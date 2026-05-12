"""
calibration/derive_dfii10_threshold.py
Pilot threshold derivation for DFII10 (10-Year TIPS real yield).

Signal:  DFII10 rolling z-score, upward crossings with regime-exit cool-off
Trade:   MINI S/L NOTE AVA (T-Note futures proxy via DGS10 + duration)
Output:  calibration/thresholds_derivation_report.txt

Revision note (2026-05-11):
  Cool-off rule revised from 3-month time-based to regime-exit (z < +0.5sigma).
  Pilot review showed 2022-06 and 2022-09 both firing during a single rate-shock
  episode where z briefly dipped to 1.728 in August before re-crossing. Counting
  one regime as two independent events overstates false positives. The regime-exit
  rule is the methodologically defensible approach regardless of whether it improves
  or worsens the result.
"""

import os, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from pathlib import Path
from datetime import date
from dotenv import load_dotenv

from common import fetch_fred_api, fetch_fred_csv, to_monthly

load_dotenv(Path(__file__).parent.parent / ".env")
API_KEY = os.getenv("FRED_API_KEY", "")

# ── Constants ──────────────────────────────────────────────
DURATION     = 8.0    # modified duration approximation for 10Y T-Note

WINDOWS      = [3, 5, 7]
Z_THRESHOLDS = [1.5, 1.75, 2.0, 2.25, 2.5]
HORIZONS     = [3, 6, 12]
LEVERAGES    = [3, 5, 10]

EXIT_LEVEL = 0.5   # z must drop below this before a new entry is allowed

# KO distance as fraction of underlying, per leverage
KO_DIST = {3: 1/3, 5: 1/5, 10: 1/10}


# ── Data ──────────────────────────────────────────────────
def load_data():
    print("Fetching DFII10...", flush=True)
    try:
        dfii10 = to_monthly(fetch_fred_api("DFII10", API_KEY), method="mean")
    except Exception:
        dfii10 = to_monthly(fetch_fred_csv("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"), method="mean")
    print("Fetching DGS10...", flush=True)
    try:
        dgs10 = to_monthly(fetch_fred_api("DGS10", API_KEY), method="mean")
    except Exception:
        dgs10 = to_monthly(fetch_fred_csv("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"), method="mean")

    idx    = dfii10.index.intersection(dgs10.index)
    dfii10 = dfii10.loc[idx]
    dgs10  = dgs10.loc[idx]

    # T-Note log-price proxy: DeltalnP approx -D_mod * Delta_y  (yield in decimal)
    tnote_lp = -DURATION * (dgs10 / 100.0)
    return dfii10, tnote_lp


# ── Z-score ────────────────────────────────────────────────
def rolling_zscore(series, window_months):
    mu  = series.rolling(window_months, min_periods=window_months).mean()
    sig = series.rolling(window_months, min_periods=window_months).std(ddof=1)
    return (series - mu) / sig.replace(0.0, np.nan)


# ── Entry detection (regime-exit cool-off) ─────────────────
def detect_up_crossings(z, threshold, exit_level=EXIT_LEVEL):
    """
    Upward crossing: z[t-1] < threshold AND z[t] >= threshold.
    After an entry, no new entry is allowed until z drops below exit_level.
    This treats a sustained elevated z as one ongoing regime rather than
    multiple independent signals.
    """
    entries  = []
    zv, zi   = z.values, z.index
    in_regime = False

    for i in range(1, len(zv)):
        if np.isnan(zv[i-1]) or np.isnan(zv[i]):
            continue
        if in_regime:
            if zv[i] < exit_level:
                in_regime = False
        else:
            if zv[i-1] < threshold <= zv[i]:
                entries.append(zi[i])
                in_regime = True
    return entries


# ── Trade evaluation ────────────────────────────────────────
def evaluate_trades(entries, tnote_lp, horizon, leverage):
    """
    Momentum  (short bond): profit = -(DeltalnP) = yields stayed high/rose
    Reversion (long  bond): profit =  (DeltalnP) = yields normalised lower
    KO triggered if max adverse move > KO_DIST[leverage] during [entry, entry+horizon].
    """
    ko_d    = KO_DIST[leverage]
    records = []

    for entry_date in entries:
        if entry_date not in tnote_lp.index:
            continue
        ei    = tnote_lp.index.get_loc(entry_date)
        end_i = ei + horizon
        if end_i >= len(tnote_lp):
            continue

        p0    = tnote_lp.iloc[ei]
        p_end = tnote_lp.iloc[end_i]
        path  = tnote_lp.iloc[ei:end_i + 1] - p0

        fwd      = p_end - p0
        max_up   =  path.max()
        max_down = -path.min()

        mom_adv = max_up
        mom_ret = -fwd
        mom_ko  = mom_adv > ko_d
        mom_lev = -1.0 if mom_ko else max(-1.0, leverage * mom_ret)

        rev_adv = max_down
        rev_ret = fwd
        rev_ko  = rev_adv > ko_d
        rev_lev = -1.0 if rev_ko else max(-1.0, leverage * rev_ret)

        records.append({
            "entry_date": entry_date,
            "fwd_pct":   fwd      * 100,
            "mom_ret":   mom_ret  * 100,
            "mom_ko":    mom_ko,
            "mom_lev":   mom_lev  * 100,
            "rev_ret":   rev_ret  * 100,
            "rev_ko":    rev_ko,
            "rev_lev":   rev_lev  * 100,
            "max_up":    max_up   * 100,
            "max_down":  max_down * 100,
        })
    return records


def score_direction(records, direction):
    if not records:
        return None
    n        = len(records)
    rets     = [r[f"{direction}_ret"] for r in records]
    kos      = [r[f"{direction}_ko"]  for r in records]
    levs     = [r[f"{direction}_lev"] for r in records]
    hit_rate = sum(1 for v in rets if v > 0) / n
    survival = 1.0 - sum(kos) / n
    avg_ret  = np.mean(rets)
    avg_lev  = np.mean(levs)
    max_dd   = min(rets)
    return {
        "n":        n,
        "hit_rate": hit_rate,
        "survival": survival,
        "avg_ret":  avg_ret,
        "avg_lev":  avg_lev,
        "max_dd":   max_dd,
        "score":    hit_rate * avg_ret,
    }


def confidence_label(hit_rate, survival, n):
    if hit_rate >= 0.60 and survival >= 0.85 and n >= 10:
        return "HIGH"
    if hit_rate >= 0.55 and survival >= 0.75 and n >= 8:
        return "MEDIUM"
    if hit_rate >= 0.50 and survival >= 0.65 and n >= 5:
        return "LOW"
    return "INSUFFICIENT"


# ── Per-event breakdown table ──────────────────────────────
def per_event_table(entries, dfii10, z, tnote_lp, horizon, leverage, direction):
    ko_d = KO_DIST[leverage]
    rows = []
    for entry_date in entries:
        if entry_date not in tnote_lp.index:
            rows.append((entry_date, dfii10.get(entry_date), z.get(entry_date),
                         None, None, None, "no fwd data"))
            continue
        ei    = tnote_lp.index.get_loc(entry_date)
        end_i = ei + horizon
        if end_i >= len(tnote_lp):
            rows.append((entry_date, dfii10.get(entry_date), z.get(entry_date),
                         None, None, None, "insufficient fwd"))
            continue
        p0    = tnote_lp.iloc[ei]
        path  = tnote_lp.iloc[ei:end_i+1] - p0
        fwd   = (tnote_lp.iloc[end_i] - p0) * 100

        if direction == "rev":
            adv    = -path.min() * 100
            ret    = fwd
            ko     = (-path.min()) > ko_d
            lev    = -100.0 if ko else max(-100.0, leverage * fwd)
        else:
            adv    = path.max() * 100
            ret    = -fwd
            ko     = path.max() > ko_d
            lev    = -100.0 if ko else max(-100.0, leverage * (-fwd))

        hit = "HIT " if ret > 0 else "MISS"
        rows.append((entry_date, dfii10.get(entry_date), z.get(entry_date),
                     ret, lev, adv, hit))
    return rows


# ── Main ──────────────────────────────────────────────────
def main():
    dfii10, tnote_lp = load_data()
    span = f"{dfii10.index[0].strftime('%Y-%m')} - {dfii10.index[-1].strftime('%Y-%m')}"
    print(f"DFII10: {span}  ({len(dfii10)} months)")

    # ── Sweep ─────────────────────────────────────────────
    all_rows  = []
    z_cache   = {}
    ent_cache = {}

    for w in WINDOWS:
        wm = w * 12
        if w not in z_cache:
            z_cache[w] = rolling_zscore(dfii10, wm)
        z = z_cache[w]
        for thresh in Z_THRESHOLDS:
            k = (w, thresh)
            if k not in ent_cache:
                ent_cache[k] = detect_up_crossings(z, thresh)
            entries = ent_cache[k]
            for h in HORIZONS:
                for lev in LEVERAGES:
                    recs   = evaluate_trades(entries, tnote_lp, h, lev)
                    sc_mom = score_direction(recs, "mom")
                    sc_rev = score_direction(recs, "rev")
                    if sc_mom is None:
                        continue
                    all_rows.append({
                        "w": w, "thresh": thresh, "h": h, "lev": lev,
                        "entries": entries, "recs": recs,
                        "mom": sc_mom, "rev": sc_rev,
                    })

    # ── Report ─────────────────────────────────────────────
    lines = []
    def L(s=""): lines.append(s)

    L("DFII10 THRESHOLD DERIVATION REPORT - PILOT (REVISED)")
    L("=" * 80)
    L(f"Generated:    {date.today().isoformat()}")
    L(f"Signal:       DFII10 (10Y TIPS Real Yield, FRED)")
    L(f"Method:       Rolling z-score (point-in-time), upward crossing detection")
    L(f"Cool-off:     REGIME-EXIT -- new entry blocked until z drops below +{EXIT_LEVEL}sigma")
    L(f"              [Revised from 3-month time-based after pilot review.]")
    L(f"              [Original 3-month rule allowed 2022-06 and 2022-09 to both fire]")
    L(f"              [during a single rate-shock episode (z dipped to 1.728 in Aug]")
    L(f"              [2022, briefly below 1.75 threshold but above exit level 0.5).]")
    L(f"              [Regime-exit rule is methodologically defensible regardless of]")
    L(f"              [whether it improves or worsens the result.]")
    L(f"Windows:      {WINDOWS} years")
    L(f"Z-thresholds: {Z_THRESHOLDS}sigma")
    L(f"Horizons:     {HORIZONS} months")
    L(f"Leverages:    {LEVERAGES}x")
    L(f"KO model:     1/leverage of underlying (3x=33%, 5x=20%, 10x=10%)")
    L(f"T-Note proxy: DGS10 + duration approx (D_mod={DURATION})")
    L(f"Data:         {span}")
    L()

    # ── Entry event inventory ──────────────────────────────
    L("-" * 80)
    L("ENTRY EVENT INVENTORY (regime-exit cool-off: z must drop below +0.5sigma to reset)")
    L("-" * 80)
    L(f"{'Win(yr)':<9} {'Z-thresh':<10} {'N':<5} Entry dates")
    L("-" * 80)
    seen = set()
    for r in all_rows:
        k = (r["w"], r["thresh"])
        if k in seen:
            continue
        seen.add(k)
        dates_str = ", ".join(d.strftime("%Y-%m") for d in r["entries"]) or "-- none --"
        L(f"{r['w']:<9} {r['thresh']:<10.2f} {len(r['entries']):<5} {dates_str}")
    L()

    # ── Full sweep tables ──────────────────────────────────
    for direction, label in [
        ("mom", "MOMENTUM  (MINI S NOTE AVA -- short bond price -- yields stay high/rise)"),
        ("rev", "REVERSION (MINI L NOTE AVA -- long  bond price -- yields normalise lower)"),
    ]:
        L("-" * 80)
        L(f"DIRECTION: {label}")
        L("-" * 80)
        L(f"{'Win':<5}{'Z':>7}{'H':>5}{'Lev':>6}{'N':>5}  "
          f"{'HitRate':>8}{'Surv%':>8}{'AvgRet%':>9}{'AvgLev%':>9}"
          f"{'MaxDD%':>9}{'Score':>8}  Conf")
        L("-" * 80)
        for r in all_rows:
            sc = r[direction]
            if sc is None:
                continue
            conf = confidence_label(sc["hit_rate"], sc["survival"], sc["n"])
            L(f"{r['w']:<5}{r['thresh']:>7.2f}{r['h']:>5}{r['lev']:>6}{sc['n']:>5}  "
              f"{sc['hit_rate']:>7.1%}{sc['survival']:>8.1%}{sc['avg_ret']:>9.2f}"
              f"{sc['avg_lev']:>9.2f}{sc['max_dd']:>9.2f}{sc['score']:>8.3f}  {conf}")
        L()

    # ── Winner selection ───────────────────────────────────
    L("-" * 80)
    L("WINNER SELECTION")
    L("-" * 80)
    L("Criterion: max(hit_rate x avg_ret) subject to survival >= 80% and n >= 3")
    L()

    best_overall = {}
    for direction, label in [("mom", "MOMENTUM"), ("rev", "REVERSION")]:
        L(f"  {label}:")
        for h in HORIZONS:
            cands = [
                r for r in all_rows
                if r["h"] == h
                and r[direction] is not None
                and r[direction]["survival"] >= 0.80
                and r[direction]["n"] >= 3
                and r[direction]["avg_ret"] > 0
            ]
            if not cands:
                L(f"    {h}m: no candidate meets survival >= 80% + n >= 3 + positive avg_ret")
                continue
            best = max(cands, key=lambda r: r[direction]["score"])
            sc   = best[direction]
            conf = confidence_label(sc["hit_rate"], sc["survival"], sc["n"])

            band = sorted(set(
                r["thresh"] for r in all_rows
                if r["h"] == h and r["w"] == best["w"] and r["lev"] == best["lev"]
                and r[direction] is not None
                and r[direction]["avg_ret"] > 0
                and r[direction]["score"] >= 0.9 * sc["score"]
            ))
            band_str = (f"{min(band):.2f}sigma - {max(band):.2f}sigma"
                        if band else "n/a")

            L(f"    {h}m: win={best['w']}yr  z={best['thresh']:.2f}sigma  "
              f"lev={best['lev']}x  n={sc['n']}  "
              f"hit={sc['hit_rate']:.0%}  surv={sc['survival']:.0%}  "
              f"avgRet={sc['avg_ret']:.2f}%  score={sc['score']:.3f}  [{conf}]")
            L(f"         sensitivity band: {band_str}")

            if direction not in best_overall or sc["score"] > best_overall[direction][2]["score"]:
                best_overall[direction] = (best, conf, sc, band_str)
        L()

    # ── Declared winner ────────────────────────────────────
    L("-" * 80)
    L("DECLARED WINNER (across all horizons and directions)")
    L("-" * 80)

    all_cands = [
        (direction, r, r[direction])
        for direction in ("mom", "rev")
        for r in all_rows
        if r[direction] is not None
        and r[direction]["survival"] >= 0.80
        and r[direction]["n"] >= 3
        and r[direction]["avg_ret"] > 0
    ]

    if all_cands:
        best_dir, best_row, best_sc = max(all_cands, key=lambda x: x[2]["score"])
        conf = confidence_label(best_sc["hit_rate"], best_sc["survival"], best_sc["n"])
        dir_label = ("MOMENTUM (MINI S NOTE AVA)" if best_dir == "mom"
                     else "REVERSION (MINI L NOTE AVA)")

        band = sorted(set(
            r["thresh"] for r in all_rows
            if r["h"] == best_row["h"] and r["w"] == best_row["w"]
            and r["lev"] == best_row["lev"]
            and r[best_dir] is not None
            and r[best_dir]["avg_ret"] > 0
            and r[best_dir]["score"] >= 0.9 * best_sc["score"]
        ))
        band_str = f"{min(band):.2f}sigma - {max(band):.2f}sigma" if band else "n/a"

        L(f"  Direction:        {dir_label}")
        L(f"  Window:           {best_row['w']}yr rolling")
        L(f"  Entry threshold:  z >= {best_row['thresh']:.2f}sigma (upward crossing)")
        L(f"  Horizon:          {best_row['h']}m")
        L(f"  Leverage:         {best_row['lev']}x")
        L(f"  N events:         {best_sc['n']}")
        L(f"  Hit rate:         {best_sc['hit_rate']:.0%}")
        L(f"  KO survival:      {best_sc['survival']:.0%}")
        L(f"  Avg return (und): {best_sc['avg_ret']:.2f}%")
        L(f"  Avg return (lev): {best_sc['avg_lev']:.2f}%")
        L(f"  Max drawdown:     {best_sc['max_dd']:.2f}%")
        L(f"  Confidence:       {conf}")
        L(f"  Sensitivity band: {band_str}  (within 10% of optimal score)")

        entry_dates_str = ", ".join(d.strftime("%Y-%m") for d in best_row["entries"])
        L(f"  Entry dates:      {entry_dates_str}")
        L()

        # Per-event breakdown for declared winner
        L("-" * 80)
        L(f"PER-EVENT BREAKDOWN -- {dir_label}")
        L(f"Window: {best_row['w']}yr  |  Threshold: {best_row['thresh']:.2f}sigma  |  "
          f"Horizon: {best_row['h']}m  |  Leverage: {best_row['lev']}x")
        L("-" * 80)

        best_z = z_cache[best_row["w"]]
        rows_ev = per_event_table(
            best_row["entries"], dfii10, best_z, tnote_lp,
            best_row["h"], best_row["lev"], best_dir
        )
        L(f"  {'Entry':<8}  {'DFII10':>7}  {'z':>7}  "
          f"{'Ret%':>8}  {'Lev%':>8}  {'MaxAdv%':>8}  Result")
        L(f"  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*8}  {'-'*8}  {'-'*8}  ------")
        for (ed, dfval, zval, ret, lev, adv, hit) in rows_ev:
            if ret is None:
                L(f"  {ed.strftime('%Y-%m')}   -- {hit}")
                continue
            L(f"  {ed.strftime('%Y-%m')}   {dfval:7.3f}  {zval:7.3f}  "
              f"{ret:8.2f}  {lev:8.2f}  {adv:8.2f}  {hit}")
        L()

        if conf in ("HIGH", "MEDIUM"):
            L("  Rationale: threshold produces directionally consistent returns with")
            L("  adequate event count and KO survival for the chosen horizon.")
        else:
            L("  Rationale: best available given 23yr data span. DFII10 regime shifts")
            L("  are rare by nature. Event count is structurally limited.")
            L("  Treat threshold as PROVISIONAL until additional cycles accumulate.")
    else:
        L("  No candidate meets all criteria. Review sweep table above.")
        best_dir = best_row = best_sc = conf = band_str = None

    L()
    L("-" * 80)
    L("METHODOLOGY NOTES")
    L("-" * 80)
    L("  1. Data: DFII10 starts Jan 2003. 7yr window yields first usable z-score")
    L("     Jan 2010. Structural event scarcity is expected, not a model failure.")
    L("  2. T-Note price proxy: DGS10 converted via duration approximation (D=8).")
    L("     Basis risk exists (real vs nominal yield). Phase 2 should use actual")
    L("     T-Note futures (ZN=F) or IEF daily closes for higher fidelity.")
    L("  3. KO simulation uses month-end prices only. Intramonth volatility spikes")
    L("     can trigger KO earlier than simulated. Real survival rates at 10x are")
    L("     likely lower. Use daily data in phase 2.")
    L("  4. Regime-exit cool-off: after entry, blocked until z < +0.5sigma. This")
    L("     collapses multi-crossing episodes (e.g., 2022 rate shock) into one event.")
    L("     Exit level of 0.5sigma was chosen to represent genuine regime normalisation;")
    L("     it is not optimised -- sensitivity to this parameter not tested.")
    L("  5. The 2022 rate shock remains the primary stress episode in the sample.")
    L("     With regime-exit rule it counts once. All conclusions remain heavily")
    L("     influenced by the pre-2022 vs post-2022 rate environment.")
    L()
    L("=" * 80)

    report = "\n".join(lines)
    print(report)

    out = Path(__file__).parent / "thresholds_derivation_report.txt"
    out.write_text(report, encoding="utf-8")
    print(f"\nSaved: {out}")

    return best_dir, best_row, best_sc, conf, band_str


if __name__ == "__main__":
    main()
