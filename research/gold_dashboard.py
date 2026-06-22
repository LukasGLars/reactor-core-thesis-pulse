#!/usr/bin/env python3
"""
research/gold_dashboard.py

Two-panel dashboard showing gold's relationship with portfolio holdings and S&P 500.

  Top:    Rolling 60-day Pearson correlation — gold vs each holding + SPY
  Bottom: Cumulative returns indexed to 100 at START_DATE

Output: research/gold_dashboard.png  (committed to repo by workflow)
"""

import os, sys, time, requests
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, date

START_DATE    = "2020-01-01"
CORR_WINDOW   = 60          # trading days

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "gold_dashboard.png")

# symbol -> (label, hex colour)
SYMBOLS = {
    "GC=F":  ("Gold",    "#F5A623"),
    "^GSPC": ("S&P 500", "#78909C"),
    "LLY":   ("LLY",     "#43A047"),
    "WMT":   ("WMT",     "#1E88E5"),
    "CCJ":   ("CCJ",     "#FB8C00"),
    "VRT":   ("VRT",     "#8E24AA"),
    "AVGO":  ("AVGO",    "#E53935"),
}


# ── fetch ─────────────────────────────────────────────────────────────────────

def fetch_yahoo(symbol):
    import datetime as dt
    p1  = int(dt.datetime(2019, 1, 1).timestamp())
    p2  = int(dt.datetime.now().timestamp())
    enc = symbol.replace("=", "%3D").replace("^", "%5E")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}"
           f"?interval=1d&period1={p1}&period2={p2}")
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            ts  = pd.to_datetime(res["timestamp"], unit="s").normalize()
            adj = res["indicators"].get("adjclose", [{}])
            closes = (adj[0].get("adjclose") if adj and adj[0].get("adjclose")
                      else res["indicators"]["quote"][0]["close"])
            s = pd.Series(closes, index=ts, dtype=float).dropna().sort_index()
            return s
        except Exception as e:
            print(f"  {symbol} attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None


# ── build chart ───────────────────────────────────────────────────────────────

def build_dashboard(prices: pd.DataFrame):
    gold_ret  = prices["GC=F"].pct_change()
    start_ts  = pd.Timestamp(START_DATE)

    # Rolling correlations of every non-gold symbol vs gold
    corr_data = {}
    for sym in prices.columns:
        if sym == "GC=F":
            continue
        r = prices[sym].pct_change()
        corr = gold_ret.rolling(CORR_WINDOW, min_periods=CORR_WINDOW // 2).corr(r)
        corr_data[sym] = corr.loc[start_ts:]

    # Indexed cumulative returns from START_DATE
    sub = prices.loc[start_ts:].dropna(how="all")
    idx_returns = {}
    for sym in prices.columns:
        s = sub[sym].dropna()
        if len(s) < 5:
            continue
        idx_returns[sym] = (s / s.iloc[0]) * 100

    # ── layout ────────────────────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(14, 10),
        gridspec_kw={"height_ratios": [1, 1.1]},
        facecolor="#0F0F12",
    )
    for ax in (ax1, ax2):
        ax.set_facecolor("#0F0F12")
        ax.tick_params(colors="#AAAAAA", labelsize=9)
        ax.xaxis.label.set_color("#AAAAAA")
        ax.yaxis.label.set_color("#AAAAAA")
        for spine in ax.spines.values():
            spine.set_edgecolor("#333333")
        ax.grid(axis="y", color="#222230", linewidth=0.7, linestyle="--")
        ax.grid(axis="x", color="#222230", linewidth=0.4, linestyle=":")

    # ── top: rolling correlation ───────────────────────────────────────────────
    ax1.axhline(0, color="#555555", linewidth=1.0, linestyle="-")
    ax1.axhline( 0.5, color="#333333", linewidth=0.5, linestyle="--")
    ax1.axhline(-0.5, color="#333333", linewidth=0.5, linestyle="--")

    plotted = []
    for sym, series in corr_data.items():
        label, colour = SYMBOLS[sym]
        lw = 2.0 if sym == "^GSPC" else 1.3
        ax1.plot(series.index, series.values, color=colour, linewidth=lw,
                 label=label, alpha=0.9)
        plotted.append((label, colour))

    ax1.set_ylim(-1.05, 1.05)
    ax1.set_ylabel(f"Pearson correlation vs Gold  ({CORR_WINDOW}d rolling)",
                   color="#888888", fontsize=9)
    ax1.set_title(
        f"Gold Correlation & Relative Performance  ·  {START_DATE} → {date.today()}",
        color="#DDDDDD", fontsize=11, fontweight="bold", pad=10,
    )
    ax1.legend(loc="upper left", fontsize=8, framealpha=0.15,
               labelcolor="#CCCCCC", facecolor="#1A1A22", edgecolor="#333333",
               ncol=3)
    ax1.set_xticklabels([])

    # shade periods where gold correlation to SPY < -0.2 (gold hedging)
    if "^GSPC" in corr_data:
        spy_corr = corr_data["^GSPC"].reindex(corr_data[list(corr_data.keys())[0]].index,
                                               method="ffill")
        hedging = spy_corr < -0.2
        ax1.fill_between(spy_corr.index, -1.05, 1.05,
                         where=hedging.values,
                         color="#F5A623", alpha=0.06, label="_")

    # ── bottom: indexed returns ────────────────────────────────────────────────
    for sym, series in idx_returns.items():
        label, colour = SYMBOLS[sym]
        lw = 2.5 if sym == "GC=F" else (1.8 if sym == "^GSPC" else 1.2)
        zo = 3   if sym == "GC=F" else (2   if sym == "^GSPC" else 1)
        alpha = 1.0 if sym in ("GC=F", "^GSPC") else 0.75
        ax2.plot(series.index, series.values, color=colour,
                 linewidth=lw, label=label, zorder=zo, alpha=alpha)

    ax2.axhline(100, color="#444444", linewidth=0.8, linestyle="--")
    ax2.set_ylabel("Indexed return  (100 = Jan 2020)", color="#888888", fontsize=9)
    ax2.legend(loc="upper left", fontsize=8, framealpha=0.15,
               labelcolor="#CCCCCC", facecolor="#1A1A22", edgecolor="#333333",
               ncol=4)

    # shared x-axis formatting
    for ax in (ax1, ax2):
        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.xaxis.set_minor_locator(mdates.MonthLocator(bymonth=[4, 7, 10]))

    fig.subplots_adjust(hspace=0.06, left=0.06, right=0.97, top=0.94, bottom=0.05)
    fig.savefig(OUTPUT_PATH, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"Saved: {OUTPUT_PATH}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Gold Dashboard  |  {date.today()}")
    print(f"Symbols: {', '.join(SYMBOLS.keys())}")
    print(f"Correlation window: {CORR_WINDOW}d  |  Start: {START_DATE}")

    prices_raw = {}
    for sym in SYMBOLS:
        label = SYMBOLS[sym][0]
        print(f"  Fetching {label}...")
        s = fetch_yahoo(sym)
        if s is not None:
            prices_raw[sym] = s
        else:
            print(f"  WARNING: {sym} failed — excluded")
        time.sleep(0.3)

    if "GC=F" not in prices_raw:
        print("ERROR: gold price unavailable — aborting")
        sys.exit(1)

    prices = pd.DataFrame(prices_raw).sort_index()
    prices = prices.ffill(limit=3)

    missing = [s for s in SYMBOLS if s not in prices.columns]
    if missing:
        print(f"  Excluded (no data): {missing}")

    print("Building chart...")
    build_dashboard(prices)


if __name__ == "__main__":
    main()
