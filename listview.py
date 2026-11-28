"""The jobs list: search box, filters, sorts and what each card shows (spec section 27).

Most of these rules run in the browser: static/index.html (passFilters, filtered, render, card, liCard,
closingSoon, daysAgo, niceDay) filters, sorts and draws the list from /jobs/list. The functions here are
faithful Python twins of that JavaScript so the shared spec can be checked against the reference.
index.html implements the same rules and MUST be kept in step with this module (tests/test_listview.py
checks that the key strings below still appear in index.html). `decorate` and `near_home` are the
server side (app.list_jobs) and are what the server actually runs.

Nothing here changes behavior: it only records it.
"""
import re
import unicodedata
from datetime import date, datetime
from functools import cmp_to_key

import closures

# ---------------------------------------------------------------- choices offered above the list
SEARCH_PLACEHOLDER = "Search title, company, notes…"
# fields the search box looks in, in this order, joined with one space (description is not sent to the page)
SEARCH_FIELDS = ("title", "company", "location", "notes", "fit_reason", "clearance")
FIT_CHOICES = (("", "Any fit"), ("High", "High"), ("Med", "Med"), ("Low", "Low"))
WHERE_CHOICES = (("", "Anywhere"), ("local", "Near home"), ("remote", "Remote"), ("relocate", "Nationwide (relocate)"))
SORT_CHOICES = (("posted", "Newest posted"), ("fit", "Best fit"), ("closing", "Closing soon"), ("company", "Company"))
DEFAULT_SORT = "posted"
NEW_ONLY_LABEL = "New only"
FIT_RANK = {"High": 0, "Med": 1, "Low": 2}
FIT_RANK_OTHER = 3            # unrated or any other value
POSTED_MISSING = "0000"       # a job with no posted date sorts as the oldest
CLOSING_MISSING = "9999"      # a job with no closing date sorts last under "Closing soon"
REMOTE_PATTERN = "(?i)remote"                          # on work_mode + " " + location
RELOCATE_PATTERN = "(?i)relocation required"           # on work_mode + " " + location
NEAR_HOME_EXCLUDE = ("remote", "relocation required")  # server: in lower(work_mode + " " + location)
APPLIED_STATUSES = ("applied", "interviewing", "offer")
TABS = (("inbox", "Inbox"), ("watch", "Watchlist"), ("applied", "Applied+"), ("dismissed", "Dismissed"), ("all", "All"))
STATUS_BUTTONS = (("interested", "★ Interested"), ("applied", "Applied"), ("interviewing", "Interviewing"),
                  ("offer", "Offer"), ("dismissed", "✕ Not for me"))
CLOSING_SOON_DAYS = 7         # closing date's 23:59:59 (local) between now and 7 x 24 h from now

# ---------------------------------------------------------------- wording (exact)
TEXT = {
    "fit_pill": "{fit} fit",
    "unrated": "Not rated",
    "unrated_running": "Rating soon…",
    "new": "NEW",
    "closed": "Closed {day}",
    "closed_up_one": " · was up {n} day",
    "closed_up_many": " · was up {n} days",
    "closed_early": " · {n} days before its deadline",
    "fast": "⚡ Usually closes in ~{n} days",
    "fast_title": "{company}'s postings have stayed up a median of {n} days. Apply soon.",
    "over_window": "Over 30 days",
    "title_only": "⚠ Title-only rating — paste the description for a full rating",
    "posted": "Posted {date} ({ago})",
    "posted_none": "Posted date unverified",
    "ago_today": "today",
    "ago_one": "1 day ago",
    "ago_many": "{n} days ago",
    "closes": "Closes {date}",
    "clearance": "Clearance: {clearance}",
    "via": "via {sources}",
    "linkedin": "Also listed on LinkedIn ↗",
    "removed": "Removed {day}",
    "removed_drafts": "Job and drafts removed {day} — ★ to keep",
    "likely_filled": "Posting closed — likely filled. Follow up or move on.",
    "closed_before": "Closed before you applied.",
    "fit_by": "Fit by {by} · ",
    "rate": "Rate now",
    "rerate": "Re-rate",
    "draft": "✎ Resume & apply",
    "draft_count": "✎ Resume & apply · {n}",
    "close": "Posting closed?",
    "reopen": "↺ Reopen — it's still up",
    "linkedin_hint": "LinkedIn doesn’t permit showing this job’s details here. Open it on LinkedIn, and to draft a "
                     "resume{more}, paste its description into Resume & apply.",
    "linkedin_hint_more": " or get a full rating",
    "tab_count": "{shown} of {all}",
    "tab_new": " · {n} new",
    "hidden_one": "Filters are hiding 1 job in this tab.",
    "hidden_many": "Filters are hiding {n} jobs in this tab.",
    "empty_watch": "Nothing on your watchlist yet. Click ★ Interested on any job to keep it here permanently.",
    "empty_filtered": "No jobs match these filters.",
}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _s(v):
    """A value as the page shows it: null -> ''."""
    return "" if v is None else str(v)


