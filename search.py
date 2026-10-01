"""Built-in daily search: settings, scheduler, runner and merge into the jobs table.

Runs entirely on this server. Results are merged by job_key (lowercase
"company|title"). Merging never touches status / notes / viewed, never blanks a
field, and never replaces a fit rating, lane, location, salary or clearance that
is already there (those may have come from a better source).
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

import fit
import notify
import profile_store as ps
import sources

SEARCH_JSON = os.path.join(ps.DATA_DIR, "search.json")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

DEFAULTS = {
    "enabled": True,
    "run_time": "05:30",
    "days": "Mon,Tue,Wed,Thu,Fri",
    "timezone": os.environ.get("TZ") or "America/New_York",
    "max_age_days": int(os.environ.get("MAX_AGE_DAYS", "30")),
    "title_include": sources.DEFAULT_TITLE_INCLUDE,
    "title_exclude": sources.DEFAULT_TITLE_EXCLUDE,
    "local_places": sources.DEFAULT_PLACES,
    "boards": sources.DEFAULT_BOARDS,
    "usajobs_api_key": "",
    "usajobs_email": "",
    "usajobs_min_grade": 14,
    "jsearch_api_key": "",
    "jsearch_provider": "rapidapi",
    "jsearch_queries": sources.DEFAULT_JSEARCH_QUERIES,
    "jsearch_pages": 1,
    "fit_provider": "anthropic",
    "email_enabled": False,
    "email_to": "",
    "email_from_name": "Noble Search",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_password": "",
    "email_manual_runs": False,
    "dashboard_url": "http://localhost:8093",
    "ntfy_url": "",
    "ntfy_topic": "",
    "ntfy_token": "",
    "detail_cap": 60,
    "max_pages": 150,
}
SECRETS = ("usajobs_api_key", "jsearch_api_key", "smtp_password", "ntfy_token")
LIMITS = {"smtp_port": (1, 65535), "max_age_days": (1, 60), "usajobs_min_grade": (1, 15), "jsearch_pages": (1, 5), "detail_cap": (5, 300), "max_pages": (5, 500)}

# never overwritten once they have a value (a rating or a hand-checked detail may already be there)
KEEP_IF_SET = {"fit", "fit_reason", "lane", "location", "work_mode", "salary", "clearance", "employment_type"}
AGGREGATOR_LINK = ("indeed.com", "dice.com", "ziprecruiter.com", "linkedin.com", "workopia", "glassdoor")

_cfg_lock = threading.Lock()
_run_lock = threading.Lock()
_state = {"running": False, "current": "", "started": None}
_db = None  # (db_fn, db_lock) supplied by app.init


# --------------------------------------------------------------------------- settings
def get_config(include_secret=False):
    with _cfg_lock:
        try:
            with open(SEARCH_JSON, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    if not include_secret:
        for sk in SECRETS:
            k = cfg.get(sk) or ""
            cfg[sk] = ("••••" + k[-4:]) if k else ""
            cfg[sk.replace("_api_key", "_key_set") if sk.endswith("_api_key") else sk + "_set"] = bool(k)
    return cfg


def save_config(new):
    with _cfg_lock:
        try:
            with open(SEARCH_JSON, encoding="utf-8") as f:
                cur = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            cur = {}
        for k, v in (new or {}).items():
            if k not in DEFAULTS:
                continue
            if k in SECRETS:
                v = str(v or "").strip()
                if not v or v.startswith("••••"):
                    continue
                if v == "CLEAR":
                    v = ""
            elif k in ("enabled", "email_enabled", "email_manual_runs"):
                v = bool(v) and v not in ("false", "0", "off")
            elif k in LIMITS:
                lo, hi = LIMITS[k]
                try:
                    v = max(lo, min(hi, int(v)))
                except (TypeError, ValueError):
                    v = DEFAULTS[k]
            elif k == "jsearch_provider":
                v = v if v in sources.JSEARCH_HOSTS else "rapidapi"
            elif k == "fit_provider":
                v = v if v in ("ollama", "anthropic", "off") else "anthropic"
            elif k == "run_time":
                v = str(v).strip()
                try:
                    datetime.strptime(v, "%H:%M")
                except ValueError:
                    v = DEFAULTS[k]
            elif k == "timezone":
                v = str(v).strip() or DEFAULTS[k]
                try:
                    ZoneInfo(v)
                except Exception:  # noqa: BLE001
                    raise ValueError(f"Unknown time zone: {v}")
            elif k == "boards":
                if not isinstance(v, list):
                    raise ValueError("boards must be a list")
                clean = []
                for b in v:
                    if not isinstance(b, dict) or not str(b.get("name", "")).strip() or not str(b.get("url", "")).strip():
                        continue
                    t = str(b.get("type") or "auto").strip().lower()
                    clean.append({"name": str(b["name"]).strip()[:80], "url": str(b["url"]).strip()[:500],
                                  "type": t if t in list(sources.READERS) + ["auto", "off"] else "auto",
                                  "note": str(b.get("note") or "")[:300]})
                v = clean
            else:
                v = str(v)[:20000]
            cur[k] = v
        os.makedirs(os.path.dirname(SEARCH_JSON), exist_ok=True)
        tmp = SEARCH_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, SEARCH_JSON)
    return get_config()


def test_source(name):
    cfg = get_config(include_secret=True)
    if name == "email":
        try:
            return notify.send_test(cfg)
        except ValueError as e:
            return {"ok": False, "detail": str(e)}
    return sources.test_source(name, cfg, sources.Context(cfg, ps.get_profile()))


def jsearch_monthly_estimate(cfg):
    n = len([l for l in str(cfg.get("jsearch_queries") or "").splitlines() if l.strip() and not l.strip().startswith("#")])
    days = len([d for d in str(cfg.get("days") or "").split(",") if d.strip()])
    return round(n * int(cfg.get("jsearch_pages") or 1) * days * 4.33)


def board_list(cfg):
    out = []
    for b in cfg.get("boards") or []:
        kind = (b.get("type") or "auto").lower()
        detected, _ = sources.detect(b.get("url") or "")
        eff = detected if kind in ("auto", "") else kind
        out.append({**b, "detected": eff, "label": sources.READER_LABELS.get(eff, eff)})
    return out


# --------------------------------------------------------------------------- database
def init(db_fn, db_lock):
    global _db
    _db = (db_fn, db_lock)
    with db_lock, db_fn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS search_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT, trigger TEXT,
            status TEXT, found INTEGER DEFAULT 0, added INTEGER DEFAULT 0, updated INTEGER DEFAULT 0,
            detail TEXT DEFAULT '[]')""")
        conn.execute("UPDATE search_runs SET status='interrupted', finished=? WHERE status='running'", (_now(),))
        # tidy values written by the first release of the search
        conn.execute("UPDATE jobs SET location='Remote (US)' WHERE location IN ('United States-Remote','US-Remote','Remote','USA-Remote')")
        conn.execute("UPDATE jobs SET location=substr(location, 1, length(location)-4) WHERE location LIKE '%, __, US'")
        conn.execute("UPDATE jobs SET clearance='' WHERE clearance='Clearance mentioned'")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _meta_get(conn, k):
    r = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else None


