#!/usr/bin/env python3
"""
2025 Gold Signal Miss — Investigation
Answers:
  1. Signal ON vs OFF days in 2025
  2. Gold actual return in 2025
  3. Extended OFF periods while gold rallied
  4. Signal lag cost analysis
  5. ASCII chart: gold price vs 90d/60d signals
  6. 60d SMA vs 90d SMA comparison
"""
import os, sys, datetime, json, urllib.request, urllib.parse
import pandas as pd
import numpy as np

FRED_API_KEY    = os.environ.get("FRED_API_KEY", "")
SMA_WINDOWS     = [60, 90]
SIGNAL_LAG      = 1          # business days
INVESTIGATE_YEAR = 2025
FETCH_START_YEAR = 2023      # need lookback before 2025 for SMA warm-up


# ── data fetching ─────────────────────────────────────────────────────────────

def fetch_fred(series_id: str) -> pd.Series:
    start = f"{FETCH_START_YEAR}-01-01"
    url = (f"https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={series_id}&api_key={FRED_API_KEY}"
           f"&file_type=json&observation_start={start}")
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read())
    obs = data["observations"]
    s = pd.Series(
        {o["date"]: float(o["value"]) for o in obs if o["value"] != "."},
        name=series_id, dtype=float,
    )
    s.index = pd.to_datetime(s.index)
    return s


def fetch_yahoo(symbol: str) -> pd.Series:
    enc = urllib.parse.quote(symbol)
    p1  = int(datetime.datetime(FETCH_START_YEAR, 1, 1).timestamp())
    p2  = int(datetime.datetime.now().timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}"
           f"?interval=1d&period1={p1}&period2={p2}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    res  = data["chart"]["result"][0]
    ts   = res["timestamp"]
    cls  = res["indicators"]["adjclose"][0]["adjclose"]
    s = pd.Series(cls, index=pd.to_datetime(ts, unit="s").normalize(),
                  name=symbol, dtype=float).dropna()
    s = s[~s.index.duplicated(keep="last")]
    return s


# ── signal helpers ────────────────────────────────────────────────────────────

def build_signal(dfii10: pd.Series, window: int) -> pd.Series:
    sma = dfii10.rolling(window, min_periods=window).mean()
    raw = (dfii10 < sma).astype(int)
    return raw.shift(SIGNAL_LAG).fillna(0).astype(int).rename(f"sig{window}")


def year_mask(idx: pd.DatetimeIndex, year: int) -> pd.Series:
    return pd.Series(idx.year == year, index=idx)


# ── analysis helpers ─────────────────────────────────────────────────────────

def signal_runs(sig: pd.Series) -> dict:
    """Return lists of consecutive ON / OFF run lengths."""
    on_runs, off_runs = [], []
    cur_val = sig.iloc[0]
    cur_len = 1
    for v in sig.iloc[1:]:
        if v == cur_val:
            cur_len += 1
        else:
            (on_runs if cur_val else off_runs).append(cur_len)
            cur_val, cur_len = v, 1
    (on_runs if cur_val else off_runs).append(cur_len)
    return {"on": on_runs, "off": off_runs}


def off_period_rallies(gold: pd.Series, sig: pd.Series,
                       min_days: int = 5, min_gain_pct: float = 3.0) -> list:
    """Contiguous OFF windows where gold gained ≥ min_gain_pct%."""
    results = []
    off_start = None
    prev = 1  # treat pre-history as ON so first transition is detected cleanly

    for dt, s in sig.items():
        if s == 0 and prev == 1:
            off_start = dt
        elif s == 1 and prev == 0 and off_start is not None:
            window = gold[off_start:dt]
            if len(window) >= min_days:
                chg = (window.iloc[-1] / window.iloc[0] - 1) * 100
                if chg >= min_gain_pct:
                    results.append({"start": off_start, "end": dt,
                                    "days": len(window), "gold_chg": chg})
            off_start = None
        prev = s

    # still-open OFF window at series end
    if off_start is not None:
        window = gold[off_start:]
        if len(window) >= min_days:
            chg = (window.iloc[-1] / window.iloc[0] - 1) * 100
            if chg >= min_gain_pct:
                results.append({"start": off_start, "end": gold.index[-1],
                                 "days": len(window), "gold_chg": chg})

    return sorted(results, key=lambda x: x["gold_chg"], reverse=True)


