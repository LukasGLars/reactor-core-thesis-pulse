#!/usr/bin/env python3
"""
backfill_logs.py — one-time historical population of macro_log.csv and asset_log.csv.

Run from repo root:
    FRED_API_KEY=<key> python backfill_logs.py

Covers:
    macro_log  from 2003-01-01 (DFII10 data origin)
    asset_log  from 2009-01-01 (OOS + in-sample periods)

Safe to re-run: skips dates already present in either file.
After this script, thesis_pulse.py daily runs append new rows.
"""

import os, sys, csv, time, requests
from datetime import date as _date, datetime, timedelta

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
_dir         = os.path.dirname(os.path.abspath(__file__))

MACRO_START     = "2003-01-01"
ASSET_START     = "2009-01-01"
END_DATE        = _date.today().isoformat()   # exclusive — today handled by live script
OIL_PATCH_START = "2020-01-01"
_MONTH_CODES    = "FGHJKMNQUVXZ"

V3_WEIGHTS = {
    "gold": 0.25, "silver": 0.10, "lly": 0.15, "wmt": 0.15,
    "vrt":  0.10, "ccj":   0.10, "avgo": 0.09, "jnj": 0.06,
}

# Must exactly match thesis_pulse.py
MACRO_HEADER = [
    "date", "dxy", "real_yield_10y", "t10y2y", "t10y3m",
    "core_pce_yoy_pct", "icsa_claims", "fed_funds_pct", "vix",
    "sp500", "credit_spread_pct", "indpro", "manemp_k",
    "oil_spread", "gs_ratio", "recession_count",
]
ASSET_HEADER = [
    "date",
    "gold_usd", "silver_usd", "lly_usd", "wmt_usd",
    "vrt_usd",  "ccj_usd",   "avgo_usd", "jnj_usd",
    "usdsek", "gold_sek", "silver_sek",
    "gold_ret", "silver_ret", "lly_ret", "wmt_ret",
    "vrt_ret",  "ccj_ret",   "avgo_ret", "jnj_ret",
    "portfolio_ret",
]

# ── HELPERS ─────────────────────────────────────────────────
def _safe(val, decimals=4):
    if val is None:
        return ""
    if isinstance(val, int):
        return str(val)
    return f"{val:.{decimals}f}"

def date_range(start, end):
    """Mon-Fri date strings from start (inclusive) to end (exclusive)."""
    out, d = [], datetime.fromisoformat(start).date()
    e = datetime.fromisoformat(end).date()
    while d < e:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out

def ffill(series, dates):
    """Forward-fill {date_str: float} to the given sorted date list."""
    out, last = {}, None
    for d in dates:
        if d in series:
            last = series[d]
        if last is not None:
            out[d] = last
    return out

def load_existing_dates(path):
    """Return set of date strings already in a CSV file."""
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)           # skip header
        return {row[0] for row in r if row}

def write_csv(path, header, rows):
    """Write rows to CSV, creating file with header if needed."""
    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(header)
        w.writerows(rows)
    print(f"  wrote {len(rows)} rows -> {os.path.basename(path)}")

