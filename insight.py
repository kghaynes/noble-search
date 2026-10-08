"""Explain and check the search settings, in plain language.

- check(draft): warnings about the settings, which wanted titles would be dropped, and how the title
  rules would change what the last run kept (before saving).
- why_not(job): walks one job through every step of the search and says where it would drop out,
  with one-click fixes.
- line_report(): how each job-board search has done over the last runs.
"""
import html
import re
import urllib.parse

import profile_store as ps
import search
import sources

WATCH = ("interested", "applied", "interviewing", "offer")
SENIORITY_HINTS = ["vice president", "head of", "senior manager", "general manager", "director", "chief", "vp",
                   "principal", "manager", "lead", "officer", "executive", "superintendent", "president"]
STOP_WORDS = {"of", "and", "the", "for", "a", "an", "to", "in", "at", "with", "on", "&", "senior", "sr", "jr",
              "i", "ii", "iii", "iv", "v", "remote", "hybrid", "us", "usa"}
KNOWN_BOARDS = ("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "dice.com", "usajobs.gov",
                "myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com")

_db = None   # (db_fn, db_lock) from app


def init(db_fn, db_lock):
    global _db
    _db = (db_fn, db_lock)


def _rows(sql, args=()):
    db_fn, lock = _db
    with lock, db_fn() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def _lines(text):
    return [l.strip() for l in str(text or "").splitlines() if l.strip() and not l.strip().startswith("#")]


def _merged(draft):
    cfg = search.get_config(include_secret=True)
    for k, v in (draft or {}).items():
        if k in search.DEFAULTS and not k.endswith(("_api_key", "_password", "_token")):
            if k in search.LIMITS:   # unsaved form values: keep the check from failing on "12.5" or ""
                lo, hi = search.LIMITS[k]
                try:
                    v = max(lo, min(hi, int(float(v))))
                except (TypeError, ValueError):
                    continue
            cfg[k] = v
    return cfg


def _show(line):
    return sources.show_term(line)