def lag_cost(gold: pd.Series, sig: pd.Series, window_days: int = 3) -> dict:
    """
    At every OFF→ON transition, measure:
      pre  = gold return over the `window_days` BEFORE the transition (missed)
      post = gold return over the `window_days` AFTER  the transition (captured)
    """
    pre_rets, post_rets = [], []
    transitions = sig.index[(sig.diff() == 1)]
    gold_arr = gold.values
    gold_idx = gold.index

    for t in transitions:
        pos = gold_idx.searchsorted(t)
        if pos < window_days or pos + window_days >= len(gold_arr):
            continue
        pre  = (gold_arr[pos]            / gold_arr[pos - window_days] - 1) * 100
        post = (gold_arr[pos + window_days] / gold_arr[pos]            - 1) * 100
        pre_rets.append(pre)
        post_rets.append(post)

    return {
        "n": len(pre_rets),
        "avg_pre":  round(float(np.mean(pre_rets)),  2) if pre_rets  else float("nan"),
        "avg_post": round(float(np.mean(post_rets)), 2) if post_rets else float("nan"),
        "med_pre":  round(float(np.median(pre_rets)), 2) if pre_rets else float("nan"),
        "med_post": round(float(np.median(post_rets)),2) if post_rets else float("nan"),
    }


# ── ASCII chart ───────────────────────────────────────────────────────────────

