#!/usr/bin/env python3
"""
research/gold_ry_exit.py

Tests gold exit signals triggered by DFII10 (10-yr TIPS real yield) expansion.

3-state gold allocation:
  Compress  (RY < SMA90 OR RY < RY[-10d])          -> 25%
  Neutral   (neither signal active)                 -> 15%
  Expand    (configurable trigger)                  -> exit floor

4 configs:
  Base      : no exit — current behaviour, 15% floor always
  Soft-5    : exit when RY > SMA90                       -> 5%
  Strict-5  : exit when RY > SMA90 AND RY > RY[-10d]    -> 5%
  Strict-0  : exit when RY > SMA90 AND RY > RY[-10d]    -> 0%

All signals shifted 1 day forward to prevent look-ahead.
LLY / JNJ fixed at current live weights (15% / 6%).
IS:  2016-04-01 -> 2026-03-31
OOS: 2009-01-01 -> 2016-03-31
"""

import os, sys, time
import requests
import numpy  as np
import pandas as pd
from datetime import date

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

IS_START  = "2016-04-01";  IS_END  = "2026-03-31"
OOS_START = "2009-01-01";  OOS_END = "2016-03-31"

DFII10_SMA_WINDOW = 90
COMPRESS_WINDOW   = 10
GOLD_NEUTRAL      = 0.15
GOLD_MAX          = 0.25

GSR_T1, GSR_T2  = 83.36, 86.45
GSR_PEAK_WINDOW = 60
GSR_FALL_PCT    = 0.05

SILVER_MAX = 0.10
VRT_IPO    = pd.Timestamp("2020-02-07")
CASH_DAILY = 0.03 / 252

LLY_W    = 0.15
JNJ_W    = 0.06
SHARED_W = {"WMT": 0.15, "CCJ": 0.10, "VRT": 0.10, "AVGO": 0.09}
AVAIL_FROM = {"VRT": VRT_IPO}

# (exit_definition, exit_floor)
# exit_definition: "none" | "soft" (>SMA90 only) | "strict" (>SMA90 AND rising 10d)
CONFIGS = {
    "Base     (no exit, 15% floor)": ("none",   0.15),
    "Soft-5   (>SMA90 -> 5%)":       ("soft",   0.05),
    "Strict-5 (>SMA+10d -> 5%)":     ("strict", 0.05),
    "Strict-0 (>SMA+10d -> 0%)":     ("strict", 0.00),
}


# ── fetch ─────────────────────────────────────────────────────────────────────

