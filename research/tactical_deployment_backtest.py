"""
research/tactical_deployment_backtest.py

Hypothesis: Signal-based tactical deployment with 3%/yr yielding cash buffer
vs. fully-deployed V3 baseline.

THREE MODES compared:
  tactical        — gold 0–25%  (full-or-nothing on signal)
  tactical_floored— gold 12.5–25% (half always deployed, half signal-gated)
  baseline        — gold always 25%

SIGNALS
  Gold:   Deploy when DFII10 < 90d SMA (CvsTC negative).
          Tactical: 0% or 25%.  Floored: 12.5% or 25%.
  Silver: T1 (deploy 5%):  GSR > 83.36 AND fallen >= 5% from 60d rolling peak.
          T2 (deploy +5%): GSR > 86.45 AND fallen >= 5% from 60d rolling peak.
          Signal OFF → uninvested silver in Spiltan.

STATIC (always fully deployed at target weight):
  LLY 15%  WMT 15%  JNJ 6%  CCJ 10%  VRT 10%  AVGO 9%
  VRT pre-IPO (< 2020-02-07): treated as Spiltan in ALL modes.

CASH YIELD: 3%/yr applied daily to uninvested gold + silver + pre-IPO VRT only.

PERIODS
  IS:  2016-04-01 to 2026-03-31  (matches baseline)
  OOS: 2009-01-01 to 2016-03-31  (secondary validation)

BASELINE REFERENCE (from phase5_v3_backtest.py, IS 2016-2026):
  Sharpe 1.851 | CAGR 30.08% | Max DD -24.86%

Sharpe computed with RF = 0% throughout.

DEPENDENCIES: pip install pandas numpy requests
"""

import os
import sys
import time
import requests
import numpy  as np
import pandas as pd
from datetime import date

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# ── Period config ─────────────────────────────────────────────
IS_START  = "2016-04-01"
IS_END    = "2026-03-31"
OOS_START = "2009-01-01"
OOS_END   = "2016-03-31"

# ── Signal parameters ─────────────────────────────────────────
DFII10_SMA_WINDOW = 90     # business-day rolling window for trendcenter SMA
GSR_T1            = 83.36  # p85 — tier 1 threshold
GSR_T2            = 86.45  # p90 — tier 2 threshold
GSR_PEAK_WINDOW   = 60     # rolling peak lookback (trading days)
GSR_FALL_PCT      = 0.05   # minimum fall from peak to qualify

# ── Weight config ─────────────────────────────────────────────
GOLD_W     = 0.25
GOLD_FLOOR = 0.125   # floored mode: minimum gold regardless of signal
SILVER_W   = 0.10
STATIC_W   = {
    "LLY":  0.15,
    "WMT":  0.15,
    "JNJ":  0.06,
    "CCJ":  0.10,
    "VRT":  0.10,
    "AVGO": 0.09,
}
VRT_IPO    = pd.Timestamp("2020-02-07")
CASH_DAILY = 0.03 / 365   # Spiltan 3%/yr → daily


# ── Data fetching ─────────────────────────────────────────────
def fetch_fred(series_id: str) -> pd.Series | None:
    """Full observation history from FRED. Returns daily pd.Series."""
    if FRED_API_KEY:
        url = (f"https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={FRED_API_KEY}"
               f"&file_type=json&sort_order=asc")
    else:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "tactical-backtest/1.0"}, timeout=30)
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
                        try:
                            rows[parts[0].strip()] = float(parts[1].strip())
                        except ValueError:
                            pass
            if not rows:
                return None
            s = pd.Series(rows, dtype=float)
            s.index = pd.to_datetime(s.index)
            return s.sort_index()
        except Exception as e:
            print(f"  FRED {series_id} attempt {attempt + 1}: {e}")
            time.sleep(2 ** attempt)
    return None


def fetch_yahoo(symbol: str) -> pd.Series | None:
    """Full daily close history from Yahoo Finance v8. Returns pd.Series.

    Uses period1/period2 timestamps instead of range=max — the range=max
    parameter silently returns monthly bars for long histories; explicit
    timestamps force daily resolution.
    """
    import datetime
    period1 = int(datetime.datetime(2000, 1, 1).timestamp())
    period2 = int(datetime.datetime.now().timestamp())
    enc = symbol.replace("=", "%3D")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}"
           f"?interval=1d&period1={period1}&period2={period2}")
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
            print(f"  Yahoo {symbol} attempt {attempt + 1}: {e}")
            time.sleep(2 ** attempt)
    return None


