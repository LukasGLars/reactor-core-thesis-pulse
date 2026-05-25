"""
Real Yield Velocity vs Gold Performance — Thesis Invalidation Analysis

Derives empirical threshold: 3m DFII10 velocity (bps) above which
gold 6m forward returns deteriorate. Pre/post-2022 regime split included.

Dependencies: pip install pandas numpy yfinance requests matplotlib scipy
"""

import io
import warnings
import requests
import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy import stats

warnings.filterwarnings("ignore")

# ─── CONFIG ──────────────────────────────────────────────────────────────────
FRED_URL    = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
GOLD_TICKER = "GC=F"
START_DATE  = "2003-01-01"
REGIME_BREAK = pd.Timestamp("2022-01-01")

# Trading-day approximations
WINDOWS  = {"1m": 21, "3m": 63, "6m": 126}
HORIZONS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}

# 3m velocity buckets in bps
BUCKETS = [
    (-np.inf, -100, "< -100"),
    (-100,     -50, "-100→-50"),
    (-50,        0, "-50→0"),
    (0,         50, "0→50"),
    (50,       100, "50→100"),
    (100,  np.inf,  "> 100"),
]

# ─── STYLE ───────────────────────────────────────────────────────────────────
BG, PANEL = "#0d1117", "#161b22"
WHITE, DIM = "white", "#8b949e"
BLUE, ORANGE, GREEN, RED = "#58a6ff", "#f0883e", "#3fb950", "#f85149"


# ─── DATA ─────────────────────────────────────────────────────────────────────
def fetch_tips() -> pd.DataFrame:
    r = requests.get(FRED_URL, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["DATE"], index_col="DATE")
    df.columns = ["DFII10"]
    return df.replace(".", np.nan).astype(float).dropna()


def fetch_gold() -> pd.DataFrame:
    raw = yf.download(GOLD_TICKER, start=START_DATE, progress=False, auto_adjust=True)
    if raw.empty:
        raise RuntimeError("yfinance returned no data for GC=F")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    return raw[["Close"]].rename(columns={"Close": "Gold"})


def build_dataset() -> pd.DataFrame:
    tips = fetch_tips()
    gold = fetch_gold()
    df = tips.join(gold, how="inner").sort_index()

    # Real yield velocity in bps
    for name, days in WINDOWS.items():
        df[f"rv_{name}"] = (df["DFII10"] - df["DFII10"].shift(days)) * 100

    # Gold log forward returns in %
    log_g = np.log(df["Gold"])
    for name, days in HORIZONS.items():
        df[f"fwd_{name}"] = (log_g.shift(-days) - log_g) * 100

    return df


# ─── ANALYSIS ────────────────────────────────────────────────────────────────
def bucket_stats(df: pd.DataFrame, vel: str, fwd: str) -> pd.DataFrame:
    rows = []
    for lo, hi, label in BUCKETS:
        sub = df.loc[(df[vel] > lo) & (df[vel] <= hi), fwd].dropna()
        if len(sub) < 5:
            continue
        t, p = stats.ttest_1samp(sub, 0)
        rows.append(dict(
            bucket=label, n=len(sub),
            mean=sub.mean(), median=sub.median(),
            hit_pct=(sub > 0).mean() * 100,
            t=t, p=p,
        ))
    return pd.DataFrame(rows)


def zero_crossing(df: pd.DataFrame, vel: str, fwd: str):
    """Linear regression zero-crossing = velocity threshold where E[return]=0."""
    c = df[[vel, fwd]].dropna()
    x, y = c[vel].values, c[fwd].values
    sl, ic, r, _, _ = stats.linregress(x, y)
    thresh = (-ic / sl) if sl != 0 else None
    return thresh, sl, ic, r ** 2


# ─── PLOT HELPERS ─────────────────────────────────────────────────────────────
def style(ax):
    ax.set_facecolor(PANEL)
    ax.tick_params(colors=DIM, labelsize=7)
    ax.xaxis.label.set_color(DIM)
    ax.yaxis.label.set_color(DIM)
    ax.title.set_color(WHITE)
    for sp in ax.spines.values():
        sp.set_color("#30363d")


