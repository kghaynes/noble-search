"""Job readers that run on this server — no cloud service needed.

Each reader pulls openings straight from an employer's own hiring system
(Workday, Greenhouse, Lever, Ashby, SmartRecruiters, BambooHR, Eightfold, or a
career site's sitemap) or from the USAJOBS API, then keeps only postings that:
  * match the title rules (level words such as Director / VP / Chief), and
  * are near home (a listed town) or remote, and
  * were posted within the age window.
Stdlib only.
"""
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
PAUSE = 0.25  # seconds between requests to the same site

# --------------------------------------------------------------------------- defaults
DEFAULT_TITLE_INCLUDE = """director
vice president
\\bvp\\b
\\bsvp\\b|\\bevp\\b
\\bchief\\b
head of
\\bcio\\b|\\bciso\\b|\\bcto\\b|\\bcoo\\b
general manager
senior manager|sr\\.? manager|sr\\.? mgr
manager (3|4|5|iii|iv)\\b
\\bl[5-7]\\b
principal,|principal (program|project|it|capture)
deputy
executive officer"""

TITLE_PRESETS = {
    "executive": ("Executive & senior leadership", DEFAULT_TITLE_INCLUDE),
    "manager": ("Manager & senior professional", """manager
director
\\blead\\b
principal
senior
\\bsr\\b
supervisor
chief
head of
program manager|project manager"""),
    "all": ("Any title (use with care — many results)", "*"),
}

INTERNAL_ONLY = re.compile(r"\binternal (candidates?|applicants?|employees?|to (the )?department)( only)?\b|\binternal only\b|\bcurrent employees only\b", re.I)

DEFAULT_TITLE_EXCLUDE = """internal (candidates|applicants|to department) only|internal only
assistant
\\bintern\\b|internship
coordinator
technician
art director|creative director|medical director|nursing|pharmacy
physician|medical officer|\bnurse\b|dentist|pharmacist|veterinar|psychologist|chaplain|attorney|\blaw clerk
director of (sales|business development) - (retail|consumer)"""

DEFAULT_PLACES = ""  # filled per user ("Suggest towns" on the Search page, or typed in)

# Each user adds their own employers (Search page → "Add an employer by name").
DEFAULT_BOARDS = []

READER_LABELS = {
    "workday": "Workday", "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters", "bamboohr": "BambooHR", "eightfold": "Eightfold",
    "sitemap": "Career site", "off": "Not readable",
}

STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "DC": "district of columbia",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
    "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
    "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia",
    "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
}
FOREIGN = re.compile(r"\b(australia|united kingdom|england|scotland|canada|india|japan|germany|france|"
                     r"mexico|singapore|poland|saudi|emirates|uae|korea|israel|italy|spain|netherlands|"
                     r"uk|philippines|brazil|norway|qatar|kuwait|bahrain|jpn|aus|gbr|can|mex|ind|deu)\b")
REMOTE_RE = re.compile(r"\bremote\b|\btelework|\bwork from home\b|\bwfh\b|\bvirtual\b|\banywhere\b")


# --------------------------------------------------------------------------- helpers
class ReaderError(Exception):
    pass


STOP = threading.Event()   # set by "Stop search"; every reader's network calls end quickly


def http(url, data=None, headers=None, timeout=30, as_json=True, retries=2):
    if STOP.is_set():
        raise ReaderError("Stopped by you")
    body = None
    hdrs = {"User-Agent": UA, "Accept": "application/json" if as_json else "text/html,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if data is not None else "GET")
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            text = raw.decode("utf-8", "replace")
            time.sleep(PAUSE)
            return json.loads(text) if as_json else text
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} from {urllib.parse.urlparse(url).netloc}"
            if e.code in (400, 401, 403, 404, 410):
                break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = f"Can't reach {urllib.parse.urlparse(url).netloc}: {getattr(e, 'reason', e)}"
        except json.JSONDecodeError:
            last = f"{urllib.parse.urlparse(url).netloc} did not return JSON"
            break
        if attempt + 1 < retries:
            time.sleep(2)
    raise ReaderError(last)


_BLOCK = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|section)\b[^>]*>", re.I)


