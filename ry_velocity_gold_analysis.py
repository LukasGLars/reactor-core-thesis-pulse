"""
Real Yield Velocity vs Gold Hit Rate — Thesis Invalidation Analysis

Signal: 3m DFII10 velocity (bps) above which the probability of a
positive 6m gold return degrades materially below the unconditional base rate.

The threshold is defined as hit rate degradation, not return sign-change,
since gold has not gone negative in any RY velocity environment since 2004.

Dependencies: pip install pandas numpy requests matplotlib scipy
"""

import io
import time
import warnings
import requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy import stats

warnings.filterwarnings("ignore")

# ─── CONFIG ──────────────────────────────────────────────────────────────────
FRED_URL     = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
START_DATE   = "2003-01-01"
REGIME_BREAK = pd.Timestamp("2022-01-01")

WINDOWS  = {"1m": 21, "3m": 63, "6m": 126}
HORIZONS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}

BUCKETS = [
    (-np.inf, -100, "< -100"),
    (-100,     -50, "-100/-50"),
    (-50,        0, "-50/0"),
    (0,         50, "0/50"),
    (50,       100, "50/100"),
    (100,  np.inf,  "> 100"),
]

# Degradation flag: hit rate drops this many pp below baseline -> "warning"
DEGRADE_THRESH_PP = 10

# ─── STYLE ───────────────────────────────────────────────────────────────────
BG, PANEL = "#0d1117", "#161b22"
WHITE, DIM = "white", "#8b949e"
BLUE, ORANGE, GREEN, RED, YELLOW = "#58a6ff", "#f0883e", "#3fb950", "#f85149", "#e3b341"


# ─── DATA ─────────────────────────────────────────────────────────────────────
def fetch_tips() -> pd.DataFrame:
    r = requests.get(FRED_URL, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["observation_date"],
                     index_col="observation_date")
    df.index.name = "DATE"
    df.columns = ["DFII10"]
    return df.replace(".", np.nan).astype(float).dropna()


def fetch_gold() -> pd.DataFrame:
    start_ts = int(pd.Timestamp(START_DATE).timestamp())
    end_ts   = int(time.time())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/GLD"
        f"?interval=1d&period1={start_ts}&period2={end_ts}&events=history"
    )
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    data       = r.json()["chart"]["result"][0]
    timestamps = data["timestamp"]
    closes     = data["indicators"]["adjclose"][0]["adjclose"]
    df = pd.DataFrame({
        "DATE": pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(None),
        "Gold": closes,
    }).set_index("DATE").dropna()
    df.index = df.index.normalize()
    return df


def build_dataset() -> pd.DataFrame:
    tips = fetch_tips()
    gold = fetch_gold()
    df   = tips.join(gold, how="inner").sort_index()

    for name, days in WINDOWS.items():
        df[f"rv_{name}"] = (df["DFII10"] - df["DFII10"].shift(days)) * 100

    log_g = np.log(df["Gold"])
    for name, days in HORIZONS.items():
        df[f"fwd_{name}"] = (log_g.shift(-days) - log_g) * 100

    return df


# ─── ANALYSIS ────────────────────────────────────────────────────────────────
def bucket_stats(df: pd.DataFrame, vel: str, fwd: str) -> pd.DataFrame:
    baseline = df[fwd].dropna()
    base_hits = int((baseline > 0).sum())
    base_n    = len(baseline)
    base_rate = base_hits / base_n

    rows = []
    for lo, hi, label in BUCKETS:
        sub = df.loc[(df[vel] > lo) & (df[vel] <= hi), fwd].dropna()
        if len(sub) < 10:
            continue
        hits   = int((sub > 0).sum())
        hr     = hits / len(sub) * 100
        delta  = hr - base_rate * 100

        # One-sided z-test: is bucket hit rate significantly below baseline?
        p_pool  = (hits + base_hits) / (len(sub) + base_n)
        se      = np.sqrt(p_pool * (1 - p_pool) * (1 / len(sub) + 1 / base_n))
        z       = (hits / len(sub) - base_hits / base_n) / se if se > 0 else 0
        p_below = stats.norm.cdf(z)   # one-sided: P(Z <= z)
        p_two   = 2 * min(p_below, 1 - p_below)

        rows.append(dict(
            bucket=label, n=len(sub),
            hit_pct=hr,
            delta_pp=delta,
            mean_ret=sub.mean(),
            p_below=p_below,
            p_two=p_two,
            sig_below=(p_below < 0.05),
            degraded=(delta < -DEGRADE_THRESH_PP),
        ))
    result = pd.DataFrame(rows)
    result.attrs["base_rate"] = base_rate * 100
    result.attrs["base_n"]    = base_n
    return result


