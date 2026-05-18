# Project Reactor Core v3 — Portfolio Summary for Claude

*Written: 2026-05-18 | Synthesised from: thesis_v3.md, invalidation_v3.md, backtest instructionv3.txt, calibration reports*

---

## 1. What This Portfolio Is

**Name:** Reactor Core v3
**Framework:** Short Abundance, Long Scarcity
**Core belief:** The next decade rewards owners of things that cannot be easily replicated, extracted, or manufactured at scale. It punishes things that are cheap and plentiful.

The portfolio is a bet on four macro tailwinds running simultaneously:
1. Monetary debasement continues — real assets outperform financial ones
2. Deglobalization makes physical supply constraints more important than financial engineering
3. Energy transition and AI infrastructure create durable scarcity in specific physical nodes
4. Abundance businesses face structural headwinds (Costco was removed on this basis)

"Short Abundance" does not mean short-selling. It means not owning businesses whose value proposition is cheap and plentiful.

---

## 2. Portfolio Construction

### 2a. Position Table

| Position | Weight | Role | Instrument |
|---|---|---|---|
| Gold | 25% | Hedge | GC=F |
| Eli Lilly | 15% | Carry | LLY |
| Walmart | 15% | Carry | WMT |
| Silver | 10% | Hedge | SI=F |
| Vertiv | 10% | Convexity | VRT |
| Cameco | 10% | Cyclical Scarcity | CCJ |
| Broadcom | 9% | Convexity | AVGO |
| J&J | 6% | Carry Defensive | JNJ |

**Bucket weights:** Precious metals hedge 35% | Carry/defensive 36% | Convexity 19% | Cyclical 10%

### 2b. What Changed from v2

v2 → v3 was a deliberate tilt toward scarcity and away from abundance. Triggered by removing Costco (great business, but abundance-beneficiary) and using the freed weight to add Silver and increase Cameco.

| Position | v2 Weight | v3 Weight | Change | Reason |
|---|---|---|---|---|
| Costco | 5% | 0% | Removed | Abundance business — low-margin, volume-driven |
| Silver | 0% | 10% | Added | Structural supply deficit + gold amplifier |
| Cameco | 5.7% | 10% | +4.3pp | Strongest scarcity thesis of any equity |
| Lilly | 19.7% | 15% | −4.7pp | Still high conviction; freed weight for scarcity |
| Walmart | 22.7% | 15% | −7.7pp | Least thesis-pure position; reduced to fund rebalance |
| Vertiv | 9% | 10% | +1pp | AI infrastructure scarcity; slightly increased |
| Broadcom | 7.9% | 9% | +1.1pp | Custom silicon moat; slightly increased |
| Gold | 25% | 25% | Unchanged | Anchor; no reason to change |
| J&J | 5% | 6% | +1pp | Rounded up; minimum position |

### 2c. Why Each Position Earns Its Place

**Gold (25%) — Hedge / Anchor**
Monetary debasement hedge. Central banks globally are net buyers. No counterparty risk. In fiscal dominance environments, gold reprices upward regardless of nominal rates when real rates stay suppressed. Role: provides ballast when equities sell off together. Invalidation: central banks become net sellers for 2+ consecutive quarters.

**Silver (10%) — Hedge / Asymmetric Upside**
Structural supply deficit: Silver Institute data shows persistent deficits since 2021. Industrial demand (solar, EVs, electronics) consumes above-ground stocks faster than mining can replace them. Gold/silver ratio at ~65 — historically mean-reverts to 40–55 in commodity bull cycles. If ratio compresses to 55, silver outperforms gold by 18% on ratio alone. Current position is a starter at ratio ~65; full deployment (~90k from DCA reserve) triggered when ratio < 55. Invalidation: industrial demand collapses, or ratio rises above 90 and holds.

**Cameco (10%) — Cyclical Scarcity (highest thesis purity)**
Uranium supply deficit is structural — existing mines depleting, new mines require 10–15 years from discovery to production. Nuclear renaissance underway (France, Japan restarts, US SMRs, AI data centers seeking 24/7 baseload). Spot price must stay elevated to incentivize supply; Cameco is the largest publicly traded pure-play. Invalidation: 3+ major new mines announced with production timeline <5 years, or nuclear policy reverses en masse.