# ---------------------------------------------------------------- server side (app.list_jobs)
def near_home(job, ctx):
    """Spec section 27: the job's location counts as near home by the section 12 rule (without loose remote),
    and its work mode + location doesn't say remote or "relocation required". No context -> False."""
    mode = f"{job.get('work_mode') or ''} {job.get('location') or ''}".lower()
    return bool(ctx) and not any(w in mode for w in NEAR_HOME_EXCLUDE) and \
        ctx.where(job.get("location") or "", loose_remote=False) == "local"


def decorate(r, ctx, speed, today, keep_days, watch_statuses=closures.WATCH):
    """Add the derived fields /jobs/list sends for one stored job (r is changed in place and returned)."""
    if r.get("closed_on"):   # "Closed Nov 7 · was up 4 days · 12 days before its deadline"
        closed = datetime.fromisoformat(r["closed_on"][:10]).date()
        r["days_up"], r["early_days"] = closures.days_up(r, closed), closures.early_days(r, closed)
        r["closed_how"] = closures.DETECTED_BY.get(r.get("closed_by") or "", "")
        r["fast_days"] = None
    else:
        r["fast_days"] = closures.fast_days(r.get("company"), speed)
    r["near_home"] = near_home(r, ctx)
    r["watch"] = r.get("status") in watch_statuses
    r["active"] = not closures.keep_rule({**r, "status": ""}, today, keep_days)["remove"]
    r["expires"] = closures.keep_rule(r, today, keep_days)["remove_on"]
    return r


def listed(r):
    """Watchlist jobs always; every other job (dismissed too) only inside its keep window."""
    return bool(r["watch"] or r["active"])


# ---------------------------------------------------------------- browser side (index.html twins)
def query(text):
    """The search box text as used: trimmed, lower-cased."""
    return _s(text).strip().lower()


def search_text(job):
    return " ".join(_s(job.get(k)) for k in SEARCH_FIELDS).lower()


def passes_search(job, text):
    q = query(text)
    return not q or q in search_text(job)


def is_remote(job):
    return re.search(REMOTE_PATTERN, f"{_s(job.get('work_mode'))} {_s(job.get('location'))}") is not None


def is_relocate(job):
    return re.search(RELOCATE_PATTERN, f"{_s(job.get('work_mode'))} {_s(job.get('location'))}") is not None


def passes_where(job, where):
    if not where:
        return True
    if where == "local":
        return bool(job.get("near_home"))
    if where == "relocate":
        return is_relocate(job)
    return is_remote(job)   # "remote" (and any other value)


def passes_filters(job, f):
    """f = {q, fit, where, only_new}; all must pass."""
    return ((not f.get("fit") or job.get("fit") == f["fit"])
            and passes_where(job, f.get("where") or "")
            and (not f.get("only_new") or not job.get("viewed"))
            and passes_search(job, f.get("q") or ""))


def filters_on(f):
    return bool(query(f.get("q")) or f.get("fit") or f.get("where") or f.get("only_new"))


def in_tab(job, tab):
    st = job.get("status")
    watch = bool(job.get("watch"))
    return {"inbox": not watch and st != "dismissed", "watch": watch, "applied": st in APPLIED_STATUSES,
            "dismissed": st == "dismissed", "all": True}[tab]


def _rank(job):
    return FIT_RANK.get(job.get("fit"), FIT_RANK_OTHER)


def _cmp(a, b):
    return (a > b) - (a < b)