# ── Signal computation ────────────────────────────────────────
def compute_gold_signal(dfii10: pd.Series) -> pd.Series:
    """
    Gold deploy signal: 1 when DFII10 < 90d SMA (CvsTC negative).
    Lagged 1 business day to avoid look-ahead bias.
    """
    sma = dfii10.rolling(DFII10_SMA_WINDOW, min_periods=DFII10_SMA_WINDOW).mean()
    raw = (dfii10 < sma).astype(int)
    return raw.shift(1).rename("gold_signal")  # use yesterday's reading


def compute_silver_signals(gold_px: pd.Series, silver_px: pd.Series) -> tuple:
    """
    T1: GSR > 83.36 AND fallen >= 5% from 60d rolling peak → deploy 5%.
    T2: GSR > 86.45 AND fallen >= 5% from 60d rolling peak → deploy further 5%.
    Both lagged 1 trading day.
    """
    gsr  = (gold_px / silver_px).rename("gsr")
    peak = gsr.rolling(GSR_PEAK_WINDOW, min_periods=GSR_PEAK_WINDOW).max()
    fall_ok = ((gsr - peak) / peak) <= -GSR_FALL_PCT

    t1 = ((gsr > GSR_T1) & fall_ok).astype(int).shift(1).rename("t1")
    t2 = ((gsr > GSR_T2) & fall_ok).astype(int).shift(1).rename("t2")
    return t1, t2


# ── Portfolio simulation ──────────────────────────────────────
def simulate(
    prices:  pd.DataFrame,
    gs:      pd.Series,
    t1:      pd.Series,
    t2:      pd.Series,
    mode:    str,   # "tactical" | "tactical_floored" | "baseline"
) -> tuple:
    """
    Vectorized daily NAV simulation starting at 1.0.
    Returns (nav, daily_returns) as pd.Series on the same DatetimeIndex.
    """
    ret = prices.pct_change().iloc[1:]   # drop first NaN row
    idx = ret.index

    def ar(sym: str) -> pd.Series:
        if sym in ret.columns:
            return ret[sym].fillna(0.0)
        return pd.Series(0.0, index=idx)

    # Forward-fill FRED/daily signals onto trading-day index
    gs_ = gs.reindex(idx, method="ffill").fillna(0.0)
    t1_ = t1.reindex(idx, method="ffill").fillna(0.0)
    t2_ = t2.reindex(idx, method="ffill").fillna(0.0)

    if mode == "tactical":
        gold_w   = GOLD_W * gs_                      # 0% or 25%
        silver_w = 0.05 * t1_ + 0.05 * t2_
    elif mode == "tactical_floored":
        gold_w   = GOLD_FLOOR + (GOLD_W - GOLD_FLOOR) * gs_  # 12.5% or 25%
        silver_w = 0.05 * t1_ + 0.05 * t2_
    else:  # baseline
        gold_w   = pd.Series(GOLD_W,   index=idx, dtype=float)
        silver_w = pd.Series(SILVER_W, index=idx, dtype=float)

    # VRT: no price before IPO → treat as Spiltan in all modes
    vrt_w = pd.Series(
        np.where(idx >= VRT_IPO, STATIC_W["VRT"], 0.0),
        index=idx, dtype=float,
    )

    # Cash = uninvested tactical gold + silver + pre-IPO VRT
    if mode in ("tactical", "tactical_floored"):
        cash_w = (GOLD_W - gold_w) + (SILVER_W - silver_w) + (STATIC_W["VRT"] - vrt_w)
    else:
        cash_w = STATIC_W["VRT"] - vrt_w   # baseline: only pre-IPO VRT in cash

    port_ret = (
        gold_w             * ar("GC=F")  +
        silver_w           * ar("SI=F")  +
        STATIC_W["LLY"]    * ar("LLY")   +
        STATIC_W["WMT"]    * ar("WMT")   +
        STATIC_W["JNJ"]    * ar("JNJ")   +
        STATIC_W["CCJ"]    * ar("CCJ")   +
        STATIC_W["AVGO"]   * ar("AVGO")  +
        vrt_w              * ar("VRT")   +
        cash_w             * CASH_DAILY
    )

    nav = (1.0 + port_ret).cumprod()
    return nav, port_ret


