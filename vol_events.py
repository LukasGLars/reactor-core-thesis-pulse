# -*- coding: utf-8 -*-
"""
Vol event calendar for Reactor Core v3.
Fetches upcoming earnings (yfinance) and macro releases (FRED).
Importable: get_vol_events() -> str
"""

import os
import sys
import time
import requests
from datetime import date, datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")

EQUITY_TICKERS = ["LLY", "WMT", "JNJ", "CCJ", "VRT", "AVGO"]

FRED_RELEASES = {
    54: ("PCE print", "macro"),
    10: ("Core CPI",  "macro"),
    50: ("NFP",       "macro"),
}

# Update annually: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_DATES_2026 = [
    date(2026, 1, 29),
    date(2026, 3, 19),
    date(2026, 5, 7),
    date(2026, 6, 18),
    date(2026, 7, 30),
    date(2026, 9, 17),
    date(2026, 10, 29),
    date(2026, 12, 10),
]

WINDOW_DAYS = 60
URGENT_DAYS = 7


def _fmt_date(d):
    return d.strftime("%b ") + str(d.day)


def _fetch_price_data(ticker):
    """Returns (price, dd_52w) via Yahoo v8 chart. Same endpoint as thesis_pulse."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1y"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            return None, None
        closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if not closes:
            return None, None
        price    = closes[-1]
        high_52w = max(closes)
        dd_52w   = (price - high_52w) / high_52w * 100
        return price, dd_52w
    except Exception:
        return None, None


def _fetch_implied_move(ticker, price, earn_date):
    """
    ATM straddle / price using the first expiry AFTER earnings date.
    That expiry is where the earnings premium is priced in.
    Returns float (%) or None on any failure.
    """
    try:
        import yfinance as yf
        t    = yf.Ticker(ticker)
        exps = t.options
        if not exps:
            return None
        # first expiry on or after earnings date — earnings vol lives here
        post = [e for e in exps if datetime.strptime(e, "%Y-%m-%d").date() >= earn_date]
        if not post:
            return None
        chain = t.option_chain(post[0])
        calls = chain.calls
        puts  = chain.puts
        if calls.empty or puts.empty:
            return None
        atm_strike = min(calls["strike"].values, key=lambda x: abs(x - price))
        call_row   = calls[calls["strike"] == atm_strike]
        put_row    = puts[puts["strike"]   == atm_strike]
        if call_row.empty or put_row.empty:
            return None
        call_mid = (call_row["bid"].values[0] + call_row["ask"].values[0]) / 2
        put_mid  = (put_row["bid"].values[0]  + put_row["ask"].values[0])  / 2
        straddle = call_mid + put_mid
        if straddle <= 0 or price <= 0:
            return None
        return straddle / price * 100
    except Exception as e:
        print(f"  implied_move {ticker}: {type(e).__name__}: {e}")
        return None


def _fetch_earnings():
    """Returns list of (date, label, 'earnings', {'dd_52w': float|None, 'impl_move': float|None})."""
    events = []
    try:
        import yfinance as yf
    except ImportError:
        print("  WARNING: yfinance not installed — skipping earnings")
        return events

    today = date.today()

    for ticker in EQUITY_TICKERS:
        earn_date = None
        try:
            t  = yf.Ticker(ticker)
            df = None
            try:
                df = t.earnings_dates
            except Exception:
                pass

            if df is not None and not df.empty:
                future = [
                    idx.date() for idx in df.index
                    if hasattr(idx, "date") and idx.date() >= today
                ]
                if future:
                    earn_date = min(future)

            if earn_date is None:
                cal = t.calendar
                if cal:
                    val = cal.get("Earnings Date")
                    if isinstance(val, list):
                        val = val[0] if val else None
                    if val is not None:
                        d = val.date() if hasattr(val, "date") else datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
                        if d >= today:
                            earn_date = d

        except Exception as e:
            print(f"  WARNING: {ticker} earnings fetch failed: {e}")

        if earn_date:
            time.sleep(1)
            price, dd_52w = _fetch_price_data(ticker)
            time.sleep(1)
            impl_move = _fetch_implied_move(ticker, price, earn_date) if price else None
            events.append((earn_date, f"{ticker} earnings", "earnings",
                           {"dd_52w": dd_52w, "impl_move": impl_move}))
        else:
            time.sleep(0.5)

    return events


def _fetch_fred_releases():
    """Returns list of (date, label, 'macro', None)."""
    events  = []
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        print("  WARNING: FRED_API_KEY not set — skipping macro releases")
        return events

    today = date.today()

    for release_id, (label, typ) in FRED_RELEASES.items():
        try:
            r = requests.get(
                "https://api.stlouisfed.org/fred/release/dates",
                params={
                    "release_id":   release_id,
                    "api_key":      api_key,
                    "file_type":    "json",
                    "sort_order":   "desc",
                    "limit":        10,
                    "include_release_dates_with_no_data": "true",
                },
                timeout=15,
            )
            r.raise_for_status()
            upcoming = [
                datetime.strptime(rd["date"], "%Y-%m-%d").date()
                for rd in r.json().get("release_dates", [])
                if datetime.strptime(rd["date"], "%Y-%m-%d").date() >= today
            ]
            if upcoming:
                events.append((min(upcoming), label, typ, None))
        except Exception as e:
            print(f"  WARNING: FRED release {release_id} ({label}) failed: {e}")

    return events


def _fomc_events(today):
    upcoming = [d for d in FOMC_DATES_2026 if d >= today]
    return [(min(upcoming), "FOMC", "macro", None)] if upcoming else []


def _demo_events():
    today = date.today()
    return [
        (today + timedelta(days=4),  "PCE print",    "macro",    None),
        (today + timedelta(days=10), "NFP",           "macro",    None),
        (today + timedelta(days=16), "Core CPI",      "macro",    None),
        (today + timedelta(days=17), "AVGO earnings", "earnings", {"dd_52w": -14.2, "impl_move": 6.2}),
        (today + timedelta(days=23), "FOMC",          "macro",    None),
        (today + timedelta(days=51), "JNJ earnings",  "earnings", {"dd_52w": -3.1,  "impl_move": 3.8}),
        (today + timedelta(days=58), "VRT earnings",  "earnings", {"dd_52w": -21.4, "impl_move": None}),
    ]


def _render(events, today):
    lines = [f"  PULSE", f"  {'─'*36}"]

    if not events:
        lines.append("  no events in next 60 days")
        return "\n".join(lines)

    for item in events:
        d, label, typ, meta = item
        days     = (d - today).days
        flag     = "⚠" if days <= URGENT_DAYS else " "
        date_str = _fmt_date(d)
        days_str = f"{days}d"

        lines.append(f"{flag} {label:<18} {date_str}  {days_str}")

        if typ == "earnings" and meta:
            dd   = meta.get("dd_52w")
            impl = meta.get("impl_move")
            dd_s   = f"{dd:+.1f}% vs 52wH" if dd   is not None else ""
            impl_s = f"±{impl:.1f}%"        if impl is not None else ""
            detail = "  ".join(x for x in [dd_s, impl_s] if x)
            if detail:
                lines.append(f"          {detail}")

    return "\n".join(lines)


def get_vol_events(demo: bool = False) -> str:
    today  = date.today()
    cutoff = today + timedelta(days=WINDOW_DAYS)

    if demo:
        events = _demo_events()
    else:
        events = _fetch_earnings() + _fetch_fred_releases() + _fomc_events(today)
        events = [e for e in events if today <= e[0] <= cutoff]
        events.sort(key=lambda x: x[0])

        seen, deduped = set(), []
        for item in events:
            key = item[:2]
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        events = deduped

    return _render(events, today)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true", help="show demo output")
    args = p.parse_args()
    print(get_vol_events(demo=args.demo))
