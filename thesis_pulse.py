# -*- coding: utf-8 -*-
"""
Reactor Core Thesis Pulse v1.0
Daily thesis monitoring for 8-position portfolio.
Runs via GitHub Actions — sends email with interpretation + raw data.
"""
import requests, json, os, sys, smtplib, time
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.stdout.reconfigure(encoding="utf-8")

# ── CONFIG ─────────────────────────────────────────────────
EDGAR_HEADERS     = {"User-Agent": "ThesisPulse research@example.com"}
FRED_API_KEY      = os.environ.get("FRED_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
EMAIL_ADDRESS     = os.environ.get("EMAIL_ADDRESS", "")
EMAIL_PASSWORD    = os.environ.get("EMAIL_PASSWORD", "")
RECIPIENT_EMAIL   = os.environ.get("RECIPIENT_EMAIL", EMAIL_ADDRESS)
SMTP_SERVER       = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT         = int(os.environ.get("SMTP_PORT", "587"))
WGC_API_AUTH      = os.environ.get("WGC_API_AUTH", "")
WGC_AUTH_COOKIE   = os.environ.get("WGC_AUTH_COOKIE", "")
WGC_AUTH_SESSION  = os.environ.get("WGC_AUTH_SESSION", "")
WGC_XSRF          = os.environ.get("WGC_XSRF", "")

_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_dir, "thesis_v3.md"),       encoding="utf-8") as f: THESIS_DOC       = f.read()
with open(os.path.join(_dir, "invalidation_v3.md"), encoding="utf-8") as f: INVALIDATION_DOC = f.read()

