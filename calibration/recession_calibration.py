"""
calibration/recession_calibration.py
Recession indicator calibration pipeline. Print report only. No files saved.
"""

import contextlib
import io
import json
import os
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
warnings.filterwarnings("ignore")

API_KEY = os.getenv("FRED_API_KEY", "")


# ── Data fetching ──────────────────────────────────────────

def fetch_fred_api(series_id, obs_start=None, retries=3):
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={API_KEY}&file_type=json"
        + (f"&observation_start={obs_start}" if obs_start else "")
    )
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=20)
            if r.status_code in (500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            data = r.json()
            if "observations" not in data:
                raise ValueError(data.get("error_message", "no observations"))
            df = pd.DataFrame(data["observations"])[["date", "value"]]
            df["date"] = pd.to_datetime(df["date"])
            df = df[df["value"] != "."]
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            return df.dropna().set_index("date")["value"]
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed after {retries} attempts")


def fetch_fred_csv(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), parse_dates=[0])
    df.columns = ["date", "value"]
    df = df[df["value"] != "."]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna().set_index("date")["value"]


def to_monthly(series, method="last"):
    if method == "mean":
        return series.resample("ME").mean()
    return series.resample("ME").last()


def load_all():
    data = {}
    failed = []

    mean_series = {"T10Y3M", "T10Y2Y", "DFF", "DFII10", "VIXCLS"}
    fred_series = ["USREC", "T10Y3M", "T10Y2Y", "DFII10", "ICSA",
                   "UMCSENT", "INDPRO", "MANEMP", "PCEPILFE", "DFF", "VIXCLS"]

    for sid in fred_series:
        try:
            method = "mean" if sid in mean_series else "last"
            data[sid] = to_monthly(fetch_fred_api(sid), method=method)
        except Exception as e:
            data[sid] = None
            failed.append(f"{sid}: {str(e)[:60]}")

    if failed:
        print("  LOAD FAILURES:")
        for f in failed:
            print(f"    {f}")

    # SP500 -- try primary, fall back to total return index
    for sid in ["SP500", "SPASTT01USM661N"]:
        try:
            s = to_monthly(fetch_fred_api(sid, obs_start="1956-01-01"), method="last")
            if s.index[0].year < 2000:
                data["SP500"] = s
                break
        except Exception:
            continue

    # Credit spread -- BAA minus AAA
    try:
        baa = to_monthly(fetch_fred_csv(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAA&observation_start=1919-01-01"))
        aaa = to_monthly(fetch_fred_csv(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=AAA&observation_start=1919-01-01"))
        aligned = pd.concat([baa, aaa], axis=1, join="inner")
        aligned.columns = ["baa", "aaa"]
        data["CREDIT_SPREAD"] = aligned["baa"] - aligned["aaa"]
    except Exception:
        data["CREDIT_SPREAD"] = None

    # CAPE -- multpl.com
    try:
        r = requests.get("https://multpl.com/shiller-pe/table/by-month",
                         timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        tables = pd.read_html(io.StringIO(r.text))
        df = tables[0].copy()
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        data["CAPE"] = df.dropna().set_index("date")["value"].resample("ME").last()
    except Exception:
        data["CAPE"] = None

    return data


# ── Recession cycles ───────────────────────────────────────

def get_recession_cycles(usrec):
    usrec = usrec.dropna()
    cycles, in_rec, start = [], False, None
    for date, val in usrec.items():
        if val == 1 and not in_rec:
            in_rec, start = True, date
        elif val == 0 and in_rec:
            in_rec = False
            cycles.append((start, date))
    if in_rec:
        cycles.append((start, usrec.index[-1]))
    return cycles


# ── Signal generation ──────────────────────────────────────

def gen_signal(series, kind, **kw):
    if series is None or series.empty:
        return None
    s = series.dropna()

    if kind == "crossing_below_zero":
        return (s < 0).reindex(series.index, fill_value=False)

    if kind == "level_above":
        return (s > kw["threshold"]).reindex(series.index, fill_value=False)

    if kind == "level_below":
        return (s < kw["threshold"]).reindex(series.index, fill_value=False)

    if kind == "sp500_below_ma":
        ma = s.rolling(kw.get("window", 10)).mean()
        return (s < ma).reindex(series.index, fill_value=False)

    if kind == "consecutive_decline":
        n = kw.get("n", 3)
        mom = s.diff()
        sig = (mom < 0).rolling(n).sum() == n
        return sig.fillna(False).reindex(series.index, fill_value=False)

    if kind == "yoy_change_above":
        yoy = s.pct_change(12) * 100
        return (yoy > kw["threshold"]).reindex(series.index, fill_value=False)

    if kind == "fed_cutting":
        n = kw.get("n", 3)
        diff = s.diff()
        sig = (diff < 0).rolling(n).sum() == n
        return sig.fillna(False).reindex(series.index, fill_value=False)

    return None


def optimize_threshold(series, kind, rec_cycles, test_vals):
    best_score, best_th = -999, test_vals[0]
    for th in test_vals:
        sig = gen_signal(series, kind, threshold=th)
        if sig is None:
            continue
        _, hits, total, fp = calibrate(sig, rec_cycles)
        if total == 0:
            continue
        score = (hits / total) - 0.5 * (fp / 100)
        if score > best_score:
            best_score, best_th = score, th
    return best_th


# ── Calibration ────────────────────────────────────────────

def calibrate(signal, rec_cycles, lookback=18, horizon=12):
    if signal is None or signal.empty:
        return 0, 0, 0, 0

    signal = signal.fillna(False)
    hits, lead_times, total = 0, [], 0

    for rs, re in rec_cycles:
        if rs < signal.index[0] or rs > signal.index[-1]:
            continue
        total += 1
        win_start = rs - pd.DateOffset(months=lookback)
        try:
            win = signal.loc[win_start:rs]
        except Exception:
            continue
        if win.any():
            hits += 1
            first_sig = win[win].index[0]
            try:
                lead = max(0, (rs.to_period("M") - first_sig.to_period("M")).n)
            except Exception:
                lead = 0
            lead_times.append(min(lead, lookback))

    sig_months = signal[signal].index
    fp = 0
    for sm in sig_months:
        in_rec = any(rs <= sm <= re for rs, re in rec_cycles)
        near_rec = any(
            0 <= (rs.to_period("M") - sm.to_period("M")).n <= horizon
            for rs, re in rec_cycles if rs >= sm
        )
        if not in_rec and not near_rec:
            fp += 1

    fp_rate = (fp / len(sig_months) * 100) if len(sig_months) > 0 else 0
    avg_lead = round(np.mean(lead_times)) if lead_times else 0
    return avg_lead, hits, total, fp_rate


def confidence_level(hit_rate, fp_rate):
    if hit_rate >= 0.75 and fp_rate < 30:
        return "HIGH"
    if hit_rate >= 0.50 and fp_rate < 50:
        return "MED"
    return "LOW"


# ── Composite probability ──────────────────────────────────

def composite_probability(signals_dict, rec_cycles, h6=6, h12=12):
    valid = {k: v for k, v in signals_dict.items() if v is not None and not v.empty}
    if not valid:
        return {}
    df = pd.DataFrame(valid).fillna(False)
    n_active = df.sum(axis=1).astype(int)
    results = {}
    for n in sorted(n_active.unique()):
        months = n_active[n_active == n].index
        rec6 = rec12 = 0
        for m in months:
            for rs, re in rec_cycles:
                if rs < m:
                    continue
                diff = (rs.to_period("M") - m.to_period("M")).n
                if 0 <= diff <= h6:
                    rec6 += 1
                    break
            for rs, re in rec_cycles:
                if rs < m:
                    continue
                diff = (rs.to_period("M") - m.to_period("M")).n
                if 0 <= diff <= h12:
                    rec12 += 1
                    break
        results[n] = {
            "months": len(months),
            "p6": round(rec6 / len(months) * 100),
            "p12": round(rec12 / len(months) * 100),
        }
    return results


# ── Live state ─────────────────────────────────────────────

def get_live_value(row, data):
    """Return (live_value_str, as_of_date_str) for the most recent observation."""
    sid      = row["series"]
    sig_kind = row["sig_kind"]
    th       = row["threshold_val"]

    # CREDIT_SPREAD is derived -- use BAA-AAA from data
    series = data.get(sid)
    if series is None or series.empty:
        return "N/A", "N/A"

    s      = series.dropna()
    last   = s.iloc[-1]
    as_of  = s.index[-1].strftime("%Y-%m-%d")

    if sig_kind == "crossing_below_zero":
        return f"{last:+.2f}%", as_of

    if sig_kind == "level_above":
        if sid == "ICSA":
            return f"{last/1000:.0f}k", as_of
        if sid in ("CREDIT_SPREAD", "DFII10"):
            return f"{last:.2f}%", as_of
        if sid == "VIXCLS":
            return f"{last:.1f}", as_of
        if sid == "CAPE":
            return f"{last:.1f}", as_of
        return f"{last:.2f}%", as_of

    if sig_kind == "level_below":
        return f"{last:.1f}", as_of

    if sig_kind == "sp500_below_ma":
        ma = s.rolling(10).mean().dropna().iloc[-1]
        pct = (last - ma) / ma * 100
        return f"{pct:+.1f}% vs 10mo MA", as_of

    if sig_kind == "consecutive_decline":
        mom = s.diff().dropna()
        neg = int((mom.iloc[-3:] < 0).sum())
        return f"{neg}/3 months declining", as_of

    if sig_kind == "yoy_change_above":
        yoy = s.pct_change(12).dropna().iloc[-1] * 100
        return f"{yoy:.1f}% YoY", as_of

    if sig_kind == "fed_cutting":
        diff  = s.diff().dropna()
        cuts  = int((diff.iloc[-3:] < 0).sum())
        return f"{last:.2f}% ({cuts}/3mo cutting)", as_of

    return f"{last:.2f}", as_of


def live_firing(row, signals):
    sig = signals.get(row["series"])
    if sig is None or sig.empty:
        return None
    last = sig.dropna()
    if last.empty:
        return None
    return bool(last.iloc[-1])


def regime_label(n_firing):
    if n_firing >= 7:
        return "HIGH -- reassess positioning"
    if n_firing >= 5:
        return "ELEVATED -- regime shift risk rising"
    return "BACKGROUND NOISE"


# ── Report ─────────────────────────────────────────────────

def print_report(rows, rec_cycles, data, signals, comp_probs):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    valid = [s for s in data.values() if s is not None]
    all_start = min(s.index[0] for s in valid).strftime("%Y-%m-%d")
    all_end   = max(s.index[-1] for s in valid).strftime("%Y-%m-%d")

    print()
    print("RECESSION INDICATOR CALIBRATION REPORT")
    print("=" * 68)
    print(f"Generated: {now}")
    print(f"Data range: {all_start} to {all_end}")
    print(f"Recession cycles analyzed: {len(rec_cycles)}")
    print("Recession periods used:")
    for rs, re in rec_cycles:
        print(f"  {rs.strftime('%Y-%m-%d')} to {re.strftime('%Y-%m-%d')}")

    composite_rows  = [r for r in rows if r["composite"]]
    monitoring_rows = [r for r in rows if not r["composite"]]
    hdr = f"{'Series':<16} {'Signal':<12} {'Threshold':<13} {'Lead Time':<13} {'Hit Rate':<12} {'FP Rate':<11} Confidence"

    print()
    print("INDICATOR RESULTS -- COMPOSITE SET (all 13 indicators)")
    print("-" * 68)
    if composite_rows:
        print(hdr)
        print("-" * 68)
        for r in composite_rows:
            print(f"{r['series']:<16} {r['signal']:<12} {r['threshold']:<13} {r['lead']:<13} {r['hit_rate']:<12} {r['fp_rate']:<11} {r['conf']}")
    else:
        print("  NO indicators pass FP <= 40% filter -- composite is empty.")
        print(f"  Best available: {min(rows, key=lambda x: float(x['fp_rate'].rstrip('%')))['series']} "
              f"at {min(rows, key=lambda x: float(x['fp_rate'].rstrip('%')))['fp_rate']} FP")
        print("  Consider raising the FP threshold to include leading indicators.")

    print()
    print("SIGNAL QUALITY RANKING")
    print("-" * 68)
    print(f"{'Rank':<8} {'Series':<16} {'Composite Score':<19} {'Status':<18} Primary Value")
    print("-" * 68)
    ranked = sorted(rows, key=lambda x: x["score"], reverse=True)
    for i, r in enumerate(ranked, 1):
        status = "COMPOSITE" if r["composite"] else "monitoring"
        print(f"{i:<8} {r['series']:<16} {r['score']:.3f}              {status:<18} {r['hit_rate']} -- lead {r['lead']}")

    print()
    print("COMPOSITE SCORE CALIBRATION")
    print("-" * 68)
    if not comp_probs or not composite_rows:
        print("  No composite calibration -- zero indicators in composite set.")
    else:
        for n, vals in sorted(comp_probs.items()):
            if n == 0:
                continue
            print(f"{n} indicator{'s' if n != 1 else ''} simultaneously at threshold:")
            print(f"  {vals['p6']}% probability recession within 6 months")
            print(f"  {vals['p12']}% probability recession within 12 months")
            print(f"  Based on {vals['months']} historical months")
            print()

    # Live state
    print()
    print("LIVE SIGNAL STATE")
    print("-" * 80)
    print(f"{'Series':<16} {'Live Value':<24} {'Threshold':<14} {'Firing':<8} Set")
    print("-" * 80)

    composite_firing = 0
    total_firing = 0
    for r in rows:
        live_val, as_of = get_live_value(r, data)
        firing = live_firing(r, signals)
        if firing is None:
            flag = "N/A"
        elif firing:
            flag = "YES"
            total_firing += 1
            if r["composite"]:
                composite_firing += 1
        else:
            flag = "NO"
        set_label = "composite" if r["composite"] else "monitoring"
        print(f"{r['series']:<16} {live_val:<24} {r['threshold']:<14} {flag:<8} {set_label}")

    n_composite = len(composite_rows)
    print("-" * 80)
    if composite_rows:
        print(f"COMPOSITE LIVE STATE: {composite_firing}/{n_composite} composite indicators at threshold")
        print(f"RECESSION PROBABILITY: {regime_label(composite_firing)}")
    else:
        print(f"COMPOSITE LIVE STATE: N/A -- no indicators in composite set")
        print(f"ALL INDICATORS: {total_firing}/{len(rows)} at threshold (monitoring only)")
    print()
    print("=" * 68)


# ── Main ───────────────────────────────────────────────────

def main():
    data = load_all()

    usrec = data.get("USREC")
    if usrec is None:
        print("FATAL: Could not load USREC")
        return

    all_cycles = get_recession_cycles(usrec)
    rec_cycles = [(rs, re) for rs, re in all_cycles if rs.year >= 1960]

    rows = []
    signals = {}

    def add(series_id, sig_type, sig_kind, threshold_val, threshold_str, sig, lead, hits, total, fp):
        hr = hits / total if total else 0
        in_composite = True
        rows.append({
            "series": series_id, "signal": sig_type, "threshold": threshold_str,
            "sig_kind": sig_kind, "threshold_val": threshold_val,
            "lead": f"{lead} months", "hit_rate": f"{hits}/{total}",
            "fp_rate": f"{fp:.0f}%", "conf": confidence_level(hr, fp),
            "score": hr * (1 - fp / 100),
            "composite": in_composite,
        })
        if sig is not None:
            signals[series_id] = sig

    # T10Y3M
    s = data.get("T10Y3M")
    if s is not None:
        sig = gen_signal(s, "crossing_below_zero")
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("T10Y3M", "crossing", "crossing_below_zero", 0, "<0%", sig, l, h, t, fp)

    # T10Y2Y
    s = data.get("T10Y2Y")
    if s is not None:
        sig = gen_signal(s, "crossing_below_zero")
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("T10Y2Y", "crossing", "crossing_below_zero", 0, "<0%", sig, l, h, t, fp)

    # DFII10
    s = data.get("DFII10")
    if s is not None:
        th = optimize_threshold(s, "level_above", rec_cycles, np.arange(0.5, 3.1, 0.25))
        sig = gen_signal(s, "level_above", threshold=th)
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("DFII10", "level", "level_above", th, f">{th:.1f}%", sig, l, h, t, fp)

    # ICSA
    s = data.get("ICSA")
    if s is not None:
        th = optimize_threshold(s, "level_above", rec_cycles, np.arange(200000, 600000, 25000))
        sig = gen_signal(s, "level_above", threshold=th)
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("ICSA", "level", "level_above", th, f">{th/1000:.0f}k", sig, l, h, t, fp)

    # UMCSENT
    s = data.get("UMCSENT")
    if s is not None:
        th = optimize_threshold(s, "level_below", rec_cycles, np.arange(60, 91, 5))
        sig = gen_signal(s, "level_below", threshold=th)
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("UMCSENT", "level", "level_below", th, f"<{th:.0f}", sig, l, h, t, fp)

    # INDPRO
    s = data.get("INDPRO")
    if s is not None:
        sig = gen_signal(s, "consecutive_decline", n=3)
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("INDPRO", "direction", "consecutive_decline", None, "3mo decline", sig, l, h, t, fp)

    # MANEMP
    s = data.get("MANEMP")
    if s is not None:
        sig = gen_signal(s, "consecutive_decline", n=3)
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("MANEMP", "direction", "consecutive_decline", None, "3mo decline", sig, l, h, t, fp)

    # PCEPILFE
    s = data.get("PCEPILFE")
    if s is not None:
        th = optimize_threshold(s, "yoy_change_above", rec_cycles, np.arange(1.0, 5.5, 0.5))
        sig = gen_signal(s, "yoy_change_above", threshold=th)
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("PCEPILFE", "direction", "yoy_change_above", th, f">{th:.1f}% YoY", sig, l, h, t, fp)

    # DFF
    s = data.get("DFF")
    if s is not None:
        sig = gen_signal(s, "fed_cutting", n=3)
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("DFF", "direction", "fed_cutting", None, "cutting 3mo", sig, l, h, t, fp)

    # SP500
    s = data.get("SP500")
    if s is not None:
        sig = gen_signal(s, "sp500_below_ma", window=10)
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("SP500", "level", "sp500_below_ma", None, "< 10mo MA", sig, l, h, t, fp)

    # VIXCLS
    s = data.get("VIXCLS")
    if s is not None:
        th = optimize_threshold(s, "level_above", rec_cycles, np.arange(15, 45, 5))
        sig = gen_signal(s, "level_above", threshold=th)
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("VIXCLS", "level", "level_above", th, f">{th:.0f}", sig, l, h, t, fp)

    # CAPE
    s = data.get("CAPE")
    if s is not None:
        th = optimize_threshold(s, "level_above", rec_cycles, np.arange(20, 45, 5))
        sig = gen_signal(s, "level_above", threshold=th)
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("CAPE", "level", "level_above", th, f">{th:.0f}", sig, l, h, t, fp)

    # CREDIT_SPREAD
    s = data.get("CREDIT_SPREAD")
    if s is not None:
        th = optimize_threshold(s, "level_above", rec_cycles, np.arange(0.5, 3.1, 0.25))
        sig = gen_signal(s, "level_above", threshold=th)
        l, h, t, fp = calibrate(sig, rec_cycles)
        add("CREDIT_SPREAD", "level", "level_above", th, f">{th:.2f}%", sig, l, h, t, fp)

    composite_signals = {r["series"]: signals[r["series"]]
                         for r in rows if r["composite"] and r["series"] in signals}
    comp_probs = composite_probability(composite_signals, rec_cycles)

    # Capture report output for file saving
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_report(rows, rec_cycles, data, signals, comp_probs)
    report_text = buf.getvalue()
    print(report_text, end="")

    # Save files
    out_dir = Path(__file__).parent
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # recession_config.json
    config = {
        "calibrated": now_str,
        "recession_cycles_analyzed": len(rec_cycles),
        "regime_thresholds": {
            "background_noise": {"label": "BACKGROUND NOISE", "max_signals": 4},
            "elevated":         {"label": "ELEVATED -- regime shift risk rising", "min_signals": 5, "max_signals": 6},
            "high":             {"label": "HIGH -- reassess positioning", "min_signals": 7},
        },
        "indicators": {
            r["series"]: {
                "signal":      r["signal"],
                "sig_kind":    r["sig_kind"],
                "threshold":   r["threshold"],
                "threshold_val": r["threshold_val"],
                "lead_months": r["lead"],
                "hit_rate":    r["hit_rate"],
                "fp_rate":     r["fp_rate"],
                "composite_score": round(r["score"], 4),
            }
            for r in rows
        },
        "composite_probabilities": {
            str(n): {"p6m": v["p6"], "p12m": v["p12"], "sample_months": v["months"]}
            for n, v in comp_probs.items()
        },
    }
    config_path = out_dir / "recession_config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    # Calibration report txt
    report_path = out_dir / "recession_calibration_report.txt"
    report_path.write_text(report_text, encoding="utf-8")

    print()
    print("FILES SAVED")
    print("-" * 68)
    print(f"  {config_path}")
    print(f"  {report_path}")


if __name__ == "__main__":
    main()