def _ckey(s):
    """Stand-in for the browser's default localeCompare: accents and case ignored first (spaces and
    punctuation < digits < letters), then unaccented before accented, then lower case before upper case."""
    prim, sec, ter = [], [], []
    for ch in unicodedata.normalize("NFD", _s(s)):
        if unicodedata.combining(ch):
            sec[-1:] = [sec[-1] + ch] if sec else [ch]
            continue
        low = ch.casefold()
        prim.append((2 if ch.isalpha() else 1 if ch.isdigit() else 0, low))
        sec.append("")
        ter.append(0 if ch == low else 1)
    return prim, sec, ter


def _cmp_company(x, y):
    a, b = _ckey(x), _ckey(y)
    return _cmp(a, b)


def sort_jobs(jobs, sort):
    """Closed jobs (closed_on set) after open ones, then by the chosen sort; stable, so remaining ties keep
    the order the list came in (storage order)."""
    def pd(j):
        return _s(j.get("posted_date")) or POSTED_MISSING

    def c(x, y):
        d = int(bool(x.get("closed_on"))) - int(bool(y.get("closed_on")))
        if d:
            return d
        if sort == "fit":
            return (_rank(x) - _rank(y)) or _cmp(pd(y), pd(x))
        if sort == "closing":
            return _cmp(_s(x.get("closing_date")) or CLOSING_MISSING, _s(y.get("closing_date")) or CLOSING_MISSING)
        if sort == "company":
            return _cmp_company(x.get("company"), y.get("company"))
        return _cmp(pd(y), pd(x)) or (_rank(x) - _rank(y))
    return sorted(jobs, key=cmp_to_key(c))


def view(jobs, f, tab, sort):
    """The list as drawn: tab labels with counts, the keys shown in order, the hidden-by-filters line and the
    empty text ('no_jobs' when there are no jobs at all: the page shows its getting-started text)."""
    on = filters_on(f)
    tabs = []
    for k, label in TABS:
        all_ = [j for j in jobs if in_tab(j, k)]
        shown = [j for j in all_ if passes_filters(j, f)]
        nn = sum(1 for j in shown if not j.get("viewed"))
        count = TEXT["tab_count"].format(shown=len(shown), all=len(all_)) if on and len(shown) != len(all_) else str(len(all_))
        if nn and k != "dismissed":
            count += TEXT["tab_new"].format(n=nn)
        tabs.append({"tab": k, "label": label, "count": count})
    tab_all = [j for j in jobs if in_tab(j, tab)]
    shown = sort_jobs([j for j in tab_all if passes_filters(j, f)], sort)
    hidden = len(tab_all) - len(shown)
    banner = "" if not (on and hidden > 0) else (TEXT["hidden_one"] if hidden == 1 else TEXT["hidden_many"].format(n=hidden))
    empty = "" if shown else ("no_jobs" if not jobs else TEXT["empty_watch"] if tab == "watch" and not tab_all
                              else TEXT["empty_filtered"])
    return {"tabs": tabs, "keys": [j.get("job_key") for j in shown], "banner": banner, "empty": empty}


def nice_day(d):
    """'2026-11-07' -> 'Nov 7' (the page uses the browser's short month and day; en-US shown)."""
    x = date.fromisoformat(_s(d)[:10])
    return f"{MONTHS[x.month - 1]} {x.day}"


def days_ago(d, today):
    s = _s(d)
    if not s or not _ISO_PREFIX.match(s):
        return ""
    if not _ISO_DAY.match(s):
        return "NaN days ago"   # the page appends a time to the text; anything after the date makes it unreadable
    n = (today - date.fromisoformat(s)).days
    return TEXT["ago_today"] if n <= 0 else TEXT["ago_one"] if n == 1 else TEXT["ago_many"].format(n=n)


def closing_soon(job, now):
    m = _ISO_PREFIX.match(_s(job.get("closing_date")))
    if not m:
        return False
    end = datetime.fromisoformat(m.group(0) + "T23:59:59")
    d = (end - now).total_seconds() / 86400
    return 0 <= d <= CLOSING_SOON_DAYS


