#!/usr/bin/env python3
"""
upstream-update-check.py
Checks mvanhorn/last30days-skill (upstream) for new commits since our fork diverged.
Compares upstream/main HEAD against what we last saw.
Exit 0 = no update. Exit 1 = new commits available, Slack payload on stdout.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = SKILL_ROOT / "data" / "upstream-watcher-state.json"
UPSTREAM_REPO = "mvanhorn/last30days-skill"
UPSTREAM_URL = f"https://api.github.com/repos/{UPSTREAM_REPO}"


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_known_sha": None, "last_checked": None}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_json(url):
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, headers={"User-Agent": "openclaw-last30days-watcher/1.0"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def get_local_upstream_sha():
    """Get the SHA that our local 'upstream/main' ref points to."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "upstream/main"],
            cwd=SKILL_ROOT,
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def get_merge_base():
    """Find where our fork diverged from upstream."""
    try:
        result = subprocess.run(
            ["git", "merge-base", "origin/main", "upstream/main"],
            cwd=SKILL_ROOT,
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def fetch_commits_since(since_sha, until_sha):
    """Fetch commit list between two SHAs from GitHub API."""
    try:
        data = fetch_json(f"{UPSTREAM_URL}/compare/{since_sha}...{until_sha}")
        commits = data.get("commits", [])
        return commits
    except Exception:
        return []


def build_slack_message(commits, old_sha, new_sha):
    old_short = old_sha[:8] if old_sha else "unknown"
    new_short = new_sha[:8] if new_sha else "unknown"
    n = len(commits)

    lines = [
        f":package: *last30days-skill* upstream has *{n} new commit{'s' if n != 1 else ''}* (`{old_short}` → `{new_short}`)",
        f"<https://github.com/{UPSTREAM_REPO}/compare/{old_short}...{new_short}|View changes on GitHub>",
        "",
        "*What's new:*",
    ]

    # Show up to 10 commits with messages
    for c in commits[-10:]:
        sha_short = c.get("sha", "")[:8]
        msg = c.get("commit", {}).get("message", "").split("\n")[0][:120]
        lines.append(f"• `{sha_short}` {msg}")

    if n > 10:
        lines.append(f"  _…and {n - 10} more_")

    # Show changed files summary
    try:
        data = fetch_json(f"{UPSTREAM_URL}/compare/{old_short}...{new_short}")
        files = data.get("files", [])
        if files:
            lines.append("")
            lines.append(f"*Files changed:* {len(files)}")
            # Highlight changes to key files
            key_files = [f.get("filename", "") for f in files
                         if any(k in f.get("filename", "").lower()
                                for k in ["skill.md", "research.md", "last30days.py",
                                           "briefing.md", "watchlist.md"])]
            if key_files:
                lines.append("⚠️ *Key files touched:* " + ", ".join(f"`{f}`" for f in key_files[:8]))
    except Exception:
        pass

    lines.extend([
        "",
        "To merge upstream improvements:",
        "```cd skills/last30days-official",
        "git fetch upstream",
        "git merge upstream/main",
        "git push origin main```",
        "",
        "_Our customizations: `variants/open/references/research.md` (Obsidian save), `variants/open/context.md`_",
    ])

    return "\n".join(lines)


def main():
    state = load_state()
    now = datetime.now(timezone.utc).isoformat()

    # Fetch latest upstream
    try:
        subprocess.run(
            ["git", "fetch", "upstream", "--quiet"],
            cwd=SKILL_ROOT,
            capture_output=True, timeout=30
        )
    except Exception as e:
        sys.stderr.write(f"Failed to fetch upstream: {e}\n")
        sys.exit(0)

    # Get upstream HEAD
    try:
        result = subprocess.run(
            ["git", "rev-parse", "upstream/main"],
            cwd=SKILL_ROOT,
            capture_output=True, text=True, timeout=10
        )
        upstream_sha = result.stdout.strip()
    except Exception:
        sys.exit(0)

    last_known = state.get("last_known_sha")

    # First run — just record current state
    if last_known is None:
        state["last_known_sha"] = upstream_sha
        state["last_checked"] = now
        save_state(state)
        sys.exit(0)

    # No change
    if upstream_sha == last_known:
        state["last_checked"] = now
        save_state(state)
        sys.exit(0)

    # New commits — fetch details
    commits = fetch_commits_since(last_known, upstream_sha)

    if not commits:
        # SHA changed but no commits found (force push?), still alert
        state["last_known_sha"] = upstream_sha
        state["last_checked"] = now
        save_state(state)
        print(f":package: *last30days-skill* upstream updated (`{last_known[:8]}` → `{upstream_sha[:8]}`) but commit details unavailable.\n"
              f"<https://github.com/{UPSTREAM_REPO}/commits/main|View on GitHub>")
        sys.exit(1)

    state["last_known_sha"] = upstream_sha
    state["last_checked"] = now
    save_state(state)
    print(build_slack_message(commits, last_known, upstream_sha))
    sys.exit(1)


if __name__ == "__main__":
    main()