def html_to_text(s):
    if not s:
        return ""
    s = html.unescape(s) if "&lt;" in s[:500] else s
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<li\b[^>]*>", "\n• ", s, flags=re.I)
    s = _BLOCK.sub("\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", s)
    return "\n".join(line.strip() for line in s.split("\n")).strip()


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date(v):
    """Return YYYY-MM-DD or '' from the many date shapes hiring systems use."""
    if v is None or v == "":
        return ""
    if isinstance(v, (int, float)):
        ts = v / 1000 if v > 1e11 else v
        return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
    s = str(v).strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return ""
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)                                   # 09/30/2026
    if m:
        try:
            return date(int(m[3]), int(m[1]), int(m[2])).isoformat()
        except ValueError:
            return ""
    for m in re.finditer(r"\b(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", s):             # 30 Sep 2026
        mi = _MONTHS.get(m[2][:3].lower())
        if mi:
            try:
                return date(int(m[3]), mi, int(m[1])).isoformat()
            except ValueError:
                pass
    for m in re.finditer(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(?:\d{1,2}:\d{2}(?::\d{2})?\s+\w+\s+)?(\d{4})", s):
        mi = _MONTHS.get(m[1][:3].lower())                                              # Sep 15, 2026 / Tue Sep 15 07:00:00 UTC 2026
        if mi:
            try:
                return date(int(m[3]), mi, int(m[2])).isoformat()
            except ValueError:
                pass
    return ""


def workday_age(posted_on):
    """'Posted Today' -> 0, 'Posted 3 Days Ago' -> 3, 'Posted 30+ Days Ago' -> None (too old / unknown)."""
    s = (posted_on or "").lower()
    if "today" in s:
        return 0
    if "yesterday" in s:
        return 1
    if "+" in s:
        return None
    m = re.search(r"(\d+)\s+day", s)
    return int(m.group(1)) if m else None


_SAL = re.compile(r"\$\s?\d{2,3}(?:,\d{3})+(?:\.\d{2})?(?:\s*(?:-|–|—|to)\s*\$?\s?\d{2,3}(?:,\d{3})+(?:\.\d{2})?)?"
                  r"|\$\s?\d{2,3}(?:\.\d)?\s?[kK]\s*(?:-|–|to)\s*\$?\s?\d{2,3}(?:\.\d)?\s?[kK]")


def find_salary(text):
    m = _SAL.search(text or "")
    return re.sub(r"\s+", " ", m.group(0)).replace(".00", "") if m else ""


def find_clearance(text):
    t = text or ""
    if re.search(r"TS\s*/\s*SCI[^.\n]{0,60}poly", t, re.I):
        return "TS/SCI with polygraph"
    if re.search(r"TS\s*/\s*SCI", t, re.I):
        return "TS/SCI"
    if re.search(r"\btop secret\b|\b(active|current)\s+TS\b|\bTS\s+clearance", t, re.I):
        return "Top Secret"
    if re.search(r"\bsecret\b[^.\n]{0,40}clearance|clearance[^.\n]{0,40}\bsecret\b", t, re.I):
        return "Secret"
    if re.search(r"public trust", t, re.I):
        return "Public Trust"
    return ""


def employment(v):
    s = str(v or "").lower()
    if "contract" in s or "temp" in s:
        return "Contract"
    if "part" in s:
        return "W2 (part-time)"
    return "W2"


# --------------------------------------------------------------------------- matching context
def _compile_lines(text):
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(re.compile(line, re.I))
        except re.error:
            out.append(re.compile(re.escape(line), re.I))
    return out


def _norm(s):
    s = urllib.parse.unquote(str(s or ""))
    return re.sub(r"\s+", " ", re.sub(r"[-_/|~+]", " ", s)).strip().lower()


def home_town(profile):
    """The profile's home town as 'Town, ST' — from Home location, else from City, State. '' if neither can be read."""
    c = Context.__new__(Context)
    for k in ("home_location", "city_state"):
        v = Context._home_city(c, (profile or {}).get(k) or "")
        if v:
            return v
    return ""


class Context:
    """Title/location/age rules shared by every reader, plus per-run counters."""

    def __init__(self, cfg, profile):
        inc = cfg.get("title_include") or DEFAULT_TITLE_INCLUDE
        self.any_title = any(l.strip() == "*" for l in inc.splitlines())
        self.include = _compile_lines("\n".join(l for l in inc.splitlines() if l.strip() != "*"))
        self.exclude = _compile_lines(cfg["title_exclude"] if "title_exclude" in cfg else DEFAULT_TITLE_EXCLUDE)
        self.fields = _compile_lines(cfg.get("title_fields") or "")   # optional: title must also name the field
        self.max_age = int(cfg.get("max_age_days") or 30)
        self.today = date.today()
        modes = (profile.get("work_modes") or "On-site, Hybrid, Remote").lower()
        self.allow_remote = "remote" in modes
        self.allow_local = ("site" in modes or "hybrid" in modes or "office" in modes) or not self.allow_remote
        places = []
        for line in (cfg.get("local_places") or DEFAULT_PLACES).splitlines():
            line = line.strip()
            if not line:
                continue
            name, _, st = line.partition(",")
            st = st.strip().upper()[:2]
            places.append((_norm(name), st, line))
        self.places = places
        states = [p[1] for p in places if p[1]]
        self.home_state = max(set(states), key=states.count) if states else ""
        self.home_state_name = STATES.get(self.home_state, "")
        self.home_city = home_town(profile) or (places[0][2] if places else "")
        if not places and self.home_city:  # no town list yet: at least count the home town itself
            name, _, st = self.home_city.partition(",")
            places = [(_norm(name), st.strip().upper()[:2], self.home_city)]
            self.places = places
            self.home_state = places[0][1]
            self.home_state_name = STATES.get(self.home_state, "")
        self.radius = int(profile.get("radius_miles") or 30)
        self.detail_cap = int(cfg.get("detail_cap") or 60)
        self.max_pages = int(cfg.get("max_pages") or 150)
        self.log = []

    def _home_city(self, loc):
        """'123 Main St, Springfield, IL 62701' / 'Springfield, Illinois' / 'Springfield IL 62701' -> 'Springfield, IL'."""
        loc = re.sub(r"\s+", " ", str(loc or "")).strip()
        loc = re.sub(r",?\s*(USA|United States( of America)?|US)\.?$", "", loc, flags=re.I).strip()
        names = {v.lower(): k for k, v in STATES.items()}
        parts = [p.strip() for p in loc.split(",") if p.strip()]
        if len(parts) >= 2:
            last = re.sub(r"\s*\d{5}(-\d{4})?$", "", parts[-1]).strip()
            ab = last.upper() if last.upper() in STATES else names.get(last.lower())
            if ab:
                return f"{parts[-2]}, {ab}"
        words = re.sub(r"\s*\d{5}(-\d{4})?$", "", parts[-1] if parts else "").split()
        for n in (3, 2, 1):   # "Salt Lake City UT", "Melbourne Florida", "Charleston West Virginia"
            if len(words) > n:
                tail = " ".join(words[-n:])
                ab = tail.upper() if n == 1 and tail.upper() in STATES else names.get(tail.lower())
                if ab:
                    return f"{' '.join(words[:-n])}, {ab}"
        return ""

    def field_ok(self, title):
        """True when no field words are set, or the title names one of them."""
        return not self.fields or any(r.search(_norm(title)) for r in self.fields)

    def title_allowed(self, title):
        """Not excluded (skip list or internal-only) and in the user's field — used where a senior
        federal grade stands in for a seniority word."""
        t = _norm(title)
        return not INTERNAL_ONLY.search(t) and not any(r.search(t) for r in self.exclude) and self.field_ok(title)

    def title_ok(self, title):
        if INTERNAL_ONLY.search(_norm(title)):
            return False   # open only to current employees: never useful to an outside candidate
        if self.any_title:
            return not any(r.search(_norm(title)) for r in self.exclude) and self.field_ok(title)
        t = _norm(title)
        return (any(r.search(t) for r in self.include) and not any(r.search(t) for r in self.exclude)
                and self.field_ok(title))

    def where(self, text, remote_flag=False, loose_remote=True):
        """Return 'local', 'remote' or None for a location string."""
        raw = str(text or "")
        t = _norm(raw)
        foreign = bool(FOREIGN.search(t)) and not re.search(r"\b(united states|usa|us)\b", t)
        hs = self.home_state
        home_state_hit = bool(hs) and (re.search(rf"\b{hs.lower()}\b", t) is not None or
                                       (self.home_state_name and self.home_state_name in t))
        other_state = False
        for ab, nm in STATES.items():
            if ab == hs:
                continue
            if re.search(rf"(,\s*|\bus[a]?\s|\bus\b\s?|^){ab}\b", raw) or re.search(rf"\b{nm}\b", t):
                other_state = True
                break
        if self.allow_local:
            for name, st, _ in self.places:
                if name and re.search(rf"\b{re.escape(name)}\b", t):
                    if home_state_hit or (not other_state and not foreign):
                        return "local"
        if self.allow_remote and (remote_flag or (loose_remote and REMOTE_RE.search(t))) and not foreign:
            return "remote"
        return None

    def age_ok(self, iso):
        if not iso:
            return True
        try:
            return (self.today - date.fromisoformat(iso)).days <= self.max_age
        except ValueError:
            return True


def make_row(ctx, *, company, title, location, where, link, posted, description="", salary="",
             clearance="", employment_type="W2", closing="", source="", work_mode=""):
    desc = (description or "").strip()
    return {
        "company": company.strip(), "title": re.sub(r"\s+", " ", title or "").strip(),
        "location": location.strip() if where != "remote" or location.strip() else "Remote (US)",
        "work_mode": work_mode or ("Remote" if where == "remote" else "On-site"),
        "employment_type": employment_type, "posted_date": posted or "", "closing_date": closing or "",
        "salary": salary or find_salary(desc), "clearance": clearance or find_clearance(desc),
        "sources": source, "link": link, "fit": "", "fit_reason": "", "lane": "",
        "open_status": "Open", "description": desc[:30000],
    }


def pick_location(ctx, locs):
    """From several location strings, return the local one if any, else the first."""
    for l in locs:
        if ctx.where(l, loose_remote=False) == "local":
            return l
    for l in locs:
        if REMOTE_RE.search(_norm(l)):
            return l
    return locs[0] if locs else ""


def tidy_loc(s):
    """'United States-Florida-Melbourne' / 'USA - Melbourne, FL' / 'US-FL-Melbourne' -> 'Melbourne, FL'."""
    s = (s or "").strip()
    m = re.match(r"^(?:United States|USA|US)\s*-\s*([A-Za-z .]+?)\s*-\s*([A-Za-z .'-]+)$", s)
    if m:
        st, city = m.group(1).strip(), m.group(2).strip()
        ab = next((k for k, v in STATES.items() if v == st.lower()), st.upper() if len(st) == 2 else st)
        return f"{city}, {ab}"
    m = re.match(r"^(?:United States|USA|US)\s*-\s*(.+)$", s)
    if m:
        s = m.group(1).strip()
    s = re.sub(r",\s*(US|USA|United States)$", "", s)
    return "Remote (US)" if s.lower() in ("remote", "remote us", "remote - us", "us remote") else s


# --------------------------------------------------------------------------- board detection
def detect(url):
    """Return (reader_type, params) for a careers URL."""
    u = urllib.parse.urlparse(url.strip())
    host = (u.netloc or "").lower()
    parts = [p for p in u.path.split("/") if p]
    qs = urllib.parse.parse_qs(u.query)
    if host.endswith(".myworkdayjobs.com"):
        tenant = host.split(".")[0]
        site = next((p for p in parts if not re.match(r"^[a-z]{2}-[A-Z]{2}$", p)), "")
        return "workday", {"host": host, "tenant": tenant, "site": site}
    if "greenhouse.io" in host:
        token = qs.get("for", [""])[0] or (parts[0] if parts and parts[0] != "embed" else "")
        return "greenhouse", {"token": token}
    if host == "jobs.lever.co":
        return "lever", {"token": parts[0] if parts else ""}
    if host == "jobs.ashbyhq.com":
        return "ashby", {"token": parts[0] if parts else ""}
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"):
        return "smartrecruiters", {"token": parts[0] if parts else ""}
    if host.endswith(".bamboohr.com"):
        return "bamboohr", {"sub": host.split(".")[0]}
    if host.endswith(".eightfold.ai") or (qs.get("domain") and parts[:1] == ["careers"]):
        # *.eightfold.ai, or an Eightfold site on the employer's own domain (careers?domain=example.com)
        dom = qs.get("domain", [""])[0] or host.split(".")[0] + ".com"
        return "eightfold", {"host": host, "domain": dom}
    if "adp.com" in host:
        return "off", {}
    sm = url if u.path.lower().endswith(".xml") else f"{u.scheme or 'https'}://{u.netloc}/sitemap.xml"
    return "sitemap", {"sitemap": sm, "host": host}


# --------------------------------------------------------------------------- readers
def read_workday(board, p, ctx, st):
    base = f"https://{p['host']}/wday/cxs/{p['tenant']}/{p['site']}"
    public = f"https://{p['host']}/{p['site']}"
    cands, offset, total = [], 0, None
    for _ in range(ctx.max_pages):
        j = http(base + "/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""})
        posts = j.get("jobPostings") or []
        if total is None:
            total = j.get("total") or 0
        if not posts:
            break
        old = 0
        for x in posts:
            age = workday_age(x.get("postedOn"))
            if age is None or age > ctx.max_age:
                old += 1
                continue
            st["scanned"] += 1
            if not ctx.title_ok(x.get("title")):
                continue
            loc = x.get("locationsText") or ""
            multi = bool(re.match(r"^\d+\s+Locations?$", loc.strip(), re.I))
            if multi or ctx.where(loc, remote_flag=bool(re.search(r"remote", x.get("remoteType") or "", re.I))):
                cands.append((x, age))
        offset += 20
        if old == len(posts) or offset >= total:
            break
    st["title_matches"] = len(cands)
    for x, age in cands[: ctx.detail_cap]:
        try:
            d = http(base + x["externalPath"]).get("jobPostingInfo") or {}
        except ReaderError as e:
            st["errors"].append(str(e))
            continue
        locs = [d.get("location") or x.get("locationsText") or ""] + list(d.get("additionalLocations") or [])
        rtype = d.get("remoteType") or x.get("remoteType") or ""
        w = ctx.where(" | ".join(locs), remote_flag=bool(re.search(r"remote", rtype, re.I)))
        if not w:
            continue
        posted = parse_date(d.get("startDate")) or (ctx.today - timedelta(days=age)).isoformat()
        if not ctx.age_ok(posted):
            continue
        mode = "Remote" if w == "remote" else ("Hybrid" if re.search(r"hybrid", rtype, re.I) else "On-site")
        yield make_row(ctx, company=board["name"], title=d.get("title") or x.get("title"),
                       location=tidy_loc(pick_location(ctx, locs)), where=w,
                       link=d.get("externalUrl") or public + x["externalPath"], posted=posted,
                       description=html_to_text(d.get("jobDescription")), work_mode=mode,
                       employment_type=employment(d.get("timeType")),
                       source=f"{board['name']} careers (Workday)")


def read_greenhouse(board, p, ctx, st):
    j = http(f"https://boards-api.greenhouse.io/v1/boards/{p['token']}/jobs?content=true", timeout=60)
    for x in j.get("jobs") or []:
        st["scanned"] += 1
        posted = parse_date(x.get("first_published") or x.get("updated_at"))
        if not ctx.age_ok(posted) or not ctx.title_ok(x.get("title")):
            continue
        st["title_matches"] += 1
        loc = (x.get("location") or {}).get("name") or ""
        w = ctx.where(loc)
        if not w:
            continue
        meta = {m.get("name"): m.get("value") for m in x.get("metadata") or [] if isinstance(m, dict)}
        yield make_row(ctx, company=board["name"], title=x.get("title"), location=loc, where=w,
                       link=x.get("absolute_url"), posted=posted, description=html_to_text(x.get("content")),
                       employment_type=employment(meta.get("Employment Type")),
                       source=f"{board['name']} careers (Greenhouse)")


def read_lever(board, p, ctx, st):
    arr = http(f"https://api.lever.co/v0/postings/{p['token']}?mode=json", timeout=60)
    for x in arr if isinstance(arr, list) else []:
        st["scanned"] += 1
        posted = parse_date(x.get("createdAt"))
        if not ctx.age_ok(posted) or not ctx.title_ok(x.get("text")):
            continue
        st["title_matches"] += 1
        cat = x.get("categories") or {}
        locs = [cat.get("location") or ""] + list(cat.get("allLocations") or [])
        wt = x.get("workplaceType") or ""
        w = ctx.where(" | ".join(locs), remote_flag=wt == "remote")
        if not w:
            continue
        parts = [x.get("descriptionPlain") or ""]
        for lst in x.get("lists") or []:
            parts.append((lst.get("text") or "") + "\n" + html_to_text(lst.get("content")))
        parts.append(x.get("additionalPlain") or "")
        sr = x.get("salaryRange") or {}
        sal = f"${sr['min']:,}–${sr['max']:,}" if sr.get("min") and sr.get("max") else ""
        yield make_row(ctx, company=board["name"], title=x.get("text"), location=pick_location(ctx, locs), where=w,
                       link=x.get("hostedUrl"), posted=posted, description="\n\n".join(parts), salary=sal,
                       work_mode={"remote": "Remote", "hybrid": "Hybrid"}.get(wt, ""),
                       employment_type=employment(cat.get("commitment")),
                       source=f"{board['name']} careers (Lever)")


def read_ashby(board, p, ctx, st):
    j = http(f"https://api.ashbyhq.com/posting-api/job-board/{p['token']}?includeCompensation=true", timeout=60)
    for x in j.get("jobs") or []:
        st["scanned"] += 1
        posted = parse_date(x.get("publishedAt"))
        if not ctx.age_ok(posted) or not ctx.title_ok(x.get("title")):
            continue
        st["title_matches"] += 1
        locs = [x.get("location") or ""] + [s.get("location") or "" for s in x.get("secondaryLocations") or []]
        w = ctx.where(" | ".join(locs), remote_flag=bool(x.get("isRemote")))
        if not w:
            continue
        comp = (x.get("compensation") or {}).get("compensationTierSummary") or ""
        yield make_row(ctx, company=board["name"], title=x.get("title"), location=pick_location(ctx, locs), where=w,
                       link=x.get("jobUrl"), posted=posted, description=x.get("descriptionPlain") or "",
                       salary=comp, employment_type=employment(x.get("employmentType")),
                       source=f"{board['name']} careers (Ashby)")


def read_smartrecruiters(board, p, ctx, st):
    base = f"https://api.smartrecruiters.com/v1/companies/{p['token']}/postings"
    cands, offset = [], 0
    for _ in range(ctx.max_pages):
        j = http(f"{base}?limit=100&offset={offset}")
        items = j.get("content") or []
        for x in items:
            st["scanned"] += 1
            posted = parse_date(x.get("releasedDate"))
            if not ctx.age_ok(posted) or not ctx.title_ok(x.get("name")):
                continue
            loc = x.get("location") or {}
            txt = loc.get("fullLocation") or ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
            w = ctx.where(txt, remote_flag=bool(loc.get("remote")))
            if w:
                cands.append((x, posted, txt, w))
        offset += 100
        if not items or offset >= (j.get("totalFound") or 0):
            break
    st["title_matches"] = len(cands)
    for x, posted, txt, w in cands[: ctx.detail_cap]:
        try:
            d = http(f"{base}/{x['id']}")
        except ReaderError as e:
            st["errors"].append(str(e))
            continue
        sec = (d.get("jobAd") or {}).get("sections") or {}
        desc = "\n\n".join(html_to_text((sec.get(k) or {}).get("text")) for k in
                           ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))
        yield make_row(ctx, company=board["name"], title=x.get("name"), location=txt, where=w,
                       link=d.get("postingUrl") or f"https://jobs.smartrecruiters.com/{p['token']}/{x['id']}",
                       posted=posted, description=desc, source=f"{board['name']} careers (SmartRecruiters)")


def read_bamboohr(board, p, ctx, st):
    base = f"https://{p['sub']}.bamboohr.com/careers"
    j = http(base + "/list")
    for x in j.get("result") or []:
        st["scanned"] += 1
        if not ctx.title_ok(x.get("jobOpeningName")):
            continue
        st["title_matches"] += 1
        loc = x.get("location") or {}
        txt = ", ".join(filter(None, [loc.get("city"), loc.get("state")]))
        w = ctx.where(txt, remote_flag=bool(x.get("isRemote")) or str(x.get("locationType")) == "1")
        if not w:
            continue
        try:
            d = (http(f"{base}/{x['id']}/detail").get("result") or {}).get("jobOpening") or {}
        except ReaderError as e:
            st["errors"].append(str(e))
            continue
        posted = parse_date(d.get("datePosted"))
        if not ctx.age_ok(posted):
            continue
        yield make_row(ctx, company=board["name"], title=x.get("jobOpeningName"), location=tidy_loc(txt), where=w,
                       link=d.get("jobOpeningShareUrl") or f"{base}/{x['id']}", posted=posted,
                       description=html_to_text(d.get("description")), salary=d.get("compensation") or "",
                       employment_type=employment(d.get("employmentStatusLabel")),
                       source=f"{board['name']} careers (BambooHR)")


def read_eightfold(board, p, ctx, st):
    base = f"https://{p['host']}/api/pcsx"
    cands, start = [], 0
    for _ in range(ctx.max_pages):
        q = urllib.parse.urlencode({"domain": p["domain"], "query": "", "location": ctx.home_city, "start": start,
                                    "sort_by": "timestamp", "filter_distance": ctx.radius,
                                    "filter_include_remote": 1})
        d = http(f"{base}/search?{q}").get("data") or {}
        pos = d.get("positions") or []
        if not pos:
            break
        old = 0
        for x in pos:
            posted = parse_date(x.get("postedTs") or x.get("creationTs"))
            if not ctx.age_ok(posted):
                old += 1
                continue
            st["scanned"] += 1
            if not ctx.title_ok(x.get("name")):
                continue
            locs = list(x.get("standardizedLocations") or x.get("locations") or [])
            wlo = (x.get("workLocationOption") or "").lower()
            tele = " ".join(x.get("efcustomTextCustteleworklocation") or [])
            w = ctx.where(" | ".join(locs), remote_flag=("remote" in wlo) or "full time" in tele.lower())
            if w:
                cands.append((x, posted, locs, w, wlo))
        start += len(pos)
        if old == len(pos) or start >= (d.get("count") or 0):
            break
    st["title_matches"] = len(cands)
    for x, posted, locs, w, wlo in cands[: ctx.detail_cap]:
        try:
            q = urllib.parse.urlencode({"position_id": x["id"], "domain": p["domain"], "hl": "en"})
            d = http(f"{base}/position_details?{q}").get("data") or {}
        except ReaderError as e:
            st["errors"].append(str(e))
            d = {}
        clr = " ".join(d.get("efcustomTextFinalclearancefortherole") or x.get("efcustomTextFinalclearancefortherole") or [])
        pay = " ".join(d.get("efcustomTextCustpayrange") or x.get("efcustomTextCustpayrange") or [])
        mode = "Remote" if w == "remote" else ("Hybrid" if "hybrid" in wlo else "On-site")
        link = d.get("publicUrl") or f"https://{p['host']}/careers/job/{x['id']}"
        yield make_row(ctx, company=board["name"], title=x.get("name"), location=pick_location(ctx, locs), where=w,
                       link=link, posted=posted, description=html_to_text(d.get("jobDescription")),
                       salary=pay.replace(".00", ""), clearance=clr if clr and clr.lower() not in ("none", "n/a") else "",
                       work_mode=mode, source=f"{board['name']} careers (Eightfold)")


# ---- generic career site: sitemap + structured data on each job page
BOT_CHECK = re.compile(r"awsWafCookieDomainList|gokuProps|cf-chl-|challenge-platform|_Incapsula_|px-captcha|"
                       r"Just a moment\.\.\.|Access Denied|captcha", re.I)


def _blocked(text):
    head = text[:4000]
    return "<urlset" not in head and "<sitemapindex" not in head and "JobPosting" not in text and bool(BOT_CHECK.search(head))


def _sitemap_urls(url, depth=0):
    xml = http(url, as_json=False, timeout=60)
    if _blocked(xml):
        raise ReaderError("Site blocks automated readers with a bot check. Set this employer to Off; "
                          "job-board search covers it.")
    out = []
    if "<sitemapindex" in xml[:2000] and depth < 2:
        subs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
        jobby = [s for s in subs if re.search(r"job|posting|career|position", s, re.I)] or subs[:5]
        for s in jobby[:6]:
            out += _sitemap_urls(html.unescape(s), depth + 1)
        return out
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", block)
        mod = re.search(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", block)
        if loc:
            out.append((html.unescape(loc.group(1)), parse_date(mod.group(1)) if mod else ""))
    return out


def _jsonld_posting(page):
    for m in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if o.get("@type") == "JobPosting" or (isinstance(o.get("@type"), list) and "JobPosting" in o["@type"]):
                    return o
                stack += [v for k, v in o.items() if k == "@graph" and isinstance(v, list)]
            elif isinstance(o, list):
                stack += o
    return None


def _ld_locations(o):
    locs = o.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    out = []
    for l in locs:
        a = (l or {}).get("address") or {}
        if isinstance(a, str):
            out.append(a)
            continue
        out.append(", ".join(filter(None, [a.get("addressLocality"), a.get("addressRegion"),
                                           a.get("addressCountry") if isinstance(a.get("addressCountry"), str) else ""])))
    return [x for x in out if x]


def _microdata(page, prop):
    m = re.search(rf'itemprop="{prop}"[^>]*content="([^"]*)"', page) or \
        re.search(rf'itemprop="{prop}"[^>]*>(.*?)</', page, re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""


def parse_job_page(page):
    """Pull title/date/locations/description from a job page (JSON-LD, then microdata, then plain HTML)."""
    o = _jsonld_posting(page)
    if o:
        sal = ""
        bs = o.get("baseSalary") or {}
        v = bs.get("value") if isinstance(bs, dict) else None
        if isinstance(v, dict) and v.get("minValue") and v.get("maxValue"):
            sal = f"${float(v['minValue']):,.0f}–${float(v['maxValue']):,.0f}"
        return {"title": html.unescape(o.get("title") or ""), "posted": parse_date(o.get("datePosted")),
                "closing": parse_date(o.get("validThrough")), "locations": _ld_locations(o),
                "remote": str(o.get("jobLocationType") or "").upper() == "TELECOMMUTE",
                "description": html_to_text(o.get("description")), "employment": o.get("employmentType") or "",
                "salary": sal, "structured": True}
    if 'itemprop="title"' in page or 'itemprop="datePosted"' in page:
        loc = ", ".join(filter(None, [_microdata(page, "addressLocality"), _microdata(page, "addressRegion")]))
        desc_m = re.search(r'itemprop="description"[^>]*>(.*?)</span>\s*</span>|class="jobdescription"[^>]*>(.*?)</div>\s*</div>', page, re.S)
        desc = html_to_text(next((g for g in (desc_m.groups() if desc_m else []) if g), "")) or \
            html_to_text(_microdata(page, "description"))
        return {"title": _microdata(page, "title"), "posted": parse_date(_microdata(page, "datePosted")), "closing": "",
                "locations": [loc] if loc else [], "remote": False, "description": desc, "employment": "",
                "salary": "", "structured": True}
    t = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', page) or re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    body = re.search(r"<main\b.*?</main>", page, re.S | re.I)
    return {"title": html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip() if t else "", "posted": "", "closing": "",
            "locations": [], "remote": False, "description": html_to_text(body.group(0) if body else page)[:20000],
            "employment": "", "salary": "", "structured": False}


def read_sitemap(board, p, ctx, st):
    urls = _sitemap_urls(p["sitemap"])
    jobs = [(u, m) for u, m in urls if re.search(r"/job(s|detail)?/", u, re.I) and not re.search(r"applied", u, re.I)]
    st["scanned"] = len(jobs)
    if not jobs:
        raise ReaderError("No job pages listed in the site map")
    hint_words = [n for n, _, _ in ctx.places] + ["remote", "united states", "telework"]
    cands = []
    for u, mod in jobs:
        slug = _norm(urllib.parse.urlparse(u).path)
        if not ctx.title_ok(slug):
            continue
        if mod and not ctx.age_ok(mod) and len({m for _, m in jobs[:50]}) > 3:
            continue  # lastmod looks real (not all one value) and is old
        near = any(re.search(rf"\b{re.escape(h)}\b", slug) for h in hint_words if h)
        cands.append((0 if near else 1, u, mod))
    cands.sort()
    st["title_matches"] = len(cands)
    for _, u, mod in cands[: max(ctx.detail_cap, 120)]:
        try:
            page = http(u, as_json=False)
        except ReaderError as e:
            st["errors"].append(str(e))
            continue
        d = parse_job_page(page)
        title = d["title"] or _norm(urllib.parse.urlparse(u).path.split("/")[-2]).title()
        if not ctx.title_ok(title):
            continue
        if d["structured"]:
            w = ctx.where(" | ".join(d["locations"]), remote_flag=d["remote"]) if (d["locations"] or d["remote"]) \
                else ctx.where(_norm(urllib.parse.urlparse(u).path), loose_remote=False)
        else:
            w = ctx.where(d["description"][:6000], loose_remote=False) or \
                (ctx.where(title) if REMOTE_RE.search(title.lower()) else None)
        if not w:
            continue
        posted = d["posted"] or mod
        if not ctx.age_ok(posted):
            continue
        loc = pick_location(ctx, d["locations"]) if d["locations"] else (
            next((pl for n, _, pl in ctx.places if re.search(rf"\b{re.escape(n)}\b", _norm(d["description"][:6000]))), "")
            if w == "local" else "Remote (US)")
        yield make_row(ctx, company=board["name"], title=title, location=tidy_loc(loc), where=w, link=u,
                       posted=d["posted"], closing=d["closing"], description=d["description"], salary=d["salary"],
                       employment_type=employment(d["employment"]), source=f"{board['name']} careers")


READERS = {
    "workday": read_workday, "greenhouse": read_greenhouse, "lever": read_lever, "ashby": read_ashby,
    "smartrecruiters": read_smartrecruiters, "bamboohr": read_bamboohr, "eightfold": read_eightfold,
    "sitemap": read_sitemap,
}


def run_board(board, ctx):
    """Run one employer board. Returns (rows, stats)."""
    kind = (board.get("type") or "auto").strip().lower()
    auto_kind, params = detect(board.get("url") or "")
    if kind in ("auto", ""):
        kind = auto_kind
    elif kind not in READERS and kind != "off":
        kind = auto_kind
    st = {"name": board.get("name"), "type": kind, "scanned": 0, "title_matches": 0, "kept": 0,
          "errors": [], "seconds": 0, "ok": True}
    if kind == "off":
        st["skipped"] = board.get("note") or "Turned off"
        return [], st
    t0 = time.time()
    rows = []
    try:
        for r in READERS[kind](board, params, ctx, st):
            rows.append(r)
    except ReaderError as e:
        st["ok"] = False
        st["errors"].insert(0, str(e))
    except Exception as e:  # noqa: BLE001 — one bad site must not stop the run
        st["ok"] = False
        st["errors"].insert(0, f"{type(e).__name__}: {e}"[:300])
    st["kept"] = len(rows)
    st["seconds"] = round(time.time() - t0, 1)
    st["errors"] = st["errors"][:5]
    return rows, st


# --------------------------------------------------------------------------- USAJOBS
def read_usajobs(cfg, ctx):
    key, email = (cfg.get("usajobs_api_key") or "").strip(), (cfg.get("usajobs_email") or "").strip()
    st = {"name": "USAJOBS", "type": "usajobs", "scanned": 0, "title_matches": 0, "kept": 0, "errors": [],
          "seconds": 0, "ok": True}
    if not key or not email:
        st["skipped"] = "Add a free USAJOBS API key and your email on the Search page."
        return [], st
    t0 = time.time()
    min_grade = int(cfg.get("usajobs_min_grade") or 14)
    city, _, stab = ctx.home_city.partition(",")
    loc_name = f"{city.strip()}, {STATES.get(stab.strip().upper(), stab.strip()).title()}"
    queries = []
    if ctx.allow_local and city.strip() and stab.strip():
        queries.append({"LocationName": loc_name, "Radius": ctx.radius})
    elif ctx.allow_local:
        st["note"] = "Near-home search skipped: add your home town (e.g. \"Springfield, IL\") on the Profile page."
    if ctx.allow_remote:
        queries.append({"RemoteIndicator": "True"})
    hdr = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key}
    seen, rows = set(), []
    try:
        for q in queries:
            page = 1
            while page <= 10:
                qs = urllib.parse.urlencode({**q, "DatePosted": min(ctx.max_age, 60), "ResultsPerPage": 500,
                                             "Page": page, "WhoMayApply": "public"})
                j = http(f"https://data.usajobs.gov/api/search?{qs}", headers=hdr)
                res = (j.get("SearchResult") or {})
                items = res.get("SearchResultItems") or []
                for it in items:
                    d = it.get("MatchedObjectDescriptor") or {}
                    pid = d.get("PositionID") or d.get("PositionURI")
                    if pid in seen:
                        continue
                    seen.add(pid)
                    st["scanned"] += 1
                    det = (d.get("UserArea") or {}).get("Details") or {}
                    plan = ((d.get("JobGrade") or [{}])[0] or {}).get("Code", "")
                    try:
                        low = int(det.get("LowGrade") or 0)
                    except ValueError:
                        low = 0
                    title = d.get("PositionTitle") or ""
                    senior = plan in ("ES", "SL", "ST", "EX") or (plan in ("GS", "GG", "GM") and low >= min_grade)
                    # Federal titles rarely say "Director", so a senior grade can stand in for a title match,
                    # but the "skip titles" list (and internal-only) still applies to everything.
                    if senior:
                        if not ctx.title_allowed(title):
                            continue
                    elif not (ctx.title_ok(title) and low >= min_grade - 1):
                        continue
                    st["title_matches"] += 1
                    remote = str(det.get("RemoteIndicator")).lower() == "true" or "RemoteIndicator" in q
                    locs = [l.get("LocationName", "") for l in d.get("PositionLocation") or []] or [d.get("PositionLocationDisplay", "")]
                    w = "remote" if remote and ctx.allow_remote else ctx.where(" | ".join(locs), loose_remote=False)
                    if "LocationName" in q and not w:
                        w = "local"  # USAJOBS already applied the radius
                    if not w:
                        continue
                    pay = (d.get("PositionRemuneration") or [{}])[0] or {}
                    sal = ""
                    if pay.get("MinimumRange"):
                        try:
                            sal = f"${float(pay['MinimumRange']):,.0f}–${float(pay['MaximumRange']):,.0f}"
                        except (TypeError, ValueError):
                            sal = ""
                    duties = det.get("MajorDuties") or []
                    desc = "\n\n".join(filter(None, [det.get("JobSummary") or "",
                                                     "\n".join("• " + x for x in duties if isinstance(x, str)),
                                                     d.get("QualificationSummary") or ""]))
                    org = d.get("OrganizationName") or d.get("DepartmentName") or "Federal"
                    grade = f"{plan}-{det.get('LowGrade')}" + (f"/{det.get('HighGrade')}" if det.get("HighGrade") and det.get("HighGrade") != det.get("LowGrade") else "") if plan else ""
                    rows.append(make_row(
                        ctx, company=org, title=f"{d.get('PositionTitle', '')}{f' ({grade})' if grade else ''}",
                        location=pick_location(ctx, locs) if w == "local" else "Remote (US)", where=w,
                        link=d.get("PositionURI") or "", posted=parse_date(d.get("PublicationStartDate")),
                        closing=parse_date(d.get("ApplicationCloseDate")), description=desc, salary=sal,
                        clearance=det.get("SecurityClearance") if det.get("SecurityClearance") not in (None, "", "Not Required", "Not Applicable") else "",
                        employment_type="Federal (" + (((d.get("PositionSchedule") or [{}])[0] or {}).get("Name") or "Full-time") + ")",
                        work_mode="Remote" if w == "remote" else ("Telework eligible" if str(det.get("TeleworkEligible")).lower() == "true" else "On-site"),
                        source="USAJOBS"))
                pages = int((res.get("UserArea") or {}).get("NumberOfPages") or 1)
                if page >= pages or not items:
                    break
                page += 1
    except ReaderError as e:
        st["ok"] = False
        st["errors"].append(str(e))
    st["kept"] = len(rows)
    st["seconds"] = round(time.time() - t0, 1)
    return rows, st


# --------------------------------------------------------------------------- JSearch (job-board aggregator)
DEFAULT_JSEARCH_QUERIES = ""  # each user adds their own (Search page → "Suggest searches from my Profile")

JSEARCH_HOSTS = {
    "rapidapi": ("https://jsearch.p.rapidapi.com/search-v2", lambda k: {"X-RapidAPI-Key": k, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}),
    "openwebninja": ("https://api.openwebninja.com/jsearch/search-v2", lambda k: {"x-api-key": k}),
}


def jsearch_queries(cfg, ctx):
    out = []
    for line in (cfg.get("jsearch_queries") or DEFAULT_JSEARCH_QUERIES).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.replace("{home}", ctx.home_city or "United States"))
    return out


def _jsearch_call(cfg, query, remote, page=1, pages=1, date_posted="month"):
    key = (cfg.get("jsearch_api_key") or "").strip()
    url, hdr = JSEARCH_HOSTS.get(cfg.get("jsearch_provider") or "rapidapi", JSEARCH_HOSTS["rapidapi"])
    params = {"query": query, "page": page, "num_pages": pages, "date_posted": date_posted, "country": "us"}
    if remote:
        params.update(remote_jobs_only="true", work_from_home="true")
    try:
        return http(f"{url}?{urllib.parse.urlencode(params)}", headers=hdr(key), timeout=60)
    except ReaderError as e:
        if "HTTP 404" in str(e) and url.endswith("-v2"):  # older plans still use /search
            return http(f"{url[:-3]}?{urllib.parse.urlencode(params)}", headers=hdr(key), timeout=60)
        if "HTTP 401" in str(e) or "HTTP 403" in str(e):
            raise ReaderError(f"{e} — check the JSearch key and that the provider setting "
                              f"({cfg.get('jsearch_provider') or 'rapidapi'}) matches where you got it")
        if "HTTP 429" in str(e):
            raise ReaderError("JSearch says the monthly or hourly request limit is used up")
        raise


def jsearch_items(j):
    data = j.get("data") if isinstance(j, dict) else j
    if isinstance(data, dict):
        data = data.get("jobs") or data.get("results") or data.get("data") or []
    return data if isinstance(data, list) else []


def jsearch_posted(x):
    d = parse_date(x.get("job_posted_at_datetime_utc") or x.get("job_posted_at_timestamp") or x.get("date_posted"))
    if d:
        return d
    rel = str(x.get("job_posted_at") or x.get("job_posted_human_readable") or "").lower()
    m = re.search(r"(\d+)\s*(hour|day|week|month)", rel)
    if "today" in rel or "just" in rel or (m and m.group(2) == "hour"):
        return date.today().isoformat()
    if m:
        n = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]
        return (date.today() - timedelta(days=n)).isoformat()
    return ""


