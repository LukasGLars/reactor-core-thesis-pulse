# -*- coding: utf-8 -*-
"""
Reactor Core Thesis Pulse v2.0
Daily thesis monitoring for 8-position portfolio.
Runs via GitHub Actions — sends email with raw data + recession tracker.
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
WGC_EMAIL         = os.environ.get("WGC_EMAIL", "")
WGC_PASSWORD      = os.environ.get("WGC_PASSWORD", "")
# Legacy cookie fallback (kept for local dev — login is preferred in Actions)
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

# ── CENTRAL BANK GOLD (IMF IFS primary, WGC cookie fallback) ───────────────
def imf_central_bank_gold():
    """
    Query IMF IFS series RAFAGOLDV (monthly gold holdings, fine troy oz) for all countries.
    Computes world net purchase TTM vs prior 12m — same underlying data as WGC.
    No auth required. Returns (ttm_tonnes, prev_ttm_tonnes, latest_date_str, lag_days).
    """
    TROY_OZ_PER_TONNE = 32150.75
    # Try HTTPS SDMX first, then data.imf.org JSON API
    candidates = [
        "https://dataservices.imf.org/REST/SDMX_JSON.svc/CompactData/IFS/M..RAFAGOLDV?startPeriod=2022-01",
        "https://data.imf.org/api/SDMX/1.0/rest/data/IFS/M..RAFAGOLDV?startPeriod=2022-01&format=jsondata",
    ]
    r = None
    for url in candidates:
        try:
            resp = requests.get(url, headers={"User-Agent": "thesis-pulse/1.0"}, timeout=60)
            if resp.status_code == 200:
                r = resp
                break
            print(f"  IMF gold: {url[30:70]} -> {resp.status_code}")
        except Exception as ex:
            print(f"  IMF gold: {url[30:70]} -> {type(ex).__name__}")
    if r is None:
        return None, None, None, None
    try:
        raw = r.json()
        levels = {}  # {country: {period: float}}

        # Format A: classic SDMX_JSON (dataservices.imf.org)
        if "CompactData" in raw:
            series_raw = raw["CompactData"]["DataSet"].get("Series", [])
            if isinstance(series_raw, dict):
                series_raw = [series_raw]
            for s in series_raw:
                country = s.get("@REF_AREA", "")
                obs = s.get("Obs", [])
                if isinstance(obs, dict):
                    obs = [obs]
                for o in obs:
                    period = o.get("@TIME_PERIOD", "")
                    val_s  = o.get("@OBS_VALUE")
                    if val_s is None:
                        continue
                    try:
                        levels.setdefault(country, {})[period] = float(val_s)
                    except ValueError:
                        pass

        # Format B: SDMX 2.1 jsondata (data.imf.org)
        elif "data" in raw:
            ds = raw["data"]
            # dimensions order: FREQ, REF_AREA, INDICATOR, ...
            dim_ids = [d["id"] for d in ds.get("structure", {}).get("dimensions", {}).get("series", [])]
            ref_area_idx = dim_ids.index("REF_AREA") if "REF_AREA" in dim_ids else 1
            time_periods  = [p["id"] for p in ds.get("structure", {}).get("dimensions", {}).get("observation", [{}])[0].get("values", [])]
            area_values   = ds.get("structure", {}).get("dimensions", {}).get("series", [{}])[ref_area_idx].get("values", [])
            for key_str, obs_dict in ds.get("dataSets", [{}])[0].get("series", {}).items():
                parts = key_str.split(":")
                area_idx = int(parts[ref_area_idx]) if len(parts) > ref_area_idx else 0
                country = area_values[area_idx]["id"] if area_idx < len(area_values) else key_str
                for t_idx_s, val_list in obs_dict.get("observations", {}).items():
                    t_idx = int(t_idx_s)
                    val   = val_list[0] if val_list else None
                    if val is None or t_idx >= len(time_periods):
                        continue
                    try:
                        levels.setdefault(country, {})[time_periods[t_idx]] = float(val)
                    except (TypeError, ValueError):
                        pass

        if not levels:
            return None, None, None, None

        # All periods in ascending order
        all_periods = sorted(set(p for c in levels.values() for p in c))
        if len(all_periods) < 14:
            return None, None, None, None

        # Monthly world net change: sum(level[t] - level[t-1]) across all countries
        monthly_change = {}
        for i in range(1, len(all_periods)):
            t0, t1 = all_periods[i-1], all_periods[i]
            net = 0.0
            for cdata in levels.values():
                v0 = cdata.get(t0)
                v1 = cdata.get(t1)
                if v0 is not None and v1 is not None:
                    net += v1 - v0
            monthly_change[t1] = net

        periods = sorted(monthly_change)
        if len(periods) < 13:
            return None, None, None, None

        ttm_periods  = periods[-12:]
        prev_periods = periods[-24:-12]
        latest_date  = ttm_periods[-1]

        ttm_oz  = sum(monthly_change[p] for p in ttm_periods)
        prev_oz = sum(monthly_change[p] for p in prev_periods) if len(prev_periods) == 12 else None

        ttm_t  = round(ttm_oz  / TROY_OZ_PER_TONNE, 0)
        prev_t = round(prev_oz / TROY_OZ_PER_TONNE, 0) if prev_oz is not None else None

        from datetime import date as _date
        lag = (_date.today() - _date.fromisoformat(latest_date + "-01")).days

        return ttm_t, prev_t, latest_date, lag

    except Exception as e:
        print(f"  IMF gold error: {e}")
        return None, None, None, None


def _wgc_login():
    """
    Log in to user.gold.org and return a requests.Session with auth cookies set.
    Uses WGC_EMAIL + WGC_PASSWORD env vars (primary) or legacy cookie env vars (fallback).
    Returns session on success, None on failure.
    """
    import re as _re
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124",
        "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer":    "https://www.gold.org/",
    }

    # ── primary: automated login ──────────────────────────────
    if WGC_EMAIL and WGC_PASSWORD:
        s = requests.Session()
        try:
            # GET login page — captures XSRF-TOKEN + wgcAuth_session
            r = s.get("https://user.gold.org/login", headers=hdrs, timeout=15)
            if r.status_code != 200:
                print(f"  WGC login GET: {r.status_code}")
                return None
            token = _re.search(r'name="_token"\s+value="([^"]+)"', r.text)
            if not token:
                print("  WGC login: _token not found")
                return None
            # POST credentials
            payload = {
                "_token":   token.group(1),
                "ema":      WGC_EMAIL,
                "password": WGC_PASSWORD,
                "remember": "1",
                "log":      "1",
            }
            post_hdrs = dict(hdrs)
            post_hdrs.update({
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer":      "https://user.gold.org/login",
                "Origin":       "https://user.gold.org",
            })
            r2 = s.post("https://user.gold.org/log", data=payload,
                        headers=post_hdrs, timeout=15, allow_redirects=True)
            # Success: session now has wgcApiAuth_cookie + wgcAuth_cookie
            if "wgcApiAuth_cookie" in s.cookies or r2.status_code in (200, 302):
                print("  WGC login: OK")
                return s
            print(f"  WGC login POST: {r2.status_code} — auth cookie not set")
            return None
        except Exception as e:
            print(f"  WGC login error: {e}")
            return None

    # ── fallback: legacy static cookies ──────────────────────
    if WGC_AUTH_SESSION:
        s = requests.Session()
        s.cookies.set("wgcApiAuth_cookie", WGC_API_AUTH,  domain=".gold.org")
        s.cookies.set("wgcAuth_cookie",    WGC_AUTH_COOKIE, domain=".gold.org")
        s.cookies.set("wgcAuth_session",   WGC_AUTH_SESSION, domain=".gold.org")
        s.cookies.set("XSRF-TOKEN",        WGC_XSRF,      domain=".gold.org")
        return s

    return None


def wgc_central_banks():
    """
    Download WGC monthly central bank gold changes (IFS source, ~2-month lag).
    Logs in via WGC_EMAIL/WGC_PASSWORD (auto-refresh) or falls back to static cookies.
    Returns (ttm_tonnes, prev_ttm_tonnes, latest_date_str, lag_days).
    """
    import io
    try:
        import openpyxl
    except ImportError:
        print("  openpyxl not installed — skipping WGC")
        return None, None, None, None
    from datetime import date as _date

    session = _wgc_login()
    if session is None:
        return None, None, None, None

    _MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    hdrs = {
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
            r = session.get(url, headers=hdrs, timeout=30)
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

        last_ci = date_cols[0][0]
        for ci, _ in date_cols:
            has_data = any(
                isinstance(row[ci], (int, float)) and row[ci] != 0
                for row in all_rows if len(row) > ci
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

# ── OIL TERM SPREAD ────────────────────────────────────────
def get_oil_term_spread():
    """
    WTI spot (FRED DCOILWTICO) vs 12-month forward (Yahoo dynamic contract).
    Positive spread = backwardation = physical supply stress.
    Thesis signal: spread >$20 STRESS | $10-20 ELEVATED | $0-10 NORMAL | <$0 CONTANGO.
    """
    spot, _, spot_date = fred_latest("DCOILWTICO")

    MONTH_CODES = "FGHJKMNQUVXZ"
    today = date.today()
    target_month = today.month
    target_year  = today.year + 1
    code = MONTH_CODES[target_month - 1]
    fwd_ticker = f"CL{code}{str(target_year)[2:]}.NYM"

    fwd = None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{fwd_ticker}?interval=1d&range=5d"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            fwd = closes[-1] if closes else None
    except Exception:
        pass

    spread = round(spot - fwd, 2) if spot and fwd else None
    return spot, fwd, spread, spot_date, fwd_ticker

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
    if isinstance(val, dict):
        val = val.get("val")
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

CARRY:
- LLY: price {facts['lly_px']} ({facts['lly_dd']} from 52wH) | revenue {facts['lly_rev']} {facts['lly_rev_yoy']} YoY ({facts['lly_rev_date']})
- WMT: price {facts['wmt_px']} ({facts['wmt_dd']} from 52wH) | revenue {facts['wmt_rev']} {facts['wmt_rev_yoy']} YoY ({facts['wmt_rev_date']})
- JNJ: price {facts['jnj_px']} ({facts['jnj_dd']} from 52wH) | revenue {facts['jnj_rev']} {facts['jnj_rev_yoy']} YoY | div/share {facts['jnj_div']} {facts['jnj_div_yoy']} YoY

CYCLICAL:
- CCJ: price {facts['ccj_px']} ({facts['ccj_dd']} from 52wH)
- Uranium: {facts['uranium']} (monthly IMF series, {facts['uranium_lag']}d lag — treat as directional, not spot). Invalidation threshold: <$50/lb — currently ${facts['uranium_dist']:.2f} above.
- Oil term spread (WTI spot vs 12M forward): {facts['oil_spread']} backwardation | spot {facts['oil_spot']} (as of {facts['oil_spot_date']}) | 12M fwd {facts['oil_fwd']} ({facts['oil_fwd_ticker']}) [{facts['oil_signal']}]
  Interpretation: spread narrows on physical fall = thesis weakening | spread narrows on futures rise = thesis strengthening | spread stays wide (>$20) = supply stress intact

CONVEXITY:
- VRT: price {facts['vrt_px']} ({facts['vrt_dd']} from 52wH) | revenue {facts['vrt_rev']} {facts['vrt_rev_yoy']} YoY (as of {facts['vrt_rev_date']})
- AVGO: price {facts['avgo_px']} ({facts['avgo_dd']} from 52wH) | revenue {facts['avgo_rev']} {facts['avgo_rev_yoy']} YoY (as of {facts['avgo_rev_date']})
- NVDA revenue: {facts['nvda_rev']} {facts['nvda_rev_yoy']} YoY (as of {facts['nvda_rev_date']}, AI spend proxy)
- Hyperscaler capex: {facts['capex']} {facts['capex_yoy']} YoY (as of {facts['capex_date']}, invalidation threshold: <-30% YoY)

INSTRUCTIONS:
- Use ONLY the pre-computed facts above. Do not recalculate or restate raw numbers beyond what is given.
- Be direct and clinical. No filler. Each section max 2-3 sentences.
- Oil term spread belongs in CYCLICAL only. Do not reference it in CONVEXITY or any other section. You MUST mention it in the CYCLICAL output section.
- Real yield direction: falling real yield = moving AWAY from 3.0% invalidation = favorable. Rising real yield = moving TOWARD invalidation = unfavorable. Never describe a falling real yield as "approaching invalidation."
- Price moves alone are never invalidation — always tie to thesis conditions.
- Write only what the numbered facts above directly show. Every clause must trace back to a specific fact value.
- Do not comment on conditions, risks, or events that have no corresponding fact — not even to say they are absent, unconfirmed, or not contradicted. If there is no fact for it, it does not appear in your output.
- Do not restate or paraphrase thesis rationale, structural arguments, or position logic from the thesis document. Use the thesis document only to know what each number means, not as content to quote or summarize.

OUTPUT FORMAT (use exactly these headers, no deviations):

OVERALL: [INTACT | ONE FLAG | REVIEW NEEDED] — [one-line reason]

MACRO: [2 sentences. Are macro conditions favorable, neutral, or headwind for the thesis?]

HEDGES: [2-3 sentences covering gold and silver. Thesis intact? G/S ratio context?]

CARRY: [2-3 sentences covering LLY, WMT, JNJ. Revenue trends vs thesis requirements?]

CYCLICAL: [2-3 sentences. Uranium thesis intact? Include oil term spread signal and its current reading.]

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

# ── STALE FLAG ─────────────────────────────────────────────
def stale_flag(date_str):
    if not date_str:
        return ""
    try:
        d = date.fromisoformat(str(date_str)[:10])
        delta = (date.today() - d).days
        return f"  S-{delta}D" if delta > 0 else ""
    except Exception:
        return ""


# ── FRED LAST N ────────────────────────────────────────────
def fred_last_n(series_id, n=15):
    session = _make_session()
    for attempt in range(1, 4):
        try:
            r = session.get(fred_url(series_id),
                            headers={"User-Agent": "thesis-pulse/1.0"}, timeout=30)
            r.raise_for_status()
            obs = r.json().get("observations", [])
            rows = [(o["date"], float(o["value"])) for o in obs
                    if o.get("value") not in (".", "")]
            return rows[-n:] if rows else []
        except Exception:
            time.sleep(0.5 * (2 ** (attempt - 1)))
    return []


# ── CAPE SCRAPER ───────────────────────────────────────────
def get_cape():
    try:
        import re
        r = requests.get("https://www.multpl.com/shiller-pe",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            m = re.search(r'<div[^>]+id=["\']current-value["\'][^>]*>\s*([0-9]+(?:\.[0-9]+)?)', r.text)
            if m:
                return float(m.group(1))
    except Exception:
        pass
    return None


# ── RECESSION TRACKER ──────────────────────────────────────
def compute_recession_signals(ry_val, ry_date):
    import json as _json
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "calibration", "recession_config.json")
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = _json.load(f)
    except Exception:
        return None
    ind_cfg = cfg["indicators"]
    probs   = cfg["composite_probabilities"]
    results = {}

    # T10Y3M
    val, _, dt = fred_latest("T10Y3M")
    results["T10Y3M"] = {
        "val": f"{val:.2f}" if val is not None else "n/a",
        "thr": "0.0",
        "sig": 1 if val is not None and val < 0 else 0,
    }
    # T10Y2Y
    val, _, dt = fred_latest("T10Y2Y")
    results["T10Y2Y"] = {
        "val": f"{val:.2f}" if val is not None else "n/a",
        "thr": "0.0",
        "sig": 1 if val is not None and val < 0 else 0,
    }
    # DFII10 — reuse fetched ry_val
    results["DFII10"] = {
        "val": f"{ry_val:.2f}" if ry_val is not None else "n/a",
        "thr": "1.0",
        "sig": 1 if ry_val is not None and ry_val > 1.0 else 0,
    }
    # ICSA
    val, _, dt = fred_latest("ICSA")
    thr = ind_cfg["ICSA"]["threshold_val"]
    results["ICSA"] = {
        "val": f"{val/1000:.1f}K" if val is not None else "n/a",
        "thr": f"{thr/1000:.0f}.0K",
        "sig": 1 if val is not None and val > thr else 0,
    }
    # UMCSENT
    val, _, dt = fred_latest("UMCSENT")
    thr = ind_cfg["UMCSENT"]["threshold_val"]
    results["UMCSENT"] = {
        "val": f"{val:.1f}" if val is not None else "n/a",
        "thr": f"{thr:.0f}.0",
        "sig": 1 if val is not None and val < thr else 0,
    }
    # INDPRO — 3 consecutive monthly declines
    rows = fred_last_n("INDPRO", n=6)
    sig = 0
    if len(rows) >= 4:
        recent = [v for _, v in rows[-4:]]
        sig = 1 if all(recent[i] < recent[i-1] for i in range(1, len(recent))) else 0
    results["INDPRO"] = {
        "val": f"{rows[-1][1]:.1f}" if rows else "n/a",
        "thr": "3mo↓",
        "sig": sig,
    }
    # MANEMP — 3 consecutive monthly declines
    rows = fred_last_n("MANEMP", n=6)
    sig = 0
    if len(rows) >= 4:
        recent = [v for _, v in rows[-4:]]
        sig = 1 if all(recent[i] < recent[i-1] for i in range(1, len(recent))) else 0
    results["MANEMP"] = {
        "val": f"{rows[-1][1]/1000:.2f}M" if rows else "n/a",
        "thr": "3mo↓",
        "sig": sig,
    }
    # PCEPILFE — YoY > 2.0%
    rows = fred_last_n("PCEPILFE", n=14)
    sig = 0
    yoy_str = "n/a"
    if len(rows) >= 13:
        curr_v, prev_v = rows[-1][1], rows[-13][1]
        yoy = (curr_v - prev_v) / prev_v * 100 if prev_v else 0
        yoy_str = f"{yoy:.1f}%"
        sig = 1 if yoy > ind_cfg["PCEPILFE"]["threshold_val"] else 0
    results["PCEPILFE"] = {"val": yoy_str, "thr": "2.0%", "sig": sig}
    # DFF — fed cutting 3 consecutive months (use FEDFUNDS monthly)
    val_dff, _, dt_dff = fred_latest("DFF")
    rows = fred_last_n("FEDFUNDS", n=6)
    sig = 0
    if len(rows) >= 4:
        recent = [v for _, v in rows[-4:]]
        sig = 1 if all(recent[i] < recent[i-1] for i in range(1, len(recent))) else 0
    results["DFF"] = {
        "val": f"{val_dff:.2f}%" if val_dff is not None else "n/a",
        "thr": "cut 3mo",
        "sig": sig,
    }
    # VIXCLS
    val, _, dt = fred_latest("VIXCLS")
    thr = ind_cfg["VIXCLS"]["threshold_val"]
    results["VIXCLS"] = {
        "val": f"{val:.1f}" if val is not None else "n/a",
        "thr": f"{thr:.0f}.0",
        "sig": 1 if val is not None and val > thr else 0,
    }
    # SP500 — below 10-month MA
    rows = fred_last_n("SP500", n=12)
    sig = 0
    thr_str = "<10mo MA"
    if rows:
        sp_val = rows[-1][1]
        val_str = f"{sp_val:.0f}"
        if len(rows) >= 10:
            ma10 = sum(v for _, v in rows[-10:]) / 10
            thr_str = f"<{ma10:.0f}"
            sig = 1 if sp_val < ma10 else 0
    else:
        val_str = "n/a"
    results["SP500"] = {"val": val_str if rows else "n/a", "thr": thr_str, "sig": sig}
    # BAA_AAA spread
    baa, _, _ = fred_latest("BAA")
    aaa, _, _ = fred_latest("AAA")
    spread = round(baa - aaa, 2) if baa is not None and aaa is not None else None
    thr = ind_cfg["CREDIT_SPREAD"]["threshold_val"]
    results["BAA_AAA_spread"] = {
        "val": f"{spread:.2f}%" if spread is not None else "n/a",
        "thr": f"{thr}%",
        "sig": 1 if spread is not None and spread > thr else 0,
    }
    # CAPE
    cape = get_cape()
    thr = ind_cfg["CAPE"]["threshold_val"]
    results["CAPE"] = {
        "val": f"{cape:.1f}" if cape is not None else "n/a",
        "thr": f"{thr:.0f}.0",
        "sig": 1 if cape is not None and cape > thr else 0,
    }

    composite = sum(r["sig"] for r in results.values())
    prob = probs.get(str(min(composite, 10)), {})
    return {
        "indicators": results,
        "composite":  composite,
        "p6m":        prob.get("p6m",  0),
        "p12m":       prob.get("p12m", 0),
    }


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

    print("Fetching oil term spread...")
    oil_spot, oil_fwd, oil_spread, oil_spot_date, oil_fwd_ticker = get_oil_term_spread()

    print("Fetching recession indicators...")
    rec = compute_recession_signals(ry_val, ry_date)

    # Compute derived values
    gs_ratio         = gold["price"] / silver["price"] if gold and silver else None
    gs_ratio_1d_ago  = (gold["price"] - gold["pts_1d"]) / (silver["price"] - silver["pts_1d"]) if gold and silver and gold.get("pts_1d") and silver.get("pts_1d") else None
    gs_ratio_4w_ago  = (gold["price"] - gold["pts_4w"]) / (silver["price"] - silver["pts_4w"]) if gold and silver and gold.get("pts_4w") and silver.get("pts_4w") else None
    gs_chg_1d        = gs_ratio - gs_ratio_1d_ago if gs_ratio and gs_ratio_1d_ago else None
    gs_chg_4w        = gs_ratio - gs_ratio_4w_ago if gs_ratio and gs_ratio_4w_ago else None
    capex_vals       = [x["val"] for x in [msft_c, googl_c, amzn_c, meta_c] if x]
    capex_prevs      = [x["val"] for x in [msft_p, googl_p, amzn_p, meta_p] if x]
    capex_total      = sum(capex_vals)  if capex_vals  else None
    capex_total_prev = sum(capex_prevs) if capex_prevs else None

    # Velocity strings
    ry_chg_1d = fmt((ry_val - ry_prev) * 100, 1, suffix="bps") if ry_val and ry_prev else "n/a"
    ry_chg_4w = fmt((ry_val - ry_4w)   * 100, 1, suffix="bps") if ry_val and ry_4w   else "n/a"
    dxy_chg_1d = fmt(dxy["pts_1d"], 2, suffix="pts") if dxy else "n/a"
    dxy_chg_4w = fmt(dxy["pts_4w"], 2, suffix="pts") if dxy and dxy["pts_4w"] is not None else "n/a"
    dxy_pts_1d = dxy["pts_1d"] if dxy else None
    dxy_pts_4w = dxy["pts_4w"] if dxy else None

    ry_dist  = int(round(300 - ry_val * 100)) if ry_val is not None else "n/a"
    dxy_dist = round(115 - dxy["price"], 2) if dxy else None
    dxy_price = dxy["price"] if dxy else None
    dxy_chg_1d_pct = dxy["chg_1d"] if dxy else None

    uranium_mom = fmt(pct(uranium, uranium_prev), 1) if uranium and uranium_prev else "n/a"
    capex_yoy = fmt(pct(capex_total, capex_total_prev), 1) if capex_total and capex_total_prev else "n/a"

    # Build output
    lines = []
    lines.append("=" * 68)
    lines.append(f"  THESIS PULSE  |  {today}")
    lines.append("=" * 68)
    lines.append("")

    # MACRO
    lines.append("  MACRO")
    lines.append(f"  {'-'*64}")
    if ry_val is not None:
        lines.append(f"  10Y Real Yield    {ry_val:.2f}%         {ry_dist}bps to 3.0%{stale_flag(ry_date)}")
        lines.append(f"  velocity          {ry_chg_1d} today  |  {ry_chg_4w} over 4wk")
    else:
        lines.append(f"  10Y Real Yield    n/a")
        lines.append(f"  velocity          n/a")
    if dxy_price is not None:
        lines.append(f"  DXY               {dxy_price:.2f}         1d {dxy_chg_1d_pct:+.1f}%  {dxy_dist:.2f}pts to 115")
        lines.append(f"  velocity          {dxy_pts_1d:+.2f}pts today  |  {dxy_pts_4w:+.2f}pts over 4wk" if dxy_pts_1d is not None and dxy_pts_4w is not None else "  velocity          n/a")
    else:
        lines.append(f"  DXY               n/a")
        lines.append(f"  velocity          n/a")
    if oil_spread is not None:
        lines.append(f"  Oil term spread   ${oil_spread:.1f}/bbl  spot ${oil_spot:.1f}  12M fwd ${oil_fwd:.1f}{stale_flag(oil_spot_date)}")
    else:
        lines.append(f"  Oil term spread   n/a")
    lines.append("")

    # HEDGES
    lines.append("  HEDGES")
    lines.append(f"  {'-'*64}")
    lines.append(f"  Gold              {fmt_px(gold)}")
    lines.append(f"  Silver            {fmt_px(silver)}")
    if gs_ratio is not None:
        dist_t1 = 83.36 - gs_ratio
        dist_t2 = 86.45 - gs_ratio
        lines.append(f"  GSR               {gs_ratio:.1f}    {dist_t1:+.1f}pts to 83.36 (T1)  {dist_t2:+.1f}pts to 86.45 (T2)")
    else:
        lines.append(f"  GSR               n/a")
    lines.append("")

    # CARRY
    lines.append("  CARRY")
    lines.append(f"  {'-'*64}")
    lines.append(f"  LLY               {fmt_px(lly_px)}")
    if lly_c:
        lly_yoy = fmt(pct(lly_c["val"], lly_p["val"] if lly_p else None), 1)
        lines.append(f"  LLY revenue       {fmt_bn(lly_c['val'])}  {lly_yoy}% YoY  ({lly_c['end']}){stale_flag(lly_c['end'])}")
    lines.append(f"  WMT               {fmt_px(wmt_px)}")
    if wmt_c:
        wmt_yoy = fmt(pct(wmt_c["val"], wmt_p["val"] if wmt_p else None), 1)
        lines.append(f"  WMT revenue       {fmt_bn(wmt_c['val'])}  {wmt_yoy}% YoY  ({wmt_c['end']}){stale_flag(wmt_c['end'])}")
    lines.append(f"  JNJ               {fmt_px(jnj_px)}")
    if jnj_c:
        jnj_yoy = fmt(pct(jnj_c["val"], jnj_p["val"] if jnj_p else None), 1)
        lines.append(f"  JNJ revenue       {fmt_bn(jnj_c['val'])}  {jnj_yoy}% YoY  ({jnj_c['end']}){stale_flag(jnj_c['end'])}")
    if jnj_div_c:
        jnj_div_yoy = fmt(pct(jnj_div_c["val"], jnj_div_p["val"] if jnj_div_p else None), 1)
        lines.append(f"  JNJ div/share     ${jnj_div_c['val']:.2f}  {jnj_div_yoy}% YoY  ({jnj_div_c['end']}){stale_flag(jnj_div_c['end'])}")
    lines.append("")

    # CYCLICAL
    lines.append("  CYCLICAL")
    lines.append(f"  {'-'*64}")
    lines.append(f"  CCJ               {fmt_px(ccj_px)}")
    lines.append(f"  Uranium           ${uranium:.2f}/lb  1m {uranium_mom}%{stale_flag(uranium_date)}" if uranium is not None else "  Uranium           n/a")
    lines.append("")

    # CONVEXITY
    lines.append("  CONVEXITY")
    lines.append(f"  {'-'*64}")
    lines.append(f"  VRT               {fmt_px(vrt_px)}")
    if vrt_c:
        vrt_yoy = fmt(pct(vrt_c["val"], vrt_p["val"] if vrt_p else None), 1)
        lines.append(f"  VRT revenue       {fmt_bn(vrt_c['val'])}  {vrt_yoy}% YoY  ({vrt_c['end']}){stale_flag(vrt_c['end'])}")
    lines.append(f"  AVGO              {fmt_px(avgo_px)}")
    if avgo_c:
        avgo_yoy = fmt(pct(avgo_c["val"], avgo_p["val"] if avgo_p else None), 1)
        lines.append(f"  AVGO revenue      {fmt_bn(avgo_c['val'])}  {avgo_yoy}% YoY  ({avgo_c['end']}){stale_flag(avgo_c['end'])}")
    if nvda_c:
        nvda_yoy = fmt(pct(nvda_c["val"], nvda_p["val"] if nvda_p else None), 1)
        lines.append(f"  NVDA revenue      {fmt_bn(nvda_c['val'])}  {nvda_yoy}% YoY  ({nvda_c['end']}){stale_flag(nvda_c['end'])}")
    if capex_total:
        lines.append(f"  Hyperscaler capex {fmt_bn(capex_total)}  {capex_yoy}% YoY")
        for ticker, c, p in [("MSFT", msft_c, msft_p), ("GOOGL", googl_c, googl_p),
                              ("AMZN", amzn_c, amzn_p), ("META",  meta_c,  meta_p)]:
            if c:
                c_yoy = fmt(pct(c["val"], p["val"] if p else None), 1)
                lines.append(f"    {ticker:<6}           {fmt_bn(c['val'])}  {c_yoy}% YoY  ({c['end']}){stale_flag(c['end'])}")
    lines.append("")

    # RECESSION TRACKER
    lines.append("  RECESSION TRACKER")
    lines.append(f"  {'-'*64}")
    if rec:
        lines.append(f"  Composite         {rec['composite']}/13")
        lines.append(f"  p(recession 6m)   {rec['p6m']}%")
        lines.append(f"  p(recession 12m)  {rec['p12m']}%")
        lines.append("")
        order = ["T10Y3M", "T10Y2Y", "DFII10", "ICSA", "UMCSENT",
                 "INDPRO", "MANEMP", "PCEPILFE", "DFF", "VIXCLS",
                 "SP500", "BAA_AAA_spread", "CAPE"]
        for ind in order:
            r = rec["indicators"].get(ind, {})
            lines.append(f"  {ind:<18}  {r.get('val','n/a'):<12}  {r.get('thr','n/a'):<12}  {r.get('sig',0)}")
    else:
        lines.append("  n/a  (recession_config.json not found)")
    lines.append("")
    lines.append("=" * 68)

    body = "\n".join(lines)
    print(body)

    subject = f"Thesis Pulse {today}"
    send_email(subject, body)


if __name__ == "__main__":
    main()