def plot_scatter(ax, df, vel, fwd, thresh, sl, ic, r2, title):
    pre = df.index < REGIME_BREAK
    ax.scatter(df.loc[pre,  vel], df.loc[pre,  fwd], alpha=0.15, s=3,
               color=BLUE, label="Pre-2022", rasterized=True)
    ax.scatter(df.loc[~pre, vel], df.loc[~pre, fwd], alpha=0.30, s=3,
               color=ORANGE, label="Post-2022", rasterized=True)
    xr = np.linspace(df[vel].quantile(0.01), df[vel].quantile(0.99), 300)
    ax.plot(xr, sl * xr + ic, color=RED, lw=1.5, label=f"R²={r2:.3f}")
    if thresh is not None:
        ax.axvline(thresh, color=ORANGE, lw=1.2, ls="--",
                   label=f"×0: {thresh:+.0f}bps")
    ax.axhline(0, color=WHITE, lw=0.5, ls=":")
    ax.axvline(0, color=WHITE, lw=0.5, ls=":")
    ax.set_title(title, fontsize=9)
    ax.set_xlabel("3m RY Velocity (bps)", fontsize=8)
    ax.set_ylabel("6m Gold Return (%)", fontsize=8)
    ax.legend(fontsize=6, framealpha=0.3)
    style(ax)


