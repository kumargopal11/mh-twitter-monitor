#!/usr/bin/env python3.11
"""
Maharashtra Political Monitor
One browser session, per-keyword searches with combined OR anchor group.
No AI — topic-aware rule-based classifier.

Setup: copy .env.example to .env and fill in your credentials.
Run:   TMPDIR=/tmp python3.11 monitor.py
"""

import os
import sys
import json
import time
import random
import warnings
from collections import Counter
from urllib.parse import quote
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(line_buffering=True)

from playwright.sync_api import sync_playwright
import gspread
from datetime import datetime, timedelta, timezone

# Load .env if present
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

os.makedirs(os.environ.get("TMPDIR", "/tmp"), exist_ok=True)

# ── Credentials (set via .env or environment variables) ───────────────────────

AUTH_TOKEN = os.environ["TWITTER_AUTH_TOKEN"]
CT0        = os.environ["TWITTER_CT0"]
SHEET_ID   = os.environ.get("SHEET_ID", "")

TWEETS_PER_KEYWORD = 30   # max tweets per keyword search
MAX_SCROLLS        = 8    # scrolls per search page
SCROLL_SLEEP       = 1.5  # seconds between scrolls
NAV_SLEEP_MIN      = 2.0  # seconds between keyword navigations (min)
NAV_SLEEP_MAX      = 3.5  # seconds between keyword navigations (max)
PAGE_LOAD_TIMEOUT  = 15000
TWEET_WAIT_TIMEOUT = 8000

TEST_MODE = False
TEST_KEYWORDS = [
    ("Agrarian Distress", "farmer suicide"),
    ("Caste Justice",     "Dalit atrocity"),
    ("Governance Fail",   "BJP corruption"),
    ("Minority Rights",   "communal violence"),
    ("Women & Gender",    "sexual assault"),
]

# ── Geographic anchor — combined OR group ─────────────────────────────────────
# Research confirms (A OR B OR C) keyword works reliably on Twitter.
# One search per keyword covers all regions simultaneously.
ANCHORS = ["Maharashtra", "Vidarbha", "Marathwada", "Mahayuti", "Fadnavis"]
ANCHOR_GROUP = "(" + " OR ".join(ANCHORS) + ")"

# ── Keyword list ──────────────────────────────────────────────────────────────
# Best practices applied:
#   - Unquoted phrases → broader matching (quoted = consecutive words only)
#   - Hashtag variants run separately — they return non-overlapping result sets
#   - Both Devanagari and Roman transliteration for same concepts
#   - Shorter, higher-frequency terms alongside specific ones
#   - -word exclusions where needed to avoid false positives

