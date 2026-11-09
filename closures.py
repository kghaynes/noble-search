"""Closed postings: notice when a posting is taken down, keep a small record of it, and turn the
records into an "apply fast" signal per employer.

How a posting is marked closed (see docs/closed-postings.md for the full story):
  employer  — it vanished from the employer's own hiring system (Workday, Greenhouse, Lever, Ashby,
              SmartRecruiters, BambooHR, Eightfold or its job sitemap) in a read that worked
  usajobs   — it dropped out of USAJOBS (the API only lists open announcements)
  link      — a watchlist job whose posting link now answers "not found"
  you       — you pressed "Posting closed"
Job-board results (JSearch: LinkedIn, Indeed, …) are never closed automatically: a search that didn't
return a job proves nothing, because the searches take turns and only return the top results.

Safety rules, so a bad day never closes good jobs:
  - only after a read that worked (no failure, not stopped, not skipped)
  - not on the first read of a source, or the first read after its settings changed
    (its link, or for USAJOBS the nationwide choice, agencies, grade or home town)
  - not when a source returned under half the listings it usually does
  - a job still listed under the same title anywhere in that source stays open, even if your title
    rules changed and it no longer counts as a match
  - only jobs younger than the window the reader scans (older watchlist jobs get the link check)
A closed job that shows up again on its employer's site or USAJOBS reopens on its own (a repost).

The `closures` and `passes` tables outlive the 30-day cleanup on purpose: they hold no notes, drafts or
descriptions — just enough to say how long each employer's postings stay up.
"""
import ipaddress
import json
import socket
import sqlite3
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

import applog

WATCH = ("interested", "applied", "interviewing", "offer")
DETECTED_BY = {"employer": "Taken down from the employer's hiring site",
               "usajobs": "Removed from USAJOBS",
               "link": "The posting link no longer works",
               "you": "You marked it closed"}
REASONS = {"certifications": "Certifications", "skills": "Specific skills", "salary": "Salary",
           "location": "Location", "other": "Other"}
MIN_CLOSURES = 5        # closures an employer needs before its speed is shown
FAST_DAYS = 10          # "closes fast" when its postings stay up this many days or fewer (median)
MIN_SEEN_SHARE = 0.5    # a read with fewer than half the usual listings closes nothing
SEEN_CAP = 8000         # sources.Context stops recording titles past this per source
AGE_MARGIN = 2          # days short of the scan window a job must be to count as "should be listed"
LINK_CHECK_MAX = 40
AGGREGATORS = ("linkedin.com", "indeed.com", "glassdoor.", "ziprecruiter.com", "dice.com", "monster.com",
               "simplyhired.com", "jooble.org", "talent.com", "builtin.com")
UA = "NobleSearch/1 (job posting status check; self-hosted)"

_db = None


def init(db_fn, db_lock):
    global _db
    _db = (db_fn, db_lock)
    with db_lock, db_fn() as conn:
        have = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        for col in ("closed_on", "closed_by"):
            if col not in have:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT DEFAULT ''")
        conn.execute("""CREATE TABLE IF NOT EXISTS closures (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT, company TEXT, title TEXT, source TEXT,
            lane TEXT, fit TEXT, posted_date TEXT, first_seen TEXT, closing_date TEXT, closed_on TEXT,
            detected_by TEXT, status_at_close TEXT, days_up INTEGER, early_days INTEGER,
            reopened_on TEXT DEFAULT '', run_id INTEGER)""")
        conn.execute("CREATE INDEX IF NOT EXISTS closures_company ON closures(company)")
        conn.execute("""CREATE TABLE IF NOT EXISTS passes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT, company TEXT, title TEXT, lane TEXT, fit TEXT,
            status_before TEXT, reasons TEXT, note TEXT, created TEXT)""")


def _today():
    return date.today()


def _d(s):
    try:
        return date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def _norm(t):
    return " ".join(str(t or "").lower().split())


def still_listed(stored_title, seen_titles):
    """True if the job's title is among the titles the reader saw (USAJOBS adds " (GS-15)" to ours)."""
    t = _norm(stored_title)
    return any(s == t or t.startswith(s + " (") for s in seen_titles)


def days_up(row, closed):
    start = _d(row.get("posted_date")) or _d(row.get("first_seen"))
    return max(0, (closed - start).days) if start else None


def early_days(row, closed):
    end = _d(row.get("closing_date"))
    return max(0, (end - closed).days) if end else 0