# ── FRED ───────────────────────────────────────────────────
def fred_url(series_id):
    return (f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&sort_order=asc")

def _make_session(max_retries=5, backoff=0.5):
    session = requests.Session()
    retry = Retry(
        total=max_retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def fred_latest(series_id):
    session = _make_session()
    for attempt in range(1, 6):
        try:
            r = session.get(
                fred_url(series_id),
                headers={"User-Agent": "thesis-pulse/1.0"},
                timeout=30,
            )
            r.raise_for_status()
            obs = r.json().get("observations", [])
            rows = [(o["date"], float(o["value"])) for o in obs
                    if o.get("value") not in (".", "")]
            if len(rows) >= 2:
                return rows[-1][1], rows[-2][1], rows[-1][0]
            if rows:
                return rows[-1][1], None, rows[-1][0]
            return None, None, None
        except Exception as e:
            wait = backoff * (2 ** (attempt - 1)) if (backoff := 0.5) else 0.5
            print(f"  FRED {series_id} retry {attempt}/5: {e}")
            time.sleep(wait)
    return None, None, None

def fred_recent(series_id, lookback=20):
    """Returns (latest, prev, lookback_val, latest_date) — lookback in business days."""
    session = _make_session()
    for attempt in range(1, 6):
        try:
            r = session.get(
                fred_url(series_id),
                headers={"User-Agent": "thesis-pulse/1.0"},
                timeout=30,
            )
            r.raise_for_status()
            obs = r.json().get("observations", [])
            rows = [(o["date"], float(o["value"])) for o in obs
                    if o.get("value") not in (".", "")]
            if not rows:
                return None, None, None, None
            latest_val, latest_date = rows[-1][1], rows[-1][0]
            prev_val   = rows[-2][1]  if len(rows) >= 2        else None
            lb_val     = rows[-lookback][1] if len(rows) >= lookback else rows[0][1]
            return latest_val, prev_val, lb_val, latest_date
        except Exception as e:
            wait = 0.5 * (2 ** (attempt - 1))
            print(f"  FRED {series_id} retry {attempt}/5: {e}")
            time.sleep(wait)
    return None, None, None, None

# ── YAHOO FINANCE ──────────────────────────────────────────
def yahoo_history(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1y"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            return None
        closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if len(closes) < 2:
            return None
        curr     = closes[-1]
        prev     = closes[-2]
        high_52w = max(closes)
        return {
            "price":    curr,
            "chg_1d":   (curr - prev) / prev * 100,
            "pts_1d":   curr - prev,
            "chg_1m":   (curr - closes[-22]) / closes[-22] * 100 if len(closes) >= 22 else None,
            "pts_4w":   curr - closes[-22] if len(closes) >= 22 else None,
            "chg_3m":   (curr - closes[-63]) / closes[-63] * 100 if len(closes) >= 63 else None,
            "high_52w": high_52w,
            "dd_52w":   (curr - high_52w) / high_52w * 100,
        }
    except Exception:
        return None

# ── EDGAR ──────────────────────────────────────────────────
CIKS = {
    "MSFT":  "CIK0000789019",
    "GOOGL": "CIK0001652044",
    "AMZN":  "CIK0001018724",
    "META":  "CIK0001326801",
    "VRT":   "CIK0001674101",
    "AVGO":  "CIK0001730168",
    "WMT":   "CIK0000104169",
    "LLY":   "CIK0000059478",
    "JNJ":   "CIK0000200406",
    "NVDA":  "CIK0001045810",
}

def edgar_concept(ticker, concept, namespace="us-gaap"):
    cik = CIKS.get(ticker)
    if not cik:
        return None, None
    url = f"https://data.sec.gov/api/xbrl/companyconcept/{cik}/{namespace}/{concept}.json"
    try:
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
        if r.status_code != 200:
            return None, None
        units = r.json().get("units", {})
        entries = units.get("USD", units.get("USD/shares", units.get("shares", units.get("pure", []))))
        filings = [e for e in entries if e.get("form") in ("10-K","10-Q","20-F","40-F")]
        filings.sort(key=lambda x: x.get("end",""), reverse=True)
        seen, unique = set(), []
        for f in filings:
            key = f.get("end")
            if key not in seen:
                seen.add(key)
                unique.append(f)
        if not unique:
            return None, None
        curr = unique[0]
        curr_fp, curr_form, curr_end = curr.get("fp",""), curr.get("form",""), curr.get("end","")
        prior = None
        for f in unique[1:]:
            if (f.get("fp") == curr_fp and f.get("form") == curr_form
                    and f.get("end","")[:4] < curr_end[:4]):
                prior = f
                break
        if prior is None:
            for f in unique[1:]:
                if f.get("form") == curr_form:
                    prior = f
                    break
        return curr, prior
    except Exception:
        return None, None

def edgar_revenue(ticker):
    from datetime import timedelta
    cutoff = (date.today() - timedelta(days=548)).isoformat()
    curr, prev = edgar_concept(ticker, "RevenueFromContractWithCustomerExcludingAssessedTax")
    if curr and curr.get("end","") >= cutoff:
        return curr, prev
    curr2, prev2 = edgar_concept(ticker, "Revenues")
    if curr2 and curr2.get("end","") >= cutoff:
        return curr2, prev2
    if curr and curr2:
        return (curr, prev) if curr.get("end","") > curr2.get("end","") else (curr2, prev2)
    return (curr, prev) if curr else (curr2, prev2)

# ── WGC CENTRAL BANK GOLD ──────────────────────────────────
def wgc_central_banks():
    """
    Download WGC monthly central bank gold changes (IFS source, ~2-month lag).
    Returns (ttm_tonnes, prev_ttm_tonnes, latest_date_str, lag_days).
    Falls back to (None, None, None, None) if cookies missing or download fails.
    Env vars: WGC_API_AUTH, WGC_AUTH_COOKIE, WGC_AUTH_SESSION, WGC_XSRF.
    """
    if not WGC_AUTH_SESSION:
        return None, None, None, None
    import io
    try:
        import openpyxl
    except ImportError:
        print("  openpyxl not installed — skipping WGC")
        return None, None, None, None
    from datetime import date as _date

    _MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    cookies = {
        "wgcApiAuth_cookie": WGC_API_AUTH,
        "wgcAuth_cookie":    WGC_AUTH_COOKIE,
        "wgcAuth_session":   WGC_AUTH_SESSION,
        "XSRF-TOKEN":        WGC_XSRF,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":    "https://www.gold.org/goldhub/data/gold-reserves-by-country",
        "Accept":     "application/octet-stream,*/*",
    }

    content = None
    today = _date.today()
    for delta in range(4):
        m = today.month - 1 - delta
        y = today.year
        while m < 0:
            m += 12
            y -= 1
        url = (f"https://www.gold.org/download/file/7741/"
               f"Changes_latest_as_of_{_MONTHS[m]}{y}_IFS.xlsx")
        try:
            r = requests.get(url, cookies=cookies, headers=headers, timeout=30)
            if r.status_code == 200 and len(r.content) > 50000:
                content = r.content
                break
            print(f"  WGC {_MONTHS[m]}{y}: status {r.status_code}")
        except Exception as e:
            print(f"  WGC {_MONTHS[m]}{y}: {e}")

    if content is None:
        return None, None, None, None

    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        ws = wb["Monthly"]
        header_row = list(ws.iter_rows(min_row=8, max_row=8, values_only=True))[0]
        date_cols = [(i, v) for i, v in enumerate(header_row) if hasattr(v, 'year')]
        if not date_cols:
            return None, None, None, None

        all_rows = list(ws.iter_rows(min_row=9, values_only=True))

        # Find last column that has real data
        last_ci = date_cols[0][0]
        for ci, _ in date_cols:
            has_data = any(
                isinstance(r[ci], (int, float)) and r[ci] != 0
                for r in all_rows if len(r) > ci
            )
            if has_data:
                last_ci = ci

        populated = [(ci, dt) for ci, dt in date_cols if ci <= last_ci]
        if len(populated) < 13:
            return None, None, None, None

        ttm_cols  = populated[-12:]
        prev_cols = populated[-24:-12]
        latest_dt = ttm_cols[-1][1]

        def _sum(cols):
            total = 0.0
            for ci, _ in cols:
                for row in all_rows:
                    v = row[ci] if len(row) > ci else None
                    if isinstance(v, (int, float)):
                        total += float(v)
            return total

        ttm  = _sum(ttm_cols)
        prev = _sum(prev_cols) if len(prev_cols) == 12 else None
        latest_date_str = latest_dt.strftime("%Y-%m")
        lag = (_date.today() - latest_dt.date()).days

        return round(ttm, 0), round(prev, 0) if prev is not None else None, latest_date_str, lag

    except Exception as e:
        print(f"  WGC parse error: {e}")
        return None, None, None, None


# ── URANIUM ────────────────────────────────────────────────
def get_uranium():
    """IMF uranium price via FRED (PURANUSDM). Monthly, ~6wk lag. Zero scraping risk."""
    val, prev, as_of = fred_latest("PURANUSDM")
    return val, prev, as_of

# ── HELPERS ────────────────────────────────────────────────
def pct(a, b):
    if a and b and b != 0:
        return (a - b) / abs(b) * 100
    return None

def fmt(val, decimals=2, prefix="", suffix=""):
    if val is None:
        return "n/a"
    return f"{prefix}{val:.{decimals}f}{suffix}"

def fmt_bn(val):
    if val is None:
        return "n/a"
    return f"${val/1e9:.1f}B"

def fmt_px(d):
    if not d:
        return "n/a"
    m = f"{d['chg_1m']:+.1f}%" if d["chg_1m"] is not None else "n/a"
    q = f"{d['chg_3m']:+.1f}%" if d["chg_3m"] is not None else "n/a"
    return (f"${d['price']:.2f}  1d {d['chg_1d']:+.1f}%  1m {m}  3m {q}  "
            f"52wH ${d['high_52w']:.2f} ({d['dd_52w']:+.1f}%)")

def _f(d, key, decimals=1, prefix="", suffix=""):
    if not d:
        return "n/a"
    v = d.get(key)
    return fmt(v, decimals, prefix, suffix) if v is not None else "n/a"

# ── INTERPRETATION ─────────────────────────────────────────
def get_interpretation(facts):
    prompt = f"""You are a portfolio analyst reviewing daily thesis pulse data for an 8-position portfolio.
Your job: assess whether each thesis is intact, weakening, or at a trigger threshold.

THESIS DOCUMENT:
{THESIS_DOC}

INVALIDATION CHECKLIST:
{INVALIDATION_DOC}

TODAY'S PRE-COMPUTED FACTS ({facts['today']}):

MACRO:
- Real yield: {facts['ry']} — {facts['ry_dist']}bps to 3.0% invalidation — {facts['ry_signal']}
  Momentum: {facts['ry_chg_1d']} today, {facts['ry_chg_4w']} over 4 weeks — {facts['ry_weeks_to_inv']} to invalidation at current pace
- DXY: {facts['dxy']} — {facts['dxy_dist']}pts to 115 invalidation — {facts['dxy_signal']}
  Momentum: {facts['dxy_chg_1d']} today, {facts['dxy_chg_4w']} over 4 weeks — {facts['dxy_weeks_to_inv']}

HEDGES:
- Gold: {facts['gold_px']} | 1m {facts['gold_1m']} | 3m {facts['gold_3m']} | {facts['gold_dd']} from 52wH
- Silver: {facts['silver_px']} | 1m {facts['silver_1m']} | 3m {facts['silver_3m']} | {facts['silver_dd']} from 52wH
- G/S ratio: {facts['gs']} (deploy trigger <55 = {facts['gs_dist_deploy']} pts away | invalidation >90 = {facts['gs_dist_inv']} pts away)
  Momentum: {facts['gs_chg_1d']} today, {facts['gs_chg_4w']} over 4 weeks — {facts['gs_velocity_label']}
- Central bank gold demand (WGC/IFS, monthly): {facts['cb_gold_ttm']} TTM net | prior 12m {facts['cb_gold_prev']} | YoY change {facts['cb_gold_yoy']} | as of {facts['cb_gold_date']} ({facts['cb_gold_lag']} lag)

CARRY:
- LLY: price {facts['lly_px']} ({facts['lly_dd']} from 52wH) | revenue {facts['lly_rev']} {facts['lly_rev_yoy']} YoY ({facts['lly_rev_date']})
- WMT: price {facts['wmt_px']} ({facts['wmt_dd']} from 52wH) | revenue {facts['wmt_rev']} {facts['wmt_rev_yoy']} YoY ({facts['wmt_rev_date']})
- JNJ: price {facts['jnj_px']} ({facts['jnj_dd']} from 52wH) | revenue {facts['jnj_rev']} {facts['jnj_rev_yoy']} YoY | div/share {facts['jnj_div']} {facts['jnj_div_yoy']} YoY

CYCLICAL:
- CCJ: price {facts['ccj_px']} ({facts['ccj_dd']} from 52wH)
- Uranium: {facts['uranium']} (monthly IMF series, {facts['uranium_lag']}d lag — treat as directional, not spot). Invalidation threshold: <$50/lb — currently ${facts['uranium_dist']:.2f} above.

CONVEXITY:
- VRT: price {facts['vrt_px']} ({facts['vrt_dd']} from 52wH) | revenue {facts['vrt_rev']} {facts['vrt_rev_yoy']} YoY (as of {facts['vrt_rev_date']})
- AVGO: price {facts['avgo_px']} ({facts['avgo_dd']} from 52wH) | revenue {facts['avgo_rev']} {facts['avgo_rev_yoy']} YoY (as of {facts['avgo_rev_date']})
- NVDA revenue: {facts['nvda_rev']} {facts['nvda_rev_yoy']} YoY (as of {facts['nvda_rev_date']}, AI spend proxy)
- Hyperscaler capex: {facts['capex']} {facts['capex_yoy']} YoY (as of {facts['capex_date']}, invalidation threshold: <-30% YoY)

INSTRUCTIONS:
- Use ONLY the pre-computed facts above. Do not recalculate or restate raw numbers beyond what is given.
- Be direct and clinical. No filler. Each section max 2-3 sentences.
- Price moves alone are never invalidation — always tie to thesis conditions.

OUTPUT FORMAT (use exactly these headers, no deviations):

OVERALL: [INTACT | ONE FLAG | REVIEW NEEDED] — [one-line reason]

MACRO: [2 sentences. Are macro conditions favorable, neutral, or headwind for the thesis?]

HEDGES: [2-3 sentences covering gold and silver. Thesis intact? G/S ratio context?]

CARRY: [2-3 sentences covering LLY, WMT, JNJ. Revenue trends vs thesis requirements?]

CYCLICAL: [2 sentences. Uranium thesis intact?]

CONVEXITY: [2-3 sentences covering VRT and AVGO. AI capex confirming? Price/fundamental divergence notable?]
"""
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 900,
                "temperature": 0.2,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"]
        return f"[Claude error {r.status_code}]"
    except Exception as e:
        return f"[Claude error: {e}]"

# ── EMAIL ──────────────────────────────────────────────────
def send_email(subject, body):
    if not all([EMAIL_ADDRESS, EMAIL_PASSWORD, RECIPIENT_EMAIL]):
        print("Email credentials not set — skipping.")
        return
    try:
        msg = MIMEMultipart()
        msg["From"]    = EMAIL_ADDRESS
        msg["To"]      = RECIPIENT_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
            s.starttls()
            s.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            s.send_message(msg)
        print(f"Email sent to {RECIPIENT_EMAIL}")
    except Exception as e:
        print(f"Email error: {e}")

# ── MAIN ───────────────────────────────────────────────────
def main():
    today = date.today().isoformat()
    print(f"Thesis Pulse | {today}")

    print("Fetching Yahoo Finance...")
    gold    = yahoo_history("GC%3DF");  time.sleep(1)
    silver  = yahoo_history("SI%3DF");  time.sleep(1)
    dxy     = yahoo_history("DX-Y.NYB"); time.sleep(1)
    lly_px  = yahoo_history("LLY");     time.sleep(1)
    wmt_px  = yahoo_history("WMT");     time.sleep(1)
    jnj_px  = yahoo_history("JNJ");     time.sleep(1)
    ccj_px  = yahoo_history("CCJ");     time.sleep(1)
    vrt_px  = yahoo_history("VRT");     time.sleep(1)
    avgo_px = yahoo_history("AVGO")

    print("Fetching FRED...")
    ry_val, ry_prev, ry_4w, ry_date = fred_recent("DFII10", lookback=20)

    print("Fetching EDGAR...")
    lly_c,     lly_p     = edgar_revenue("LLY")
    wmt_c,     wmt_p     = edgar_revenue("WMT")
    jnj_c,     jnj_p     = edgar_revenue("JNJ")
    vrt_c,     vrt_p     = edgar_revenue("VRT")
    avgo_c,    avgo_p    = edgar_revenue("AVGO")
    nvda_c,    nvda_p    = edgar_revenue("NVDA")
    jnj_div_c, jnj_div_p = edgar_concept("JNJ", "CommonStockDividendsPerShareCashPaid")
    msft_c,    msft_p    = edgar_concept("MSFT",  "PaymentsToAcquirePropertyPlantAndEquipment")
    googl_c,   googl_p   = edgar_concept("GOOGL", "PaymentsToAcquirePropertyPlantAndEquipment")
    amzn_c,    amzn_p    = edgar_concept("AMZN",  "PaymentsToAcquireProductiveAssets")
    meta_c,    meta_p    = edgar_concept("META",  "PaymentsToAcquirePropertyPlantAndEquipment")

    print("Fetching uranium...")
    uranium, uranium_prev, uranium_date = get_uranium()

    print("Fetching WGC central bank gold...")
    cb_ttm, cb_prev, cb_date, cb_lag = wgc_central_banks()

    # Compute
    gs_ratio         = gold["price"] / silver["price"] if gold and silver else None
    gs_ratio_1d_ago  = (gold["price"] - gold["pts_1d"]) / (silver["price"] - silver["pts_1d"]) if gold and silver and gold.get("pts_1d") and silver.get("pts_1d") else None
    gs_ratio_4w_ago  = (gold["price"] - gold["pts_4w"]) / (silver["price"] - silver["pts_4w"]) if gold and silver and gold.get("pts_4w") and silver.get("pts_4w") else None
    gs_chg_1d        = gs_ratio - gs_ratio_1d_ago if gs_ratio and gs_ratio_1d_ago else None
    gs_chg_4w        = gs_ratio - gs_ratio_4w_ago if gs_ratio and gs_ratio_4w_ago else None
    capex_vals       = [x["val"] for x in [msft_c, googl_c, amzn_c, meta_c] if x]
    capex_prevs      = [x["val"] for x in [msft_p, googl_p, amzn_p, meta_p] if x]
    capex_total      = sum(capex_vals)  if capex_vals  else None
    capex_total_prev = sum(capex_prevs) if capex_prevs else None

    # Facts for prompt
    facts = {
        "today":          today,
        "ry":             fmt(ry_val, 2, suffix="%"),
        "ry_dist":        fmt(300 - ry_val * 100, 0) if ry_val else "n/a",
        "ry_signal":      ("TAILWIND" if ry_val < 2.0 else "NEUTRAL" if ry_val < 2.5 else "WATCH" if ry_val < 3.0 else "INVALIDATION") if ry_val else "n/a",
        "ry_chg_1d":      fmt((ry_val - ry_prev) * 100, 1, suffix="bps") if ry_val and ry_prev else "n/a",
        "ry_chg_4w":      fmt((ry_val - ry_4w)   * 100, 1, suffix="bps") if ry_val and ry_4w   else "n/a",
        "ry_weeks_to_inv": fmt(
            (300 - ry_val * 100) / ((ry_val - ry_4w) * 100 / 4), 0, suffix=" weeks to invalidation"
        ) if ry_val and ry_4w and (ry_val - ry_4w) > 0 else ("moving away from invalidation" if ry_val and ry_4w and ry_val < ry_4w else "n/a"),
        "dxy":            fmt(dxy["price"], 2) if dxy else "n/a",
        "dxy_dist":       fmt(115 - dxy["price"], 2) if dxy else "n/a",
        "dxy_signal":     ("TAILWIND" if dxy["price"] < 100 else "NEUTRAL" if dxy["price"] < 105 else "WATCH" if dxy["price"] < 115 else "INVALIDATION") if dxy else "n/a",
        "dxy_chg_1d":     fmt(dxy["pts_1d"], 2, suffix="pts") if dxy else "n/a",
        "dxy_chg_4w":     fmt(dxy["pts_4w"], 2, suffix="pts") if dxy and dxy["pts_4w"] is not None else "n/a",
        "dxy_weeks_to_inv": fmt(
            (115 - dxy["price"]) / (dxy["pts_4w"] / 4), 0, suffix=" weeks to invalidation"
        ) if dxy and dxy.get("pts_4w") and dxy["pts_4w"] > 0 else (
            "moving away from invalidation" if dxy and dxy.get("pts_4w") and dxy["pts_4w"] < 0 else "n/a"
        ),
        "gold_px":        fmt(gold["price"], 2, prefix="$") if gold else "n/a",
        "gold_1m":        _f(gold,   "chg_1m", suffix="%"),
        "gold_3m":        _f(gold,   "chg_3m", suffix="%"),
        "gold_dd":        _f(gold,   "dd_52w",  suffix="%"),
        "silver_px":      fmt(silver["price"], 2, prefix="$") if silver else "n/a",
        "silver_1m":      _f(silver, "chg_1m", suffix="%"),
        "silver_3m":      _f(silver, "chg_3m", suffix="%"),
        "silver_dd":      _f(silver, "dd_52w",  suffix="%"),
        "gs":             fmt(gs_ratio, 1) if gs_ratio else "n/a",
        "gs_dist_deploy": fmt(gs_ratio - 55, 1) if gs_ratio else "n/a",
        "gs_dist_inv":    fmt(90 - gs_ratio, 1) if gs_ratio else "n/a",
        "gs_chg_1d":      fmt(gs_chg_1d, 1) if gs_chg_1d is not None else "n/a",
        "gs_chg_4w":      fmt(gs_chg_4w, 1) if gs_chg_4w is not None else "n/a",
        "gs_velocity_label": (
            fmt((gs_ratio - 55) / (-gs_chg_4w / 4), 0, suffix=" weeks to deploy trigger") if gs_chg_4w and gs_chg_4w < 0 and gs_ratio and gs_ratio > 55
            else fmt((90 - gs_ratio) / (gs_chg_4w / 4), 0, suffix=" weeks to invalidation") if gs_chg_4w and gs_chg_4w > 0 and gs_ratio and gs_ratio < 90
            else "n/a"
        ) if gs_ratio and gs_chg_4w else "n/a",
        "cb_gold_ttm":  f"{cb_ttm:+.0f}t" if cb_ttm is not None else "n/a",
        "cb_gold_prev": f"{cb_prev:.0f}t"  if cb_prev is not None else "n/a",
        "cb_gold_yoy":  f"{cb_ttm - cb_prev:+.0f}t" if cb_ttm is not None and cb_prev is not None else "n/a",
        "cb_gold_date": cb_date or "n/a",
        "cb_gold_lag":  f"{cb_lag}d" if cb_lag is not None else "n/a",
        "lly_px":         fmt(lly_px["price"], 2, prefix="$") if lly_px else "n/a",
        "lly_dd":         _f(lly_px, "dd_52w", suffix="%"),
        "lly_rev":        fmt_bn(lly_c["val"]) if lly_c else "n/a",
        "lly_rev_yoy":    fmt(pct(lly_c["val"], lly_p["val"] if lly_p else None), 1, suffix="%") if lly_c else "n/a",
        "lly_rev_date":   lly_c["end"] if lly_c else "n/a",
        "wmt_px":         fmt(wmt_px["price"], 2, prefix="$") if wmt_px else "n/a",
        "wmt_dd":         _f(wmt_px, "dd_52w", suffix="%"),
        "wmt_rev":        fmt_bn(wmt_c["val"]) if wmt_c else "n/a",
        "wmt_rev_yoy":    fmt(pct(wmt_c["val"], wmt_p["val"] if wmt_p else None), 1, suffix="%") if wmt_c else "n/a",
        "wmt_rev_date":   wmt_c["end"] if wmt_c else "n/a",
        "jnj_px":         fmt(jnj_px["price"], 2, prefix="$") if jnj_px else "n/a",
        "jnj_dd":         _f(jnj_px, "dd_52w", suffix="%"),
        "jnj_rev":        fmt_bn(jnj_c["val"]) if jnj_c else "n/a",
        "jnj_rev_yoy":    fmt(pct(jnj_c["val"], jnj_p["val"] if jnj_p else None), 1, suffix="%") if jnj_c else "n/a",
        "jnj_div":        fmt(jnj_div_c["val"], 2, prefix="$") if jnj_div_c else "n/a",
        "jnj_div_yoy":    fmt(pct(jnj_div_c["val"], jnj_div_p["val"] if jnj_div_p else None), 1, suffix="%") if jnj_div_c else "n/a",
        "ccj_px":         fmt(ccj_px["price"], 2, prefix="$") if ccj_px else "n/a",
        "ccj_dd":         _f(ccj_px, "dd_52w", suffix="%"),
        "uranium_lag":    (date.today() - date.fromisoformat(uranium_date)).days if uranium_date else None,
        "uranium":        fmt(uranium, 2, prefix="$", suffix="/lb") + (
            f" (as of {uranium_date}, {(date.today() - date.fromisoformat(uranium_date)).days}d ago — monthly series)"
            if uranium_date else ""
        ) if uranium else "n/a",
        "uranium_dist":   uranium - 50 if uranium else 0,
        "vrt_px":         fmt(vrt_px["price"], 2, prefix="$") if vrt_px else "n/a",
        "vrt_dd":         _f(vrt_px, "dd_52w", suffix="%"),
        "vrt_rev":        fmt_bn(vrt_c["val"]) if vrt_c else "n/a",
        "vrt_rev_yoy":    fmt(pct(vrt_c["val"], vrt_p["val"] if vrt_p else None), 1, suffix="%") if vrt_c else "n/a",
        "vrt_rev_date":   vrt_c["end"] if vrt_c else "n/a",
        "avgo_px":        fmt(avgo_px["price"], 2, prefix="$") if avgo_px else "n/a",
        "avgo_dd":        _f(avgo_px, "dd_52w", suffix="%"),
        "avgo_rev":       fmt_bn(avgo_c["val"]) if avgo_c else "n/a",
        "avgo_rev_yoy":   fmt(pct(avgo_c["val"], avgo_p["val"] if avgo_p else None), 1, suffix="%") if avgo_c else "n/a",
        "avgo_rev_date":  avgo_c["end"] if avgo_c else "n/a",
        "nvda_rev":       fmt_bn(nvda_c["val"]) if nvda_c else "n/a",
        "nvda_rev_yoy":   fmt(pct(nvda_c["val"], nvda_p["val"] if nvda_p else None), 1, suffix="%") if nvda_c else "n/a",
        "nvda_rev_date":  nvda_c["end"] if nvda_c else "n/a",
        "capex":          fmt_bn(capex_total) if capex_total else "n/a",
        "capex_yoy":      fmt(pct(capex_total, capex_total_prev), 1, suffix="%") if capex_total else "n/a",
        "capex_date":     max(c["end"] for c in [msft_c, googl_c, amzn_c, meta_c] if c) if any([msft_c, googl_c, amzn_c, meta_c]) else "n/a",
    }

    print("Calling Claude...")
    interpretation = get_interpretation(facts)

    # Build output
    lines = []
    lines.append("=" * 68)
    lines.append(f"  THESIS PULSE  |  {today}")
    lines.append("=" * 68)
    lines.append("")
    lines.append(interpretation)
    lines.append("")
    lines.append("=" * 68)
    lines.append("  RAW DATA")
    lines.append("=" * 68)
    lines.append("")
    lines.append("  MACRO")
    lines.append(f"  {'-'*64}")
    ry_signal = facts["ry_signal"]
    ry_dist   = facts["ry_dist"]
    lines.append(f"  10Y Real Yield    {fmt(ry_val, 2, suffix='%'):<12}  (as of {ry_date})  {ry_dist}bps to 3.0%  [{ry_signal}]")
    lines.append(f"  velocity          {facts['ry_chg_1d']} today  |  {facts['ry_chg_4w']} over 4wk  |  {facts['ry_weeks_to_inv']}")
    dxy_signal = facts["dxy_signal"]
    dxy_dist   = facts["dxy_dist"]
    lines.append(f"  DXY               {fmt(dxy['price'], 2) if dxy else 'n/a':<12}  "
                 f"1d {fmt(dxy['chg_1d'],1,suffix='%') if dxy else 'n/a'}  "
                 f"{dxy_dist}pts to 115  [{dxy_signal}]")
    lines.append(f"  velocity          {facts['dxy_chg_1d']} today  |  {facts['dxy_chg_4w']} over 4wk  |  {facts['dxy_weeks_to_inv']}")
    lines.append("")
    lines.append("  HEDGES")
    lines.append(f"  {'-'*64}")
    lines.append(f"  Gold              {fmt_px(gold)}")
    lines.append(f"  Silver            {fmt_px(silver)}")
    lines.append(f"  G/S Ratio         {fmt(gs_ratio, 1):<12}  (deploy trigger <55)")
    lines.append(f"  velocity          {facts['gs_chg_1d']} today  |  {facts['gs_chg_4w']} over 4wk  |  {facts['gs_velocity_label']}")
    if cb_ttm is not None:
        lines.append(f"  CB Gold demand    {cb_ttm:+.0f}t TTM net  "
                     f"(vs {cb_prev:.0f}t prior yr  |  {cb_ttm - cb_prev:+.0f}t YoY)  "
                     f"as of {cb_date}  ({cb_lag}d lag, WGC/IFS monthly)")
    else:
        lines.append("  CB Gold demand    n/a  (set WGC_AUTH_* secrets — monthly series)")
    lines.append("")
    lines.append("  CARRY")
    lines.append(f"  {'-'*64}")
    lines.append(f"  LLY price         {fmt_px(lly_px)}")
    if lly_c:
        lines.append(f"  LLY revenue       {fmt_bn(lly_c['val'])}  "
                     f"{fmt(pct(lly_c['val'], lly_p['val'] if lly_p else None), 1, suffix='%')} YoY  "
                     f"({lly_c['end']} {lly_c['fp']})")
    lines.append(f"  WMT price         {fmt_px(wmt_px)}")
    if wmt_c:
        lines.append(f"  WMT revenue       {fmt_bn(wmt_c['val'])}  "
                     f"{fmt(pct(wmt_c['val'], wmt_p['val'] if wmt_p else None), 1, suffix='%')} YoY  "
                     f"({wmt_c['end']} {wmt_c['fp']})")
    lines.append(f"  JNJ price         {fmt_px(jnj_px)}")
    if jnj_c:
        lines.append(f"  JNJ revenue       {fmt_bn(jnj_c['val'])}  "
                     f"{fmt(pct(jnj_c['val'], jnj_p['val'] if jnj_p else None), 1, suffix='%')} YoY  "
                     f"({jnj_c['end']} {jnj_c['fp']})")
    if jnj_div_c:
        lines.append(f"  JNJ div/share     ${jnj_div_c['val']:.2f}  "
                     f"{fmt(pct(jnj_div_c['val'], jnj_div_p['val'] if jnj_div_p else None), 1, suffix='%')} YoY  "
                     f"({jnj_div_c['end']} {jnj_div_c['fp']})")
    lines.append("")
    lines.append("  CYCLICAL")
    lines.append(f"  {'-'*64}")
    lines.append(f"  CCJ price         {fmt_px(ccj_px)}")
    uranium_mom = fmt(pct(uranium, uranium_prev), 1, suffix="%") if uranium and uranium_prev else "n/a"
    lines.append(f"  Uranium (IMF/FRED) {fmt(uranium, 2, prefix='$', suffix='/lb') if uranium else 'n/a'}"
                 f"  1m {uranium_mom}"
                 + (f"  (as of {uranium_date}, monthly)" if uranium_date else ""))
    lines.append("")
    lines.append("  CONVEXITY")
    lines.append(f"  {'-'*64}")
    lines.append(f"  VRT price         {fmt_px(vrt_px)}")
    if vrt_c:
        lines.append(f"  VRT revenue       {fmt_bn(vrt_c['val'])}  "
                     f"{fmt(pct(vrt_c['val'], vrt_p['val'] if vrt_p else None), 1, suffix='%')} YoY  "
                     f"({vrt_c['end']} {vrt_c['fp']})")
    lines.append(f"  AVGO price        {fmt_px(avgo_px)}")
    if avgo_c:
        lines.append(f"  AVGO revenue      {fmt_bn(avgo_c['val'])}  "
                     f"{fmt(pct(avgo_c['val'], avgo_p['val'] if avgo_p else None), 1, suffix='%')} YoY  "
                     f"({avgo_c['end']} {avgo_c['fp']})")
    if nvda_c:
        lines.append(f"  NVDA revenue      {fmt_bn(nvda_c['val'])}  "
                     f"{fmt(pct(nvda_c['val'], nvda_p['val'] if nvda_p else None), 1, suffix='%')} YoY  "
                     f"({nvda_c['end']} {nvda_c['fp']})")
    if capex_total:
        lines.append(f"  Hyperscaler capex {fmt_bn(capex_total)}  "
                     f"{fmt(pct(capex_total, capex_total_prev), 1, suffix='%')} YoY")
        for ticker, c, p in [("MSFT",msft_c,msft_p),("GOOGL",googl_c,googl_p),
                              ("AMZN",amzn_c,amzn_p),("META",meta_c,meta_p)]:
            if c:
                lines.append(f"    {ticker:<6} {fmt_bn(c['val'])}  "
                             f"{fmt(pct(c['val'], p['val'] if p else None), 1, suffix='%')} YoY  "
                             f"({c['end']})")
    lines.append("")
    lines.append("=" * 68)

    body = "\n".join(lines)
    print(body)

    # Extract OVERALL line for subject
    overall = next((l for l in lines if l.startswith("OVERALL:")), "INTACT")
    status  = overall.split("—")[0].replace("OVERALL:", "").strip()
    subject = f"Thesis Pulse {today} [{status}]"
    send_email(subject, body)


if __name__ == "__main__":
    main()