KEYWORDS = [

    # ── Caste Justice ─────────────────────────────────────────────────────────
    # News accounts use: "Dalit man beaten in [district]", "SC/ST Act invoked"
    # Activists use: "#DalitLivesMatter", "caste atrocity", "Manuwadi"
    # Citizens react: "Another Dalit killed, where is justice?"
    ("Caste Justice", "Dalit atrocity"),
    ("Caste Justice", "Dalit killed"),
    ("Caste Justice", "Dalit beaten"),
    ("Caste Justice", "Dalit attacked"),
    ("Caste Justice", "Dalit protest"),
    ("Caste Justice", "#Dalit"),
    ("Caste Justice", "#DalitAtrocity"),
    ("Caste Justice", "#DalitLivesMatter"),
    ("Caste Justice", "SC ST atrocity"),
    ("Caste Justice", "SC/ST Act"),          # legal reference in news & activist tweets
    ("Caste Justice", "caste violence"),
    ("Caste Justice", "caste discrimination"),
    ("Caste Justice", "untouchability"),
    ("Caste Justice", "Ambedkar"),           # very high volume — protest, quote, news tweets
    ("Caste Justice", "Bhim Army"),          # activist org with MH roots
    ("Caste Justice", "bahujan"),
    ("Caste Justice", "Manuwadi"),           # activist slang for casteist/caste supremacist
    ("Caste Justice", "OBC reservation"),
    ("Caste Justice", "दलित अत्याचार"),
    ("Caste Justice", "दलित हत्या"),
    ("Caste Justice", "दलित आंदोलन"),
    ("Caste Justice", "अत्याचार"),           # atrocity in Marathi — high signal, used broadly
    ("Caste Justice", "जाति हिंसा"),
    ("Caste Justice", "अस्पृश्यता"),
    ("Caste Justice", "बाबासाहेब"),          # how Ambedkar is commonly addressed in Marathi

    # ── Agrarian Distress ──────────────────────────────────────────────────────
    # MH has highest farmer suicide rate in India — Vidarbha & Marathwada worst hit
    # People write: "shetkari aatmahatya" (Roman) OR "शेतकरी आत्महत्या" (Devanagari) — separate results
    # "annadata" (food-giver) is the emotional term activists use for farmers
    ("Agrarian Distress", "farmer suicide"),
    ("Agrarian Distress", "farmer death"),
    ("Agrarian Distress", "farmer protest"),
    ("Agrarian Distress", "#FarmerSuicide"),
    ("Agrarian Distress", "#KisanAndolan"),
    ("Agrarian Distress", "kisan protest"),
    ("Agrarian Distress", "shetkari aatmahatya"), # Roman transliteration — different results
    ("Agrarian Distress", "loan waiver"),
    ("Agrarian Distress", "karz mafi"),      # Hinglish for loan waiver — very natural
    ("Agrarian Distress", "crop loss"),
    ("Agrarian Distress", "crop damage"),
    ("Agrarian Distress", "MSP demand"),
    ("Agrarian Distress", "Vidarbha crisis"), # region with highest MH farmer suicides
    ("Agrarian Distress", "annadata"),        # emotional activist term for farmer
    ("Agrarian Distress", "farm distress"),
    ("Agrarian Distress", "शेतकरी आत्महत्या"),
    ("Agrarian Distress", "शेतकरी मृत्यू"),
    ("Agrarian Distress", "शेतकरी आंदोलन"),
    ("Agrarian Distress", "शेतकरी मोर्चा"),  # farmer march — very common in MH politics
    ("Agrarian Distress", "शेती संकट"),
    ("Agrarian Distress", "दुष्काळ"),
    ("Agrarian Distress", "कर्जमाफी"),
    ("Agrarian Distress", "पीक नुकसान"),

    # ── Minority Rights ────────────────────────────────────────────────────────
    # March 2025 Nagpur violence (VHP/Bajrang Dal vs Muslims, 1 killed, curfew) was biggest MH event
    # "danga" (Roman) and "दंगा" (Devanagari) are how Indians write "riot" naturally
    # "gau raksha" violence (cow vigilantes) is a distinct and frequent attack type
    ("Minority Rights", "Muslim attacked"),
    ("Minority Rights", "Muslim lynched"),
    ("Minority Rights", "mosque demolished"),
    ("Minority Rights", "mosque bulldozed"),
    ("Minority Rights", "communal violence"),
    ("Minority Rights", "communal clashes"),  # how news wires phrase it
    ("Minority Rights", "communal riot"),
    ("Minority Rights", "mob lynching"),
    ("Minority Rights", "lynching"),          # shorter, more natural
    ("Minority Rights", "#Lynching"),
    ("Minority Rights", "hate crime"),
    ("Minority Rights", "waqf"),              # very current MH/national controversy
    ("Minority Rights", "Nagpur violence"),   # March 2025 major event
    ("Minority Rights", "Nagpur riots"),      # alternate phrasing
    ("Minority Rights", "gau raksha"),        # cow vigilante attacks
    ("Minority Rights", "gau rakshak"),       # cow vigilante (person)
    ("Minority Rights", "danga"),             # Roman for riot — natural Hinglish
    ("Minority Rights", "CAA protest"),
    ("Minority Rights", "मुस्लिम हल्ला"),
    ("Minority Rights", "सांप्रदायिक हिंसा"),
    ("Minority Rights", "दंगा"),
    ("Minority Rights", "मशीद तोडणे"),
    ("Minority Rights", "अल्पसंख्याक अन्याय"),

    # ── Women & Gender ─────────────────────────────────────────────────────────
    # Badlapur school case (Aug 2024) caused Maharashtra Bandh — biggest women's safety protest
    # People tweet "nirbhaya phir se" (Nirbhaya again) as shorthand for any major rape outrage
    # "dowry death" and "honor killing" are distinct crime types frequently in MH news
    ("Women & Gender", "rape -encounter"),
    ("Women & Gender", "rape case"),
    ("Women & Gender", "sexual assault"),
    ("Women & Gender", "sexual harassment"),
    ("Women & Gender", "acid attack"),
    ("Women & Gender", "domestic violence"),
    ("Women & Gender", "dowry death"),        # important, common MH crime type
    ("Women & Gender", "honor killing"),      # inter-caste marriage murders
    ("Women & Gender", "POCSO"),              # legal term — used by journalists & activists
    ("Women & Gender", "eve teasing"),
    ("Women & Gender", "molestation"),
    ("Women & Gender", "women safety"),
    ("Women & Gender", "nirbhaya"),           # used as shorthand for rape outrage
    ("Women & Gender", "#MeToo"),
    ("Women & Gender", "महिला अत्याचार"),
    ("Women & Gender", "बलात्कार -एनकाउंटर"),
    ("Women & Gender", "लैंगिक हिंसा"),
    ("Women & Gender", "महिला सुरक्षा"),
    ("Women & Gender", "छेडछाड"),
    ("Women & Gender", "हुंडा मृत्यू"),       # dowry death in Marathi

    # ── Governance Fail ────────────────────────────────────────────────────────
    # Ladki Bahin Yojana fraud (Rs 17,000+ crore, 26L ineligible) is the HOTTEST current MH issue
    # Key coalition figures: Fadnavis (CM), Eknath Shinde (DyCM), Ajit Pawar (DyCM)
    # "ghotala" and "bhrashtachar" are how Indians write scam/corruption in Roman
    # Sanjay Raut (Shiv Sena UBT MP) is the most active MH opposition voice on Twitter
    ("Governance Fail", "BJP corruption"),
    ("Governance Fail", "Fadnavis scam"),
    ("Governance Fail", "Fadnavis resign"),
    ("Governance Fail", "#FadnavisResign"),
    ("Governance Fail", "Mahayuti scam"),
    ("Governance Fail", "#MahayutiScam"),
    ("Governance Fail", "Eknath Shinde scam"),
    ("Governance Fail", "Ajit Pawar scam"),
    ("Governance Fail", "NCP corruption"),
    ("Governance Fail", "Ladki Bahin scam"),  # hottest current MH political issue
    ("Governance Fail", "Ladki Bahin fraud"), # variant phrasing
    ("Governance Fail", "BMC scam"),          # Mumbai civic body scandal
    ("Governance Fail", "NEET scam"),
    ("Governance Fail", "ghotala"),           # Roman — how Indians write "scam" naturally
    ("Governance Fail", "bhrashtachar"),      # Roman — how Indians write "corruption"
    ("Governance Fail", "Sanjay Raut"),       # most active MH opposition tweeter, consistently critical
    ("Governance Fail", "police atrocity"),
    ("Governance Fail", "fake encounter"),
    ("Governance Fail", "custodial death"),
    ("Governance Fail", "government failed"),
    ("Governance Fail", "BJP failed"),
    ("Governance Fail", "unemployment Maharashtra"),
    ("Governance Fail", "inflation Maharashtra"),
    ("Governance Fail", "BJP भ्रष्टाचार"),
    ("Governance Fail", "फडणवीस घोटाळा"),
    ("Governance Fail", "महायुती भ्रष्टाचार"),
    ("Governance Fail", "सरकारी अपयश"),
    ("Governance Fail", "बेरोजगारी"),
    ("Governance Fail", "महागाई"),
    ("Governance Fail", "पोलीस अत्याचार"),

    # ── Anti-Hindutva ──────────────────────────────────────────────────────────
    # Bajrang Dal + VHP often appear together in critical tweets
    # "saffron brigade" and "saffron terror" are common activist phrases
    # "Hindu Rashtra" appears in critical tweets fearing majoritarianism
    # "gau raksha" overlap with Minority Rights — different angle, worth searching both
    ("Anti-Hindutva", "Hindutva violence"),
    ("Anti-Hindutva", "Hindutva mob"),
    ("Anti-Hindutva", "RSS attack"),
    ("Anti-Hindutva", "RSS goons"),
    ("Anti-Hindutva", "VHP attack"),
    ("Anti-Hindutva", "Bajrang Dal"),
    ("Anti-Hindutva", "bulldozer politics"),
    ("Anti-Hindutva", "#BulldozerPolitics"),
    ("Anti-Hindutva", "hate speech BJP"),
    ("Anti-Hindutva", "saffron terror"),
    ("Anti-Hindutva", "saffron brigade"),
    ("Anti-Hindutva", "Hindu Rashtra"),       # criticism of majoritarian state
    ("Anti-Hindutva", "communalism BJP"),
    ("Anti-Hindutva", "minority persecution"),
    ("Anti-Hindutva", "mob violence"),
    ("Anti-Hindutva", "हिंदुत्व हिंसा"),
    ("Anti-Hindutva", "संघ हल्ला"),
    ("Anti-Hindutva", "सांप्रदायिक दंगा"),
    ("Anti-Hindutva", "बुलडोजर राजकारण"),
    ("Anti-Hindutva", "धर्मांध हिंसा"),
]