**Vertiv (10%) — Convexity to AI**
Data center power infrastructure (thermal management, power distribution) is the binding constraint on the AI buildout. Cannot be substituted or produced at scale quickly. AI capex from hyperscalers is multi-year infrastructure, not discretionary. Convexity to AI theme without owning AI itself. Invalidation: AI capex collapses (hyperscaler combined capex down >30% YoY for 2 consecutive quarters).

**Broadcom (9%) — Convexity / Custom Silicon**
Custom silicon (ASICs) for hyperscalers is winner-take-most. Broadcom designs chips that cannot be replicated by merchant silicon at the same efficiency for specific workloads. VMware acquisition creates durable software revenue. Highest single-stock conviction in the convexity bucket. Invalidation: 2+ hyperscalers publicly return to NVIDIA-only merchant silicon.

**Eli Lilly (15%) — Carry / Pharmaceutical Scarcity**
GLP-1 (Mounjaro/Zepbound) is the largest pharmaceutical market in history. Patent protection to 2033+. Manufacturing capacity is the binding constraint — Lilly is spending $23bn on new facilities, creating a multi-year moat even against future competition. 1bn+ addressable population globally. Scarcity angle is manufacturing capacity, not just patents. Reduced from 19.7% to 15% to fund scarcity rebalance, but still second-largest position. Invalidation: GLP-1 patents successfully challenged before 2030, or a competitor achieves >20% better efficacy at comparable cost.

**Walmart (15%) — Carry / Stability**
Pricing power moat in consumer staples. Grocery dominance. Advertising business (Walmart Connect) growing 30%+ YoY, adding higher-margin revenue. Most explicitly the least thesis-pure position — held for stability, not scarcity. First candidate for replacement if a better carry name emerges. Positive in 5/6 historical regimes including COVID crash. Invalidation: grocery market share declining >3pp over 2 years, or gross margin compression >300bps YoY for 2 consecutive years.

**J&J (6%) — Carry Defensive / Minimum Position**
Diversified healthcare (MedTech + Pharma). Near-zero drawdown in most regimes. Dividend aristocrat. Talc litigation resolved post-Kenvue spin. Held for diversification and stability, not high conviction. Minimum position — exits here do not meaningfully change portfolio character. Invalidation: dividend cut, or litigation re-escalates materially.

### 2d. What This Portfolio Is Not

- Not momentum-driven. Past returns justify construction but do not drive it.
- Not macro-timing. No HY/IG spread triggers, no regime-switching. Simple DCA.
- Not diversification-maximizing. Correlation is accepted where the thesis is shared (gold + silver, Vertiv + Broadcom).
- Not minimum-variance. Volatility is accepted in exchange for thesis exposure.

---

## 3. Operating Rules

### DCA / Contribution
- Monthly DCA ~25k across all 8 positions at fixed weights, funded from external DCA reserve
- External DCA reserve: minimum 75–100k at all times (covers both monthly DCA and opportunistic deployment)
- Rebalance trigger: +/− 5 percentage points from target weight
- No tactical allocation changes based on macro calls

### Silver Entry Protocol (data-derived)
The silver deployment rule was derived empirically from 2000–2026 data. Two relevant triggers:

**Full deployment from DCA reserve (~90k):** When gold/silver ratio < 55 (thesis confirmation — ratio compressing from current ~65 toward historical mean of 40–55). One move, no averaging. This is the thesis-based rule.

**Adding during extreme regime:** When GSR > 86.4 (95th percentile) AND compressing (ROC positive over 1 month), historical data shows:
- 75% hit rate over 24 months
- Average return: 118% per trade
- Average max adverse excursion: −5.87% (very low drawdown risk)
- Only 4 historical events since 2000, with 3 hits and 1 miss

**Current signal state:** GSR ~65 = "normal" regime. Signal is OFF. No deployment from reserve triggered yet. Existing starter position held.

**SOP when signal off (GSR 60–79):** Hold existing silver. Do not add. Average 365-day forward return 16%/year with 60% hit rate — respectable, but silver still underperforms gold at this ratio. When ratio falls below 55 (P25), silver historically underperforms gold — consider trimming back to target weight.

### Drawdown Opportunistic Deployment
On −20%+ drawdown in core positions (Lilly, Vertiv, Broadcom) with thesis intact: deploy from DCA reserve. Priority order: Lilly, Vertiv, Broadcom.

---

## 4. Tests Performed

The portfolio was validated through a 10-step quantitative backtest framework. Here is what each test covers and why it matters.

