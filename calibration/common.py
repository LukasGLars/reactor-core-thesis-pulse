"""
calibration/common.py
Shared utilities for calibration scripts.
"""

import io
import time

import numpy as np
import pandas as pd
import requests


# ── Data fetching ──────────────────────────────────────────

def fetch_fred_api(series_id, api_key, obs_start=None, retries=3):
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={api_key}&file_type=json"
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
        except Exception:
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


def optimize_threshold(series, kind, rec_cycles, test_vals, calibrate_fn):
    best_score, best_th = -999, test_vals[0]
    for th in test_vals:
        sig = gen_signal(series, kind, threshold=th)
        if sig is None:
            continue
        _, hits, total, fp = calibrate_fn(sig, rec_cycles)
        if total == 0:
            continue
        score = (hits / total) - 0.5 * (fp / 100)
        if score > best_score:
            best_score, best_th = score, th
    return best_th


# ── Scoring ────────────────────────────────────────────────

def confidence_level(hit_rate, fp_rate):
    if hit_rate >= 0.75 and fp_rate < 30:
        return "HIGH"
    if hit_rate >= 0.50 and fp_rate < 50:
        return "MED"
    return "LOW"