# --------------------------------------------------------------------------- settings check
def check(draft=None):
    cfg = _merged(draft)
    prof = ps.get_profile()
    ctx = sources.Context(cfg, prof)
    warn = []

    def add(level, text, fix=None):
        warn.append({"level": level, "text": text, **({"fix": fix} if fix else {})})

    # where to search
    if ctx.allow_local and not ctx.home_city:
        add("warn", "Your Profile home town can't be read, so near-home searches are skipped. Enter it as “Town, ST”.",
            {"view": "profile", "label": "Open Profile"})
    if not ctx.allow_remote:
        add("info", "Remote isn't in your Profile work modes, so only near-home jobs are searched.",
            {"view": "profile", "label": "Change work modes"})
    elif not ctx.allow_local:
        add("info", "Your Profile work modes are remote only, so near-home jobs aren't searched.",
            {"view": "profile", "label": "Change work modes"})
    if ctx.allow_local and not _lines(cfg.get("local_places")):
        add("info", "Only your home town counts as near home. Add the towns around you (✨ Suggest towns can fill them).")

    # job-board searches
    plan = sources.jsearch_plan(cfg, ctx)
    if cfg.get("jsearch_api_key") and not plan["searches"]:
        add("warn", "Job boards are set up but there are no jobs to look for, so LinkedIn, Indeed and the other boards are skipped.")
    if plan["rotating"]:
        every = plan["total"] / plan["per_run"]
        add("info", f"{plan['total']} job-board searches, but your {plan['budget']}-request plan allows {plan['per_run']} "
                    f"per run, so they take turns: each one runs about every {every:.1f} runs. "
                    "Fewer jobs to look for means each runs more often.")

    # title words
    inc, fld, exc = (_lines(cfg.get(k)) for k in ("title_include", "title_fields", "title_exclude"))
    if not inc:
        add("warn", "There are no seniority words, so no job title can match. Add some (director, VP…) or a line with * for any title.")
    for name, lines in (("seniority", inc), ("field", fld), ("skip", exc)):
        for l in lines:
            if sources.is_pattern_line(l):
                try:
                    re.compile(l)
                except re.error:
                    add("warn", f"The {name} line “{l}” isn't a valid pattern, so it's matched as plain words.")
    plain = lambda ls: [l for l in ls if not sources.is_pattern_line(l)]   # noqa: E731
    for words, label in ((plain(fld), "field"), (plain(inc), "seniority")):
        for a in words:
            ra = sources.term_pattern(a)
            for b in words:
                if a != b and ra.search(sources._norm(b)):
                    add("info", f"The {label} word “{b}” is already covered by “{a}”; you can remove it.")
    for s in exc:
        rs = sources.term_pattern(s)
        for w in inc + fld:
            if not sources.is_pattern_line(w) and rs.search(sources._norm(w)):
                add("warn", f"The skip word “{_show(s)}” also blocks your word “{w}”, so titles with “{w}” are dropped.",
                    {"list": "title_exclude", "op": "remove", "value": s, "label": f"Remove “{_show(s)}” from skip"})

    # titles the person wants: examples from ✨ Suggest titles, starred jobs, High-fit jobs
    wanted = [(t, "example") for t in _lines(cfg.get("title_examples"))]
    try:
        for r in _rows("SELECT title, status, fit FROM jobs WHERE status IN (?,?,?,?) OR fit='High' LIMIT 60", WATCH):
            wanted.append((re.sub(r"\s*\((?:GS|ES|SL|ST|GG|GM)-[^)]*\)\s*$", "", r["title"]),
                           "starred" if r["status"] in WATCH else "High fit"))
    except Exception:  # noqa: BLE001
        pass
    seen, examples = set(), []
    for t, why in wanted:
        if t.lower() in seen:
            continue
        seen.add(t.lower())
        ex = ctx.explain_title(t)
        examples.append({"title": t, "from": why, "ok": ex["ok"], "reason": ex["reason"],
                         "fixes": _title_fixes(t, ex, cfg)})
    misses = [e for e in examples if not e["ok"]]
    if misses:
        add("warn", f"{len(misses)} of the titles you want would be dropped by your title rules (see below).")

    return {"warnings": warn, "examples": examples, "preview": preview(cfg), "plan": _plan_view(plan)}


def _plan_view(plan):
    today = {s["q"] for s in plan["today"]}
    return {**{k: plan[k] for k in ("per_run", "total", "rotating", "monthly", "budget", "modes", "notes", "pages")},
            "searches": [{**s, "today": s["q"] in today} for s in plan["searches"]]}


def preview(cfg):
    """How the (draft) title rules would change what the last run kept — title rules only."""
    runs = [r for r in search.recent_titles(3) if r[2]]   # the latest run that looked at titles
    if not runs:
        return None
    rid, started, rows = runs[0]
    old = sources.Context(search.get_config(include_secret=True), ps.get_profile())
    new = sources.Context(cfg, ps.get_profile())
    before = after = 0
    gained, lost, done = [], [], set()
    for src, title, _out, _why in rows:
        if src == "USAJOBS":   # federal grades also count there; skip to keep the comparison honest
            continue
        key = title.lower()
        if key in done:
            continue
        done.add(key)
        a, b = old.explain_title(title)["ok"], new.explain_title(title)["ok"]
        before += a
        after += b
        if b and not a and len(gained) < 8:
            gained.append(f"{title} ({src})")
        if a and not b and len(lost) < 8:
            lost.append(f"{title} ({src})")
    if not before and not after:
        return None
    return {"run_started": started, "before": before, "after": after, "gained": gained, "lost": lost}


def _title_words(title):
    words = re.findall(r"[a-z0-9+#]+", sources._norm(title))
    return [w for w in words if w not in STOP_WORDS and not w.isdigit()]


