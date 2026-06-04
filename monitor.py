#!/usr/bin/env python3.11
"""
Maharashtra Political Monitor
One browser session, per-keyword searches, mandatory Maharashtra anchor.
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

TWEETS_PER_KEYWORD = 30   # max tweets collected per keyword search
MAX_SCROLLS        = 7    # scrolls per keyword page
SCROLL_SLEEP       = 1.5  # seconds between scrolls within one page
NAV_SLEEP_MIN      = 2.0  # seconds between keyword navigations (min)
NAV_SLEEP_MAX      = 3.0  # seconds between keyword navigations (max)
PAGE_LOAD_TIMEOUT  = 15000  # ms — page.goto timeout
TWEET_WAIT_TIMEOUT = 8000   # ms — wait for first tweet to appear

# Set to True to run only TEST_KEYWORDS for a quick sanity check
TEST_MODE = True
TEST_KEYWORDS = [
    ("Agrarian Distress", "farmer suicide"),
    ("Caste Justice",     "Dalit atrocity"),
    ("Governance Fail",   "BJP corruption"),
    ("Anti-Hindutva",     "communal violence"),
    ("Women & Gender",    "sexual assault"),
]

# ── Maharashtra anchor ────────────────────────────────────────────────────────
# Prepended to every search query. Every result tweet must contain at least one
# of these identifiers — enforces geographic relevance without a hard keyword.

# Plain AND anchors — Twitter processes these reliably unlike parenthetical OR groups.
# Each keyword search runs once per anchor, results merged and deduplicated.
ANCHORS = ["Maharashtra"]  # set back to full list for production runs

# ── Per-keyword search list ───────────────────────────────────────────────────
# (topic_label, twitter_search_term)
# "quoted phrase" = exact match   |   word word = both words anywhere in tweet
# -word = exclude tweets containing that word

KEYWORDS = [

    # ── Caste Justice ─────────────────────────────────────────────────────────
    ("Caste Justice", "Dalit atrocity"),
    ("Caste Justice", "caste violence"),
    ("Caste Justice", "attack on muslim"),
    ("Caste Justice", "caste discrimination"),
    ("Caste Justice", "untouchability"),
    ("Caste Justice", "Dalit killed"),
    ("Caste Justice", "Dalit attack"),
    ("Caste Justice", "Buddha statue demolished"),
    ("Caste Justice", "Ambedkar protest"),
    ("Caste Justice", "bahujan protest"),
    ("Caste Justice", "OBC discrimination"),
    ("Caste Justice", "scheduled caste atrocity"),
    ("Caste Justice", "दलित अत्याचार"),
    ("Caste Justice", "जातिभेद"),
    ("Caste Justice", "अस्पृश्यता"),
    ("Caste Justice", "बुद्ध प्रतिमा तोडणे"),
    ("Caste Justice", "बुलडोजर दलित"),
    ("Caste Justice", "जाति हिंसा"),

    # ── Agrarian Distress ──────────────────────────────────────────────────────
    ("Agrarian Distress", "farmer suicide"),
    ("Agrarian Distress", "kisan debt crisis"),
    ("Agrarian Distress", "loan waiver sham"),
    ("Agrarian Distress", "loan waiver fraud"),
    ("Agrarian Distress", "loan waiver miss"),
    ("Agrarian Distress", "farm distress"),
    ("Agrarian Distress", "farmer protest"),
    ("Agrarian Distress", "crop failure"),
    ("Agrarian Distress", "MSP demand"),
    ("Agrarian Distress", "agricultural crisis"),
    ("Agrarian Distress", "drought farmers"),
    ("Agrarian Distress", "शेतकरी आत्महत्या"),
    ("Agrarian Distress", "शेतकरी आंदोलन"),
    ("Agrarian Distress", "दुष्काळ शेतकरी"),
    ("Agrarian Distress", "कर्जमाफी फसवणूक"),
    ("Agrarian Distress", "कर्जमाफी नाटक"),
    ("Agrarian Distress", "पीक नुकसान"),

    # ── Minority Rights ────────────────────────────────────────────────────────
    ("Minority Rights", "Muslim attacked"),
    ("Minority Rights", "mosque demolished"),
    ("Minority Rights", "mosque attacked"),
    ("Minority Rights", "communal violence"),
    ("Minority Rights", "minority attacked"),
    ("Minority Rights", "CAA protest"),
    ("Minority Rights", "mob lynching"),
    ("Minority Rights", "hate crime"),
    ("Minority Rights", "religious persecution"),
    ("Minority Rights", "communal riot"),
    ("Minority Rights", "minority discrimination"),
    ("Minority Rights", "मुस्लिम हल्ला"),
    ("Minority Rights", "सांप्रदायिक हिंसा"),
    ("Minority Rights", "अल्पसंख्याक अन्याय"),
    ("Minority Rights", "मशीद तोडणे"),

    # ── Women & Gender ─────────────────────────────────────────────────────────
    ("Women & Gender", "rape -encounter"),
    ("Women & Gender", "sexual assault"),
    ("Women & Gender", "women atrocity"),
    ("Women & Gender", "sexual violence"),
    ("Women & Gender", "acid attack"),
    ("Women & Gender", "domestic violence"),
    ("Women & Gender", "gender crime"),
    ("Women & Gender", "women protest"),
    ("Women & Gender", "महिला अत्याचार"),
    ("Women & Gender", "बलात्कार -एनकाउंटर"),
    ("Women & Gender", "लैंगिक हिंसा"),
    ("Women & Gender", "महिला आंदोलन"),

    # ── Governance Fail ────────────────────────────────────────────────────────
    ("Governance Fail", "BJP corruption"),
    ("Governance Fail", "Fadnavis scam"),
    ("Governance Fail", "Mahayuti scam"),
    ("Governance Fail", "Fadnavis failure"),
    ("Governance Fail", "NEET scam"),
    ("Governance Fail", "scheme sham"),
    ("Governance Fail", "waiver fraud"),
    ("Governance Fail", "government failure"),
    ("Governance Fail", "police brutality"),
    ("Governance Fail", "unemployment BJP"),
    ("Governance Fail", "price rise BJP"),
    ("Governance Fail", "birth certificate scam"),
    ("Governance Fail", "BJP भ्रष्टाचार"),
    ("Governance Fail", "फडणवीस घोटाळा"),
    ("Governance Fail", "महायुती बेरोजगारी"),
    ("Governance Fail", "सरकारी अपयश"),
    ("Governance Fail", "महागाई BJP"),

    # ── Anti-Hindutva ──────────────────────────────────────────────────────────
    ("Anti-Hindutva", "Hindutva violence"),
    ("Anti-Hindutva", "RSS attack"),
    ("Anti-Hindutva", "VHP attack"),
    ("Anti-Hindutva", "Bajrang Dal attack"),
    ("Anti-Hindutva", "bulldozer politics"),
    ("Anti-Hindutva", "hate speech BJP"),
    ("Anti-Hindutva", "minority persecution"),
    ("Anti-Hindutva", "secular protest"),
    ("Anti-Hindutva", "हिंदुत्व हिंसा"),
    ("Anti-Hindutva", "संघ हल्ला"),
    ("Anti-Hindutva", "सांप्रदायिक दंगा"),
    ("Anti-Hindutva", "बुलडोजर राजकारण"),
    ("Anti-Hindutva", "धर्मनिरपेक्ष आंदोलन"),
]

# ── Twitter scraper — single browser session ──────────────────────────────────

def fetch_all(since: str, keywords: list) -> list[dict]:
    all_tweets = []
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

        search_total = total * len(ANCHORS)
        n = 0
        for i, (topic, keyword) in enumerate(keywords):
            for anchor in ANCHORS:
                n += 1
                query   = f"{anchor} {keyword} since:{since}"
                encoded = query.replace(" ", "%20").replace('"', "%22")
                url     = f"https://x.com/search?q={encoded}&src=typed_query&f=live"

                print(f"  [{n:3d}/{search_total}] [{topic}] {anchor} + {keyword}")
                tweets = _scrape_page(page, url)

                new = 0
                for t in tweets:
                    if t["id"] not in seen_globally:
                        seen_globally.add(t["id"])
                        t["topic"] = topic
                        all_tweets.append(t)
                        new += 1
                        preview = t["text"][:100].replace("\n", " ")
                        print(f"           + @{t['author_username']} [♥{t['likes']} RT{t['retweets']}]: {preview}")

                print(f"           → {new} new | {len(tweets)} fetched | running total: {len(all_tweets)}")

                if n < search_total:
                    time.sleep(random.uniform(NAV_SLEEP_MIN, NAV_SLEEP_MAX))

        browser.close()

    return all_tweets


def _parse_count(text: str) -> int:
    """Convert '1.2K', '3.4M', '42', '' to integer."""
    t = text.strip().replace(",", "")
    if not t:
        return 0
    try:
        if t.upper().endswith("K"):
            return int(float(t[:-1]) * 1_000)
        if t.upper().endswith("M"):
            return int(float(t[:-1]) * 1_000_000)
        return int(t)
    except Exception:
        return 0


def _get_count(card, testid: str) -> int:
    """Extract numeric count from a tweet action button (like/retweet)."""
    el = card.query_selector(f'[data-testid="{testid}"]')
    if not el:
        return 0
    # Twitter renders count in a nested span inside the button
    span = el.query_selector("span[data-testid='app-text-transition-container'] span")
    if span:
        return _parse_count(span.inner_text())
    # Fallback: aria-label on the button e.g. "1234 Likes"
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
                # Extract handle from the profile link href: /<handle>/status/... or /<handle>
                handle_el = card.query_selector('a[href^="/"][href*="status"] , a[data-testid="User-Name"] , [data-testid="User-Name"] a[role="link"]')
                username = "unknown"
                if user_el:
                    for a in user_el.query_selector_all("a[href]"):
                        href_val = a.get_attribute("href") or ""
                        if href_val.startswith("/") and "/" not in href_val[1:]:
                            username = href_val.lstrip("/")
                            break
                    if username == "unknown":
                        # fallback: look for @handle text inside the User-Name block
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

# ── Rule-based classifier (no AI) ─────────────────────────────────────────────
#
# Design principle:
#   "Problem" topics (Caste, Agrarian, Minority, Women, Anti-Hindutva) —
#   tweets found by searching for atrocity/violence/injustice keywords are
#   inherently reporting a problem and lean left UNLESS strong pro-govt signals
#   override them.
#
#   "Governance Fail" — neutral topic name; needs explicit critical signals
#   to be flagged left-leaning since BJP also tweets about BJP.

_GOVT_POSITIVE = [
    # BJP/Fadnavis self-promotion signals (marks tweet as NOT left-leaning)
    "inaugurated", "hails", "proud moment", "historical achievement",
    "fadnavis hails", "cm inaugurated", "record development",
    "strong action taken", "government has taken action",
    "relief provided by", "bjp wins", "mahayuti wins",
    "encounter model",
    # Pro-Hindutva / right-wing defense framing
    "islamic violence", "targeting hindus", "genocidal islamic",
    "hindus targeted", "hindu persecution", "jihadist", "love jihad",
    "anti-hindu", "hinduphobia", "hindutva is the result",
    "defending hindus", "protecting hindus",
    # Marathi equivalents
    "शेतकऱ्यांच्या पाठीशी खंबीरपणे", "महायुती सरकारची ऐतिहासिक",
    "दिलेला शब्द पाळणारे", "ऐतिहासिक निर्णय", "विकास कामे",
    "फडणवीस यांनी उद्घाटन", "सरकारने दिलासा",
]

_CRITICAL = [
    # English — governance failure / corruption
    "corruption", "corrupt", "corrupted", "corrupting",
    "sham", "fraud", "scam", "scandal", "failure", "fails", "failed",
    "cover up", "cover-up", "coverup", "exposed",
    "negligence", "negligent", "mismanagement", "mismanaged",
    "shocking", "disturbing", "horrific", "horrifying", "appalling",
    "crime", "criminal", "illegal", "unlawful", "lawless",
    "looting", "loot", "plunder", "embezzle", "embezzlement",
    "bribe", "bribery", "kickback",
    "accountability", "accountable", "impeach",
    "shame on", "shameful", "disgusting", "pathetic",
    "threatening", "threatened", "intimidated", "silenced",
    # English — injustice / harm
    "injustice", "atrocity", "brutality", "killed", "murder", "murdered",
    "attack", "attacked", "assault", "assaulted", "beaten", "thrashed", "lynched",
    "rape", "raped", "molestation", "molested", "harassment", "harassed",
    "violence", "riot", "demolish", "demolished", "bulldozed", "razed",
    "suicide", "suicides", "died", "death", "dead",
    "protest", "protesters", "demand justice", "outrage", "condemn", "condemned",
    "arrested", "detained", "jailed", "imprisoned",
    "broken promise", "empty promise", "no action", "ignored", "inaction",
    "denied", "deprived", "exploited", "oppressed", "suppressed",
    "missing out", "miss out",
    # English — critical discourse markers
    "why is", "why are", "how long", "enough is enough",
    "wake up", "wake up india", "stop ignoring",
    "we demand", "people demand", "justice for",
    "government failed", "state failed", "system failed",
    "no justice", "still no", "yet no",
    # Marathi/Hindi — problem / injustice framing
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

    base = {
        "pos_hits":  pos_hits,
        "crit_hits": crit_hits,
    }

    # Strong government PR framing → not left-leaning
    if pos_hits >= 2 or (pos_hits == 1 and crit_hits == 0):
        return {**base,
            "is_left_leaning": False,
            "confidence":      0.0,
            "reason":          f"pro-govt framing ({pos_hits} positive signals)",
        }

    if topic in _PROBLEM_TOPICS:
        if crit_hits == 0:
            return {**base,
                "is_left_leaning": False,
                "confidence":      0.0,
                "reason":          "no critical signals — likely neutral/historical",
            }
        conf = min(0.70 + crit_hits * 0.05, 0.92)
        return {**base,
            "is_left_leaning": True,
            "confidence":      conf,
            "reason":          f"problem reporting — {topic} ({crit_hits} critical signals)",
        }

    # Governance Fail: needs explicit criticism to qualify
    if crit_hits >= 1:
        conf = min(0.65 + crit_hits * 0.06, 0.92)
        return {**base,
            "is_left_leaning": True,
            "confidence":      conf,
            "reason":          f"governance criticism ({crit_hits} critical signals)",
        }

    return {**base,
        "is_left_leaning": False,
        "confidence":      0.0,
        "reason":          "no critical signals",
    }

# ── Google Sheets ─────────────────────────────────────────────────────────────

SHEET_HEADERS = [
    "Date", "Topic", "Author", "Handle", "Likes", "Retweets",
    "Pos Signals", "Crit Signals", "Confidence", "Left-Leaning", "Reason",
    "Tweet URL", "Tweet Text",
]

def _gspread_client():
    """Return an authenticated gspread client.
    Prefers GSPREAD_CREDS_JSON (JSON string, for Railway) over
    GSPREAD_CREDS_PATH (local file path)."""
    creds_json = os.environ.get("GSPREAD_CREDS_JSON")
    if creds_json:
        return gspread.service_account_from_dict(json.loads(creds_json))
    creds_path = os.environ.get("GSPREAD_CREDS_PATH", os.path.expanduser("~/.config/gspread/credentials.json"))
    return gspread.service_account(filename=creds_path)

def get_or_create_tab(run_label: str):
    """Open the spreadsheet and create a new tab named by run_label (e.g. '2026-06-05 08:30').
    Returns the worksheet."""
    gc = _gspread_client()
    spreadsheet = gc.open_by_key(SHEET_ID)
    try:
        ws = spreadsheet.add_worksheet(title=run_label, rows=2000, cols=len(SHEET_HEADERS))
    except Exception:
        # Tab already exists (duplicate run) — append a suffix
        ws = spreadsheet.add_worksheet(title=run_label + " (2)", rows=2000, cols=len(SHEET_HEADERS))
    ws.append_row(SHEET_HEADERS, value_input_option="RAW")
    return ws

def flush_to_sheet(ws, tweets: list[dict]):
    """Write all tweets to the worksheet in one batch call."""
    rows = []
    for t in tweets:
        rows.append([
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            t.get("topic", ""),
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
    since        = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    active_kws   = TEST_KEYWORDS if TEST_MODE else KEYWORDS
    mode_label   = f"TEST ({len(active_kws)} keywords)" if TEST_MODE else f"FULL ({len(active_kws)} keywords)"
    print(f"Maharashtra Political Monitor  [{mode_label}]")
    print(f"Date filter : since {since}")
    print(f"Anchors     : {ANCHORS}")
    print(f"Total searches: {len(active_kws) * len(ANCHORS)}\n")

    # ── Fetch ──────────────────────────────────────────────────────────────────
    all_tweets = fetch_all(since, active_kws)
    print(f"\nTotal unique tweets fetched: {len(all_tweets)}")

    # ── Google Sheets — create new tab for this run ───────────────────────────
    run_label = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    sheet_ws  = None
    try:
        sheet_ws = get_or_create_tab(run_label)
        print(f"Google Sheet tab created: '{run_label}'\n")
    except Exception as e:
        print(f"Google Sheets unavailable ({e}) — skipping export.\n")

    # ── Classify and annotate all tweets ──────────────────────────────────────
    left_count = 0
    for i, tweet in enumerate(all_tweets, 1):
        result = classify(tweet["text"], tweet["topic"])

        tweet["pos_hits"]        = result["pos_hits"]
        tweet["crit_hits"]       = result["crit_hits"]
        tweet["confidence"]      = round(result["confidence"], 2)
        tweet["is_left_leaning"] = result["is_left_leaning"]
        tweet["reason"]          = result["reason"]

        left_label = "LEFT-LEANING" if result["is_left_leaning"] else "NOT left-leaning"
        conf_str   = f"{result['confidence']:.0%}"

        print(f"{'─'*90}")
        print(f"[{i}] {tweet['topic']}  |  @{tweet['author_username']}  |  ♥{tweet['likes']}  RT{tweet['retweets']}")
        print(f"     {tweet['url']}")
        print(f"     {tweet['text'][:200].replace(chr(10), ' ')}")
        print(f"     ┌ Positive signals : {result['pos_hits']}")
        print(f"     ├ Critical signals : {result['crit_hits']}")
        print(f"     ├ Confidence       : {conf_str}")
        print(f"     └ Judgment         : {left_label} — {result['reason']}")

        if result["is_left_leaning"]:
            left_count += 1

    print(f"{'─'*90}")

    # ── Save JSON ──────────────────────────────────────────────────────────────
    out_path = os.path.join(os.environ.get("TMPDIR", "/tmp"), "mh_tweets_today.json")
    with open(out_path, "w") as f:
        json.dump(all_tweets, f, ensure_ascii=False, indent=2)
    print(f"\nAll tweets saved → {out_path}")

    # ── Flush all tweets to Sheets in one batch call ──────────────────────────
    if sheet_ws:
        try:
            flush_to_sheet(sheet_ws, all_tweets)
            print(f"Sheet tab '{run_label}' updated ({len(all_tweets)} rows)")
            print(f"Sheet → https://docs.google.com/spreadsheets/d/{SHEET_ID}")
        except Exception as e:
            print(f"Sheet write failed: {e}")

    # ── Summary ────────────────────────────────────────────────────────────────
    print(f"\nLeft-leaning: {left_count} / {len(all_tweets)} tweets")
    by_topic = Counter(t["topic"] for t in all_tweets if t.get("is_left_leaning"))
    for topic, count in sorted(by_topic.items(), key=lambda x: -x[1]):
        print(f"  {count:3d}  {topic}")


if __name__ == "__main__":
    main()