# Re-posting sites that copy jobs from elsewhere (often stale, sometimes scams). Links to these are
# skipped; the job is kept only if another link (employer or a known board) is available.
DEFAULT_JSEARCH_SKIP = """mysmartpros.com
dedyn.io
liveblog365.com
trabajo.org
hijobs.co.com
workopia.io
jobilize.com
jooble.org
bebee.com"""


def _skip_list(cfg):
    return [s.strip().lower().lstrip("*.").lstrip(".") for s in str(cfg.get("jsearch_skip_sites", DEFAULT_JSEARCH_SKIP) or "").replace(",", "\n").splitlines() if s.strip()]


def _skipped_site(url, skip):
    host = urllib.parse.urlparse(url or "").netloc.lower().split(":")[0]
    return bool(host) and any(host == s or host.endswith("." + s) for s in skip)


def jsearch_link(x, skip=()):
    """Best apply link: the employer's own, then any allowed board, never a skipped site. '' if none."""
    opts = [o for o in (x.get("apply_options") or []) if o.get("apply_link") and not _skipped_site(o["apply_link"], skip)]
    direct = next((o["apply_link"] for o in opts if o.get("is_direct")), None)
    main = x.get("job_apply_link") if not _skipped_site(x.get("job_apply_link"), skip) else ""
    return direct or main or (opts[0]["apply_link"] if opts else "")