def _title_fixes(title, ex, cfg):
    """One-click fixes for a title the rules drop."""
    fixes = []
    if ex.get("internal"):
        return fixes
    if ex.get("skip"):
        fixes.append({"list": "title_exclude", "op": "remove", "value": ex["skip"],
                      "label": f"Remove “{_show(ex['skip'])}” from skip words"})
        return fixes
    t = sources._norm(title)
    if not ex.get("seniority") and not ex.get("field") and ex["reason"].startswith("No seniority"):
        hit = next((h for h in SENIORITY_HINTS if re.search(rf"(?<![a-z]){re.escape(h)}(?![a-z])", t)), None)
        if hit:
            fixes.append({"list": "title_include", "op": "add", "value": hit, "label": f"Add “{hit}” as a seniority word"})
        return fixes
    if ex["reason"].startswith("No field"):
        inc = sources._compile_lines(cfg.get("title_include") or "")
        cands = [w for w in _title_words(title) if not sources.first_match(inc, w) and w not in SENIORITY_HINTS]
        for w in cands[:2]:
            fixes.append({"list": "title_fields", "op": "add", "value": w, "label": f"Add “{w}” as a field word"})
    return fixes


# --------------------------------------------------------------------------- why didn't I see this job?
USAJOBS_ID = re.compile(r"usajobs\.gov/(?:job|GetJob/ViewDetails)/(\d{4,12})", re.I)


def _page_title(url):
    """Best effort: the job title from a USAJOBS posting (og:title / <title>). '' otherwise.
    Only USAJOBS pages are read, at an address rebuilt from the job number — never an arbitrary link
    (most other boards block automated reading anyway, so the person types the title)."""
    m = USAJOBS_ID.search(str(url or "")[:1000])
    if not m:
        return ""
    try:
        text = sources.http(f"https://www.usajobs.gov/job/{m.group(1)}", as_json=False, timeout=15, retries=1)
    except Exception:  # noqa: BLE001
        return ""
    m = (re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', text, re.I)
         or re.search(r"<title[^>]*>([^<]+)</title>", text, re.I))
    if not m:
        return ""
    t = html.unescape(m.group(1)).strip()
    t = re.split(r"\s+[|–—-]\s+(?:USAJOBS|LinkedIn|Indeed|Glassdoor|Careers?)\b", t)[0]
    return re.sub(r"\s+at\s+.+$", "", t).strip()[:160]


def _similar(a, b):
    """Same job title: the same words in any order (ignoring 'of', 'and', punctuation and a federal grade).
    'AI Program Director' and 'IT Program Director' are different jobs."""
    wa, wb = set(_title_words(_drop_grade(a))), set(_title_words(_drop_grade(b)))
    return bool(wa) and wa == wb


def _drop_grade(title):
    """'deputy cio (es 00)' -> 'deputy cio' (plain string handling: no slow patterns on user text)."""
    s = sources._norm(str(title or "")[:300])
    i = s.rfind("(")
    if i >= 0 and s.endswith(")") and s[i + 1:i + 4] in ("gs ", "es ", "sl ", "st ", "gg ", "gm "):
        s = s[:i].strip()
    return s