def card(job, now, fit_running=False, draft_count=0):
    """What one job card shows. job = a /jobs/list row; now = local datetime; draft_count = drafts for the job.
    pills, then title, sub line, meta items, alert line, reason line, rated-by line, buttons."""
    today = now.date()
    t = TEXT
    closed = bool(job.get("closed_on"))
    watch = bool(job.get("watch"))
    fit = job.get("fit")
    closed_lbl = ""
    if closed:
        closed_lbl = t["closed"].format(day=nice_day(job["closed_on"]))
        if job.get("days_up") is not None:
            n = job["days_up"]
            closed_lbl += (t["closed_up_one"] if n == 1 else t["closed_up_many"]).format(n=n)
        if job.get("early_days"):
            closed_lbl += t["closed_early"].format(n=job["early_days"])
    alert = ""
    if closed and watch:
        alert = t["likely_filled"] if job.get("status") in APPLIED_STATUSES else t["closed_before"]
    li = bool(job.get("linkedin_link"))
    draft = t["draft_count"].format(n=draft_count) if draft_count else t["draft"]
    close_btn = t["reopen"] if closed else t["close"]
    out = {"dim": job.get("status") == "dismissed", "closed": closed, "pills": [], "title": _s(job.get("title")),
           "title_link": None, "sub": "", "meta": [], "alert": alert, "why": "", "rated_by": None,
           "draft_button": draft, "close_button": close_btn, "hint": ""}
    pills = out["pills"]
    if job.get("linkedin_only"):
        if fit:
            pills.append({"kind": "fit", "text": t["fit_pill"].format(fit=fit)})
        if not job.get("has_description"):
            pills.append({"kind": "title_only", "text": t["title_only"]})
        if not job.get("viewed"):
            pills.append({"kind": "new", "text": t["new"]})
        if closed:
            pills.append({"kind": "closed", "text": closed_lbl})
        out["sub"] = _s(job.get("company"))
        if li:
            out["meta"].append({"kind": "linkedin", "text": t["linkedin"], "link": job["linkedin_link"]})
        if job.get("has_description") and job.get("fit_reason"):
            out["why"] = _s(job["fit_reason"])
        out["hint"] = t["linkedin_hint"].format(more="" if job.get("has_description") else t["linkedin_hint_more"])
        return out
    if fit:
        pills.append({"kind": "fit", "text": t["fit_pill"].format(fit=fit)})
    else:
        pills.append({"kind": "unrated", "text": t["unrated_running"] if fit_running else t["unrated"]})
    if not job.get("viewed"):
        pills.append({"kind": "new", "text": t["new"]})
    if closed:
        pills.append({"kind": "closed", "text": closed_lbl, "title": _s(job.get("closed_how"))})
    if not closed and job.get("fast_days") is not None:
        n = job["fast_days"]
        pills.append({"kind": "fast", "text": t["fast"].format(n=n),
                      "title": t["fast_title"].format(company=_s(job.get("company")), n=n)})
    if job.get("open_status") and job["open_status"] not in ("Open", "Closed"):
        pills.append({"kind": "open_status", "text": _s(job["open_status"])})
    if watch and not job.get("active"):
        pills.append({"kind": "over_window", "text": t["over_window"]})
    out["title_link"] = _s(job.get("link"))
    out["sub"] = f"{_s(job.get('company'))} · {_s(job.get('location'))}" + (f" · {job['work_mode']}" if job.get("work_mode") else "")
    meta = out["meta"]
    pd = job.get("posted_date")
    meta.append({"kind": "posted", "text": t["posted"].format(date=pd, ago=days_ago(pd, today)) if pd else t["posted_none"]})
    if job.get("closing_date"):
        meta.append({"kind": "closes", "text": t["closes"].format(date=job["closing_date"]), "highlight": closing_soon(job, now)})
    if job.get("salary"):
        meta.append({"kind": "salary", "text": _s(job["salary"])})
    if job.get("clearance"):
        meta.append({"kind": "clearance", "text": t["clearance"].format(clearance=job["clearance"])})
    if job.get("sources"):
        meta.append({"kind": "via", "text": t["via"].format(sources=job["sources"])})
    if li:
        meta.append({"kind": "linkedin", "text": t["linkedin"], "link": job["linkedin_link"]})
    if not watch and job.get("expires") and (job.get("status") == "dismissed" or draft_count):
        day = nice_day(job["expires"])
        meta.append({"kind": "removed", "text": (t["removed_drafts"] if draft_count else t["removed"]).format(day=day),
                     "highlight": bool(draft_count)})
    if job.get("fit_reason"):
        out["why"] = _s(job["fit_reason"]) + (f" · {job['lane']}" if job.get("lane") else "")
    out["rated_by"] = (t["fit_by"].format(by=job["fit_by"]) if job.get("fit_by") else "") + (t["rerate"] if fit else t["rate"])
    return out