# ── Performance metrics ───────────────────────────────────────
def metrics(nav: pd.Series, rets: pd.Series, label: str = "") -> dict:
    cal_days = (nav.index[-1] - nav.index[0]).days
    years    = max(cal_days / 365.25, 1e-6)

    gaps      = pd.Series(nav.index).diff().dt.days.dropna()
    med_gap   = gaps.median()
    periods_per_year = 365.25 / med_gap if med_gap > 0 else 252

    cagr    = nav.iloc[-1] ** (1.0 / years) - 1
    ann_ret = rets.mean() * periods_per_year
    ann_vol = rets.std()  * np.sqrt(periods_per_year)
    sharpe  = ann_ret / ann_vol if ann_vol > 0 else np.nan

    peak   = nav.expanding().max()
    dd     = (nav - peak) / peak
    max_dd = dd.min()
    calmar = cagr / abs(max_dd) if max_dd < 0 else np.nan

    return {
        "label":        label,
        "n_days":       len(nav),
        "total_return": nav.iloc[-1] - 1.0,
        "cagr":         cagr,
        "ann_vol":      ann_vol,
        "sharpe":       sharpe,
        "max_dd":       max_dd,
        "calmar":       calmar,
    }


def signal_stats(
    idx: pd.DatetimeIndex,
    gs:  pd.Series,
    t1:  pd.Series,
    t2:  pd.Series,
) -> dict:
    gs_ = gs.reindex(idx, method="ffill").fillna(0)
    t1_ = t1.reindex(idx, method="ffill").fillna(0)
    t2_ = t2.reindex(idx, method="ffill").fillna(0)

    silver_deployed = 0.05 * t1_ + 0.05 * t2_

    # Original: gold goes to 0 when signal OFF
    gold_cash_orig   = GOLD_W * (1 - gs_)
    # Floored: only the upper half (12.5%) goes to cash
    gold_cash_floor  = (GOLD_W - GOLD_FLOOR) * (1 - gs_)

    silver_cash = SILVER_W - silver_deployed
    vrt_cash    = pd.Series(np.where(idx < VRT_IPO, STATIC_W["VRT"], 0.0), index=idx)

    return {
        "gold_on_pct":        gs_.mean()   * 100,
        "silver_t1_on_pct":   t1_.mean()   * 100,
        "silver_t2_on_pct":   t2_.mean()   * 100,
        "avg_cash_orig_pct":  (gold_cash_orig  + silver_cash + vrt_cash).mean() * 100,
        "avg_cash_floor_pct": (gold_cash_floor + silver_cash + vrt_cash).mean() * 100,
    }


def annual_breakdown(
    nav_orig:    pd.Series,
    nav_floored: pd.Series,
    nav_base:    pd.Series,
) -> pd.DataFrame:
    df = pd.concat([
        nav_orig.rename("orig"),
        nav_floored.rename("floor"),
        nav_base.rename("base"),
    ], axis=1)
    df["orig_ret"]  = df["orig"].pct_change()
    df["floor_ret"] = df["floor"].pct_change()
    df["base_ret"]  = df["base"].pct_change()

    rows = []
    for year, grp in df.groupby(df.index.year):
        o_ret = (1 + grp["orig_ret"].fillna(0)).prod()  - 1
        f_ret = (1 + grp["floor_ret"].fillna(0)).prod() - 1
        b_ret = (1 + grp["base_ret"].fillna(0)).prod()  - 1
        rows.append({
            "year":        year,
            "orig":        o_ret,
            "floored":     f_ret,
            "baseline":    b_ret,
            "delta_orig":  o_ret - b_ret,
            "delta_floor": f_ret - b_ret,
        })
    return pd.DataFrame(rows).set_index("year")