def ascii_chart(gold_norm: pd.Series, sig90: pd.Series, sig60: pd.Series,
                year: int, width: int = 70) -> str:
    mask = gold_norm.index.year == year
    g   = gold_norm[mask].dropna()
    s90 = sig90[mask].reindex(g.index, method="ffill").fillna(0)
    s60 = sig60[mask].reindex(g.index, method="ffill").fillna(0)

    n    = len(g)
    step = max(1, n // width)
    idx  = list(range(0, n, step))[:width]
    g_s  = g.iloc[idx]
    s90_s = s90.iloc[idx]
    s60_s = s60.iloc[idx]
    cols  = len(g_s)

    # price chart
    HEIGHT   = 14
    g_min, g_max = float(g_s.min()), float(g_s.max())
    g_range  = g_max - g_min or 1.0
    grid     = [[" "] * cols for _ in range(HEIGHT)]
    for j, v in enumerate(g_s):
        row = HEIGHT - 1 - int((v - g_min) / g_range * (HEIGHT - 1))
        row = max(0, min(HEIGHT - 1, row))
        grid[row][j] = "●"

    lines = [f"\n  Gold (Jan {year} = 100)"]
    lines.append(f"  {'─' * (cols + 9)}")
    for i, row in enumerate(grid):
        pct = g_max - i * g_range / (HEIGHT - 1)
        lines.append(f"  {pct:6.0f} │ {''.join(row)}")
    lines.append(f"         └{'─' * cols}")

    # month labels
    month_row = [" "] * cols
    prev_m    = None
    for j, dt in enumerate(g_s.index):
        if dt.month != prev_m:
            label = dt.strftime("%b")
            for k, c in enumerate(label):
                if j + k < cols:
                    month_row[j + k] = c
            prev_m = dt.month
    lines.append(f"           {''.join(month_row)}")

    lines.append("")
    lines.append(f"  90d sig  │ {''.join('▓' if v else '·' for v in s90_s)}")
    lines.append(f"  60d sig  │ {''.join('▓' if v else '·' for v in s60_s)}")
    lines.append(f"           ▓=deployed  ·=cash")

    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    SEP = "=" * 74
    DIV = "-" * 60
    Y   = INVESTIGATE_YEAR

    print(SEP)
    print(f"  2025 GOLD SIGNAL MISS — INVESTIGATION  |  {datetime.date.today()}")
    print(SEP)

    # fetch
    print(f"\nFetching DFII10 (FRED, from {FETCH_START_YEAR})...")
    dfii10 = fetch_fred("DFII10")
    dfii10 = dfii10.reindex(
        pd.bdate_range(dfii10.index.min(), dfii10.index.max())
    ).ffill()
    print(f"  {dfii10.index[0].date()} → {dfii10.index[-1].date()}  ({len(dfii10)} obs)")

    print(f"Fetching GC=F (Yahoo, from {FETCH_START_YEAR})...")
    gold = fetch_yahoo("GC=F")
    gold = gold.reindex(
        pd.bdate_range(gold.index.min(), gold.index.max())
    ).ffill()
    print(f"  {gold.index[0].date()} → {gold.index[-1].date()}  ({len(gold)} days)")

    # align
    common = dfii10.index.intersection(gold.index)
    dfii10 = dfii10.reindex(common)
    gold   = gold.reindex(common)

    # signals
    sig = {w: build_signal(dfii10, w) for w in SMA_WINDOWS}
    s90, s60 = sig[90], sig[60]

    # year slices
    m25 = gold.index.year == Y
    gold_25  = gold[m25]
    dfii10_25 = dfii10[m25]
    s90_25   = s90[m25]
    s60_25   = s60[m25]
    n25      = len(s90_25)

    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"  1. SIGNAL STATUS IN {Y}  (90d SMA)")
    print(f"  {DIV}")

    n_on  = int(s90_25.sum())
    n_off = n25 - n_on
    print(f"  Trading days:        {n25}")
    print(f"  Signal ON  (deploy): {n_on:>4}  ({100*n_on/n25:.1f}%)")
    print(f"  Signal OFF (cash):   {n_off:>4}  ({100*n_off/n25:.1f}%)")

    runs = signal_runs(s90_25)
    if runs["on"]:
        print(f"\n  ON  runs : n={len(runs['on'])}, "
              f"avg={np.mean(runs['on']):.0f}d, "
              f"max={max(runs['on'])}d, "
              f"min={min(runs['on'])}d")
    if runs["off"]:
        print(f"  OFF runs : n={len(runs['off'])}, "
              f"avg={np.mean(runs['off']):.0f}d, "
              f"max={max(runs['off'])}d, "
              f"min={min(runs['off'])}d")

    print(f"\n  Monthly breakdown — {Y}:")
    print(f"  {'Mo':<5} {'Days':>5} {'ON':>5} {'OFF':>5} {'%ON':>7} {'Gold ret':>10} {'DFII10 avg':>12}")
    print(f"  {'-'*50}")
    for mo in range(1, 13):
        mm = (gold.index.year == Y) & (gold.index.month == mo)
        if mm.sum() < 2:
            continue
        td   = int(mm.sum())
        on   = int(s90[mm].sum())
        gret = (gold[mm].iloc[-1] / gold[mm].iloc[0] - 1) * 100
        davg = dfii10[mm].mean()
        mname = datetime.date(Y, mo, 1).strftime("%b")
        print(f"  {mname:<5} {td:>5} {on:>5} {td-on:>5} {100*on/td:>6.0f}% "
              f"{gret:>9.1f}% {davg:>11.2f}%")

    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"  2. GOLD ACTUAL RETURN IN {Y}")
    print(f"  {DIV}")

    g_start = float(gold_25.iloc[0])
    g_end   = float(gold_25.iloc[-1])
    g_ret   = (g_end / g_start - 1) * 100
    g_peak  = float(gold_25.max())
    g_trough = float(gold_25.min())
    print(f"  Jan 2025 open:   {g_start:>8.2f}")
    print(f"  Year-end:        {g_end:>8.2f}")
    print(f"  Peak intra-year: {g_peak:>8.2f}")
    print(f"  Trough intra-yr: {g_trough:>8.2f}")
    print(f"  Full-year return:{g_ret:>8.1f}%")

    print(f"\n  Quarterly:")
    for qs, qe, qn in [(1,3,"Q1"),(4,6,"Q2"),(7,9,"Q3"),(10,12,"Q4")]:
        qm = (gold.index.year == Y) & gold.index.month.isin(range(qs, qe+1))
        if qm.sum() < 2:
            continue
        qr  = (gold[qm].iloc[-1] / gold[qm].iloc[0] - 1) * 100
        qon = 100 * s90[qm].mean()
        print(f"    {qn}: gold {qr:+6.1f}%  |  signal ON {qon:.0f}% of days")

    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"  3. EXTENDED OFF PERIODS WHERE GOLD RALLIED  (≥5 days, ≥3% gain)")
    print(f"  {DIV}")

    # restrict to Y onwards
    m_y = gold.index.year >= Y
    rallies = off_period_rallies(gold[m_y], s90[m_y], min_days=5, min_gain_pct=3.0)

    if rallies:
        print(f"  {'Start':<12} {'End':<12} {'Days':>6} {'Gold gain':>11}")
        print(f"  {'-'*44}")
        for r in rallies:
            print(f"  {str(r['start'].date()):<12} {str(r['end'].date()):<12} "
                  f"{r['days']:>6}  {r['gold_chg']:>9.1f}%")
    else:
        print("  No qualifying OFF-and-rallying periods found.")

    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"  4. SIGNAL LAG COST ANALYSIS  (1-day lag, 3-day window)")
    print(f"  {DIV}")

    lc_25  = lag_cost(gold[m_y], s90[m_y],  window_days=3)
    lc_all = lag_cost(gold,      s90,        window_days=3)

    for label, lc in [(f"{Y}+", lc_25), ("Full dataset", lc_all)]:
        print(f"  [{label}]  n transitions = {lc['n']}")
        if lc["n"] > 0:
            print(f"    Avg gold return 3d BEFORE transition (missed): "
                  f"{lc['avg_pre']:+.2f}%  (median {lc['med_pre']:+.2f}%)")
            print(f"    Avg gold return 3d AFTER  transition (captured): "
                  f"{lc['avg_post']:+.2f}%  (median {lc['med_post']:+.2f}%)")
            note = ("LAG IS COSTLY" if lc["avg_pre"] > 1.0
                    else "lag not significant")
            print(f"    → {note}")
        print()

    # ─────────────────────────────────────────────────────────────────────────
    print(f"{SEP}")
    print(f"  5. ASCII CHART: GOLD PRICE vs 90d / 60d SIGNAL  ({Y})")
    print(f"  {DIV}")

    gold_norm = gold / float(gold[m25].iloc[0]) * 100
    print(ascii_chart(gold_norm, s90, s60, year=Y, width=70))

    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"  6. 60d vs 90d SMA COMPARISON  ({Y})")
    print(f"  {DIV}")

    n_on_90 = int(s90_25.sum())
    n_on_60 = int(s60_25.sum())
    extra_on  = int(((s60_25 == 1) & (s90_25 == 0)).sum())
    extra_off = int(((s60_25 == 0) & (s90_25 == 1)).sum())

    print(f"  90d SMA: ON {n_on_90}/{n25} days ({100*n_on_90/n25:.1f}%)")
    print(f"  60d SMA: ON {n_on_60}/{n25} days ({100*n_on_60/n25:.1f}%)")
    print(f"  60d ON but 90d OFF (extra deployed by 60d):  {extra_on} days")
    print(f"  60d OFF but 90d ON (fewer deployed by 60d):  {extra_off} days")

    extra_mask = m25 & (s60 == 1) & (s90 == 0)
    if extra_mask.sum() > 0:
        daily_ret  = gold.pct_change()
        cum_gain   = ((1 + daily_ret[extra_mask]).prod() - 1) * 100
        avg_daily  = float(daily_ret[extra_mask].mean()) * 100
        print(f"\n  Gold return on those {extra_mask.sum()} 'extra 60d' days:")
        print(f"    Cumulative gain: {cum_gain:+.2f}%")
        print(f"    Avg daily:       {avg_daily:+.3f}%")
        print(f"    (Positive = 60d would have captured more gold upside)")

    print(f"\n  Monthly {Y} — signal ON% by window + gold return:")
    print(f"  {'Mo':<5} {'90d ON%':>9} {'60d ON%':>9} {'Δ':>5} {'Gold ret':>10}")
    print(f"  {'-'*42}")
    for mo in range(1, 13):
        mm = (gold.index.year == Y) & (gold.index.month == mo)
        if mm.sum() < 2:
            continue
        p90  = 100 * s90[mm].mean()
        p60  = 100 * s60[mm].mean()
        gret = (gold[mm].iloc[-1] / gold[mm].iloc[0] - 1) * 100
        mname = datetime.date(Y, mo, 1).strftime("%b")
        print(f"  {mname:<5} {p90:>8.0f}% {p60:>8.0f}% {p60-p90:>+4.0f}% {gret:>9.1f}%")

    print(f"\n{SEP}")
    print("  END OF INVESTIGATION")
    print(SEP)


if __name__ == "__main__":
    main()