def why_not(job):
    """job = {link, title, company, location}. Returns {title, steps:[{name, ok, text, fixes}], verdict}."""
    link = str(job.get("link") or "").strip()[:1000]
    title = str(job.get("title") or "").strip()[:200]
    company = str(job.get("company") or "").strip()[:120]
    location = str(job.get("location") or "").strip()[:160]
    cfg = search.get_config(include_secret=True)
    prof = ps.get_profile()
    ctx = sources.Context(cfg, prof)
    steps = []

    def step(name, ok, text, fixes=None, short=""):
        steps.append({"name": name, "ok": ok, "text": text, "fixes": fixes or [], "short": short})

    host = urllib.parse.urlparse(link).netloc.lower().split(":")[0] if link else ""
    if link and not title:
        title = _page_title(link)
    if not title:
        return {"need": "title", "text": "Couldn't read the job title from that link (many sites block automated reading). "
                                         "Type the job title (and the location if you know it), then check again."}

    # 1. already in the list?
    hits = []
    for r in _rows("SELECT job_key, title, company, location, status, link, posted_date, last_seen FROM jobs"):
        if (link and r["link"] and r["link"].split("?")[0] == link.split("?")[0]) or \
           (_similar(r["title"], title) and (not company or company.lower()[:8] in (r["company"] or "").lower())):
            hits.append(r)
    if hits:
        r = hits[0]
        if r["status"] == "dismissed":
            step("In your list", False, f"“{r['title']}” ({r['company']}) is in your list, marked dismissed.",
                 [{"op": "restore", "value": r["job_key"], "label": "Move it back to the Inbox"}])
        else:
            tab = "Watchlist" if r["status"] in WATCH else "Inbox"
            step("In your list", True, f"“{r['title']}” ({r['company']}) is in your {tab}.")
        verdict = ("You dismissed this job, so it's in the Dismissed tab." if r["status"] == "dismissed"
                   else "It's already in your list.")
        return {"title": title, "steps": steps, "verdict": verdict}
    step("In your list", None, "Not in your list.")

    # 2. link
    skip = sources._skip_list(cfg)
    if link and sources._skipped_site(link, skip):
        step("Link", False, f"{host} is on your list of re-posting sites, so its links are never used. "
                            "If the job is real, its original posting (employer site, LinkedIn, Indeed…) is found instead.",
             short="it's only on a re-posting site you skip")
    elif link:
        known = any(host == d or host.endswith("." + d) for d in KNOWN_BOARDS)
        step("Link", True, f"{host} is {'a known job site' if known else 'not on your skip list'}.")

    # 3. title
    federal = host == "usajobs.gov" or host.endswith(".usajobs.gov")
    ex = ctx.explain_title(title)
    if ex["ok"]:
        bits = [f"seniority “{_show(ex['seniority'])}”" if ex["seniority"] else "",
                f"field “{_show(ex['field'])}”" if ex["field"] else ""]
        step("Title", True, "Title matches your rules (" + ", ".join(b for b in bits if b) + ")." if any(bits) else "Title matches your rules.")
    elif federal and ctx.explain_title(title, senior=True)["ok"]:
        step("Title", True, f"No seniority word, but federal jobs at GS-{cfg.get('usajobs_min_grade') or 14} and up "
                            "(and SES) count as senior, so the title passes if the grade does.")
    else:
        step("Title", False, ex["reason"] + ".", _title_fixes(title, ex, cfg), short="your title rules drop it")

    # 4. location
    loc_l = location.lower()
    if location:
        remote_flag = bool(re.search(r"\bremote\b|work from home|anywhere", loc_l))
        nationwide = sources.NATIONWIDE_RE.search(location)
        w = ctx.where(location, remote_flag=remote_flag, loose_remote=False)
        if w:
            step("Location", True, "Near home." if w == "local" else "Remote, and your Profile accepts remote work.")
        elif federal and nationwide:
            on = cfg.get("usajobs_nationwide") in ("agencies", "all")
            step("Location", on, "A nationwide federal posting (relocation required). "
                 + ("Your nationwide setting keeps these." if on else "These are only kept when “Federal jobs posted nationwide” is on."),
                 [] if on else [{"set": "usajobs_nationwide", "value": "all", "label": "Turn on nationwide federal jobs"}],
                 short="" if on else "nationwide federal postings are turned off")
        elif remote_flag and not ctx.allow_remote:
            step("Location", False, "It's remote, but Remote isn't in your Profile work modes.",
                 [{"view": "profile", "label": "Change work modes"}], short="it's remote and your Profile doesn't accept remote")
        else:
            town = location.split("(")[0].strip()
            fixes = []
            if re.match(r"^[A-Za-z .'-]+,\s*[A-Za-z]{2}\b", town):
                fixes.append({"list": "local_places", "op": "add", "value": town[:40], "label": f"Count {town[:40]} as near home"})
            step("Location", False, f"“{location}” isn't near home (your towns list) and isn't remote.", fixes,
                 short="the location isn't near home or remote")
    else:
        step("Location", None, "Add the job's location (a town, or “remote”) to check this step.")

    # 5. did a search see it?
    found = None
    for rid, started, rows in search.recent_titles(3):
        for src, t, out, why in rows:
            if _similar(t, title):
                found = (started, src, out, why)
                break
        if found:
            break
    phrase = " ".join(_title_words(title)[:6])
    add_phrase = {"list": "jsearch_what", "op": "add", "value": phrase, "label": f"Look for “{phrase}” on job boards"}
    boards = [b.get("name", "").lower() for b in cfg.get("boards") or []]
    add_emp = ({"op": "employer", "value": company, "label": f"Add {company} as an employer"}
               if company and not any(company.lower()[:6] in b for b in boards) else None)
    if found:
        started, src, out, why = found
        day = (started or "")[:10]
        if out == "kept":
            step("Seen by a search", True, f"{src} found it on {day} and kept it.")
        else:
            step("Seen by a search", False, f"{src} found it on {day} but dropped it: {why}.",
                 _title_fixes(title, ex, cfg) if out == "title" else [], short=f"{src} found it but dropped it ({why.lower()})")
    else:
        fixes = [add_phrase] + ([add_emp] if add_emp else [])
        step("Seen by a search", False,
             "None of your last 3 searches came across this title. Job-board searches return only their top results, "
             "and employers not on your list are only found through job boards.", fixes,
             short="no search came across it — add it to your job-board searches or add the employer")

    first_fail = next((s for s in steps if s["ok"] is False), None)
    verdict = (f"Most likely reason: {first_fail['short'] or first_fail['text']}." if first_fail
               else "Every step passes, so the next search should keep it if a search returns it.")
    return {"title": title, "steps": steps, "verdict": verdict}


