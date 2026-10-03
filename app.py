"""Noble Search — a job search portal for transitioning military and veterans.

Single-user, self-hosted web app: Python 3.12 standard library + SQLite + one static page.
Runs a daily search, rates fit with AI, drafts resumes and application text. User fields
(status, notes, viewed) are never overwritten by a search.

Copyright (c) 2026 Kenneth Haynes and CyberCloudAI. PolyForm Noncommercial License 1.0.0.
"""
import csv
import io
import json
import os
import sqlite3
import threading
import time
import base64
import hmac
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import drafts
import fit
import llm
import profile_store as ps
import resume
import discover
import search
import suggest
import sources

DB_PATH = os.environ.get("DB_PATH", "/data/jobs.db")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8093"))
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "30"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

LISTING_FIELDS = [
    "title", "company", "location", "work_mode", "employment_type", "posted_date",
    "closing_date", "salary", "clearance", "sources", "link", "fit", "fit_reason",
    "lane", "open_status", "description",
]
USER_STATUSES = {"", "interested", "applied", "interviewing", "offer", "dismissed"}
WATCH_STATUSES = ("interested", "applied", "interviewing", "offer")

_db_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with _db_lock, db() as conn:
        cols = ", ".join(f"{f} TEXT DEFAULT ''" for f in LISTING_FIELDS)
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS jobs (
                job_key TEXT PRIMARY KEY,
                {cols},
                first_seen TEXT, last_seen TEXT,
                status TEXT DEFAULT '', notes TEXT DEFAULT '',
                viewed INTEGER DEFAULT 0, status_changed TEXT DEFAULT '',
                updated_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)"""
        )
        have = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        for f in LISTING_FIELDS:  # add columns introduced after first install
            if f not in have:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {f} TEXT DEFAULT ''")


def housekeeping_loop():
    """Every 30 minutes: remove expired jobs (and their drafts)."""
    while True:
        time.sleep(30 * 60)
        try:
            purge_expired()
        except Exception as exc:  # noqa: BLE001
            print(f"purge: {exc}", flush=True)


def ref_date(r):
    """The date a job's 30-day window counts from: posted date, else when it was last seen."""
    return (r.get("posted_date") or r.get("last_seen") or r.get("first_seen") or "")[:10]


def expires_on(r):
    """Date the job (and its drafts) will be removed; None for watchlist jobs (kept)."""
    if r.get("status") in WATCH_STATUSES:
        return None
    ref = ref_date(r)
    try:
        return (datetime.fromisoformat(ref) + timedelta(days=MAX_AGE_DAYS + 1)).date().isoformat()
    except ValueError:
        return None


