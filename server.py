import json
import re
import socket
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
CACHE_FILE = BASE_DIR / "cache" / "briefing.json"
STATES_FILE = BASE_DIR / "data" / "states.json"
PORT = int(__import__("os").environ.get("PORT", 3847))

app = Flask(__name__, static_folder="public", static_url_path="")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
STATES = json.loads(STATES_FILE.read_text(encoding="utf-8"))
is_updating = False
update_started_at = None

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


def translate_to_chinese(text):
    if not text or len(text) < 2:
        return ""
    trimmed = text[:450]
    url = (
        "https://api.mymemory.translated.net/get?"
        + urllib.parse.urlencode({"q": trimmed, "langpair": "en|zh-CN"})
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "US-Local-News/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if data.get("responseStatus") == 200:
            return data.get("responseData", {}).get("translatedText", "")
    except Exception:
        pass
    return ""


def fetch_state_news(state):
    try:
        feed = feedparser.parse(
            state["feed"],
            request_headers={"User-Agent": "US-Local-News-Briefing/1.0"},
        )
        entries = feed.entries or []
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


def add_translations(briefing):
    stories = [
        (state_data, story)
        for state_data in briefing["states"]
        for story in state_data["stories"]
    ]

    def translate_story(pair):
        _, story = pair
        story["titleZh"] = translate_to_chinese(story["title"]) or story["title"]
        return story

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(translate_story, stories))

    return briefing


def build_briefing():
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_state_news, state): state for state in STATES}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda r: r["state"]["code"])
    states = [
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
    briefing = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "totalStates": len(STATES),
        "statesWithNews": sum(1 for s in states if s["stories"]),
        "states": states,
    }
    return add_translations(briefing)


def read_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def write_cache(briefing):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
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
    global is_updating, update_started_at
    if is_updating:
        if update_started_at and (time.time() - update_started_at) > 300:
            is_updating = False
        else:
            return jsonify({"error": "Update already in progress", "updating": True}), 409
    is_updating = True
    update_started_at = time.time()
    try:
        briefing = build_briefing()
        write_cache(briefing)
        briefing["fromCache"] = False
        briefing["updating"] = False
        return jsonify(briefing)
    except Exception as exc:
        return jsonify({"error": str(exc), "updating": False}), 500
    finally:
        is_updating = False
        update_started_at = None


@app.route("/api/status", methods=["GET"])
def status():
    cached = read_cache()
    ip = get_local_ip()
    return jsonify(
        {
            "updating": is_updating,
            "lastUpdated": cached.get("updatedAt") if cached else None,
            "statesWithNews": cached.get("statesWithNews", 0) if cached else 0,
            "totalStates": len(STATES),
            "mobileUrl": f"http://{ip}:{PORT}",
            "localUrl": f"http://localhost:{PORT}",
        }
    )


@app.route("/api/story/<story_id>", methods=["GET"])
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