def jsearch_row(ctx, x, w, skip=()):
    city = ", ".join(filter(None, [x.get("job_city"), x.get("job_state")]))
    loc = city or x.get("job_location") or ""
    link = jsearch_link(x, skip)
    sal = x.get("job_salary_string") or ""
    if not sal and x.get("job_min_salary") and x.get("job_max_salary"):
        per = (x.get("job_salary_period") or "").lower()
        sal = f"${float(x['job_min_salary']):,.0f}–${float(x['job_max_salary']):,.0f}" + (f" /{per}" if per and per != "year" else "")
    hl = x.get("job_highlights") or {}
    extra = "\n\n".join(f"{k}:\n" + "\n".join("• " + i for i in v) for k, v in hl.items() if isinstance(v, list) and v)
    desc = (x.get("job_description") or "") + ("\n\n" + extra if extra and extra[:200] not in (x.get("job_description") or "") else "")
    posted = jsearch_posted(x)
    pub = x.get("job_publisher") or ""
    return make_row(ctx, company=x.get("employer_name") or "", title=x.get("job_title") or "", location=tidy_loc(loc), where=w,
                    link=link, posted=posted, description=desc, salary=sal,
                    employment_type=employment(x.get("job_employment_type")),
                    source="JSearch" + (f" ({pub})" if pub else ""))