def degradation_threshold(bdf: pd.DataFrame) -> str:
    """First bucket where hit rate drops >= DEGRADE_THRESH_PP below baseline."""
    warn = bdf[bdf["degraded"]]
    if warn.empty:
        return "none in range"
    return warn.iloc[0]["bucket"]


def rolling_hit_rate(df: pd.DataFrame, fwd: str, window: int = 252) -> pd.Series:
    s = (df[fwd] > 0).astype(float)
    return s.rolling(window).mean() * 100


# ─── PLOT HELPERS ─────────────────────────────────────────────────────────────
def style(ax):
    ax.set_facecolor(PANEL)
    ax.tick_params(colors=DIM, labelsize=7)
    ax.xaxis.label.set_color(DIM)
    ax.yaxis.label.set_color(DIM)
    ax.title.set_color(WHITE)
    for sp in ax.spines.values():
        sp.set_color("#30363d")


def plot_hit_rate_bars(ax, bdf: pd.DataFrame, title: str):
    if bdf.empty:
        ax.set_visible(False)
        return
    base = bdf.attrs.get("base_rate", 50)
    xs   = range(len(bdf))

    colors = []
    for row in bdf.itertuples():
        if row.sig_below and row.degraded:
            colors.append(RED)
        elif row.degraded:
            colors.append(ORANGE)
        else:
            colors.append(GREEN)

    bars = ax.bar(xs, bdf["hit_pct"], color=colors, alpha=0.85, edgecolor=PANEL, width=0.6)
    ax.axhline(base, color=YELLOW, lw=1.2, ls="--", label=f"Baseline {base:.1f}%")
    ax.axhline(base - DEGRADE_THRESH_PP, color=RED, lw=0.8, ls=":",
               label=f"-{DEGRADE_THRESH_PP}pp threshold")
    ax.set_xticks(xs)
    ax.set_xticklabels(bdf["bucket"], rotation=35, ha="right", fontsize=6)
    ax.set_ylim(0, 105)
    ax.set_title(title, fontsize=9)
    ax.set_ylabel("Hit Rate (% positive 6m returns)", fontsize=8)
    ax.legend(fontsize=6, framealpha=0.3)

    for bar, row in zip(bars, bdf.itertuples()):
        sig = "*" if row.sig_below else ""
        label = f"n={row.n}{sig}\n{row.delta_pp:+.0f}pp"
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                label, ha="center", va="bottom", fontsize=5, color=DIM)
    style(ax)