# ── DATA FETCHERS ────────────────────────────────────────────
def yahoo_full(symbol, start_date):
    """Return {date_str: price} daily closes from Yahoo Finance."""
    start_unix = int(datetime.fromisoformat(start_date).timestamp())
    end_unix   = int(datetime.now().timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval=1d&period1={start_unix}&period2={end_unix}")
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            if r.status_code != 200:
                print(f"  {symbol}: HTTP {r.status_code}")
                return {}
            result_data = r.json()["chart"]["result"][0]
            timestamps  = result_data["timestamp"]
            closes      = result_data["indicators"]["quote"][0]["close"]
            out = {}
            for ts, c in zip(timestamps, closes):
                if c is not None:
                    out[datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")] = c
            print(f"  Yahoo {symbol}: {len(out)} days")
            return out
        except Exception as e:
            wait = 2 ** attempt
            print(f"  {symbol} attempt {attempt+1}: {e} -- retry in {wait}s")
            time.sleep(wait)
    return {}

def fred_full(series_id):
    """Return {date_str: float} full observation history from FRED."""
    if not FRED_API_KEY:
        return {}
    url = (f"https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={series_id}&api_key={FRED_API_KEY}"
           f"&file_type=json&sort_order=asc")
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "thesis-pulse/1.0"}, timeout=60)
            r.raise_for_status()
            obs = r.json().get("observations", [])
            out = {o["date"]: float(o["value"])
                   for o in obs if o.get("value") not in (".", "")}
            print(f"  FRED {series_id}: {len(out)} obs")
            time.sleep(0.25)    # respect rate limit
            return out
        except Exception as e:
            wait = 2 ** attempt
            print(f"  FRED {series_id} attempt {attempt+1}: {e} -- retry in {wait}s")
            time.sleep(wait)
    return {}

# ── MONTHLY SIGNAL HELPERS ───────────────────────────────────
def _three_mo_decline(monthly_sorted):
    """
    For each month, return 1 if the last 3 month-over-month changes are all negative
    (i.e., 4 consecutive values are each lower than the prior), else 0.
    Input: [(date_str, value), ...] sorted ascending.
    Returns: {date_str: 0|1}
    """
    out = {}
    vals = [v for _, v in monthly_sorted]
    dates = [d for d, _ in monthly_sorted]
    for i in range(len(dates)):
        if i >= 3:
            sig = int(vals[i] < vals[i-1] < vals[i-2] < vals[i-3])
        else:
            sig = 0
        out[dates[i]] = sig
    return out

def _pcepilfe_yoy(monthly_sorted, threshold=2.0):
    """Return {date_str: 0|1} where 1 = YoY core PCE > threshold %."""
    out = {}
    vals  = [v for _, v in monthly_sorted]
    dates = [d for d, _ in monthly_sorted]
    for i in range(len(dates)):
        if i >= 12:
            yoy = (vals[i] - vals[i-12]) / vals[i-12] * 100 if vals[i-12] else 0
            out[dates[i]] = 1 if yoy > threshold else 0
        else:
            out[dates[i]] = 0
    return out

def _sp500_vs_ma10(monthly_sorted):
    """Return {date_str: 0|1} where 1 = SP500 below 10-month MA."""
    out = {}
    vals  = [v for _, v in monthly_sorted]
    dates = [d for d, _ in monthly_sorted]
    for i in range(len(dates)):
        if i >= 9:
            ma10 = sum(vals[i-9:i+1]) / 10
            out[dates[i]] = 1 if vals[i] < ma10 else 0
        else:
            out[dates[i]] = 0
    return out

def monthly_sorted(series_dict):
    """Sort a {date_str: float} dict by date and return as [(date, val), ...]."""
    return sorted(series_dict.items())