def _meta_set(conn, k, v):
    conn.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def merge_rows(rows, listing_fields):
    """Upsert search results. Returns (added, updated, added_keys)."""
    db_fn, lock = _db
    today = datetime.now().date().isoformat()
    cutoff = (datetime.now().date() - timedelta(days=int(os.environ.get("MAX_AGE_DAYS", "30")))).isoformat()
    ts = _now()
    added = updated = 0
    added_keys = []
    with lock, db_fn() as conn:
        for r in rows:
            key = f"{r['company']}|{r['title']}".strip().lower()
            if key == "|":
                continue
            ex = conn.execute("SELECT * FROM jobs WHERE job_key=?", (key,)).fetchone()
            if ex is None and r.get("posted_date") and r["posted_date"][:10] < cutoff:
                continue  # already past the window — don't add (or re-add) it
            if ex is None:
                fields = ["job_key"] + listing_fields + ["first_seen", "last_seen", "updated_at"]
                conn.execute(f"INSERT INTO jobs ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                             [key] + [str(r.get(f) or "") for f in listing_fields] + [today, today, ts])
                added += 1
                added_keys.append(key)
                continue
            ex = dict(ex)
            sets = {}
            for f in listing_fields:
                new, old = str(r.get(f) or ""), str(ex.get(f) or "")
                if not new or new == old:
                    continue
                if f == "sources":
                    have = [s.strip() for s in old.replace(";", ",").split(",") if s.strip()]
                    if new not in have:
                        sets[f] = "; ".join(have + [new]) if have else new
                elif f == "link":
                    if not old or any(a in old for a in AGGREGATOR_LINK):
                        sets[f] = new  # prefer the employer's own posting
                elif f == "description":
                    if len(new) > len(old):
                        sets[f] = new
                elif f in KEEP_IF_SET:
                    if not old:
                        sets[f] = new
                elif f == "posted_date":
                    if not old:
                        sets[f] = new
                else:
                    sets[f] = new
            sets["last_seen"] = today
            sets["updated_at"] = ts
            conn.execute(f"UPDATE jobs SET {', '.join(k + '=?' for k in sets)} WHERE job_key=?",
                         list(sets.values()) + [key])
            if len(sets) > 2:
                updated += 1
    return added, updated, added_keys


# --------------------------------------------------------------------------- running
def status():
    return dict(_state)


def recent_runs(limit=10):
    db_fn, lock = _db
    with lock, db_fn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM search_runs ORDER BY id DESC LIMIT ?", (limit,))]
    for r in rows:
        try:
            r["detail"] = json.loads(r.get("detail") or "[]")
        except json.JSONDecodeError:
            r["detail"] = []
    return rows


def run(trigger="manual", listing_fields=None, only=None):
    """Run every enabled source (or only the named ones). Blocking. Returns the run record id."""
    if not _run_lock.acquire(blocking=False):
        raise RuntimeError("A search is already running")
    db_fn, lock = _db
    try:
        _state.update(running=True, current="starting", started=_now())
        with lock, db_fn() as conn:
            cur = conn.execute("INSERT INTO search_runs(started, trigger, status) VALUES(?,?, 'running')",
                               (_now(), trigger))
            run_id = cur.lastrowid
        cfg = get_config(include_secret=True)
        ctx = sources.Context(cfg, ps.get_profile())
        detail, total_found, total_added, total_updated = [], 0, 0, 0
        new_keys = []

        def save(rows, st):
            nonlocal total_found, total_added, total_updated
            a, u, keys = merge_rows(rows, listing_fields)
            new_keys.extend(keys)
            st["added"], st["updated"] = a, u
            total_found += len(rows)
            total_added += a
            total_updated += u
            detail.append(st)
            with lock, db_fn() as conn:
                conn.execute("UPDATE search_runs SET found=?, added=?, updated=?, detail=?, added_keys=? WHERE id=?",
                             (total_found, total_added, total_updated, json.dumps(detail), json.dumps(new_keys), run_id))

        boards = cfg.get("boards") or []
        if only:
            boards = [b for b in boards if b.get("name") in only]
        if not only or "USAJOBS" in only:
            _state["current"] = "USAJOBS"
            save(*sources.read_usajobs(cfg, ctx))
        if not only or "JSearch (job boards)" in only:
            _state["current"] = "JSearch (job boards)"
            save(*sources.read_jsearch(cfg, ctx))
        for b in boards:
            _state["current"] = b.get("name")
            save(*sources.run_board(b, ctx))
        bad = sum(1 for d in detail if not d.get("ok"))
        stat = "done" if not bad else ("partial" if bad < len(detail) else "failed")
        with lock, db_fn() as conn:
            conn.execute("UPDATE search_runs SET status=?, finished=? WHERE id=?", (stat, _now(), run_id))
        notify.after_search(run_id, trigger, cfg)  # rate the new jobs, then email / push the summary
        return run_id
    finally:
        _state.update(running=False, current="", started=None)
        _run_lock.release()


def run_async(trigger, listing_fields, only=None):
    if _state["running"]:
        raise RuntimeError("A search is already running")
    threading.Thread(target=lambda: _safe_run(trigger, listing_fields, only), daemon=True).start()


def _safe_run(trigger, listing_fields, only):
    try:
        run(trigger, listing_fields, only)
    except Exception as e:  # noqa: BLE001
        print(f"search run failed: {e}", flush=True)


def next_run(cfg=None):
    cfg = cfg or get_config()
    if not cfg.get("enabled"):
        return None
    try:
        tz = ZoneInfo(cfg.get("timezone") or "America/New_York")
    except Exception:  # noqa: BLE001
        return None
    days = {d.strip()[:3].title() for d in str(cfg.get("days") or "").split(",") if d.strip()}
    hh, mm = map(int, (cfg.get("run_time") or "05:30").split(":"))
    now = datetime.now(tz)
    for add in range(0, 8):
        d = (now + timedelta(days=add)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if DAYS[d.weekday()] in days and d > now:
            return d.isoformat(timespec="minutes")
    return None


def scheduler_loop(listing_fields):
    """Check every 30 s; run once per scheduled day, at or after the set time."""
    db_fn, lock = _db
    while True:
        try:
            cfg = get_config()
            if cfg.get("enabled"):
                tz = ZoneInfo(cfg.get("timezone") or "America/New_York")
                now = datetime.now(tz)
                days = {d.strip()[:3].title() for d in str(cfg.get("days") or "").split(",") if d.strip()}
                hh, mm = map(int, (cfg.get("run_time") or "05:30").split(":"))
                due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                today = now.date().isoformat()
                with lock, db_fn() as conn:
                    last = _meta_get(conn, "search_last_scheduled")
                # run if due today and not yet run; allow up to 6 h late (e.g. after a restart)
                if DAYS[now.weekday()] in days and due <= now <= due.replace(hour=min(23, hh + 6)) and last != today:
                    with lock, db_fn() as conn:
                        _meta_set(conn, "search_last_scheduled", today)
                    if not _state["running"]:
                        _safe_run("scheduled", listing_fields, None)
        except Exception as e:  # noqa: BLE001
            print(f"scheduler: {e}", flush=True)
        time.sleep(30)