# --------------------------------------------------------------------------- report card
def line_report(runs=7):
    cfg = search.get_config()
    stats = {}
    n_runs = 0
    for r in search.recent_runs(runs):
        js = next((d for d in r.get("detail") or [] if d.get("type") == "jsearch"), None)
        if not js:
            continue
        n_runs += 1
        this_run = set()
        for l in js.get("lines") or []:   # a phrase runs as "near home" and "remote": count the run once
            key = l["phrase"].lower()
            s = stats.setdefault(key, {"phrase": l["phrase"], "runs": 0, "returned": 0, "kept": 0, "new": 0})
            if key not in this_run:
                s["runs"] += 1
                this_run.add(key)
            for k in ("returned", "kept", "new", "overlap"):
                s[k] = s.get(k, 0) + int(l.get(k) or 0)
    phrases = sources.split_search_lines(cfg.get("jsearch_what") or "")[0]
    exact = _lines(cfg.get("jsearch_queries"))
    out = []
    for p in phrases + exact:
        s = stats.get(p.lower(), {"phrase": p, "runs": 0, "returned": 0, "kept": 0, "new": 0})
        if s["runs"] == 0:
            s["status"], s["level"] = "Not run yet", "info"
        elif s["runs"] >= 3 and s["kept"] == 0 and not s.get("overlap"):
            s["status"], s["level"] = "Finds nothing you keep — consider replacing it", "warn"
        elif s["runs"] >= 3 and s["new"] == 0:
            s["status"], s["level"] = "Only finds jobs you already have", "info"
        else:
            s["status"], s["level"] = "Working", "ok"
        out.append(s)
    return {"runs": n_runs, "lines": out}
