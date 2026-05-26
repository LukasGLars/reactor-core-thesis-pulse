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
    54: ("PCE print",  "macro"),
    10: ("Core CPI",   "macro"),
    23: ("FOMC",       "macro"),
    50: ("NFP",        "macro"),
}

WINDOW_DAYS = 60
URGENT_DAYS = 7


def _fmt_date(d):
    return d.strftime("%b ") + str(d.day)


def _fetch_earnings():
    events = []
    try:
        import yfinance as yf
    except ImportError:
        print("  WARNING: yfinance not installed — skipping earnings")
        return events

    today = date.today()

    for ticker in EQUITY_TICKERS:
        try:
            t = yf.Ticker(ticker)

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
                    events.append((min(future), f"{ticker} earnings", "earnings"))
                    time.sleep(0.5)
                    continue

            cal = t.calendar
            if not cal:
                time.sleep(0.5)
                continue

            val = cal.get("Earnings Date")
            if val is None:
                time.sleep(0.5)
                continue

            if isinstance(val, list):
                val = val[0] if val else None
            if val is None:
                time.sleep(0.5)
                continue

            d = val.date() if hasattr(val, "date") else datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
            if d >= today:
                events.append((d, f"{ticker} earnings", "earnings"))

        except Exception as e:
            print(f"  WARNING: {ticker} earnings fetch failed: {e}")

        time.sleep(0.5)

    return events


def _fetch_fred_releases():
    events = []
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
                    "release_id": release_id,
                    "api_key":    api_key,
                    "file_type":  "json",
                    "sort_order": "asc",
                    "limit":      10,
                },
                timeout=15,
            )
            r.raise_for_status()

            for rd in r.json().get("release_dates", []):
                d = datetime.strptime(rd["date"], "%Y-%m-%d").date()
                if d >= today:
                    events.append((d, label, typ))
                    break

        except Exception as e:
            print(f"  WARNING: FRED release {release_id} ({label}) failed: {e}")

    return events


def _demo_events():
    today = date.today()
    return [
        (today + timedelta(days=4),  "PCE print",       "macro"),
        (today + timedelta(days=10), "NFP",              "macro"),
        (today + timedelta(days=16), "Core CPI",         "macro"),
        (today + timedelta(days=17), "AVGO earnings",    "earnings"),
        (today + timedelta(days=23), "FOMC",             "macro"),
        (today + timedelta(days=51), "JNJ earnings",     "earnings"),
        (today + timedelta(days=58), "VRT earnings",     "earnings"),
    ]


def _render(events, today):
    header  = f"PULSE{' ' * 51}{today.strftime('%Y-%m-%d')}"
    divider = "─" * 68
    lines   = [header, divider]

    if not events:
        lines.append("  no events in next 60 days")
    else:
        for d, label, typ in events:
            days     = (d - today).days
            flag     = "⚠" if days <= URGENT_DAYS else " "
            date_str = _fmt_date(d)
            days_str = f"{days}d"
            lines.append(f"{flag} {label:<22} {date_str:<9} {days_str:<6} {typ}")

    return "\n".join(lines)


def get_vol_events(demo: bool = False) -> str:
    today  = date.today()
    cutoff = today + timedelta(days=WINDOW_DAYS)

    if demo:
        events = _demo_events()
    else:
        events = _fetch_earnings() + _fetch_fred_releases()
        events = [(d, label, typ) for d, label, typ in events if today <= d <= cutoff]
        events.sort(key=lambda x: x[0])

        seen, deduped = set(), []
        for item in events:
            if item[:2] not in seen:
                seen.add(item[:2])
                deduped.append(item)
        events = deduped

    return _render(events, today)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true", help="show output with hardcoded demo data")
    args = p.parse_args()
    print(get_vol_events(demo=args.demo))