# ── Twitter scraper — single browser session ──────────────────────────────────

def fetch_all(since: str, keywords: list) -> list[dict]:
    all_tweets  = []
    seen_globally = set()
    total = len(keywords)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        ctx.add_cookies([
            {"name": "auth_token", "value": AUTH_TOKEN, "domain": ".twitter.com", "path": "/"},
            {"name": "ct0",        "value": CT0,        "domain": ".twitter.com", "path": "/"},
            {"name": "auth_token", "value": AUTH_TOKEN, "domain": ".x.com",       "path": "/"},
            {"name": "ct0",        "value": CT0,        "domain": ".x.com",       "path": "/"},
        ])
        page = ctx.new_page()

        for i, (topic, keyword) in enumerate(keywords, 1):
            # One query per keyword — OR anchor group covers all regions at once
            query   = f"{ANCHOR_GROUP} {keyword} since:{since}"
            encoded = quote(query, safe="")
            url     = f"https://x.com/search?q={encoded}&src=typed_query&f=live"

            print(f"  [{i:3d}/{total}] [{topic}] {keyword}")
            tweets = _scrape_page(page, url)

            new = 0
            for t in tweets:
                if t["id"] not in seen_globally:
                    seen_globally.add(t["id"])
                    t["topic"]   = topic
                    t["keyword"] = keyword
                    t["anchor"]  = ANCHOR_GROUP
                    all_tweets.append(t)
                    new += 1
                    preview = t["text"][:100].replace("\n", " ")
                    print(f"           + @{t['author_username']} [♥{t['likes']} RT{t['retweets']}]: {preview}")

            print(f"           → {new} new | {len(tweets)} fetched | running total: {len(all_tweets)}")

            if i < total:
                time.sleep(random.uniform(NAV_SLEEP_MIN, NAV_SLEEP_MAX))

        browser.close()

    return all_tweets