# ── RECESSION COUNT ──────────────────────────────────────────
def build_recession_count(daily_dates, daily_series, monthly_signals):
    """
    Compute recession composite count for each date in daily_dates.
    Replicates compute_recession_signals() from thesis_pulse.py (11 indicators;
    UMCSENT and CAPE permanently excluded -- empirically confirmed high FP rate).
    Returns {date_str: int}.
    """
    # Forward-fill daily FRED series to the date index
    t10y3m = ffill(daily_series["T10Y3M"], daily_dates)
    t10y2y = ffill(daily_series["T10Y2Y"], daily_dates)
    dfii10 = ffill(daily_series["DFII10"], daily_dates)
    icsa   = ffill(daily_series["ICSA"],   daily_dates)
    vixcls = ffill(daily_series["VIXCLS"], daily_dates)
    baa    = ffill(daily_series["BAA"],    daily_dates)
    aaa    = ffill(daily_series["AAA"],    daily_dates)

    # Forward-fill monthly signals to daily
    indpro_sig = ffill(monthly_signals["indpro_3mo"], daily_dates)
    manemp_sig = ffill(monthly_signals["manemp_3mo"], daily_dates)
    pcepilfe_s = ffill(monthly_signals["pcepilfe_yoy"], daily_dates)
    dff_sig    = ffill(monthly_signals["dff_3mo"], daily_dates)
    sp500_sig  = ffill(monthly_signals["sp500_ma10"], daily_dates)

    out = {}
    for d in daily_dates:
        count = 0
        v = t10y3m.get(d)
        if v is not None and v < 0:                     count += 1  # T10Y3M
        v = t10y2y.get(d)
        if v is not None and v < 0:                     count += 1  # T10Y2Y
        v = dfii10.get(d)
        if v is not None and v > 1.0:                   count += 1  # DFII10
        v = icsa.get(d)
        if v is not None and v > 225000:                count += 1  # ICSA
        if indpro_sig.get(d, 0):                        count += 1  # INDPRO
        if manemp_sig.get(d, 0):                        count += 1  # MANEMP
        if pcepilfe_s.get(d, 0):                        count += 1  # PCEPILFE
        if dff_sig.get(d, 0):                           count += 1  # DFF
        v = vixcls.get(d)
        if v is not None and v > 25:                    count += 1  # VIXCLS
        if sp500_sig.get(d, 0):                         count += 1  # SP500
        b, a = baa.get(d), aaa.get(d)
        if b is not None and a is not None and (b - a) > 0.75: count += 1  # credit spread
        out[d] = count
    return out

# ── COLUMN PATCHER ───────────────────────────────────────────
def patch_macro_log():
    """Fill blank icsa_claims (all history) and oil_spread (2020+) in macro_log."""
    path = os.path.join(_dir, "macro_log.csv")
    if not os.path.exists(path):
        return

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows   = [list(r) for r in reader]

    icsa_idx = header.index("icsa_claims")
    oil_idx  = header.index("oil_spread")
    date_idx = header.index("date")

    cs_idx     = header.index("credit_spread_pct")

    needs_icsa = [r for r in rows if not r[icsa_idx]]
    needs_oil  = [r for r in rows if not r[oil_idx] and r[date_idx] >= OIL_PATCH_START]
    # Detect monthly BAA/AAA data by low unique-value count (<500 = ~23yrs of monthly)
    cs_unique  = len(set(r[cs_idx] for r in rows if r[cs_idx]))
    needs_cs   = cs_unique < 500

    if not needs_icsa and not needs_oil and not needs_cs:
        print("  patch: nothing to do")
        return

    print(f"  patch: {len(needs_icsa)} icsa blanks, {len(needs_oil)} oil blanks, credit_spread migration={'yes' if needs_cs else 'no'}")

    # ── ICSA ────────────────────────────────────────────────
    icsa_filled = {}
    if needs_icsa:
        raw = fred_full("ICSA")
        if raw:
            # ICSA uses week-ending Saturday dates; merge with CSV weekday dates
            # so forward-fill carries Saturday values into subsequent Mon-Fri dates
            csv_dates = sorted(r[date_idx] for r in rows)
            combined  = sorted(set(raw.keys()) | set(csv_dates))
            icsa_ff   = ffill(raw, combined)
            icsa_filled = {d: icsa_ff[d] for d in csv_dates if d in icsa_ff}

    # ── CREDIT SPREAD (monthly BAA/AAA → daily DBAA/DAAA) ───
    cs_filled = {}
    if needs_cs:
        dbaa_raw = fred_full("DBAA")
        daaa_raw = fred_full("DAAA")
        if dbaa_raw and daaa_raw:
            all_dates_sorted = sorted(r[date_idx] for r in rows)
            combined = sorted(set(dbaa_raw.keys()) | set(daaa_raw.keys()) | set(all_dates_sorted))
            dbaa_ff  = ffill(dbaa_raw, combined)
            daaa_ff  = ffill(daaa_raw, combined)
            for d in all_dates_sorted:
                b = dbaa_ff.get(d)
                a = daaa_ff.get(d)
                if b is not None and a is not None:
                    cs_filled[d] = round(b - a, 4)

    # ── OIL SPREAD ──────────────────────────────────────────
    oil_filled = {}
    if needs_oil:
        spot_raw = fred_full("DCOILWTICO")
        all_dates_sorted = sorted(r[date_idx] for r in rows)
        spot_ff = ffill(spot_raw, all_dates_sorted)

        # Determine unique forward tickers needed
        unique_tickers = {}
        for r in needs_oil:
            d    = datetime.fromisoformat(r[date_idx]).date()
            code = _MONTH_CODES[d.month - 1]
            tkr  = f"CL{code}{str(d.year + 1)[2:]}.NYM"
            unique_tickers.setdefault(tkr, set()).add(r[date_idx])

        # Fetch each ticker's history once
        ticker_data = {}
        for tkr in sorted(unique_tickers):
            print(f"    fetching {tkr}...")
            ticker_data[tkr] = yahoo_full(tkr, "2019-01-01")
            time.sleep(0.3)

        # Compute spread per date
        for r in needs_oil:
            d_str = r[date_idx]
            d     = datetime.fromisoformat(d_str).date()
            code  = _MONTH_CODES[d.month - 1]
            tkr   = f"CL{code}{str(d.year + 1)[2:]}.NYM"
            spot  = spot_ff.get(d_str)
            fwd   = ticker_data.get(tkr, {}).get(d_str)
            if spot is not None and fwd is not None:
                oil_filled[d_str] = round(spot - fwd, 2)

    # ── APPLY ───────────────────────────────────────────────
    patched_icsa = patched_oil = patched_cs = 0
    for r in rows:
        d = r[date_idx]
        if not r[icsa_idx] and d in icsa_filled:
            r[icsa_idx] = f"{icsa_filled[d]:.0f}"
            patched_icsa += 1
        if d in cs_filled:
            r[cs_idx] = f"{cs_filled[d]:.4f}"
            patched_cs += 1
        if not r[oil_idx] and d in oil_filled:
            r[oil_idx] = f"{oil_filled[d]:.2f}"
            patched_oil += 1

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

    print(f"  patch: filled {patched_icsa} icsa, {patched_oil} oil, {patched_cs} credit_spread rows")