### Step 1 — Core Performance Metrics (3Y / 5Y / 10Y)
**What:** Mean-variance Sharpe-maximized weights vs fixed proposed weights, for 3-, 5-, and 10-year windows ending 2026-03-31.
**Metrics:** Sharpe, Annualized Return, Annualized Vol, Max Drawdown, Calmar, Total Return.
**Why:** Multi-window consistency is required. A portfolio that looks good only in one lookback window is curve-fitted.

### Step 2 — Individual Asset Metrics
**What:** Standalone performance (Sharpe, Ann Return, Max Drawdown) for each of the 8 assets across 3Y/5Y/10Y.
**Why:** Confirms each asset has individual merit before relying on portfolio-level diversification effects.

### Step 3 — Regime Analysis (6 Regimes)
**What:** Total return per asset and the portfolio across 6 distinct market regimes:
1. Pre-COVID Bull (Apr 2016 – Feb 2020): steady growth, low vol
2. COVID Crash (Feb 2020 – Mar 2020): pandemic selloff
3. COVID Recovery (Mar 2020 – Dec 2021): stimulus-driven rally
4. Rate Hike/Inflation (Jan 2022 – Jun 2023): Fed tightening cycle
5. Post-Hike/AI Bull (Jul 2023 – Aug 2024): AI capex boom
6. Rate Cut (Sep 2024 – Mar 2026): easing cycle + gold run

**Why:** A portfolio that works in 3 of 6 regimes is fragile. Target ≥ 4/6 wins per position and portfolio.

### Step 4 — Gold Cap Sensitivity
**What:** Sharpe-maximizing optimization at gold caps of 10%, 15%, 20%, 25%, 30%, 35%, and uncapped, across 3Y/5Y/10Y.
**Why:** Tests whether the 25% gold cap is structurally optimal, or whether lower/higher caps materially improve risk-adjusted returns. 80 random restarts per optimization to avoid local maxima.

### Step 5 — Floor Position Audit (Leave-One-Out)
**What:** For each of the 8 positions, remove it entirely and re-optimize the remaining 7. Calculate ΔSharpe (removing it helps or hurts?), correlation to gold, correlation to portfolio, and regime wins.
**Pass criteria:** KEEP if ≥3/4: (1) removing it hurts Sharpe in at least one window, (2) correlation to gold < 0.75, (3) ≥4/6 regime wins, (4) removing it doesn't eliminate the last position in its role category.
**Why:** Ensures every position earns its place — no free riders. If removing a position improves Sharpe in all windows, it is a drag and should be reconsidered.

### Step 6 — Gold Stress Test
**What:** Apply permanent level shocks to gold (−10% to −50%) across all cap levels. Also a combined gold + silver stress (both metals shocked simultaneously) — new for v3 given the 35% precious metals weight.
**Why:** The debasement thesis is the core macro bet. If it fails (gold corrects hard), how bad does the portfolio perform? The combined stress is critical for v3 because silver was 0% in v2.

### Step 7 — Rolling 3Y Optimization (Quarterly Steps)
**What:** Step through all quarterly 3-year windows from earliest available data to 2026-03-31. In each window, run 80-restart Sharpe optimization and record optimal weights for all 8 positions.
**Why:** Reveals whether the optimizer consistently wants the positions at these weights, or whether gold/silver weights drift wildly with regime. Specifically tests correlation between optimal gold weight and gold 3Y total return.

### Step 8 — Out-of-Sample Validation (2009–2016)
**What:** Apply the fixed v3 proposed weights (not optimized) to 2009–2016 price data — a period the optimizer never saw. 7 positions (Vertiv excluded, pre-IPO).
**Interpretation thresholds:**
- OOS Sharpe > 1.0 → strong structural alpha
- OOS Sharpe 0.5–1.0 → partial structural alpha, some curve-fitting
- OOS Sharpe < 0.5 → mostly curve-fitted
**Why:** The single most important test. If the portfolio has structural alpha (the thesis is real, not curve-fitted), it should outperform in a completely different historical period.

### Step 9 — DCA Simulation (2018–2026, in SEK)
**What:** Starting capital 1,000,000 SEK, monthly contribution 6,000 SEK. USD prices × daily USD/SEK FX rate. No rebalancing — monthly contributions allocated at target weights, existing holdings drift. Compared side-by-side: v2 weights vs v3 weights.
**Why:** The real-world execution test. Shows actual kronor outcomes including FX effect, whole-share rounding, and contribution timing. This is the actual investment being made.

