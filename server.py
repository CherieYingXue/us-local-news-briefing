import json
import os
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import feedparser
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = Path(__file__).parent
CACHE_DIR = Path("/tmp/us-news-cache") if os.name != "nt" else BASE_DIR / "cache"
CACHE_FILE = CACHE_DIR / "briefing.json"
TRANSLATION_CACHE_FILE = CACHE_DIR / "translations.json"
UPDATE_LOCK_FILE = CACHE_DIR / "update.lock"
STATES_FILE = BASE_DIR / "data" / "states.json"
PORT = int(__import__("os").environ.get("PORT", 3847))
UPDATE_MAX_SECONDS = 240
TRANSLATION_BUDGET_SECONDS = 90
FETCH_TIMEOUT_SECONDS = 5

_translation_cache = None
_translation_cache_dirty = False
_update_lock = threading.Lock()

app = Flask(__name__, static_folder="public", static_url_path="")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
STATES = json.loads(STATES_FILE.read_text(encoding="utf-8"))
is_updating = False
update_started_at = None
update_error = None

CATEGORY_KEYWORDS = {
    "political": [
        "politic", "election", "governor", "legisl", "congress", "senate", "house",
        "mayor", "vote", "ballot", "democrat", "republican", "policy", "capitol",
        "lawmaker", "bill", "veto", "campaign", "primary", "caucus", "immigration",
        "supreme court", "attorney general", "secretary of state", "regulation",
    ],
    "economic": [
        "econom", "business", "job", "market", "inflation", "housing", "trade",
        "finance", "budget", "tax", "wage", "unemployment", "gdp", "bank",
        "investment", "real estate", "cost of living", "tariff", "minimum wage",
        "workforce", "industry", "agriculture", "energy price",
    ],
    "social": [
        "community", "education", "school", "health", "hospital", "crime",
        "police", "fire", "weather", "disaster", "flood", "wildfire",
        "homeless", "family", "child", "university", "public safety", "court",
        "prison", "mental health", "environment", "water", "climate",
    ],
    "tech": [
        "tech", "technology", "digital", "cyber", " ai ", "artificial intelligence",
        "software", "data privacy", "internet", "broadband", "semiconductor",
        "innovation", "startup", "robot", "electric vehicle", "social media",
        "blockchain", "automation",
    ],
}

CATEGORY_LABELS = {
    "political": {"en": "Political", "zh": "政治"},
    "economic": {"en": "Economic", "zh": "经济"},
    "social": {"en": "Social", "zh": "社会"},
    "tech": {"en": "Tech", "zh": "科技"},
}