def _parse_count(text: str) -> int:
    t = text.strip().replace(",", "")
    if not t:
        return 0
    try:
        if t.upper().endswith("K"): return int(float(t[:-1]) * 1_000)
        if t.upper().endswith("M"): return int(float(t[:-1]) * 1_000_000)
        return int(t)
    except Exception:
        return 0


def _get_count(card, testid: str) -> int:
    el = card.query_selector(f'[data-testid="{testid}"]')
    if not el:
        return 0
    span = el.query_selector("span[data-testid='app-text-transition-container'] span")
    if span:
        return _parse_count(span.inner_text())
    label = el.get_attribute("aria-label") or ""
    parts = label.split()
    if parts:
        return _parse_count(parts[0])
    return 0


def _scrape_page(page, url: str) -> list[dict]:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
        page.wait_for_selector('[data-testid="tweet"]', timeout=TWEET_WAIT_TIMEOUT)
    except Exception:
        return []

    tweets   = []
    seen_ids = set()
    scrolls  = 0

    while len(tweets) < TWEETS_PER_KEYWORD and scrolls < MAX_SCROLLS:
        for card in page.query_selector_all('[data-testid="tweet"]'):
            try:
                text_el = card.query_selector('[data-testid="tweetText"]')
                if not text_el:
                    continue
                text = text_el.inner_text().strip()
                if not text:
                    continue

                user_el  = card.query_selector('[data-testid="User-Name"]')
                name     = user_el.query_selector("span").inner_text().strip() if user_el else "unknown"
                username = "unknown"
                if user_el:
                    for a in user_el.query_selector_all("a[href]"):
                        href_val = a.get_attribute("href") or ""
                        if href_val.startswith("/") and "/" not in href_val[1:]:
                            username = href_val.lstrip("/")
                            break
                    if username == "unknown":
                        for span in user_el.query_selector_all("span"):
                            txt = span.inner_text().strip()
                            if txt.startswith("@"):
                                username = txt.lstrip("@")
                                break

                link_el   = card.query_selector('a[href*="/status/"]')
                href      = link_el.get_attribute("href") if link_el else ""
                tweet_id  = href.split("/status/")[-1].split("?")[0] if "/status/" in href else text[:20]
                tweet_url = f"https://twitter.com{href}" if href.startswith("/") else href

                if tweet_id in seen_ids:
                    continue
                seen_ids.add(tweet_id)

                tweets.append({
                    "id":              tweet_id,
                    "text":            text,
                    "author_name":     name,
                    "author_username": username,
                    "url":             tweet_url,
                    "likes":           _get_count(card, "like"),
                    "retweets":        _get_count(card, "retweet"),
                })
                if len(tweets) >= TWEETS_PER_KEYWORD:
                    break
            except Exception:
                continue

        if len(tweets) >= TWEETS_PER_KEYWORD:
            break
        page.evaluate("window.scrollBy(0, 1500)")
        time.sleep(SCROLL_SLEEP)
        scrolls += 1

    return tweets