### Step 10 — Head-to-Head vs v2
**What:** Single comparison table of all key metrics for v2 vs v3.
**Why:** The portfolio needs to justify each change. v3 must not simply be worse than v2 after adding complexity.

---

## 5. Performance Baselines and Expected Performance

### 5a. v2 Baseline (the benchmark v3 must match or beat)

| Metric | v2 Actual |
|---|---|
| 10Y Sharpe (optimized) | 1.851 |
| 10Y Ann Return | 30.08% |
| 10Y Ann Volatility | 16.25% |
| 10Y Max Drawdown | −24.86% |
| 10Y Calmar | 1.21 |
| 10Y Total Return | 755.3% |
| 5Y Sharpe (optimized) | 2.033 |
| 5Y Ann Return | 32.68% |
| 5Y Max Drawdown | −22.74% |
| 3Y Sharpe (optimized) | 2.684 |
| 3Y Ann Return | 43.92% |
| 3Y Max Drawdown | −28.75% |
| OOS Sharpe (2010–2016) | 0.955 |
| OOS Sharpe (2009–2016) | 1.057 |
| Gold −40% Sharpe (25% cap) | 1.706 |
| DCA Final Value (SEK) | 10,469,331 |
| DCA Total Profit (SEK) | 8,917,331 |

The v2 OOS Sharpe of 1.057 for 2009–2016 is significant: it indicates genuine structural alpha, not just curve-fitting to the 2016–2026 in-sample period. This is the most important result from the test suite.

### 5b. What v3 Changes and the Performance Implication

v3 adds Silver (10%) and tilts further into scarcity. This has two effects:

**Likely to improve:**
- Tail diversification — silver is a gold amplifier in commodity bull cycles but has low correlation to equities
- Upside capture — if gold/silver ratio compresses from ~65 to 45, silver outperforms gold by ~40% on ratio alone
- Regime performance in commodity bull / inflationary regimes

**Accepted trade-offs:**
- Slightly higher volatility — silver is ~3× more volatile than gold (annualized ~30% vs ~10%)
- Combined precious metals stress is more severe — at 35% combined weight, a gold+silver drawdown hits harder than at 25% (gold-only)
- Reduced carry weight (Lilly down 4.7pp, Walmart down 7.7pp) — slightly lower defensive floor in risk-off

**Net expectation:** v3 Sharpe approximately equal to or slightly below v2 in isolation; DCA value close to or above v2 given silver's higher expected return if thesis plays out. The gold+silver combined stress at −40% is the critical unknown.

### 5c. Recession Monitoring (Live as of 2026-05-11)

A 13-indicator composite model monitors recession probability. Calibrated on 9 recession cycles from 1960 to 2020.

**Current composite state: 4/13 indicators at threshold = BACKGROUND NOISE**

| Indicator | Live Value | Threshold | Firing? |
|---|---|---|---|
| DFII10 (real yields) | 1.94% | > 1.0% | YES |
| UMCSENT (consumer sentiment) | 53.3 | < 90 | YES |
| PCEPILFE (core PCE inflation) | 3.2% YoY | > 2.0% | YES |
| CAPE (equity valuation) | 42.0 | > 20 | YES |
| T10Y3M (yield curve) | +0.71% | < 0% | NO |
| T10Y2Y (yield curve) | +0.49% | < 0% | NO |
| ICSA (initial claims) | 200k | > 225k | NO |
| INDPRO (industrial production) | 2/3 months declining | 3-month decline | NO |
| DFF (Fed funds — cutting?) | 1/3 months cutting | cutting | NO |
| SP500 (vs 10-month MA) | +3.1% above | below MA | NO |
| VIXCLS (volatility) | 17.4 | > 25 | NO |
| CREDIT_SPREAD | 0.61% | > 0.75% | NO |
| MANEMP (manufacturing employment) | 1/3 months declining | 3-month decline | NO |

At 4/13 firing: historically 5–7% recession probability within 12 months. Interpretation: elevated background noise but not a warning. No portfolio action required.

**Recession action rule:** Below 5 indicators firing → no action. At 5+ (16–29% probability within 12 months) → review thesis. Recession itself does not dictate selling; individual position invalidation checklists determine action.

### 5d. Silver Deployment Signal (Live as of 2026-05-12)

**Current GSR ~65 → Normal regime. Silver signal is OFF.**

