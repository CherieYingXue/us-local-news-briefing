import json
import os
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

BASE_DIR = Path(__file__).parent
CACHE_DIR = Path("/tmp/us-news-cache") if os.name != "nt" else BASE_DIR / "cache"
CACHE_FILE = CACHE_DIR / "briefing.json"
TRANSLATION_CACHE_FILE = CACHE_DIR / "translations.json"
UPDATE_LOCK_FILE = CACHE_DIR / "update.lock"
UPDATE_STATUS_FILE = CACHE_DIR / "update-status.json"
STATES_FILE = BASE_DIR / "data" / "states.json"
PORT = int(os.environ.get("PORT", 3847))
TRANSLATION_BUDGET_SECONDS = 90
FETCH_TIMEOUT_SECONDS = 10
FETCH_WORKERS = 10
MAX_ITEMS_PER_FEED = 8
STORIES_PER_STATE = 3

_translation_cache = None
_translation_cache_dirty = False

app = Flask(__name__, static_folder="public", static_url_path="")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
STATES = json.loads(STATES_FILE.read_text(encoding="utf-8"))

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


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_within_24_hours(item):
    dt = item["pubDate"]
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


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _child_text(elem, *names):
    for child in elem:
        if _local_name(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def _atom_link(elem):
    for child in elem:
        if _local_name(child.tag) == "link" and child.get("rel", "alternate") == "alternate":
            return child.get("href", "")
    return ""


def fetch_feed_items(feed_url, limit=MAX_ITEMS_PER_FEED, timeout=FETCH_TIMEOUT_SECONDS):
    """Stream-parse an RSS/Atom feed, stopping after `limit` items.

    Feeds embed full article bodies (~1 MB each); reading only the first few
    items keeps updates fast on small CPUs.
    """
    req = urllib.request.Request(
        feed_url,
        headers={"User-Agent": "US-Local-News-Briefing/1.0"},
    )
    items = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for _, elem in ET.iterparse(resp, events=("end",)):
            if _local_name(elem.tag) not in ("item", "entry"):
                continue
            items.append(
                {
                    "title": unescape(_child_text(elem, "title")) or "Untitled",
                    "link": _child_text(elem, "link") or _atom_link(elem),
                    "description": _child_text(elem, "description", "summary"),
                    "pubDate": parse_date(
                        _child_text(elem, "pubDate", "published", "updated", "date")
                    ),
                }
            )
            elem.clear()
            if len(items) >= limit:
                break
    return items


def fetch_state_news(state):
    try:
        items = fetch_feed_items(state["feed"])
        recent = [i for i in items if is_within_24_hours(i)]
        pool = recent if recent else items

        stories = []
        for item in pool[:STORIES_PER_STATE]:
            title = item["title"]
            description = strip_html(item["description"])[:500]
            category = categorize(f"{title} {description}")
            pub = item["pubDate"] or datetime.now(timezone.utc)
            stories.append(
                {
                    "id": story_id(state["code"], item["link"], title),
                    "title": title,
                    "titleZh": "",
                    "description": description,
                    "descriptionZh": "",
                    "link": item["link"],
                    "pubDate": pub.isoformat(),
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


def build_briefing(on_progress=None):
    results = []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
        for result in executor.map(fetch_state_news, STATES):
            results.append(result)
            if on_progress:
                on_progress(len(results))
    states = [
        {
            "code": r["state"]["code"],
            "name": r["state"]["name"],
            "source": r["state"]["source"],
            "feed": r["state"]["feed"],
            "stories": r["stories"],
            "error": r["error"],
        }
        for r in sorted(results, key=lambda r: r["state"]["code"])
    ]
    briefing = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "totalStates": len(STATES),
        "statesWithNews": sum(1 for s in states if s["stories"]),
        "states": states,
    }
    return apply_cached_translations(briefing)


# Update state lives on disk, guarded by an flock, so every gunicorn worker
# sees the same status and only one update runs at a time.
_local_update_lock = threading.Lock()


def _write_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_update_status():
    try:
        return json.loads(UPDATE_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _try_lock():
    """Return an open, exclusively locked file handle, or None if an update is running."""
    if not _local_update_lock.acquire(blocking=False):
        return None
    if fcntl is None:
        return _local_update_lock
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(UPDATE_LOCK_FILE, "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        _local_update_lock.release()
        return None


def _release_lock(handle):
    if handle is not _local_update_lock:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
    _local_update_lock.release()


def update_in_progress():
    handle = _try_lock()
    if handle is None:
        return True
    _release_lock(handle)
    return False


def run_update_job(lock_handle, translate):
    status = {"startedAt": time.time(), "progress": 0, "error": None}
    _write_json_atomic(UPDATE_STATUS_FILE, status)

    def on_progress(done):
        status["progress"] = done
        _write_json_atomic(UPDATE_STATUS_FILE, status)

    try:
        briefing = build_briefing(on_progress)
        if briefing["statesWithNews"] == 0:
            raise RuntimeError("No news could be fetched from any state")
        write_cache(briefing)
        if translate:
            add_translations(briefing)
            write_cache(briefing)
    except Exception as exc:
        status["error"] = str(exc)
    finally:
        status["finishedAt"] = time.time()
        _write_json_atomic(UPDATE_STATUS_FILE, status)
        _release_lock(lock_handle)


def start_update(translate=False):
    handle = _try_lock()
    if handle is None:
        return False
    threading.Thread(target=run_update_job, args=(handle, translate), daemon=True).start()
    return True


def update_state():
    updating = update_in_progress()
    status = read_update_status()
    started = status.get("startedAt")
    return {
        "updating": updating,
        "progress": status.get("progress", 0) if updating else None,
        "elapsedSeconds": int(time.time() - started) if updating and started else None,
        "updateError": None if updating else status.get("error"),
    }


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
    _write_json_atomic(CACHE_FILE, briefing)


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
    return send_from_directory(BASE_DIR / "public", "index.html")


@app.route("/api/briefing/update", methods=["POST"])
def update_briefing():
    translate = request.args.get("translate", "").lower() in ("1", "true", "yes")
    started = start_update(translate=translate)
    return jsonify(
        {
            **update_state(),
            "updating": True,
            "started": started,
            "message": "Update started" if started else "Update already in progress",
        }
    ), 202


@app.route("/api/briefing/update", methods=["GET"])
def update_briefing_status():
    return jsonify(update_state())


@app.route("/api/status", methods=["GET"])
def status():
    cached = read_cache()
    public_url = request.host_url.rstrip("/")
    return jsonify(
        {
            **update_state(),
            "lastUpdated": cached.get("updatedAt") if cached else None,
            "statesWithNews": cached.get("statesWithNews", 0) if cached else 0,
            "totalStates": len(STATES),
            "mobileUrl": public_url,
            "localUrl": public_url,
            "commit": (os.environ.get("RENDER_GIT_COMMIT") or "")[:7] or None,
        }
    )


@app.route("/api/briefing", methods=["GET"])
def get_briefing():
    cached = read_cache()
    if cached:
        cached["fromCache"] = True
        cached["updating"] = update_in_progress()
        return jsonify(cached)
    return jsonify(
        {
            "updatedAt": None,
            "states": [],
            "message": 'No briefing yet. Click "Update Daily Briefing" to fetch news.',
            "updating": update_in_progress(),
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