def fetch_fred(series_id):
    if FRED_API_KEY:
        url = (f"https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&sort_order=asc")
    else:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "gold-ry-exit/1.0"}, timeout=30)
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
                        try: rows[parts[0].strip()] = float(parts[1].strip())
                        except ValueError: pass
            if not rows: return None
            s = pd.Series(rows, dtype=float)
            s.index = pd.to_datetime(s.index)
            return s.sort_index()
        except Exception as e:
            print(f"  FRED {series_id} attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None


def fetch_yahoo(symbol):
    """Fetch dividend+split-adjusted prices; falls back to close for futures."""
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
            res = r.json()["chart"]["result"][0]
            ts  = pd.to_datetime(res["timestamp"], unit="s").normalize()
            adj_block = res["indicators"].get("adjclose", [{}])
            if adj_block and adj_block[0].get("adjclose"):
                closes = adj_block[0]["adjclose"]
            else:
                closes = res["indicators"]["quote"][0]["close"]
            s = pd.Series(closes, index=ts, dtype=float)
            return s.dropna().sort_index()
        except Exception as e:
            print(f"  Yahoo {symbol} attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None


# ── data quality audit ────────────────────────────────────────────────────────

def check_data_quality(prices_df, symbols):
    print("\nDATA QUALITY AUDIT")
    print("-" * 58)
    STALE_RUN = 5
    COV_FLOOR = 0.90
    issues = []

    for sym in symbols:
        if sym not in prices_df.columns:
            print(f"  {sym:<8} MISSING entirely — CRITICAL")
            issues.append(f"{sym}: missing")
            continue

        for label, start, end in [("IS ", IS_START, IS_END),
                                   ("OOS", OOS_START, OOS_END)]:
            eff_start = max(pd.Timestamp(start), AVAIL_FROM.get(sym, pd.Timestamp(start)))
            eff_end   = pd.Timestamp(end)

            if eff_start > eff_end:
                print(f"  {sym:<8} {label}: n/a (pre-listing)  ok")
                continue

            window = prices_df.loc[eff_start:eff_end, sym]
            total  = len(window)
            filled = int(window.notna().sum())
            pct    = filled / total if total > 0 else 0

            cov_tag = "ok" if pct >= COV_FLOOR else "WARNING"
            if pct < COV_FLOOR:
                issues.append(f"{sym}/{label.strip()}: {pct:.0%} coverage")

            s = window.dropna()
            max_stale = 0
            if len(s) > 1:
                run = 0
                for v in (s.diff() == 0):
                    run = run + 1 if v else 0
                    max_stale = max(max_stale, run)

            stale_tag = f"stale_max={max_stale}"
            if max_stale > STALE_RUN:
                stale_tag += " WARNING"
                issues.append(f"{sym}/{label.strip()}: {max_stale} consecutive stale prices")

            print(f"  {sym:<8} {label}: {pct:5.1%} coverage ({filled}/{total})  "
                  f"{cov_tag:<8}  {stale_tag}")

    print()
    if not issues:
        print("  All checks passed — data clean.")
    else:
        print(f"  {len(issues)} issue(s) flagged:")
        for issue in issues:
            print(f"    ! {issue}")
    return issues


# ── signals ───────────────────────────────────────────────────────────────────

def build_gold_signals(dfii10):
    """
    Returns (compress, expand_soft, expand_strict), all shifted 1 day.

    compress:      (RY < SMA90) OR (RY < RY[-10d])    — entry
    expand_soft:   RY > SMA90                          — structural exit only
    expand_strict: (RY > SMA90) AND (RY > RY[-10d])   — structural + momentum exit

    compress and expand_strict are mutually exclusive by construction.
    compress takes priority over expand_soft in simulate().
    """
    sma        = dfii10.rolling(DFII10_SMA_WINDOW, min_periods=DFII10_SMA_WINDOW).mean()
    below_sma  = dfii10 < sma
    above_sma  = dfii10 > sma
    ry_falling = dfii10 < dfii10.shift(COMPRESS_WINDOW)
    ry_rising  = dfii10 > dfii10.shift(COMPRESS_WINDOW)

    compress      = (below_sma | ry_falling).astype(int).shift(1).fillna(0)
    expand_soft   = above_sma.astype(int).shift(1).fillna(0)
    expand_strict = (above_sma & ry_rising).astype(int).shift(1).fillna(0)

    return compress, expand_soft, expand_strict


def signal_state_pct(compress, expand, index):
    """Returns (pct_compress, pct_neutral, pct_expand) over index."""
    c = compress.reindex(index, method="ffill").fillna(0)
    e = expand.reindex(index, method="ffill").fillna(0)
    # compress takes priority
    in_compress = c.astype(bool)
    in_expand   = (~in_compress) & e.astype(bool)
    in_neutral  = ~(in_compress | in_expand)
    n = len(index)
    return float(in_compress.sum()/n), float(in_neutral.sum()/n), float(in_expand.sum()/n)


def compute_silver_signals(gp, sp):
    gsr  = (gp / sp).rename("gsr")
    peak = gsr.rolling(GSR_PEAK_WINDOW, min_periods=GSR_PEAK_WINDOW).max()
    fall = ((gsr - peak) / peak) <= -GSR_FALL_PCT
    t1   = ((gsr > GSR_T1) & fall).astype(int).shift(1).rename("t1")
    t2   = ((gsr > GSR_T2) & fall).astype(int).shift(1).rename("t2")
    return t1, t2


# ── simulation ────────────────────────────────────────────────────────────────

def simulate(prices_df, compress, expand_soft, expand_strict, t1, t2,
             exit_def, exit_floor):
    ret = prices_df.pct_change().iloc[1:]
    idx = ret.index

    def ar(sym):
        return ret[sym].fillna(0.0) if sym in ret.columns else pd.Series(0.0, index=idx)

    compress_ = compress.reindex(idx, method="ffill").fillna(0.0)
    t1_       = t1.reindex(idx, method="ffill").fillna(0.0)
    t2_       = t2.reindex(idx, method="ffill").fillna(0.0)

    if exit_def == "soft":
        expand_ = expand_soft.reindex(idx, method="ffill").fillna(0.0)
    elif exit_def == "strict":
        expand_ = expand_strict.reindex(idx, method="ffill").fillna(0.0)
    else:
        expand_ = pd.Series(0.0, index=idx)

    # 3-state: compress takes priority over expand
    gold_w = pd.Series(
        np.where(compress_.astype(bool), GOLD_MAX,
        np.where(expand_.astype(bool),   exit_floor,
                 GOLD_NEUTRAL)),
        index=idx
    )
    silver_w = 0.05 * t1_ + 0.05 * t2_
    vrt_w    = pd.Series(np.where(idx >= VRT_IPO, SHARED_W["VRT"], 0.0), index=idx)
    cash_w   = (GOLD_MAX - gold_w) + (SILVER_MAX - silver_w) + (SHARED_W["VRT"] - vrt_w)

    w_total = (
        gold_w + silver_w + vrt_w + cash_w +
        SHARED_W["WMT"] + SHARED_W["CCJ"] + SHARED_W["AVGO"] + LLY_W + JNJ_W
    )
    max_dev = float((w_total - 1.0).abs().max())
    assert max_dev < 1e-9, f"Weight sum deviation {max_dev:.2e} exceeds tolerance"

    port_ret = (
        gold_w           * ar("GC=F")  +
        silver_w         * ar("SI=F")  +
        LLY_W            * ar("LLY")   +
        SHARED_W["WMT"]  * ar("WMT")   +
        JNJ_W            * ar("JNJ")   +
        SHARED_W["CCJ"]  * ar("CCJ")   +
        SHARED_W["AVGO"] * ar("AVGO")  +
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
            "max_dd": max_dd, "calmar": calmar, "terminal": nav.iloc[-1]}


def year_by_year(nav):
    annual = {}
    for yr in range(nav.index[0].year, nav.index[-1].year + 1):
        yr_nav = nav[nav.index.year == yr]
        if len(yr_nav) < 2:
            continue
        annual[yr] = yr_nav.iloc[-1] / yr_nav.iloc[0] - 1
    return annual


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    SEP = "=" * 76
    print(SEP)
    print(f"  GOLD RY EXIT SIGNAL TEST  |  {date.today()}")
    print(f"  Entry: RY < SMA90 OR RY < RY[-10d]  ->  25%")
    print(f"  Neutral: neither signal              ->  15%")
    print(f"  Exit:  configurable (see configs)    ->  exit floor")
    print(f"  IS:  {IS_START} -> {IS_END}   |   OOS: {OOS_START} -> {OOS_END}")
    print(f"  LLY={LLY_W:.0%} / JNJ={JNJ_W:.0%} fixed (current portfolio)")
    print(SEP)

    print("\nFetching DFII10...")
    dfii10 = fetch_fred("DFII10")
    if dfii10 is None:
        print("ERROR: could not fetch DFII10"); sys.exit(1)
    print(f"  {dfii10.index[0].date()} -> {dfii10.index[-1].date()}")
    time.sleep(0.3)

    tickers = {"GC=F": "Gold", "SI=F": "Silver", "LLY": "LLY",
               "WMT": "WMT",  "JNJ": "JNJ",   "CCJ": "CCJ",
               "VRT": "VRT",  "AVGO": "AVGO"}
    print("Fetching prices (dividend-adjusted)...")
    prices_raw = {}
    for sym, name in tickers.items():
        print(f"  {name}...")
        s = fetch_yahoo(sym)
        if s is not None:
            prices_raw[sym] = s
        time.sleep(0.2)

    prices_df = pd.DataFrame(prices_raw).sort_index()

    issues = check_data_quality(prices_df, list(tickers.keys()))
    critical = [i for i in issues if "missing" in i or "coverage" in i]
    if critical:
        print(f"\nAborting — critical data issues: {critical}")
        sys.exit(1)

    compress, expand_soft, expand_strict = build_gold_signals(dfii10)
    t1, t2 = compute_silver_signals(prices_raw["GC=F"], prices_raw["SI=F"])

    # Signal state diagnostics
    p_is_idx  = prices_df.loc[IS_START:IS_END].index
    p_oos_idx = prices_df.loc[OOS_START:OOS_END].index

    print(f"\n{SEP}")
    print("  GOLD SIGNAL STATE DISTRIBUTION")
    print(f"  {'Period':<8}  {'Compress':>10}  {'Neutral':>10}  {'Exp-Soft':>10}  {'Exp-Strict':>11}")
    print(f"  {'-'*58}")
    for label, idx in [("IS", p_is_idx), ("OOS", p_oos_idx)]:
        c_s,  n_s,  e_s  = signal_state_pct(compress, expand_soft,   idx)
        c_st, n_st, e_st = signal_state_pct(compress, expand_strict, idx)
        print(f"  {label:<8}  {c_s:>9.1%}   {n_s:>9.1%}   {e_s:>9.1%}   {e_st:>10.1%}")

    results_is  = {}
    results_oos = {}
    navs_is     = {}

    for label, (exit_def, exit_floor) in CONFIGS.items():
        p_is = prices_df.loc[IS_START:IS_END]
        nav_is, ret_is = simulate(p_is, compress, expand_soft, expand_strict,
                                  t1, t2, exit_def, exit_floor)
        results_is[label]  = metrics(nav_is, ret_is)
        navs_is[label]     = nav_is

        p_oos = prices_df.loc[OOS_START:OOS_END]
        nav_oos, ret_oos = simulate(p_oos, compress, expand_soft, expand_strict,
                                    t1, t2, exit_def, exit_floor)
        results_oos[label] = metrics(nav_oos, ret_oos)

    # ── IS results ────────────────────────────────────────────────────────────
    W = 34
    print(f"\n{SEP}")
    print("  IN-SAMPLE RESULTS  (2016-04-01 -> 2026-03-31)")
    print(f"  {'Config':<{W}}  {'Sharpe':>7}  {'Calmar':>7}  {'CAGR':>8}  {'MaxDD':>8}  {'Terminal':>9}")
    print(f"  {'-'*76}")
    for label, m in results_is.items():
        print(f"  {label:<{W}}  {m['sharpe']:7.3f}  {m['calmar']:7.3f}  "
              f"{m['cagr']:7.2%}  {m['max_dd']:7.2%}  {m['terminal']:9.4f}x")

    base_is = results_is[list(CONFIGS.keys())[0]]
    print(f"\n  DELTAS vs Base (IS)")
    print(f"  {'Config':<{W}}  {'DSharpe':>8}  {'DCalmar':>8}  {'DCAGR':>8}  {'DMaxDD':>8}  {'DTerminal':>10}")
    print(f"  {'-'*76}")
    for label, m in list(results_is.items())[1:]:
        print(f"  {label:<{W}}  "
              f"{m['sharpe']-base_is['sharpe']:+8.3f}  "
              f"{m['calmar']-base_is['calmar']:+8.3f}  "
              f"{m['cagr']-base_is['cagr']:+8.2%}  "
              f"{m['max_dd']-base_is['max_dd']:+8.2%}  "
              f"{m['terminal']-base_is['terminal']:+10.4f}x")

    # ── OOS results ───────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  OUT-OF-SAMPLE RESULTS  (2009-01-01 -> 2016-03-31)")
    print(f"  {'Config':<{W}}  {'Sharpe':>7}  {'Calmar':>7}  {'CAGR':>8}  {'MaxDD':>8}  {'Terminal':>9}")
    print(f"  {'-'*76}")
    for label, m in results_oos.items():
        print(f"  {label:<{W}}  {m['sharpe']:7.3f}  {m['calmar']:7.3f}  "
              f"{m['cagr']:7.2%}  {m['max_dd']:7.2%}  {m['terminal']:9.4f}x")

    base_oos = results_oos[list(CONFIGS.keys())[0]]
    print(f"\n  DELTAS vs Base (OOS)")
    print(f"  {'Config':<{W}}  {'DSharpe':>8}  {'DCalmar':>8}  {'DCAGR':>8}  {'DMaxDD':>8}  {'DTerminal':>10}")
    print(f"  {'-'*76}")
    for label, m in list(results_oos.items())[1:]:
        print(f"  {label:<{W}}  "
              f"{m['sharpe']-base_oos['sharpe']:+8.3f}  "
              f"{m['calmar']-base_oos['calmar']:+8.3f}  "
              f"{m['cagr']-base_oos['cagr']:+8.2%}  "
              f"{m['max_dd']-base_oos['max_dd']:+8.2%}  "
              f"{m['terminal']-base_oos['terminal']:+10.4f}x")

    # ── year-by-year IS ───────────────────────────────────────────────────────
    labels  = list(CONFIGS.keys())
    yby     = {lbl: year_by_year(navs_is[lbl]) for lbl in labels}
    all_yrs = sorted(set().union(*[set(y.keys()) for y in yby.values()]))

    print(f"\n{SEP}")
    print("  YEAR-BY-YEAR IS — gold exit signal impact")
    print(f"  {'Year':>6}  {'Base':>9}  {'Soft-5':>9}  {'Strict-5':>9}  {'Strict-0':>9}  {'S0-Base':>9}")
    print(f"  {'-'*66}")
    for yr in all_yrs:
        vals = [yby[lbl].get(yr, float("nan")) for lbl in labels]
        b, s = vals[0], vals[-1]
        d = s - b if not (pd.isna(b) or pd.isna(s)) else float("nan")
        row = f"  {yr:>6}"
        for v in vals:
            row += f"  {v:8.2%}" if not pd.isna(v) else f"  {'n/a':>8}"
        row += f"  {d:+8.2%}" if not pd.isna(d) else f"  {'n/a':>8}"
        print(row)

    print(f"\n{SEP}")


if __name__ == "__main__":
    main()