# ── MAIN ─────────────────────────────────────────────────────
def main():
    if not FRED_API_KEY:
        sys.exit("ERROR: FRED_API_KEY env var not set. Export it before running.")

    macro_path = os.path.join(_dir, "macro_log.csv")
    asset_path = os.path.join(_dir, "asset_log.csv")
    existing_macro = load_existing_dates(macro_path)
    existing_asset = load_existing_dates(asset_path)
    print(f"Existing macro rows: {len(existing_macro)} | asset rows: {len(existing_asset)}")

    # ── FETCH YAHOO (assets + DXY + USDSEK) ─────────────────
    print("\nFetching Yahoo Finance...")
    ymap = {
        "gold":   yahoo_full("GC%3DF",    ASSET_START),
        "silver": yahoo_full("SI%3DF",    ASSET_START),
        "lly":    yahoo_full("LLY",       ASSET_START),
        "wmt":    yahoo_full("WMT",       ASSET_START),
        "vrt":    yahoo_full("VRT",       "2020-02-07"),  # IPO date
        "ccj":    yahoo_full("CCJ",       ASSET_START),
        "avgo":   yahoo_full("AVGO",      ASSET_START),
        "jnj":    yahoo_full("JNJ",       ASSET_START),
        "dxy":    yahoo_full("DX-Y.NYB",  MACRO_START),
        "usdsek": yahoo_full("USDSEK%3DX", ASSET_START),
    }

    # ── FETCH FRED (macro series) ────────────────────────────
    print("\nFetching FRED...")
    fred = {
        "T10Y3M":  fred_full("T10Y3M"),
        "T10Y2Y":  fred_full("T10Y2Y"),
        "DFII10":  fred_full("DFII10"),
        "ICSA":    fred_full("ICSA"),
        "INDPRO":  fred_full("INDPRO"),
        "MANEMP":  fred_full("MANEMP"),
        "PCEPILFE":fred_full("PCEPILFE"),
        "FEDFUNDS":fred_full("FEDFUNDS"),
        "VIXCLS":  fred_full("VIXCLS"),
        "SP500":   fred_full("SP500"),
        "BAA":     fred_full("DBAA"),
        "AAA":     fred_full("DAAA"),
    }

    # ── MONTHLY SIGNALS ──────────────────────────────────────
    print("\nComputing monthly signals...")
    monthly_signals = {
        "indpro_3mo":  _three_mo_decline(monthly_sorted(fred["INDPRO"])),
        "manemp_3mo":  _three_mo_decline(monthly_sorted(fred["MANEMP"])),
        "pcepilfe_yoy":_pcepilfe_yoy(monthly_sorted(fred["PCEPILFE"])),
        "dff_3mo":     _three_mo_decline(monthly_sorted(fred["FEDFUNDS"])),
        "sp500_ma10":  _sp500_vs_ma10(monthly_sorted(fred["SP500"])),
    }

    # ── BUILD DATE INDEXES ────────────────────────────────────
    macro_dates = date_range(MACRO_START, END_DATE)
    asset_dates = date_range(ASSET_START, END_DATE)

    # ── RECESSION COUNTS ─────────────────────────────────────
    print("\nComputing recession counts...")
    rec_counts = build_recession_count(
        macro_dates,
        {k: fred[k] for k in ["T10Y3M","T10Y2Y","DFII10","ICSA","VIXCLS","BAA","AAA"]},
        monthly_signals,
    )

    # ── MACRO LOG ─────────────────────────────────────────────
    print("\nBuilding macro_log rows...")
    dxy_d    = ffill(ymap["dxy"],         macro_dates)
    dfii10_d = ffill(fred["DFII10"],      macro_dates)
    t10y2y_d = ffill(fred["T10Y2Y"],      macro_dates)
    t10y3m_d = ffill(fred["T10Y3M"],      macro_dates)
    icsa_d   = ffill(fred["ICSA"],        macro_dates)
    fedfunds = ffill(fred["FEDFUNDS"],    macro_dates)
    vixcls_d = ffill(fred["VIXCLS"],      macro_dates)
    sp500_d  = ffill(fred["SP500"],       macro_dates)
    baa_d    = ffill(fred["BAA"],         macro_dates)
    aaa_d    = ffill(fred["AAA"],         macro_dates)
    indpro_d = ffill(fred["INDPRO"],      macro_dates)

    # MANEMP: FRED unit is thousands of persons; manemp_k column stores that value
    manemp_d = ffill(fred["MANEMP"],      macro_dates)

    # PCEPILFE YoY -- compute daily (forward-filled from monthly)
    pce_monthly = monthly_sorted(fred["PCEPILFE"])
    pce_yoy_monthly = {}
    pce_vals  = [v for _, v in pce_monthly]
    pce_dates = [d for d, _ in pce_monthly]
    for i in range(len(pce_dates)):
        if i >= 12 and pce_vals[i-12]:
            pce_yoy_monthly[pce_dates[i]] = (pce_vals[i] - pce_vals[i-12]) / pce_vals[i-12] * 100
    pce_yoy_d = ffill(pce_yoy_monthly, macro_dates)

    # Gold/silver ratio
    gold_all   = ffill(ymap["gold"],   macro_dates)
    silver_all = ffill(ymap["silver"], macro_dates)

    macro_rows = []
    for d in macro_dates:
        if d in existing_macro:
            continue
        g = gold_all.get(d)
        s = silver_all.get(d)
        gsr = g / s if g and s else None
        b, a = baa_d.get(d), aaa_d.get(d)
        credit = (b - a) if b is not None and a is not None else None
        macro_rows.append([
            d,
            _safe(dxy_d.get(d),      4),
            _safe(dfii10_d.get(d),   4),
            _safe(t10y2y_d.get(d),   4),
            _safe(t10y3m_d.get(d),   4),
            _safe(pce_yoy_d.get(d),  2),
            _safe(icsa_d.get(d),     0),   # raw claims e.g. 200000
            _safe(fedfunds.get(d),   4),
            _safe(vixcls_d.get(d),   2),
            _safe(sp500_d.get(d),    2),
            _safe(credit,            4),
            _safe(indpro_d.get(d),   2),
            _safe(manemp_d.get(d),   0),   # thousands of persons (FRED native)
            "",                            # oil_spread -- handled by patch_macro_log
            _safe(gsr,               4),
            rec_counts.get(d, ""),
        ])

    macro_rows.sort(key=lambda r: r[0])
    if macro_rows:
        write_csv(macro_path, MACRO_HEADER, macro_rows)
    else:
        print("  macro_log: nothing new to write")

    # ── ASSET LOG ─────────────────────────────────────────────
    print("\nBuilding asset_log rows...")

    # Build daily returns: {date: pct_change} from price series
    def daily_rets(prices):
        """Returns {date_str: pct_change} from a {date_str: price} dict."""
        sorted_items = sorted(prices.items())
        out = {}
        for i in range(1, len(sorted_items)):
            d, p = sorted_items[i]
            p0   = sorted_items[i-1][1]
            if p0:
                out[d] = (p - p0) / p0 * 100
        return out

    price_series = {k: ymap[k] for k in V3_WEIGHTS}
    ret_series   = {k: daily_rets(v) for k, v in price_series.items()}
    usdsek_d     = ymap["usdsek"]

    asset_rows = []
    for d in asset_dates:
        if d in existing_asset:
            continue
        prices = {k: price_series[k].get(d) for k in V3_WEIGHTS}
        rets   = {k: ret_series[k].get(d)   for k in V3_WEIGHTS}
        usdsek_p = usdsek_d.get(d)
        gold_p   = prices["gold"]
        silver_p = prices["silver"]
        gold_sek   = gold_p   * usdsek_p if gold_p   and usdsek_p else None
        silver_sek = silver_p * usdsek_p if silver_p and usdsek_p else None

        # Portfolio return normalised across assets with data that day
        valid = {k: v for k, v in rets.items() if v is not None}
        if valid:
            total_w = sum(V3_WEIGHTS[k] for k in valid)
            port_ret = sum(V3_WEIGHTS[k] * v for k, v in valid.items()) / total_w if total_w else None
        else:
            port_ret = None

        asset_rows.append([
            d,
            _safe(prices["gold"],   4), _safe(prices["silver"], 4),
            _safe(prices["lly"],    4), _safe(prices["wmt"],    4),
            _safe(prices["vrt"],    4), _safe(prices["ccj"],    4),
            _safe(prices["avgo"],   4), _safe(prices["jnj"],    4),
            _safe(usdsek_p,         4),
            _safe(gold_sek,         2), _safe(silver_sek,       2),
            _safe(rets["gold"],     4), _safe(rets["silver"],   4),
            _safe(rets["lly"],      4), _safe(rets["wmt"],      4),
            _safe(rets["vrt"],      4), _safe(rets["ccj"],      4),
            _safe(rets["avgo"],     4), _safe(rets["jnj"],      4),
            _safe(port_ret,         4),
        ])

    asset_rows.sort(key=lambda r: r[0])
    if asset_rows:
        write_csv(asset_path, ASSET_HEADER, asset_rows)
    else:
        print("  asset_log: nothing new to write")

    print("\nPatching blank columns...")
    patch_macro_log()

    print("\nDone.")

if __name__ == "__main__":
    main()