# ── Output formatting ─────────────────────────────────────────
def print_comparison(
    title:    str,
    m_orig:   dict,
    m_floor:  dict,
    m_base:   dict,
    ss:       dict,
) -> None:
    W = 80
    print()
    print("=" * W)
    print(f"  {title}")
    print(f"  {'-' * (W-2)}")
    print(f"  Signal Statistics:")
    print(f"    Gold ON:           {ss['gold_on_pct']:5.1f}% of days")
    print(f"    Silver T1 ON:      {ss['silver_t1_on_pct']:5.1f}% of days")
    print(f"    Silver T2 ON:      {ss['silver_t2_on_pct']:5.1f}% of days")
    print(f"    Avg cash (orig):   {ss['avg_cash_orig_pct']:5.1f}% of portfolio")
    print(f"    Avg cash (floor):  {ss['avg_cash_floor_pct']:5.1f}% of portfolio")
    print()
    print(f"  {'Metric':<22}  {'Orig (0–25%)':>13}  {'Floor (12.5–25%)':>17}  {'Baseline':>10}  {'ΔOrig':>7}  {'ΔFloor':>7}")
    print(f"  {'-' * (W-2)}")

    def row(lbl, key, fmt):
        o = m_orig.get(key)
        f = m_floor.get(key)
        b = m_base.get(key)
        do = (o - b) if (o is not None and b is not None) else None
        df = (f - b) if (f is not None and b is not None) else None
        def _f(v): return f"{v:{fmt}}" if v is not None else "n/a"
        def _d(v): return (("+" if v > 0 else "") + f"{v:{fmt}}") if v is not None else "n/a"
        print(f"  {lbl:<22}  {_f(o):>13}  {_f(f):>17}  {_f(b):>10}  {_d(do):>7}  {_d(df):>7}")

    row("CAGR",              "cagr",         ".2%")
    row("Ann Volatility",    "ann_vol",       ".2%")
    row("Sharpe  (RF=0%)",   "sharpe",        ".3f")
    row("Max Drawdown",      "max_dd",        ".2%")
    row("Calmar Ratio",      "calmar",        ".3f")
    row("Total Return",      "total_return",  ".2%")
    print(f"  {'Days simulated':<22}  {m_orig['n_days']:>13,}  {m_floor['n_days']:>17,}  {m_base['n_days']:>10,}")


def print_annual(ann: pd.DataFrame, title: str) -> None:
    print()
    print(f"  Year-by-Year  ({title})")
    print(f"  {'Year':<6}  {'Orig':>8}  {'Floored':>9}  {'Baseline':>10}  {'ΔOrig':>8}  {'ΔFloor':>8}")
    print(f"  {'-' * 56}")
    for yr, r in ann.iterrows():
        do = ("+" if r["delta_orig"]  > 0 else "") + f"{r['delta_orig']:7.2%}"
        df = ("+" if r["delta_floor"] > 0 else "") + f"{r['delta_floor']:7.2%}"
        print(f"  {yr:<6}  {r['orig']:>8.2%}  {r['floored']:>9.2%}  {r['baseline']:>10.2%}  {do:>8}  {df:>8}")


# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 80)
    print(f"  TACTICAL DEPLOYMENT BACKTEST  |  {date.today().isoformat()}")
    print("=" * 80)
    print()
    print(f"  Gold signal:    DFII10 < {DFII10_SMA_WINDOW}d SMA  (CvsTC negative)")
    print(f"  Orig tactical:  0% gold when OFF,   25% when ON")
    print(f"  Floored:        {GOLD_FLOOR:.1%} gold when OFF, {GOLD_W:.1%} when ON  (half-floor)")
    print(f"  Silver T1:      GSR > {GSR_T1} AND >= {GSR_FALL_PCT:.0%} below {GSR_PEAK_WINDOW}d peak")
    print(f"  Silver T2:      GSR > {GSR_T2} AND >= {GSR_FALL_PCT:.0%} below {GSR_PEAK_WINDOW}d peak")
    print(f"  Cash yield:     {CASH_DAILY*365:.1%}/yr (Spiltan) on uninvested gold + silver")
    print(f"  Baseline ref:   Sharpe 1.851 | CAGR 30.08% | Max DD -24.86%")
    print()

    # ── Fetch FRED ────────────────────────────────────────────
    print("Fetching DFII10 (FRED)...")
    dfii10 = fetch_fred("DFII10")
    if dfii10 is None:
        print("ERROR: DFII10 fetch failed"); sys.exit(1)
    print(f"  {dfii10.index[0].date()} to {dfii10.index[-1].date()}  ({len(dfii10)} obs)")
    time.sleep(0.5)

    # ── Fetch Yahoo Finance ───────────────────────────────────
    print("Fetching Yahoo Finance prices...")
    tickers = {
        "GC=F":  "Gold futures",
        "SI=F":  "Silver futures",
        "LLY":   "Eli Lilly",
        "WMT":   "Walmart",
        "JNJ":   "J&J",
        "CCJ":   "Cameco",
        "VRT":   "Vertiv",
        "AVGO":  "Broadcom",
    }
    prices_raw = {}
    for sym, name in tickers.items():
        print(f"  {name} ({sym})...")
        p = fetch_yahoo(sym)
        if p is None:
            print(f"  ERROR: {sym} fetch failed"); sys.exit(1)
        prices_raw[sym] = p
        print(f"    {p.index[0].date()} to {p.index[-1].date()}  ({len(p):,} days)")
        time.sleep(0.5)

    # ── Compute signals (full history) ────────────────────────
    print("\nComputing signals...")
    gs = compute_gold_signal(dfii10)
    t1, t2 = compute_silver_signals(prices_raw["GC=F"], prices_raw["SI=F"])

    print(f"  Gold signal ON:  {gs.mean()*100:.1f}% of all available days")
    print(f"  Silver T1 ON:    {t1.mean()*100:.1f}% of all available days")
    print(f"  Silver T2 ON:    {t2.mean()*100:.1f}% of all available days")

    def run_period(start: str, end: str, label: str) -> dict:
        print(f"\nRunning {label} ({start} to {end})...")
        prices_df = pd.DataFrame(prices_raw).sort_index().loc[start:end].ffill()

        nav_o, ret_o = simulate(prices_df, gs, t1, t2, mode="tactical")
        nav_f, ret_f = simulate(prices_df, gs, t1, t2, mode="tactical_floored")
        nav_b, ret_b = simulate(prices_df, gs, t1, t2, mode="baseline")

        idx = ret_o.index
        ss  = signal_stats(idx, gs, t1, t2)
        m_o = metrics(nav_o, ret_o, f"Tactical-Orig {label}")
        m_f = metrics(nav_f, ret_f, f"Tactical-Floor {label}")
        m_b = metrics(nav_b, ret_b, f"Baseline {label}")
        ann = annual_breakdown(nav_o, nav_f, nav_b)

        return {"orig": m_o, "floor": m_f, "base": m_b, "ss": ss, "ann": ann, "label": label}

    is_res  = run_period(IS_START,  IS_END,  "IS")
    oos_res = run_period(OOS_START, OOS_END, "OOS")

    # ── Print results ─────────────────────────────────────────
    print_comparison(
        f"IN-SAMPLE  {IS_START} → {IS_END}",
        is_res["orig"], is_res["floor"], is_res["base"], is_res["ss"],
    )
    print_annual(is_res["ann"], "IS")

    print_comparison(
        f"OUT-OF-SAMPLE  {OOS_START} → {OOS_END}",
        oos_res["orig"], oos_res["floor"], oos_res["base"], oos_res["ss"],
    )
    print_annual(oos_res["ann"], "OOS")

    print()
    print("=" * 80)
    print("  INTERPRETATION NOTES")
    print(f"  {'-' * 76}")
    print("  Sharpe with RF = 0% (consistent with baseline computation).")
    print("  Calmar = CAGR / |Max Drawdown|. Higher = better risk-adjusted.")
    print("  Orig: gold 0% (OFF) or 25% (ON).  Floor: gold 12.5% (OFF) or 25% (ON).")
    print("  Silver signal unchanged across all modes (0/5/10%).")
    print("  VRT pre-IPO (2020-02-07) → cash in all modes.")
    print("  No transaction costs or spread modelled.")
    print("  Signal lag: 1 business day to avoid look-ahead bias.")
    print("=" * 80)
    print()


if __name__ == "__main__":
    main()