# ── Rule-based classifier ──────────────────────────────────────────────────────

_GOVT_POSITIVE = [
    "inaugurated", "hails", "proud moment", "historical achievement",
    "fadnavis hails", "cm inaugurated", "record development",
    "strong action taken", "government has taken action",
    "relief provided by", "bjp wins", "mahayuti wins", "encounter model",
    "islamic violence", "targeting hindus", "genocidal islamic",
    "hindus targeted", "hindu persecution", "jihadist", "love jihad",
    "anti-hindu", "hinduphobia", "hindutva is the result",
    "defending hindus", "protecting hindus",
    "शेतकऱ्यांच्या पाठीशी खंबीरपणे", "महायुती सरकारची ऐतिहासिक",
    "दिलेला शब्द पाळणारे", "ऐतिहासिक निर्णय", "विकास कामे",
    "फडणवीस यांनी उद्घाटन", "सरकारने दिलासा",
]

_CRITICAL = [
    # Corruption / governance failure
    "corruption", "corrupt", "corrupted",
    "ghotala", "bhrashtachar",           # Roman Hinglish — how Indians write scam/corruption
    "sham", "fraud", "scam", "scandal", "failure", "fails", "failed",
    "cover up", "cover-up", "coverup", "exposed",
    "negligence", "negligent", "mismanagement",
    "shocking", "disturbing", "horrific", "horrifying", "appalling",
    "crime", "criminal", "illegal", "unlawful",
    "looting", "loot", "plunder", "embezzle", "embezzlement",
    "bribe", "bribery", "kickback",
    "accountability", "accountable",
    "resign", "resignation",             # demand for accountability
    "chor",                              # thief — very common political slur in MH
    "shame on", "shameful", "disgusting", "pathetic",
    "threatening", "threatened", "intimidated", "silenced",
    "nirbhaya",                          # used as shorthand for rape outrage tweets
    # Injustice / harm
    "injustice", "atrocity", "brutality", "killed", "murder", "murdered",
    "attack", "attacked", "assault", "assaulted", "beaten", "thrashed", "lynched",
    "rape", "raped", "molestation", "molested", "harassment", "harassed",
    "violence", "riot", "danga",             # danga = Roman for riot in Hinglish
    "demolish", "demolished", "bulldozed", "razed",
    "suicide", "suicides", "died", "death", "dead",
    "protest", "protesters", "demand justice", "outrage", "condemn", "condemned",
    "arrested", "detained", "jailed", "imprisoned",
    "broken promise", "empty promise", "no action", "ignored", "inaction",
    "denied", "deprived", "exploited", "oppressed", "suppressed",
    # Discourse markers
    "why is", "why are", "how long", "enough is enough",
    "we demand", "people demand", "justice for",
    "government failed", "state failed", "system failed",
    "no justice", "still no", "yet no",
    # Marathi/Hindi
    "अन्याय", "अत्याचार", "हिंसा", "हल्ला", "विरोध", "आंदोलन",
    "भ्रष्टाचार", "घोटाळा", "अपयश", "निषेध",
    "तोडणे", "गिराई", "ध्वस्त", "बुलडोजर",
    "फसवणूक", "नाटक", "लूट", "बलात्कार", "गुन्हा",
    "शर्मनाक", "भयंकर", "दुर्दैव",
]