def plot_hit_rate_curve(ax, df: pd.DataFrame, vel: str, fwd: str, base_rate: float, title: str):
    """Smooth hit rate by velocity using rolling percentile bins."""
    clean = df[[vel, fwd]].dropna().sort_values(vel)
    x     = clean[vel].values
    y     = (clean[fwd].values > 0).astype(float)

    # Rolling mean over sorted velocity (200-obs window)
    win = min(200, len(x) // 5)
    hr_smooth = pd.Series(y).rolling(win, center=True, min_periods=20).mean() * 100

    pre_mask  = df.index < REGIME_BREAK
    post_mask = ~pre_mask
    pre_clean  = df.loc[pre_mask,  [vel, fwd]].dropna().sort_values(vel)
    post_clean = df.loc[post_mask, [vel, fwd]].dropna().sort_values(vel)

    def smooth(sub):
        w = min(100, max(20, len(sub) // 5))
        return sub[vel].values, \
               pd.Series((sub[fwd].values > 0).astype(float)) \
                 .rolling(w, center=True, min_periods=10).mean().values * 100

    xp, hrp = smooth(pre_clean)
    xq, hrq = smooth(post_clean)

    ax.plot(x, hr_smooth, color=WHITE, lw=1.5, label="Full period", zorder=3)
    ax.plot(xp, hrp, color=BLUE,   lw=1, alpha=0.8, ls="--", label="Pre-2022")
    ax.plot(xq, hrq, color=ORANGE, lw=1, alpha=0.8, ls="--", label="Post-2022")
    ax.axhline(base_rate,                    color=YELLOW, lw=1, ls="--", label=f"Base {base_rate:.1f}%")
    ax.axhline(base_rate - DEGRADE_THRESH_PP, color=RED,   lw=0.8, ls=":", label=f"-{DEGRADE_THRESH_PP}pp")
    ax.axvline(0, color=WHITE, lw=0.5, ls=":")
    ax.set_xlim(x[int(len(x)*0.02)], x[int(len(x)*0.98)])
    ax.set_ylim(0, 105)
    ax.set_title(title, fontsize=9)
    ax.set_xlabel("3m RY Velocity (bps)", fontsize=8)
    ax.set_ylabel("Hit Rate (%)", fontsize=8)
    ax.legend(fontsize=6, framealpha=0.3)
    style(ax)


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print("Fetching data...")
    df = build_dataset()
    print(f"  {df.index[0].date()} to {df.index[-1].date()}  |  {len(df):,} observations")

    vel, fwd = "rv_3m", "fwd_6m"
    pre  = df[df.index < REGIME_BREAK]
    post = df[df.index >= REGIME_BREAK]

    bkt_full = bucket_stats(df,   vel, fwd)
    bkt_pre  = bucket_stats(pre,  vel, fwd)
    bkt_post = bucket_stats(post, vel, fwd)

    base_full = bkt_full.attrs["base_rate"]
    base_pre  = bkt_pre.attrs["base_rate"]
    base_post = bkt_post.attrs["base_rate"]

    dg_full = degradation_threshold(bkt_full)
    dg_pre  = degradation_threshold(bkt_pre)
    dg_post = degradation_threshold(bkt_post)

    # ── Console output ────────────────────────────────────────────────────────
    sep = "=" * 68
    print(f"\n{sep}")
    print("  3m RY VELOCITY -> HIT RATE DEGRADATION  |  THESIS INVALIDATION")
    print(sep)
    print(f"  Degradation defined as: hit rate drops >={DEGRADE_THRESH_PP}pp below baseline")
    print(f"  * = significant below baseline (one-sided z-test, p<0.05)")

    cols_show = ["bucket", "n", "hit_pct", "delta_pp", "mean_ret", "p_below"]

    def show(label, bdf, base, dg):
        print(f"\n-- {label}  (baseline hit rate: {base:.1f}%) --")
        display = bdf[cols_show].rename(columns={
            "hit_pct":  "hit%",
            "delta_pp": "vs_base_pp",
            "mean_ret": "avg_ret%",
            "p_below":  "p(below)",
        })
        print(display.to_string(index=False, float_format="{:.2f}".format))
        print(f"  Degradation threshold: {dg}")

    show(f"FULL PERIOD {df.index[0].year}-{df.index[-1].year}", bkt_full, base_full, dg_full)
    show("PRE-2022", bkt_pre, base_pre, dg_pre)
    show("POST-2022", bkt_post, base_post, dg_post)

    print(f"\n-- HIT RATE SENSITIVITY across forward horizons (3m velocity, full period) --")
    for h in ["1m", "3m", "6m", "12m"]:
        bkt_h = bucket_stats(df, vel, f"fwd_{h}")
        dg_h  = degradation_threshold(bkt_h)
        base_h = bkt_h.attrs["base_rate"]
        print(f"  {h:>3s} fwd:  baseline={base_h:.1f}%   degradation threshold={dg_h}")

    # ── Figure ────────────────────────────────────────────────────────────────
    plt.rcParams.update({
        "figure.facecolor": BG, "axes.facecolor": PANEL,
        "text.color": WHITE, "xtick.color": DIM, "ytick.color": DIM,
    })
    fig = plt.figure(figsize=(17, 13), facecolor=BG)
    fig.suptitle(
        "3m RY Velocity -> Gold Hit Rate Degradation  |  Thesis Invalidation Signal\n"
        "Hit rate = % of 6m forward windows with positive gold return  "
        f"| Degradation flag = >={DEGRADE_THRESH_PP}pp below baseline  "
        "| Red bar = significant (p<0.05)",
        fontsize=10, color=WHITE, y=0.998,
    )
    gs = gridspec.GridSpec(3, 3, figure=fig, hspace=0.55, wspace=0.35)

    # Row 0 — hit rate bars: full, pre, post
    plot_hit_rate_bars(fig.add_subplot(gs[0, 0]), bkt_full,
                       f"Full Period | base={base_full:.1f}% | degrade>{dg_full}")
    plot_hit_rate_bars(fig.add_subplot(gs[0, 1]), bkt_pre,
                       f"Pre-2022 | base={base_pre:.1f}% | degrade>{dg_pre}")
    plot_hit_rate_bars(fig.add_subplot(gs[0, 2]), bkt_post,
                       f"Post-2022 | base={base_post:.1f}% | degrade>{dg_post}")

    # Row 1 — smooth hit rate curve (full span) + pre/post overlay
    ax_curve = fig.add_subplot(gs[1, 0:2])
    plot_hit_rate_curve(ax_curve, df, vel, fwd, base_full,
                        "Hit Rate vs 3m RY Velocity (rolling smooth, full + regime split)")

    # Row 1 right — hit rate heatmap across all velocity x horizon combos
    ax_heat = fig.add_subplot(gs[1, 2])
    horizons_h = ["1m", "3m", "6m", "12m"]
    bucket_labels = [b[2] for b in BUCKETS]
    heat_data = np.full((len(BUCKETS), len(horizons_h)), np.nan)
    for j, h in enumerate(horizons_h):
        bkt_h = bucket_stats(df, vel, f"fwd_{h}")
        base_h = bkt_h.attrs["base_rate"]
        for i, (_, _, blabel) in enumerate(BUCKETS):
            row = bkt_h[bkt_h["bucket"] == blabel]
            if not row.empty:
                heat_data[i, j] = row.iloc[0]["hit_pct"] - base_h

    im = ax_heat.imshow(heat_data, aspect="auto", cmap="RdYlGn",
                        vmin=-25, vmax=25, interpolation="nearest")
    ax_heat.set_xticks(range(len(horizons_h)))
    ax_heat.set_xticklabels(horizons_h, fontsize=7, color=DIM)
    ax_heat.set_yticks(range(len(bucket_labels)))
    ax_heat.set_yticklabels(bucket_labels, fontsize=7, color=DIM)
    ax_heat.set_title("Hit Rate Delta vs Baseline\n(pp above/below, by velocity x horizon)", fontsize=9)
    for i in range(heat_data.shape[0]):
        for j in range(heat_data.shape[1]):
            v = heat_data[i, j]
            if not np.isnan(v):
                ax_heat.text(j, i, f"{v:+.0f}", ha="center", va="center",
                             fontsize=6, color="white")
    plt.colorbar(im, ax=ax_heat, fraction=0.046, pad=0.04).ax.tick_params(labelsize=6)
    ax_heat.set_facecolor(PANEL)
    ax_heat.title.set_color(WHITE)

    # Row 2 — rolling 12m hit rate timeline
    ax_roll = fig.add_subplot(gs[2, 0:2])
    rhr = rolling_hit_rate(df, fwd, 252)
    ax_roll.plot(rhr.index, rhr.values, color=BLUE, lw=1, label="Rolling 12m hit rate")
    ax_roll.axhline(base_full, color=YELLOW, lw=1, ls="--",
                    label=f"Full-period baseline {base_full:.1f}%")
    ax_roll.axhline(base_full - DEGRADE_THRESH_PP, color=RED, lw=0.8, ls=":",
                    label=f"Degradation floor {base_full - DEGRADE_THRESH_PP:.1f}%")
    ax_roll.axvline(REGIME_BREAK, color=ORANGE, lw=1.2, ls="--", label="Jan 2022")
    ax_roll.fill_between(rhr.index, rhr.values, base_full,
                         where=(rhr.values < base_full), alpha=0.2, color=RED)
    ax_roll.set_ylim(0, 105)
    ax_roll.set_title("Rolling 12m Gold Hit Rate (6m forward return > 0)", fontsize=9)
    ax_roll.set_ylabel("Hit Rate (%)", fontsize=8)
    ax_roll.legend(fontsize=7, framealpha=0.3)
    style(ax_roll)

    # Row 2 right — per-horizon degradation threshold summary
    ax_sum = fig.add_subplot(gs[2, 2])
    h_bases, h_dg = [], []
    for h in horizons_h:
        bkt_h = bucket_stats(df, vel, f"fwd_{h}")
        h_bases.append(bkt_h.attrs["base_rate"])
        h_dg.append(degradation_threshold(bkt_h))
    y_pos = range(len(horizons_h))
    ax_sum.barh(y_pos, h_bases, color=BLUE, alpha=0.6, label="Baseline hit rate")
    ax_sum.barh(y_pos, [b - DEGRADE_THRESH_PP for b in h_bases],
                color=RED, alpha=0.3, label=f"-{DEGRADE_THRESH_PP}pp floor")
    ax_sum.set_yticks(y_pos)
    ax_sum.set_yticklabels([f"{h} fwd" for h in horizons_h], fontsize=7, color=DIM)
    ax_sum.set_xlabel("Hit Rate (%)", fontsize=8)
    ax_sum.set_title("Baseline Hit Rate by Forward Horizon\n(3m velocity, full period)", fontsize=9)
    for i, (b, dg) in enumerate(zip(h_bases, h_dg)):
        ax_sum.text(b + 0.5, i, f"{b:.1f}%  | degrade>{dg}", va="center",
                    fontsize=6, color=DIM)
    ax_sum.set_xlim(0, 110)
    ax_sum.legend(fontsize=6, framealpha=0.3)
    style(ax_sum)

    out = "ry_velocity_gold_analysis.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=BG)
    print(f"\nChart saved: {out}")
    plt.show()
    print("Done.")


if __name__ == "__main__":
    main()