# --------------------------------------------------------------------------- marking
def _mark(conn, row, by, today, run_id=None):
    conn.execute("UPDATE jobs SET closed_on=?, closed_by=?, open_status='Closed' WHERE job_key=?",
                 (today.isoformat(), by, row["job_key"]))
    conn.execute("""INSERT INTO closures (job_key, company, title, source, lane, fit, posted_date, first_seen,
                    closing_date, closed_on, detected_by, status_at_close, days_up, early_days, run_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (row["job_key"], row.get("company") or "", row.get("title") or "", row.get("sources") or "",
                  row.get("lane") or "", row.get("fit") or "", row.get("posted_date") or "", row.get("first_seen") or "",
                  row.get("closing_date") or "", today.isoformat(), by, row.get("status") or "",
                  days_up(row, today), early_days(row, today), run_id))


def reopen_in(conn, key, today=None):
    """Clear a closure (the job is listed again). Called inside an open transaction."""
    today = today or _today()
    conn.execute("UPDATE jobs SET closed_on='', closed_by='', open_status='Open' WHERE job_key=?", (key,))
    conn.execute("UPDATE closures SET reopened_on=? WHERE id=(SELECT MAX(id) FROM closures WHERE job_key=? AND reopened_on='')",
                 (today.isoformat(), key))


def set_closed(key, closed):
    """The person pressed "Posting closed" (closed=True) or "Reopen" (closed=False)."""
    db_fn, lock = _db
    today = _today()
    with lock, db_fn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_key=?", (key,)).fetchone()
        if not row:
            raise KeyError(key)
        row = dict(row)
        if closed and not row.get("closed_on"):
            _mark(conn, row, "you", today)
        elif not closed and row.get("closed_on"):
            reopen_in(conn, key, today)


def should_reopen(existing, row):
    """A closed job seen again reopens — unless you closed it and only a job board still shows it
    (boards often keep stale listings)."""
    if _db is None or not existing.get("closed_on"):
        return False
    strong = "careers" in str(row.get("sources") or "") or str(row.get("sources") or "") == "USAJOBS"
    return existing.get("closed_by") != "you" or strong


# --------------------------------------------------------------------------- detection after a source read
def _meta(conn, k, v=None):
    if v is None:
        r = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else None
    conn.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def _source_jobs(conn, name, kind):
    if kind == "usajobs":
        q, args = "SELECT * FROM jobs WHERE instr(sources, 'USAJOBS') > 0", ()
    else:   # employer reads label their jobs "<Name> careers (Workday)" etc.
        q, args = "SELECT * FROM jobs WHERE lower(company)=lower(?) AND instr(sources, ?) > 0", (name, f"{name} careers")
    return [dict(r) for r in conn.execute(q + " AND COALESCE(closed_on,'')=''", args)]


def detect(name, kind, st, seen_titles, fingerprint, max_age, run_id=None, today=None):
    """After one source read: mark the jobs it no longer lists as closed. Returns (closed_keys, note)."""
    today = today or _today()
    if kind == "jsearch":
        return [], "job boards never close jobs automatically"
    if not st.get("ok") or st.get("skipped"):
        return [], "read didn't work"
    seen = {_norm(t) for t in seen_titles if t}
    n = len(seen_titles)
    if n == 0 or n >= SEEN_CAP:
        return [], "no complete list of titles"
    db_fn, lock = _db
    with lock, db_fn() as conn:
        prev_raw = _meta(conn, f"closures:{name}")
        try:
            prev = json.loads(prev_raw) if prev_raw else None
        except json.JSONDecodeError:
            prev = None
        if not prev or prev.get("fp") != fingerprint:
            _meta(conn, f"closures:{name}", json.dumps({"fp": fingerprint, "seen": n}))
            return [], "first read (or first since its settings changed)"
        if n < MIN_SEEN_SHARE * (prev.get("seen") or 0):
            return [], f"only {n} listings (usually {prev.get('seen')})"
        _meta(conn, f"closures:{name}", json.dumps({"fp": fingerprint, "seen": n}))
        closed = []
        for row in _source_jobs(conn, name, kind):
            if row.get("last_seen") == today.isoformat() or still_listed(row.get("title"), seen):
                continue
            start = _d(row.get("posted_date")) or _d(row.get("first_seen"))
            if not start or (today - start).days > max_age - AGE_MARGIN:
                continue   # older than the window this reader scans: only the link check may close it
            _mark(conn, row, "usajobs" if kind == "usajobs" else "employer", today, run_id)
            closed.append(row["job_key"])
    if closed:
        applog.info("closed", f"{name}: {len(closed)} posting(s) taken down")
    return closed, ""


# --------------------------------------------------------------------------- link check (watchlist only)
def _public_host(url):
    try:
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        for info in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True
    except (OSError, ValueError):
        return False


def link_gone(url, timeout=15):
    """True only when the site plainly says the posting is gone (404/410, or Greenhouse's error redirect).
    Bot checks, timeouts and anything else mean "don't know" (False)."""
    if not url or any(a in url.lower() for a in AGGREGATORS) or not _public_host(url):
        return False
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 — public http(s) only, checked above
            final = r.geturl() or ""
            return "greenhouse.io" in final and "error=true" in final
    except urllib.error.HTTPError as e:
        return e.code in (404, 410)
    except (urllib.error.URLError, OSError, ValueError):
        return False


def link_check(run_id=None, limit=LINK_CHECK_MAX, checker=None):
    """Check the posting links of open watchlist jobs. Returns closed keys."""
    checker = checker or link_gone
    db_fn, lock = _db
    with lock, db_fn() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(WATCH))}) AND COALESCE(closed_on,'')='' "
            "AND link LIKE 'http%' ORDER BY last_seen", WATCH)][:limit]
    gone = [r for r in rows if checker(r.get("link"))]
    if gone:
        today = _today()
        with lock, db_fn() as conn:
            for r in gone:
                _mark(conn, r, "link", today, run_id)
        applog.info("closed", f"Link check: {len(gone)} watchlist posting(s) gone")
    return [r["job_key"] for r in gone]


# --------------------------------------------------------------------------- the "apply fast" signal
def employer_speed():
    """{company (lowercase): {"n": closures, "median": days postings stayed up}} — reposts excluded."""
    if _db is None:
        return {}
    db_fn, lock = _db
    try:
        with lock, db_fn() as conn:
            rows = conn.execute("SELECT lower(company), days_up FROM closures WHERE reopened_on='' AND days_up IS NOT NULL").fetchall()
    except sqlite3.OperationalError:   # not set up yet
        return {}
    by = {}
    for c, d in rows:
        by.setdefault(c, []).append(d)
    return {c: {"n": len(v), "median": round(statistics.median(v))} for c, v in by.items()}


def fast_days(company, speed):
    """Median days an employer's postings stay up, when it's known (5+ closures) and short (≤10)."""
    s = speed.get(str(company or "").lower())
    return s["median"] if s and s["n"] >= MIN_CLOSURES and s["median"] <= FAST_DAYS else None


def closed_in_run(run_id):
    """Jobs closed by this search run, with their current status (for the morning email)."""
    if not run_id or _db is None:
        return []
    db_fn, lock = _db
    try:
        conn_rows = None
        with lock, db_fn() as conn:
            conn_rows = [dict(r) for r in conn.execute(
            """SELECT c.*, COALESCE(j.status,'') AS status, j.link AS link FROM closures c
               LEFT JOIN jobs j ON j.job_key=c.job_key WHERE c.run_id=? AND c.reopened_on='' ORDER BY c.company""", (run_id,))]
        return conn_rows
    except sqlite3.OperationalError:
        return []


# --------------------------------------------------------------------------- "Not for me" reasons (watchlist only)
def record_pass(row, reasons, note=""):
    clean = [r for r in (reasons or []) if r in REASONS]
    db_fn, lock = _db
    with lock, db_fn() as conn:
        conn.execute("""INSERT INTO passes (job_key, company, title, lane, fit, status_before, reasons, note, created)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (row["job_key"], row.get("company") or "", row.get("title") or "", row.get("lane") or "",
                      row.get("fit") or "", row.get("status") or "", json.dumps(clean), str(note or "")[:500],
                      datetime.now(timezone.utc).isoformat(timespec="seconds")))


def summary():
    """Counts for the Search page: closures by how they were detected, employer speeds, pass reasons."""
    db_fn, lock = _db
    with lock, db_fn() as conn:
        by = dict(conn.execute("SELECT detected_by, COUNT(*) FROM closures WHERE reopened_on='' GROUP BY detected_by").fetchall())
        reposts = conn.execute("SELECT COUNT(*) FROM closures WHERE reopened_on!=''").fetchone()[0]
        names = dict(conn.execute("SELECT lower(company), MAX(company) FROM closures GROUP BY lower(company)").fetchall())
        reasons = {}
        for (rs,) in conn.execute("SELECT reasons FROM passes"):
            for r in json.loads(rs or "[]"):
                reasons[r] = reasons.get(r, 0) + 1
    speed = employer_speed()
    top = sorted(([names.get(c, c), s["n"], s["median"]] for c, s in speed.items() if s["n"] >= MIN_CLOSURES), key=lambda x: x[2])
    return {"by": by, "reposts": reposts, "employers": top[:15], "reasons": reasons,
            "min_closures": MIN_CLOSURES, "fast_days": FAST_DAYS}