def read_jsearch(cfg, ctx):
    st = {"name": "JSearch (job boards)", "type": "jsearch", "scanned": 0, "title_matches": 0, "kept": 0, "errors": [],
          "seconds": 0, "ok": True, "requests": 0}
    if (cfg.get("jsearch_api_key") or "").strip() and not jsearch_queries(cfg, ctx):
        st["skipped"] = "No job-board searches yet. Add some on the Search page (or use “Suggest searches”)."
        return [], st
    if not (cfg.get("jsearch_api_key") or "").strip():
        st["skipped"] = "Add a JSearch API key on the Search page to include Indeed, LinkedIn, ZipRecruiter and other boards."
        return [], st
    t0 = time.time()
    pages = max(1, min(5, int(cfg.get("jsearch_pages") or 1)))
    date_posted = "week" if ctx.max_age <= 7 else "month"
    seen, rows = set(), []
    skip = _skip_list(cfg)
    st["skipped_sites"] = 0
    for q in jsearch_queries(cfg, ctx):
        remote = bool(re.search(r"\bremote\b", q, re.I))
        try:
            j = _jsearch_call(cfg, q, remote, pages=pages, date_posted=date_posted)
        except ReaderError as e:
            st["errors"].append(str(e))
            if "limit" in str(e) or "key" in str(e):
                st["ok"] = False
                break
            continue
        st["requests"] += pages
        for x in jsearch_items(j):
            jid = x.get("job_id") or x.get("job_uid") or (x.get("employer_name"), x.get("job_title"))
            if jid in seen:
                continue
            seen.add(jid)
            st["scanned"] += 1
            if not ctx.title_ok(x.get("job_title")):
                continue
            st["title_matches"] += 1
            posted = jsearch_posted(x)
            if not ctx.age_ok(posted):
                continue
            is_remote = bool(x.get("job_is_remote")) or "remote" in str(x.get("work_arrangement") or "").lower()
            loc = ", ".join(filter(None, [x.get("job_city"), x.get("job_state"), x.get("job_country")])) or x.get("job_location") or ""
            w = ctx.where(loc, remote_flag=is_remote, loose_remote=False)
            if w:
                if not jsearch_link(x, skip):
                    st["skipped_sites"] += 1   # only re-posting sites had it
                    continue
                rows.append(jsearch_row(ctx, x, w, skip))
    if st["errors"] and not rows and st["requests"] == 0:
        st["ok"] = False
    st["kept"] = len(rows)
    if st["skipped_sites"]:
        st["note"] = f"{st['skipped_sites']} skipped: only listed on re-posting sites you chose to skip."
    st["seconds"] = round(time.time() - t0, 1)
    return rows, st


