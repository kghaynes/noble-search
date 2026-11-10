"""Check for updates, and what the About section shows.

Once a day (and when you click "Check now") the app asks GitHub for the latest Noble Search release:
one anonymous request to api.github.com — no account, no key, nothing about you or your data. If a newer
version exists, the page shows a banner with the release notes and the one-line update command. Nothing
is ever downloaded or installed automatically. Turn it off in Settings → About ("Check for updates").
"""
import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import applog
import profile_store as ps
from version import __version__

REPO = "kghaynes/noble-search"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_EVERY = timedelta(hours=24)

LINKS = {
    "website": "https://www.cybercloudai.tech/noble-search/",
    "repo": f"https://github.com/{REPO}",
    "releases": f"https://github.com/{REPO}/releases",
    "changelog": f"https://github.com/{REPO}/blob/main/CHANGELOG.md",
    "bug": f"https://github.com/{REPO}/issues/new?template=bug_report.yml",
    "employer": f"https://github.com/{REPO}/issues/new?template=employer.yml",
    "feature": f"https://github.com/{REPO}/issues/new?template=feature_request.yml",
    "privacy": f"https://github.com/{REPO}/blob/main/PRIVACY.md",
    "security": f"https://github.com/{REPO}/blob/main/SECURITY.md",
    "license": f"https://github.com/{REPO}/blob/main/LICENSE.md",
    "update_help": f"https://github.com/{REPO}#updating-backing-up-stopping-and-removing",
    "sponsor": "https://github.com/sponsors/kghaynes",
    "kofi": "https://ko-fi.com/kenhaynes",
    "company": "https://cybercloudai.tech",
}
SUPPORT_EMAIL = "support@cybercloudai.tech"

_db = None
_lock = threading.Lock()


def init(db_fn, db_lock):
    global _db
    _db = (db_fn, db_lock)


def parse(v):
    """'v0.4.1' -> (0, 4, 1). Anything unreadable sorts lowest."""
    nums = re.findall(r"\d+", str(v or "").split("-")[0])[:3]
    return tuple(int(n) for n in nums) + (0,) * (3 - len(nums)) if nums else (0, 0, 0)


def newer(latest, current=__version__):
    return parse(latest) > parse(current)


def _meta(k, v=None):
    db_fn, lock = _db
    with lock, db_fn() as conn:
        if v is None:
            r = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return json.loads(r[0]) if r and r[0] else {}
        conn.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))


def enabled():
    return ps.get_settings().get("update_check", True) is not False


def _fetch():
    req = urllib.request.Request(LATEST_URL, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": f"NobleSearch/{__version__} (update check)"})
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 — fixed https URL
        return json.loads(r.read().decode("utf-8"))


def check(force=False, fetch=None):
    """Ask GitHub for the latest release (at most once a day unless forced). Returns status()."""
    if not force and not enabled():
        return status()
    with _lock:
        last = _meta("update_check")
        when = last.get("checked")
        if not force and when:
            try:
                if datetime.now(timezone.utc) - datetime.fromisoformat(when) < CHECK_EVERY:
                    return status()
            except ValueError:
                pass
        rec = {"checked": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            rel = (fetch or _fetch)()
            rec.update(latest=str(rel.get("tag_name") or "").lstrip("v"), url=rel.get("html_url") or LINKS["releases"],
                       published=str(rel.get("published_at") or "")[:10], name=rel.get("name") or "")
            if newer(rec["latest"]):
                applog.info("update", f"Noble Search {rec['latest']} is available (you have {__version__})")
        except (urllib.error.URLError, OSError, ValueError) as e:
            rec.update({k: last.get(k) for k in ("latest", "url", "published", "name") if last.get(k)},
                       error=f"Couldn't reach GitHub: {getattr(e, 'reason', e)}")
        _meta("update_check", rec)
    return status()


def dismiss(version):
    _meta("update_dismissed", {"version": str(version or "")})
    return status()


def status():
    rec = _meta("update_check") if _db else {}
    latest = rec.get("latest") or ""
    is_newer = bool(latest) and newer(latest)
    dismissed = (_meta("update_dismissed") if _db else {}).get("version") == latest
    return {"current": __version__, "latest": latest, "newer": is_newer, "show_banner": is_newer and not dismissed,
            "url": rec.get("url") or LINKS["releases"], "published": rec.get("published", ""), "checked": rec.get("checked", ""),
            "error": rec.get("error", ""), "enabled": enabled()}


def about():
    return {"version": __version__, "update": status(), "links": LINKS, "support_email": SUPPORT_EMAIL}


def loop():
    """Background thread: check a minute after start, then hourly (check() itself limits it to once a day)."""
    time.sleep(60)
    while True:
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            applog.error("update", str(exc))
        time.sleep(3600)