def is_active(r):
    """Listing still in the 30-day window (by posted or last_seen date)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).date().isoformat()
    ref = ref_date(r)
    return ref >= cutoff if ref else True


def purge_expired():
    """Delete jobs past their 30-day window that are not on the watchlist — with their drafts."""
    with _db_lock, db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT job_key, status, posted_date, last_seen, first_seen FROM jobs")]
    gone = [r["job_key"] for r in rows if r.get("status") not in WATCH_STATUSES and not is_active(r)]
    for key in gone:
        drafts.delete_for_job(key)
    if gone:
        with _db_lock, db() as conn:
            conn.executemany("DELETE FROM jobs WHERE job_key=?", [(k,) for k in gone])
        print(f"purge: removed {len(gone)} expired jobs and their drafts", flush=True)
    return len(gone)


def list_jobs():
    with _db_lock, db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM jobs")]
    for r in rows:
        r["has_description"] = bool(r.pop("description", ""))
    for r in rows:
        r["watch"] = r["status"] in WATCH_STATUSES
        r["active"] = is_active(r)
        r["expires"] = expires_on(r)
    # Watchlist items never age out; everything else (dismissed too) shows only inside the 30-day window
    return [r for r in rows if r["watch"] or r["active"]]


def update_job(key, data):
    sets, args = [], []
    if "status" in data:
        st = str(data["status"] or "").lower()
        if st not in USER_STATUSES:
            raise ValueError("bad status")
        sets += ["status=?", "status_changed=?"]
        args += [st, now_iso()]
    if "notes" in data:
        sets.append("notes=?")
        args.append(str(data["notes"])[:20000])
    if "viewed" in data:
        sets.append("viewed=?")
        args.append(1 if data["viewed"] else 0)
    if not sets:
        return
    sets.append("updated_at=?")
    args.append(now_iso())
    with _db_lock, db() as conn:
        cur = conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_key=?", args + [key])
        if cur.rowcount == 0:
            raise KeyError(key)


def export_csv():
    rows = list_jobs()
    buf = io.StringIO()
    fields = ["status", "notes", "posted_date", "closing_date", "title", "company", "location",
              "work_mode", "salary", "clearance", "fit", "fit_reason", "link", "first_seen"]
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def setup_state(has_run):
    """Getting-started checklist shown on the Jobs page until the basics are done."""
    s = ps.get_settings()
    p = ps.get_profile()
    cfg = search.get_config()
    prof_missing = [x for x, ok in (("your full name", (p.get("full_name") or "").strip()),
                                    ('a home town the search can read, like "Melbourne, FL" (Home location or City, State)',
                                     sources.home_town(p))) if not ok]
    ai = (s.get("provider") == "ollama" and bool(s.get("ollama_model"))) or bool(s.get("anthropic_key_set"))
    steps = [
        {"id": "ai", "done": ai, "view": "settings", "label": "Add your Claude API key",
         "hint": "Settings → AI model. It reads each job and writes your drafts (a few dollars a month)."},
        {"id": "profile", "done": not prof_missing, "view": "profile", "label": "Fill in your Profile",
         "hint": ("Still needed: " + "; ".join(prof_missing) + ".") if prof_missing else
                 "Name, contact details, home town, search radius, target levels and fields."},
        {"id": "career", "done": bool(ps.list_resumes()) or len(ps.get_inventory().strip()) > 200,
         "view": "profile", "label": "Upload your resume (.docx) and build your career inventory",
         "hint": "Drafts only use facts from these. The more detail (budgets, headcount, results), the better."},
        {"id": "search", "done": bool(cfg.get("boards") or cfg.get("usajobs_key_set") or cfg.get("jsearch_key_set")),
         "view": "search", "label": "Choose where to search",
         "hint": "Add employers by name, and/or free USAJOBS and JSearch keys in Settings. Use ✨ Suggest for towns and searches."},
        {"id": "run", "done": has_run, "view": "search", "label": "Run your first search",
         "hint": "Search → Run now. After that it runs on its own every weekday morning."},
    ]
    return {"steps": steps, "done": all(x["done"] for x in steps)}


def jsearch_publishers():
    """Which job boards JSearch results actually came from (jobs currently in the dashboard)."""
    import re as _re
    counts = {}
    with _db_lock, db() as conn:
        for (src,) in conn.execute("SELECT sources FROM jobs WHERE sources LIKE '%JSearch (%'"):
            for m in _re.finditer(r"JSearch \(([^)]+)\)", src or ""):
                counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def drafts_library():
    """Every job that has drafts, with its drafts and when it will be removed."""
    today = datetime.now(timezone.utc).date()
    by_job = {}
    for d in drafts.list_all():
        by_job.setdefault(d["job_key"], []).append(d)
    if not by_job:
        return {"jobs": [], "retention_days": MAX_AGE_DAYS}
    with _db_lock, db() as conn:
        keys = list(by_job)
        jobs = {r["job_key"]: dict(r) for r in conn.execute(
            f"SELECT job_key, title, company, status, posted_date, last_seen, first_seen, link FROM jobs "
            f"WHERE job_key IN ({','.join('?' * len(keys))})", keys)}
    out = []
    for key, ds in by_job.items():
        j = jobs.get(key)
        if not j:
            continue
        exp = expires_on(j)
        days_left = (datetime.fromisoformat(exp).date() - today).days if exp else None
        out.append({"job_key": key, "title": j["title"], "company": j["company"], "status": j["status"] or "",
                    "link": j["link"], "expires": exp, "days_left": days_left,
                    "kept": j["status"] in WATCH_STATUSES, "drafts": ds})
    out.sort(key=lambda x: (x["days_left"] is None, x["days_left"] if x["days_left"] is not None else 0))
    return {"jobs": out, "retention_days": MAX_AGE_DAYS}


class Handler(BaseHTTPRequestHandler):
    server_version = "NobleSearch/1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj))

    def _authorized(self):
        """Optional password (APP_PASSWORD in .env). Any user name; the browser asks once."""
        if not APP_PASSWORD or urlparse(self.path).path == "/health":
            return True
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(auth[6:]).decode("utf-8", "replace").partition(":")
                if hmac.compare_digest(pw.encode(), APP_PASSWORD.encode()):
                    return True
            except ValueError:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Noble Search"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self._authorized():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/jobs/setup":
            return self._json(200, setup_state(bool(search.recent_runs(1))))
        if path == "/jobs/list":
            runs = search.recent_runs(1)
            return self._json(200, {"jobs": list_jobs(), "fit": fit.status(), "search": {
                                        "status": search.status(), "last": runs[0] if runs else None},
                                    "draft_counts": drafts.counts_by_job(), "queue": drafts.queue_state(),
                                    "setup": setup_state(bool(runs))})
        qs = parse_qs(urlparse(self.path).query)
        if path == "/profile":
            return self._json(200, {"profile": ps.get_profile(), "resumes": ps.list_resumes(),
                                    "home_read": sources.home_town(ps.get_profile()),
                                    "inventory": ps.get_inventory(),
                                    "inventory_drafts": drafts.list_for(drafts.PROFILE_KEY)[:3]})
        if path == "/settings":
            s = ps.get_settings()
            s["labels"] = {"ollama": llm.model_label(s, "ollama"), "anthropic": llm.model_label(s, "anthropic")}
            return self._json(200, s)
        if path == "/drafts/list":
            return self._json(200, {"drafts": drafts.list_for(qs.get("job_key", [""])[0]),
                                    "queue": drafts.queue_state()})
        if path == "/drafts/library":
            with open(os.path.join(STATIC_DIR, "library.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/drafts/all":
            return self._json(200, drafts_library())
        if path == "/drafts/txt":
            info, row = drafts.get(int(qs.get("id", ["0"])[0] or 0))
            if not row:
                return self._json(404, {"error": "no draft"})
            data = (row.get("text") or "").encode("utf-8")
            kind = "Application" if row.get("kind") == "application" else "Resume"
            company = "".join(ch for ch in (drafts._job(row["job_key"]) or {}).get("company", "") if ch.isalnum())[:30]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{kind}_{company or "job"}_{row["id"]}.txt"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if path == "/drafts/docx":
            info, row = drafts.get(int(qs.get("id", ["0"])[0] or 0))
            if not row or not info["has_docx"]:
                return self._json(404, {"error": "no document"})
            with open(row["docx_path"], "rb") as f:
                data = f.read()
            name = (ps.get_profile().get("full_name") or "Resume").replace(" ", "_")
            company = "".join(ch for ch in os.path.basename(row["docx_path"]).split("-", 2)[-1] if ch.isalnum() or ch == ".")
            fname = f"{name}_Resume_{company}" if company.endswith(".docx") else f"{name}_Resume.docx"
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if path == "/jobs/export.csv":
            self.send_response(200)
            data = export_csv().encode("utf-8")
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", "attachment; filename=job-search.csv")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if path == "/search":
            cfg = search.get_config()
            return self._json(200, {"config": cfg, "boards": search.board_list(cfg), "status": search.status(),
                                    "runs": search.recent_runs(8), "next_run": search.next_run(cfg),
                                    "fit": fit.status(), "unrated": len(fit.pending_keys()),
                                    "jsearch_monthly": search.jsearch_monthly_estimate(cfg),
                                    "jsearch_publishers": jsearch_publishers(),
                                    "notify": search.notify.status(),
                                    "readers": sources.READER_LABELS,
                                    "title_presets": {k: {"label": l, "include": v} for k, (l, v) in sources.TITLE_PRESETS.items()}})
        if path == "/health":
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})

    def _post_extra(self, path, data):
        """Profile / settings / drafts endpoints. Returns a dict, or None if the path isn't ours."""
        if path == "/profile/save":
            if "profile" in data:
                ps.save_profile(data["profile"])
            if "inventory" in data:
                ps.save_inventory(data["inventory"])
            return {"ok": True}
        if path == "/profile/upload":
            name = ps.save_resume_upload(data.get("filename"), data.get("data_b64"))
            try:
                preview = resume.extract_docx_text(ps.resume_path(name))[:400]
            except Exception:  # noqa: BLE001
                ps.delete_resume(name)
                raise ValueError("Couldn't read that .docx file")
            return {"ok": True, "name": name, "preview": preview}
        if path == "/profile/delete-resume":
            ps.delete_resume(data.get("name"))
            return {"ok": True}
        if path == "/profile/set-template":
            ps.set_template(data.get("name"))
            return {"ok": True}
        if path == "/profile/build-inventory":
            return {"ok": True, "id": drafts.create(drafts.PROFILE_KEY, "inventory", provider=data.get("provider"))}
        if path == "/settings/save":
            return ps.save_settings(data)
        if path == "/settings/test":
            return llm.test_connection(ps.get_settings(include_secret=True), data.get("provider"))
        if path == "/search/save":
            return {"ok": True, "config": search.save_config(data)}
        if path == "/jobs/rate":
            key = str(data.get("job_key") or "")
            try:
                if key:
                    fit.run_async([key], data.get("provider"), force=True)
                else:
                    fit.run_async(None, data.get("provider"))
            except RuntimeError as e:
                raise ValueError(str(e))
            return {"ok": True}
        if path == "/search/test":
            return search.test_source(str(data.get("source") or ""))
        if path == "/search/discover":
            name = str(data.get("name") or "").strip()
            if len(name) < 2:
                raise ValueError("Type the company's name")
            return discover.discover(name, str(data.get("website") or "").strip(), search.get_config(include_secret=True))
        if path == "/search/suggest":
            return suggest.suggest(str(data.get("what") or ""))
        if path == "/search/stop":
            return {"ok": search.stop()}
        if path == "/search/run":
            only = data.get("only") or None
            try:
                search.run_async("manual", LISTING_FIELDS, only)
            except RuntimeError as e:
                raise ValueError(str(e))
            return {"ok": True}
        if path == "/drafts/fetch-jd":
            with _db_lock, db() as conn:
                row = conn.execute("SELECT link, description FROM jobs WHERE job_key=?", (data.get("job_key"),)).fetchone()
            if row and (row["description"] or "").strip() and not data.get("force"):
                return {"ok": True, "text": row["description"], "reason": "", "source": "daily search"}
            ok, text, reason = resume.fetch_posting(row["link"] if row else "")
            return {"ok": ok, "text": text, "reason": reason, "source": "posting"}
        if path == "/drafts/create":
            key = str(data.get("job_key", ""))
            with _db_lock, db() as conn:
                if not conn.execute("SELECT 1 FROM jobs WHERE job_key=?", (key,)).fetchone():
                    raise ValueError("unknown job")
            kind = data.get("kind") if data.get("kind") in ("resume", "application") else "resume"
            qs = [q.strip() for q in str(data.get("questions") or "").splitlines() if q.strip()][:25]
            return {"ok": True, "id": drafts.create(key, kind, data.get("jd_text", ""), data.get("provider"),
                                                     {"questions": qs} if qs else None)}
        if path == "/drafts/delete":
            drafts.delete(int(data.get("id") or 0))
            return {"ok": True}
        return None

    def do_POST(self):
        if not self._authorized():
            return
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        if path == "/jobs/update":
            try:
                update_job(str(data.get("job_key", "")), data)
            except KeyError:
                return self._json(404, {"error": "unknown job"})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            return self._json(200, {"ok": True})
        try:
            res = self._post_extra(path, data)
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except llm.LLMError as e:
            return self._json(200, {"ok": False, "detail": str(e)})
        if res is not None:
            return self._json(200, res)
        if path == "/jobs/mark-all-viewed":
            with _db_lock, db() as conn:
                conn.execute("UPDATE jobs SET viewed=1")
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})


def main():
    init_db()
    drafts.init()
    search.init(db, _db_lock)
    search.notify.init(db, _db_lock)
    fit.init(db, _db_lock)
    threading.Thread(target=housekeeping_loop, daemon=True).start()
    threading.Thread(target=search.scheduler_loop, args=(LISTING_FIELDS,), daemon=True).start()

    def rate_backlog():  # rate anything left unrated (e.g. after an upgrade or restart)
        time.sleep(60)
        try:
            purge_expired()
        except Exception as exc:  # noqa: BLE001
            print(f"purge: {exc}", flush=True)
        if search.get_config().get("fit_provider", "ollama") != "off" and fit.pending_keys():
            try:
                fit.run_async()
            except RuntimeError:
                pass
    threading.Thread(target=rate_backlog, daemon=True).start()
    print(f"Noble Search on :{PORT} (data in {os.path.dirname(DB_PATH)})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