def plot_bars(ax, bdf, title):
    if bdf.empty:
        ax.set_visible(False)
        return
    colors = [GREEN if v > 0 else RED for v in bdf["mean"]]
    bars = ax.bar(range(len(bdf)), bdf["mean"], color=colors, alpha=0.85, edgecolor=PANEL)
    ax.set_xticks(range(len(bdf)))
    ax.set_xticklabels(bdf["bucket"], rotation=35, ha="right", fontsize=6)
    ax.axhline(0, color=WHITE, lw=0.6, ls="--")
    ax.set_title(title, fontsize=9)
    ax.set_ylabel("Avg 6m Return (%)", fontsize=8)
    for bar, row in zip(bars, bdf.itertuples()):
        sig = "*" if row.p < 0.05 else ""
        ypos = bar.get_height() + (0.4 if bar.get_height() >= 0 else -1.0)
        ax.text(bar.get_x() + bar.get_width() / 2, ypos,
                f"n={row.n}{sig}", ha="center", va="bottom", fontsize=5.5, color=DIM)
    style(ax)


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print("Fetching data...")
    df = build_dataset()
    print(f"  {df.index[0].date()} → {df.index[-1].date()}  |  {len(df):,} observations")

    vel, fwd = "rv_3m", "fwd_6m"
    pre  = df[df.index < REGIME_BREAK]
    post = df[df.index >= REGIME_BREAK]

    # Bucket tables
    bkt_full = bucket_stats(df,   vel, fwd)
    bkt_pre  = bucket_stats(pre,  vel, fwd)
    bkt_post = bucket_stats(post, vel, fwd)

    # Thresholds
    t_full, s_full, i_full, r2_full = zero_crossing(df,   vel, fwd)
    t_pre,  s_pre,  i_pre,  r2_pre  = zero_crossing(pre,  vel, fwd)
    t_post, s_post, i_post, r2_post = zero_crossing(post, vel, fwd)

    def fmt_t(t): return f"{t:+.0f}bps" if t is not None else "undefined"

    # ── Console output ────────────────────────────────────────────────────────
    sep = "═" * 66
    print(f"\n{sep}")
    print("  3m DFII10 VELOCITY → 6m GOLD RETURN  |  EMPIRICAL THRESHOLDS")
    print(sep)

    cols = ["bucket", "n", "mean", "hit_pct", "p"]

    def show(label, bdf, thresh, r2):
        print(f"\n── {label} ──")
        display = bdf[cols].rename(columns={"mean": "avg_ret%", "hit_pct": "hit%"})
        print(display.to_string(index=False, float_format="{:.2f}".format))
        print(f"  Zero-crossing threshold: {fmt_t(thresh)}  (R²={r2:.3f})")

    show(f"FULL PERIOD  {df.index[0].year}–{df.index[-1].year}", bkt_full, t_full, r2_full)
    show("PRE-2022", bkt_pre, t_pre, r2_pre)
    show("POST-2022", bkt_post, t_post, r2_post)

    print(f"\n── THRESHOLD SENSITIVITY  (3m velocity → N-month forward return, full period) ──")
    for h in ["1m", "3m", "6m", "12m"]:
        t, s, _, r2 = zero_crossing(df, vel, f"fwd_{h}")
        print(f"  {h:>3s} fwd:  threshold={fmt_t(t):>10s}   slope={s:+.3f}   R²={r2:.3f}")

    # ── Figure ────────────────────────────────────────────────────────────────
    plt.rcParams.update({
        "figure.facecolor": BG, "axes.facecolor": PANEL,
        "text.color": WHITE, "xtick.color": DIM, "ytick.color": DIM,
    })
    fig = plt.figure(figsize=(17, 13), facecolor=BG)
    fig.suptitle(
        "Real Yield Velocity → Gold Forward Returns  |  Thesis Invalidation Signal\n"
        "3m DFII10 velocity (bps)  ×  6m gold log return (%)",
        fontsize=12, color=WHITE, y=0.995,
    )
    gs = gridspec.GridSpec(3, 3, figure=fig, hspace=0.52, wspace=0.35)

    # Row 0 — scatter: full (colored by regime), pre, post
    plot_scatter(fig.add_subplot(gs[0, 0]), df,   vel, fwd,
                 t_full, s_full, i_full, r2_full, "Full Period (2003–present)")
    plot_scatter(fig.add_subplot(gs[0, 1]), pre,  vel, fwd,
                 t_pre,  s_pre,  i_pre,  r2_pre,
                 f"Pre-2022  |  threshold={fmt_t(t_pre)}")
    plot_scatter(fig.add_subplot(gs[0, 2]), post, vel, fwd,
                 t_post, s_post, i_post, r2_post,
                 f"Post-2022  |  threshold={fmt_t(t_post)}")

    # Row 1 — bucket bars: full, pre, post
    plot_bars(fig.add_subplot(gs[1, 0]), bkt_full, "Full Period — Avg 6m Return / Velocity Bucket")
    plot_bars(fig.add_subplot(gs[1, 1]), bkt_pre,  "Pre-2022 — Avg 6m Return / Velocity Bucket")
    plot_bars(fig.add_subplot(gs[1, 2]), bkt_post, "Post-2022 — Avg 6m Return / Velocity Bucket")

    # Row 2 left — rolling 12m correlation
    ax_roll = fig.add_subplot(gs[2, 0:2])
    clean = df[[vel, fwd]].dropna()
    roll_r = clean[vel].rolling(252).corr(clean[fwd])
    ax_roll.plot(roll_r.index, roll_r.values, color=BLUE, lw=1)
    ax_roll.axhline(0, color=WHITE, lw=0.6, ls="--")
    ax_roll.axvline(REGIME_BREAK, color=ORANGE, lw=1.2, ls="--", label="Jan 2022 break")
    ax_roll.fill_between(roll_r.index, roll_r, 0, where=roll_r < 0,
                         alpha=0.2, color=RED,   label="Velocity hurts gold")
    ax_roll.fill_between(roll_r.index, roll_r, 0, where=roll_r > 0,
                         alpha=0.2, color=GREEN, label="Velocity helps gold")
    ax_roll.set_title("Rolling 12m Pearson r: 3m RY Velocity → 6m Gold Return", fontsize=9)
    ax_roll.set_ylabel("Pearson r", fontsize=8)
    ax_roll.legend(fontsize=7, framealpha=0.3)
    style(ax_roll)

    # Row 2 right — threshold by forward horizon
    ax_hz = fig.add_subplot(gs[2, 2])
    h_labels = ["1m", "3m", "6m", "12m"]
    h_thresh, h_r2 = [], []
    for h in h_labels:
        t, _, _, r2 = zero_crossing(df, vel, f"fwd_{h}")
        h_thresh.append(t if t is not None else np.nan)
        h_r2.append(r2)
    bars = ax_hz.bar(h_labels, h_thresh, color=BLUE, alpha=0.8, edgecolor=PANEL)
    ax_hz.axhline(0, color=WHITE, lw=0.6, ls="--")
    ax_hz.set_title("Zero-Return Threshold by Forward Horizon\n(3m velocity, full period)", fontsize=9)
    ax_hz.set_ylabel("Velocity Threshold (bps)", fontsize=8)
    for bar, r2 in zip(bars, h_r2):
        ax_hz.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                   f"R²={r2:.3f}", ha="center", va="bottom", fontsize=6, color=DIM)
    style(ax_hz)

    out = "ry_velocity_gold_analysis.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=BG)
    print(f"\nChart saved → {out}")
    plt.show()
    print("Done.")


if __name__ == "__main__":
    main()