def test_source(name, cfg, ctx):
    """One small live call to prove a key works. Returns {ok, detail, sample}."""
    if name == "usajobs":
        if not cfg.get("usajobs_api_key") or not cfg.get("usajobs_email"):
            return {"ok": False, "detail": "Enter the USAJOBS key and the email you registered with."}
        hdr = {"Host": "data.usajobs.gov", "User-Agent": cfg["usajobs_email"], "Authorization-Key": cfg["usajobs_api_key"]}
        try:
            j = http("https://data.usajobs.gov/api/search?" + urllib.parse.urlencode(
                {"Keyword": "information technology", "ResultsPerPage": 5, "DatePosted": 7}), headers=hdr)
        except ReaderError as e:
            return {"ok": False, "detail": f"{e} — check the key and that the email matches the one you registered."}
        res = j.get("SearchResult") or {}
        items = [((i.get("MatchedObjectDescriptor") or {}).get("PositionTitle") or "") for i in res.get("SearchResultItems") or []]
        return {"ok": True, "detail": f"USAJOBS key works · {res.get('SearchResultCountAll', 0)} IT postings this week",
                "sample": items[:3]}
    if name == "jsearch":
        if not cfg.get("jsearch_api_key"):
            return {"ok": False, "detail": "Enter the JSearch key."}
        try:
            j = _jsearch_call(cfg, f"director information technology in {ctx.home_city or 'United States'}", False)
        except ReaderError as e:
            return {"ok": False, "detail": str(e)}
        data = jsearch_items(j)
        out = {"ok": True, "detail": f"JSearch key works ({cfg.get('jsearch_provider') or 'rapidapi'}) · {len(data)} results · used 1 request",
               "sample": [f"{x.get('job_title')} — {x.get('employer_name')}" for x in data[:3]]}
        if not data:  # show the shape so a format change is easy to spot
            out["response_keys"] = list(j.keys())[:15] if isinstance(j, dict) else str(type(j))
        else:
            out["job_fields"] = sorted(data[0].keys())[:60]
        return out
    return {"ok": False, "detail": "Unknown source"}
