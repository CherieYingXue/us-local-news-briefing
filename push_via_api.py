"""Push changed files to GitHub via Git Data API when git push HTTPS fails."""
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = "CherieYingXue/us-local-news-briefing"
BRANCH = "master"
BASE = Path(__file__).parent
GH = os.environ.get("GH_PATH") or shutil.which("gh") or os.path.expandvars(r"%TEMP%\\gh\\bin\\gh.exe")

DEFAULT_FILES = [
    "DEPLOY.md",
    "Procfile",
    "public/js/app.js",
    "render.yaml",
    "server.py",
    "public/js/app.js",
]


def api(method, path, data=None):
    token = subprocess.check_output([GH, "auth", "token"], text=True).strip()
    url = f"https://api.github.com{path}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "us-local-news-deploy",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main():
    message = sys.argv[1] if len(sys.argv) > 1 else "Deploy update"
    files = sys.argv[2:] if len(sys.argv) > 2 else DEFAULT_FILES
    files = list(dict.fromkeys(f.replace("\\", "/") for f in files if (BASE / f).exists()))

    ref = api("GET", f"/repos/{REPO}/git/ref/heads/{BRANCH}")
    parent_sha = ref["object"]["sha"]
    parent_commit = api("GET", f"/repos/{REPO}/git/commits/{parent_sha}")
    base_tree_sha = parent_commit["tree"]["sha"]

    tree_entries = []
    for rel in files:
        content = (BASE / rel).read_text(encoding="utf-8")
        blob = api(
            "POST",
            f"/repos/{REPO}/git/blobs",
            {"content": content, "encoding": "utf-8"},
        )
        tree_entries.append(
            {"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )

    tree = api(
        "POST",
        f"/repos/{REPO}/git/trees",
        {"base_tree": base_tree_sha, "tree": tree_entries},
    )
    commit = api(
        "POST",
        f"/repos/{REPO}/git/commits",
        {
            "message": message,
            "tree": tree["sha"],
            "parents": [parent_sha],
        },
    )
    api(
        "PATCH",
        f"/repos/{REPO}/git/refs/heads/{BRANCH}",
        {"sha": commit["sha"], "force": False},
    )
    print(f"Pushed commit {commit['sha'][:8]} to {BRANCH}")
    print(f"https://github.com/{REPO}/commit/{commit['sha']}")


if __name__ == "__main__":
    main()