Historical forward returns from normal GSR regime (59.6–79.2):
- 365-day avg return: 16.3%, hit rate 60%
- Silver still earns its keep; no need to add or reduce

**Deploy additional silver when:** GSR < 55 (ratio compresses to thesis confirmation level). One move, approximately 90k from DCA reserve.

**Trim silver when:** GSR falls below P25 (~60). Silver underperforms gold at that point (avg −2.8%/yr). Trim back to target weight.

---

## 6. Thesis Invalidation Protocol

The invalidation checklist (`invalidation_v3.md`) is the mechanism for disciplined position management. Key principle: **price moves alone are never invalidation. Ask whether reality has changed, not price.**

**Severity levels:**
- Yellow (1 trigger): Watch closely. No action. Re-run checklist in 30 days.
- Red (2+ triggers, or 1 marked CRITICAL): Thesis broken. Size down or exit.

**Portfolio-level overrides** (any one of these triggers full portfolio review):
- Broad commodity index (DBC) −40% over 12 months
- Major multilateral trade deal significantly reducing supply chain friction (deglobalization reversing)
- Real rates above 3% sustained 12+ months with central bank balance sheet reduction
- DXY above 115 sustained 6+ months
- Hyperscaler combined capex down >30% YoY for 2 consecutive quarters (AI capex collapsing)

**When NOT to run the checklist:**
- After a −10% portfolio month (price is not thesis)
- After reading a bearish article about a position
- When a position is down and others are up (rebalance, don't exit)

**Run it when:** earnings reveal structural problems, macro data fundamentally shifts, or a competitor achieves something that changes supply/demand math.

---

## 7. Technical Infrastructure (thesis_pulse.py)

A daily automated monitoring system runs via GitHub Actions and delivers an email report. It does not make trading decisions — it surfaces raw data for human review.

**What it monitors:**
- Gold: WGC gold demand/supply data (central bank flows), spot price
- Silver: spot price, gold/silver ratio, Silver Institute supply deficit data
- Cameco: spot uranium price (via EDGAR or public sources)
- Vertiv/Broadcom: hyperscaler capex announcements (SEC EDGAR filings: MSFT, GOOGL, AMZN, META)
- Lilly: GLP-1 patent status, competitor drug approvals
- Walmart: market share data, advertising revenue
- Macro: FRED data (real rates, yield curve, credit spreads)
- Recession tracker: composite indicator score against calibrated thresholds

**Invalidation triggers monitored automatically** map to the checklists in `invalidation_v3.md`. When a threshold is crossed, the pulse email flags it.

**Calibration files:**
- `calibration/recession_calibration.py` — derives probability tables from 9 historical recession cycles
- `calibration/derive_silver_adding_trigger.py` — tests all GSR threshold + reversion combinations
- `calibration/derive_silver_deployment.py` — full hypothesis A/B/C comparison for silver entry timing
- `calibration/recession_config.json` — stores derived thresholds and indicator parameters
- `config/thresholds.yaml` — all signal thresholds in one place
- `config/data_sources.yaml` — all data source definitions

---

## 8. One-Paragraph Summary

Reactor Core v3 is an 8-position portfolio built on a single macro conviction: the next decade rewards scarcity and punishes abundance. The framework is deliberately simple — no regime-switching, no tactical overlays, just monthly DCA at fixed weights with a ±5pp rebalance trigger. Gold (25%) and Silver (10%) anchor the portfolio against monetary debasement and provide insurance when equities correlate to the downside. Cameco (10%) is the purest expression of the scarcity thesis — structural uranium deficit meeting a nuclear renaissance. Vertiv (10%) and Broadcom (9%) provide convexity to the AI buildout without owning the software beneficiaries, capturing instead the physical infrastructure scarcity that no amount of code solves. Lilly (15%) adds carry through manufacturing-constrained pharmaceutical dominance; Walmart (15%) and J&J (6%) provide stability. The v2 version of this portfolio — which excluded silver — demonstrated a 10-year Sharpe of 1.851, annualized return of 30.1%, and out-of-sample Sharpe of 1.057 (2009–2016), indicating genuine structural alpha rather than curve-fitting. v3's shift to 35% precious metals weight trades some defensive carry for higher asymmetric upside if the commodity supercycle plays out as the thesis expects. The invalidation checklist and daily pulse monitor keep the framework honest: exit is driven by thesis failure, not price action.