def strip_html(html):
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def categorize(text):
    lower = f" {text.lower()} "
    scores = {
        cat: sum(1 for kw in keywords if kw in lower)
        for cat, keywords in CATEGORY_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "social"


def parse_date(entry):
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    return None


def is_within_24_hours(entry):
    dt = parse_date(entry)
    if not dt:
        return True
    return (datetime.now(timezone.utc) - dt).total_seconds() <= 86400


def story_id(state_code, link, title):
    import base64
    raw = f"{state_code}-{link or title}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def load_translation_cache():
    global _translation_cache
    if _translation_cache is None:
        _translation_cache = {}
        for path in (TRANSLATION_CACHE_FILE, BASE_DIR / "cache" / "translations.json"):
            if path.exists():
                try:
                    _translation_cache = json.loads(path.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass
    return _translation_cache


def save_translation_cache():
    global _translation_cache_dirty
    if not _translation_cache_dirty:
        return
    cache = load_translation_cache()
    TRANSLATION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRANSLATION_CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _translation_cache_dirty = False


def read_update_lock():
    if not UPDATE_LOCK_FILE.exists():
        return None
    try:
        lock = json.loads(UPDATE_LOCK_FILE.read_text(encoding="utf-8"))
        if lock.get("pid") != os.getpid():
            return None
        return lock
    except Exception:
        return None


def clear_stale_update_lock():
    """Remove lock files left by a previous worker/process."""
    if not UPDATE_LOCK_FILE.exists():
        return
    try:
        lock = json.loads(UPDATE_LOCK_FILE.read_text(encoding="utf-8"))
        if lock.get("pid") != os.getpid():
            UPDATE_LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        try:
            UPDATE_LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass


def write_update_lock():
    UPDATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_LOCK_FILE.write_text(
        json.dumps({"startedAt": time.time(), "pid": os.getpid()}, indent=2),
        encoding="utf-8",
    )


def clear_update_lock():
    if UPDATE_LOCK_FILE.exists():
        try:
            UPDATE_LOCK_FILE.unlink()
        except Exception:
            pass


def get_update_elapsed():
    lock = read_update_lock()
    if lock and lock.get("startedAt"):
        return int(time.time() - lock["startedAt"])
    if update_started_at:
        return int(time.time() - update_started_at)
    return None


def reset_update_state(reason=""):
    global is_updating, update_started_at, update_error
    with _update_lock:
        is_updating = False
        update_started_at = None
        if reason:
            update_error = reason
        clear_update_lock()


def refresh_update_state():
    """Clear stuck or stale update locks (e.g. after timeout or redeploy)."""
    clear_stale_update_lock()
    elapsed = get_update_elapsed()
    if elapsed is None or elapsed <= UPDATE_MAX_SECONDS:
        return False

    lock = read_update_lock()
    if lock and lock.get("startedAt"):
        cached = read_cache()
        updated_at = cached.get("updatedAt") if cached else None
        if updated_at:
            try:
                cache_ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()
                if cache_ts > lock["startedAt"]:
                    reset_update_state()
                    return True
            except Exception:
                pass

    reset_update_state("Update timed out and was reset")
    return True


def is_valid_chinese_translation(source, translated):
    if not translated or not translated.strip():
        return False
    if source.strip().lower() == translated.strip().lower():
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", translated))


def http_get_json(url, timeout=10):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def translate_google_gtx(text):
    params = urllib.parse.urlencode(
        {"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text}
    )
    data = http_get_json(f"https://translate.googleapis.com/translate_a/single?{params}")
    if isinstance(data, list) and data and isinstance(data[0], list):
        parts = [part[0] for part in data[0] if part and part[0]]
        return "".join(parts).strip()
    return ""


def translate_google_dict(text):
    params = urllib.parse.urlencode(
        {"client": "dict-chrome-ex", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text}
    )
    data = http_get_json(
        f"https://clients5.google.com/translate_a/t?{params}"
    )
    if isinstance(data, list):
        if data and isinstance(data[0], list):
            parts = [part[0] for part in data[0] if part and part[0]]
            return "".join(parts).strip()
        if data and isinstance(data[0], str):
            return data[0].strip()
    return ""


def translate_mymemory(text):
    url = (
        "https://api.mymemory.translated.net/get?"
        + urllib.parse.urlencode({"q": text, "langpair": "en|zh-CN"})
    )
    data = http_get_json(url, timeout=8)
    if data.get("responseStatus") == 200:
        return data.get("responseData", {}).get("translatedText", "").strip()
    return ""


def translate_to_chinese(text):
    global _translation_cache_dirty
    if not text or len(text) < 2:
        return ""
    trimmed = text.strip()[:450]
    cache = load_translation_cache()
    if trimmed in cache:
        return cache[trimmed]

    for translator in (translate_google_gtx, translate_google_dict, translate_mymemory):
        try:
            translated = translator(trimmed)
            if is_valid_chinese_translation(trimmed, translated):
                cache[trimmed] = translated
                _translation_cache_dirty = True
                return translated
        except Exception:
            continue
    return ""


def fetch_feed_entries(feed_url, timeout=5):
    req = urllib.request.Request(
        feed_url,
        headers={"User-Agent": "US-Local-News-Briefing/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read()
    return feedparser.parse(content).entries or []


def fetch_state_news(state):
    try:
        entries = fetch_feed_entries(state["feed"], timeout=FETCH_TIMEOUT_SECONDS)
        recent = [e for e in entries if is_within_24_hours(e)]
        pool = recent[:8] if recent else entries[:3]

        stories = []
        for entry in pool[:3]:
            title = entry.get("title", "Untitled")
            description = strip_html(
                entry.get("summary") or entry.get("description") or ""
            )[:500]
            category = categorize(f"{title} {description}")
            pub = parse_date(entry)
            stories.append(
                {
                    "id": story_id(state["code"], entry.get("link", ""), title),
                    "title": title,
                    "titleZh": "",
                    "description": description,
                    "descriptionZh": "",
                    "link": entry.get("link", ""),
                    "pubDate": pub.isoformat() if pub else datetime.now(timezone.utc).isoformat(),
                    "category": category,
                    "categoryLabel": CATEGORY_LABELS[category],
                    "source": state["source"],
                }
            )
        return {"state": state, "stories": stories, "error": None}
    except Exception as exc:
        return {"state": state, "stories": [], "error": str(exc)}


def add_translations(briefing, deadline=None):
    stories = [
        story
        for state_data in briefing["states"]
        for story in state_data["stories"]
    ]

    # Pass 1: apply cached translations instantly
    for story in stories:
        cached = load_translation_cache().get(story["title"].strip()[:450], "")
        if cached:
            story["titleZh"] = cached

    # Pass 2: translate missing titles within time budget
    missing = [s for s in stories if not s.get("titleZh")]
    if not missing:
        return briefing

    budget_end = deadline or (time.time() + TRANSLATION_BUDGET_SECONDS)

    def translate_story(story):
        if time.time() >= budget_end:
            return story
        if not story.get("titleZh"):
            story["titleZh"] = translate_to_chinese(story["title"])
        return story

    workers = min(6, max(2, len(missing)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(translate_story, missing))

    save_translation_cache()
    return briefing


def apply_cached_translations(briefing):
    for state_data in briefing["states"]:
        for story in state_data["stories"]:
            cached = load_translation_cache().get(story["title"].strip()[:450], "")
            if cached:
                story["titleZh"] = cached
    return briefing


def fetch_all_states(progress_callback=None):
    results = []
    batch_size = 10
    for i in range(0, len(STATES), batch_size):
        batch = STATES[i : i + batch_size]
        batch_results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(fetch_state_news, state): state for state in batch}
            for future in as_completed(futures):
                batch_results.append(future.result())
        results.extend(batch_results)
        if progress_callback:
            progress_callback(results)
    results.sort(key=lambda r: r["state"]["code"])
    return [
        {
            "code": r["state"]["code"],
            "name": r["state"]["name"],
            "source": r["state"]["source"],
            "feed": r["state"]["feed"],
            "stories": r["stories"],
            "error": r["error"],
        }
        for r in results
    ]


def build_briefing_news_only():
    partial = []

    def save_progress(results):
        states = sorted(
            [
                {
                    "code": r["state"]["code"],
                    "name": r["state"]["name"],
                    "source": r["state"]["source"],
                    "feed": r["state"]["feed"],
                    "stories": r["stories"],
                    "error": r["error"],
                }
                for r in results
            ],
            key=lambda s: s["code"],
        )
        briefing = {
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "totalStates": len(STATES),
            "statesWithNews": sum(1 for s in states if s["stories"]),
            "states": states,
        }
        apply_cached_translations(briefing)
        write_cache(briefing)

    states = fetch_all_states(progress_callback=save_progress)
    briefing = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "totalStates": len(STATES),
        "statesWithNews": sum(1 for s in states if s["stories"]),
        "states": states,
    }
    return apply_cached_translations(briefing)


def run_update_job():
    global is_updating, update_error, update_started_at
    try:
        briefing = build_briefing_news_only()
        write_cache(briefing)
        update_error = None
    except Exception as exc:
        update_error = str(exc)
    finally:
        with _update_lock:
            is_updating = False
            update_started_at = None
            clear_update_lock()


def read_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Bootstrap from bundled cache on first run (Render / fresh deploy)
    bundled = BASE_DIR / "cache" / "briefing.json"
    if bundled.exists():
        try:
            return json.loads(bundled.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def write_cache(briefing):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(briefing, ensure_ascii=False, indent=2), encoding="utf-8")


def get_local_ip():
    preferred = []
    fallback = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127."):
                continue
            if ip.startswith("192.168.") or ip.startswith("10."):
                preferred.append(ip)
            else:
                fallback.append(ip)
    except Exception:
        pass
    if preferred:
        return preferred[0]
    if fallback:
        return fallback[0]
    return "localhost"


@app.route("/")
def index():
    refresh_update_state()
    return send_from_directory(BASE_DIR / "public", "index.html")


@app.route("/api/briefing", methods=["GET"])
def get_briefing():
    cached = read_cache()
    if cached:
        cached["fromCache"] = True
        cached["updating"] = is_updating
        return jsonify(cached)
    return jsonify(
        {
            "updatedAt": None,
            "states": [],
            "message": 'No briefing yet. Click "Update Daily Briefing" to fetch news.',
            "updating": is_updating,
        }
    )


@app.route("/api/briefing/update", methods=["POST"])
def update_briefing():
    global is_updating, update_started_at, update_error
    refresh_update_state()

    with _update_lock:
        if is_updating:
            elapsed = get_update_elapsed() or 0
            return jsonify(
                {
                    "updating": True,
                    "message": f"Update in progress ({elapsed}s). Please wait…",
                    "elapsedSeconds": elapsed,
                }
            ), 202

        is_updating = True
        update_started_at = time.time()
        update_error = None
        write_update_lock()

    thread = threading.Thread(target=run_update_job, daemon=True)
    thread.start()
    return jsonify(
        {
            "updating": True,
            "message": "Update started. Fetching news from 50 states (~1 min)…",
        }
    ), 202


@app.route("/api/briefing/update", methods=["GET"])
def update_briefing_status():
    refresh_update_state()
    elapsed = get_update_elapsed()
    return jsonify(
        {
            "updating": is_updating,
            "updateError": update_error,
            "elapsedSeconds": elapsed,
        }
    )


@app.route("/api/status", methods=["GET"])
def status():
    refresh_update_state()
    cached = read_cache()
    public_url = request.host_url.rstrip("/")
    return jsonify(
        {
            "updating": is_updating,
            "updateError": update_error,
            "elapsedSeconds": get_update_elapsed(),
            "lastUpdated": cached.get("updatedAt") if cached else None,
            "statesWithNews": cached.get("statesWithNews", 0) if cached else 0,
            "totalStates": len(STATES),
            "mobileUrl": public_url,
            "localUrl": public_url,
        }
    )


@app.route("/api/briefing/translate", methods=["POST"])
def translate_briefing_batch():
    """Translate up to 30 untranslated titles per call (runs after news update)."""
    cached = read_cache()
    if not cached:
        return jsonify({"error": "No briefing loaded"}), 404

    limit = 30
    translated_count = 0
    for state_data in cached.get("states", []):
        for story in state_data.get("stories", []):
            if translated_count >= limit:
                break
            title = story.get("title", "")
            if story.get("titleZh") and story["titleZh"] != title:
                continue
            zh = translate_to_chinese(title)
            if zh:
                story["titleZh"] = zh
                translated_count += 1
        if translated_count >= limit:
            break

    save_translation_cache()
    if translated_count:
        write_cache(cached)

    remaining = sum(
        1
        for st in cached.get("states", [])
        for s in st.get("stories", [])
        if not s.get("titleZh") or s.get("titleZh") == s.get("title")
    )
    return jsonify({"translated": translated_count, "remaining": remaining, "briefing": cached})


@app.route("/api/story/<story_id>")
def get_story(story_id):
    cached = read_cache()
    if not cached:
        return jsonify({"error": "No briefing loaded"}), 404
    for state in cached.get("states", []):
        for story in state.get("stories", []):
            if story["id"] == story_id:
                return jsonify({**story, "state": state["name"], "stateCode": state["code"]})
    return jsonify({"error": "Story not found"}), 404


clear_stale_update_lock()


if __name__ == "__main__":
    ip = get_local_ip()
    print()
    print("  US Local News Daily Briefing")
    print("  =============================")
    print(f"  Computer:  http://localhost:{PORT}")
    print(f"  Phone:     http://{ip}:{PORT}")
    print()
    print("  Add the phone link to your home screen for daily access.")
    print()
    app.run(host="0.0.0.0", port=PORT, debug=False)