_PROBLEM_TOPICS = {"Caste Justice", "Agrarian Distress", "Minority Rights",
                   "Women & Gender", "Anti-Hindutva"}


def classify(text: str, topic: str) -> dict:
    t = text.lower()
    pos_hits  = sum(1 for s in _GOVT_POSITIVE if s.lower() in t)
    crit_hits = sum(1 for s in _CRITICAL      if s.lower() in t)
    base = {"pos_hits": pos_hits, "crit_hits": crit_hits}

    if pos_hits >= 2 or (pos_hits == 1 and crit_hits == 0):
        return {**base, "is_left_leaning": False, "confidence": 0.0,
                "reason": f"pro-govt framing ({pos_hits} positive signals)"}

    if topic in _PROBLEM_TOPICS:
        if crit_hits == 0:
            return {**base, "is_left_leaning": False, "confidence": 0.0,
                    "reason": "no critical signals — likely neutral/historical"}
        conf = min(0.70 + crit_hits * 0.05, 0.92)
        return {**base, "is_left_leaning": True, "confidence": conf,
                "reason": f"problem reporting — {topic} ({crit_hits} critical signals)"}

    if crit_hits >= 1:
        conf = min(0.65 + crit_hits * 0.06, 0.92)
        return {**base, "is_left_leaning": True, "confidence": conf,
                "reason": f"governance criticism ({crit_hits} critical signals)"}

    return {**base, "is_left_leaning": False, "confidence": 0.0,
            "reason": "no critical signals"}


# ── Google Sheets ─────────────────────────────────────────────────────────────

SHEET_HEADERS = [
    "Date", "Topic", "Keyword", "Author", "Handle", "Likes", "Retweets",
    "Pos Signals", "Crit Signals", "Confidence", "Left-Leaning", "Reason",
    "Tweet URL", "Tweet Text",
]

def _gspread_client():
    for var in ("GSPREAD_CREDS_JSON", "GSPREAD_CREDS_PATH"):
        val = os.environ.get(var, "").strip()
        if not val:
            continue
        if val.startswith("{"):
            return gspread.service_account_from_dict(json.loads(val))
        return gspread.service_account(filename=val)
    return gspread.service_account(filename=os.path.expanduser("~/.config/gspread/credentials.json"))

def get_or_create_tab(run_label: str):
    gc = _gspread_client()
    spreadsheet = gc.open_by_key(SHEET_ID)
    try:
        ws = spreadsheet.add_worksheet(title=run_label, rows=2000, cols=len(SHEET_HEADERS))
    except Exception:
        ws = spreadsheet.add_worksheet(title=run_label + " (2)", rows=2000, cols=len(SHEET_HEADERS))
    ws.append_row(SHEET_HEADERS, value_input_option="RAW")
    return ws

def flush_to_sheet(ws, tweets: list[dict]):
    rows = []
    for t in tweets:
        rows.append([
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            t.get("topic", ""),
            t.get("keyword", ""),
            t.get("author_name", ""),
            f"@{t.get('author_username', '')}",
            t.get("likes", 0),
            t.get("retweets", 0),
            t.get("pos_hits", 0),
            t.get("crit_hits", 0),
            f"{t.get('confidence', 0):.0%}",
            "Yes" if t.get("is_left_leaning") else "No",
            t.get("reason", ""),
            t.get("url", ""),
            t.get("text", ""),
        ])
    if rows:
        ws.append_rows(rows, value_input_option="RAW")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    since      = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    active_kws = TEST_KEYWORDS if TEST_MODE else KEYWORDS
    mode_label = f"TEST ({len(active_kws)} keywords)" if TEST_MODE else f"FULL ({len(active_kws)} keywords)"

    print(f"Maharashtra Political Monitor  [{mode_label}]")
    print(f"Date filter  : since {since}")
    print(f"Anchor group : {ANCHOR_GROUP}")
    print(f"Total searches: {len(active_kws)}\n")

    all_tweets = fetch_all(since, active_kws)
    print(f"\nTotal unique tweets fetched: {len(all_tweets)}")

    # ── Google Sheets — new tab per run ───────────────────────────────────────
    run_label = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    sheet_ws  = None
    try:
        sheet_ws = get_or_create_tab(run_label)
        print(f"Google Sheet tab created: '{run_label}'\n")
    except Exception as e:
        print(f"Google Sheets unavailable ({e}) — skipping export.\n")

    # ── Classify and print ────────────────────────────────────────────────────
    left_count = 0
    for i, tweet in enumerate(all_tweets, 1):
        result = classify(tweet["text"], tweet["topic"])
        tweet["pos_hits"]        = result["pos_hits"]
        tweet["crit_hits"]       = result["crit_hits"]
        tweet["confidence"]      = round(result["confidence"], 2)
        tweet["is_left_leaning"] = result["is_left_leaning"]
        tweet["reason"]          = result["reason"]

        left_label = "LEFT-LEANING" if result["is_left_leaning"] else "NOT left-leaning"

        print(f"{'─'*90}")
        print(f"[{i}] {tweet['topic']}  |  @{tweet['author_username']}  |  ♥{tweet['likes']}  RT{tweet['retweets']}")
        print(f"     {tweet['url']}")
        print(f"     {tweet['text'][:200].replace(chr(10), ' ')}")
        print(f"     ┌ Positive signals : {result['pos_hits']}")
        print(f"     ├ Critical signals : {result['crit_hits']}")
        print(f"     ├ Confidence       : {result['confidence']:.0%}")
        print(f"     └ Judgment         : {left_label} — {result['reason']}")

        if result["is_left_leaning"]:
            left_count += 1

    print(f"{'─'*90}")

    # ── Flush to Sheets ───────────────────────────────────────────────────────
    if sheet_ws:
        try:
            flush_to_sheet(sheet_ws, all_tweets)
            print(f"Sheet tab '{run_label}' updated ({len(all_tweets)} rows)")
            print(f"Sheet → https://docs.google.com/spreadsheets/d/{SHEET_ID}")
        except Exception as e:
            print(f"Sheet write failed: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\nLeft-leaning: {left_count} / {len(all_tweets)} tweets")
    by_topic = Counter(t["topic"] for t in all_tweets if t.get("is_left_leaning"))
    for topic, count in sorted(by_topic.items(), key=lambda x: -x[1]):
        print(f"  {count:3d}  {topic}")


if __name__ == "__main__":
    main()
