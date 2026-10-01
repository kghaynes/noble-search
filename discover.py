"""Find an employer's job board from just its name (or website).

Steps, cheapest first:
  1. Try the public job-board APIs under names derived from the company name
     (Greenhouse, Lever, Ashby, SmartRecruiters, BambooHR).
  2. Open the company website, follow its Careers link(s), and look for links to a
     hiring system (Workday, Greenhouse, Lever, Ashby, SmartRecruiters, BambooHR,
     Eightfold) — or a site map with job pages we can read directly.
  3. If a JSearch key is set, look at the company's own "apply" links in one
     JSearch result page (costs 1 request).
Every candidate is then test-read to count open jobs. Systems we can't read
(ADP, iCIMS, Taleo, Oracle, ...) are reported by name so the user knows why.
"""
import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import sources

TIMEOUT = 12
BUDGET = 45  # seconds for the whole search

SUFFIX = re.compile(r"\b(inc|llc|l\.l\.c|corp|corporation|co|company|ltd|plc|group|holdings|technologies|technology|"
                    r"systems|international|incorporated|the)\b\.?", re.I)

UNSUPPORTED = [
    (r"workforcenow\.adp\.com|recruiting\.adp\.com|myjobs\.adp\.com", "ADP Workforce Now"),
    (r"icims\.com", "iCIMS"), (r"taleo\.net", "Oracle Taleo"), (r"oraclecloud\.com", "Oracle Recruiting Cloud"),
    (r"jobvite\.com", "Jobvite"), (r"paylocity\.com", "Paylocity"), (r"ultipro\.com|ukg\.net|ukg\.com", "UKG"),
    (r"dayforcehcm\.com", "Dayforce"), (r"applytojob\.com", "JazzHR"), (r"paycom(online)?\.com", "Paycom"),
    (r"workable\.com", "Workable"), (r"breezy\.hr", "Breezy"), (r"rippling(-ats)?\.com", "Rippling"),
    (r"clearcompany\.com", "ClearCompany"), (r"recruitee\.com", "Recruitee"), (r"avature\.net", "Avature"),
    (r"successfactors\.(com|eu)", "SAP SuccessFactors (main site)"), (r"phenompeople\.com", "Phenom"),
]
ATS_HINT = re.compile(r"https?://[^\s\"'<>]*(myworkdayjobs\.com|greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|"
                      r"bamboohr\.com|eightfold\.ai|icims\.com|adp\.com|taleo\.net|oraclecloud\.com|jobvite\.com|"
                      r"paylocity\.com|ultipro\.com|ukg\.net|dayforcehcm\.com|applytojob\.com|paycom|workable\.com|"
                      r"breezy\.hr|rippling|clearcompany\.com|recruitee\.com|avature\.net|successfactors)[^\s\"'<>]*", re.I)


def slugs(name):
    base = SUFFIX.sub(" ", name.lower().replace("&", " and "))
    words = re.findall(r"[a-z0-9]+", base)
    full = re.findall(r"[a-z0-9]+", name.lower())
    out = []
    for w in (words, full):
        if w:
            out += ["".join(w), "-".join(w)]
    if words:
        out.append(words[0])
    seen, res = set(), []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            res.append(s)
    return res[:5]


def fetch(url, as_json=False, timeout=TIMEOUT, retries=1):
    """GET a page; returns (final_url, text|json) or (None, error)."""
    req = urllib.request.Request(url, headers={"User-Agent": sources.UA,
                                               "Accept": "application/json" if as_json else "text/html,*/*;q=0.8",
                                               "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(3_000_000).decode("utf-8", "replace")
            final = r.geturl()
        if as_json:
            import json
            return final, json.loads(raw)
        return final, raw
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, str(getattr(e, "reason", e))[:120]


def board_from_url(u, site_domain=""):
    """Turn any link into a job-board URL our readers understand. Returns (url, kind) or (None, unsupported_name)."""
    u = html.unescape(u).strip().rstrip("\\")
    p = urllib.parse.urlparse(u)
    host, parts = p.netloc.lower(), [x for x in p.path.split("/") if x]
    qs = urllib.parse.parse_qs(p.query)
    if host.endswith(".myworkdayjobs.com"):
        site = next((x for x in parts if not re.match(r"^[a-z]{2}-[A-Z]{2}$", x)), "")
        if site and site.lower() not in ("wday", "job"):
            return f"https://{host}/{site}", "workday"
        return None, None
    if host.endswith("greenhouse.io"):
        t = qs.get("for", [""])[0] or (parts[0] if parts and parts[0] not in ("embed", "v1") else "")
        if not t and len(parts) > 2 and parts[:2] == ["v1", "boards"]:
            t = parts[2]
        return (f"https://boards.greenhouse.io/{t}", "greenhouse") if t else (None, None)
    if host == "jobs.lever.co" and parts:
        return f"https://jobs.lever.co/{parts[0]}", "lever"
    if host == "jobs.ashbyhq.com" and parts:
        return f"https://jobs.ashbyhq.com/{parts[0]}", "ashby"
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and parts:
        return f"https://jobs.smartrecruiters.com/{parts[0]}", "smartrecruiters"
    if host.endswith(".bamboohr.com") and host.split(".")[0] not in ("www", "api"):
        return f"https://{host}/careers", "bamboohr"
    if host.endswith(".eightfold.ai"):
        dom = qs.get("domain", [""])[0] or site_domain or host.split(".")[0] + ".com"
        return f"https://{host}/careers?domain={dom}", "eightfold"
    for pat, label in UNSUPPORTED:
        if re.search(pat, host):
            return None, label
    return None, None


def count_jobs(url, kind):
    """Read the board lightly: (open_job_count, sample_titles) or raises."""
    k, p = sources.detect(url)
    kind = kind or k
    if kind == "workday":
        j = sources.http(f"https://{p['host']}/wday/cxs/{p['tenant']}/{p['site']}/jobs",
                         {"appliedFacets": {}, "limit": 5, "offset": 0, "searchText": ""}, timeout=TIMEOUT, retries=1)
        return int(j.get("total") or 0), [x.get("title") for x in (j.get("jobPostings") or [])[:3]]
    if kind == "greenhouse":
        j = sources.http(f"https://boards-api.greenhouse.io/v1/boards/{p['token']}/jobs", timeout=TIMEOUT, retries=1)
        jobs = j.get("jobs") or []
        return len(jobs), [x.get("title") for x in jobs[:3]]
    if kind == "lever":
        arr = sources.http(f"https://api.lever.co/v0/postings/{p['token']}?mode=json", timeout=TIMEOUT, retries=1)
        arr = arr if isinstance(arr, list) else []
        return len(arr), [x.get("text") for x in arr[:3]]
    if kind == "ashby":
        j = sources.http(f"https://api.ashbyhq.com/posting-api/job-board/{p['token']}", timeout=TIMEOUT, retries=1)
        jobs = j.get("jobs") or []
        return len(jobs), [x.get("title") for x in jobs[:3]]
    if kind == "smartrecruiters":
        j = sources.http(f"https://api.smartrecruiters.com/v1/companies/{p['token']}/postings?limit=3", timeout=TIMEOUT, retries=1)
        return int(j.get("totalFound") or 0), [x.get("name") for x in (j.get("content") or [])[:3]]
    if kind == "bamboohr":
        j = sources.http(f"https://{p['sub']}.bamboohr.com/careers/list", timeout=TIMEOUT, retries=1)
        res = j.get("result") or []
        return len(res), [x.get("jobOpeningName") for x in res[:3]]
    if kind == "eightfold":
        q = urllib.parse.urlencode({"domain": p["domain"], "query": "", "start": 0})
        d = sources.http(f"https://{p['host']}/api/pcsx/search?{q}", timeout=TIMEOUT, retries=1).get("data") or {}
        return int(d.get("count") or 0), [x.get("name") for x in (d.get("positions") or [])[:3]]
    if kind == "sitemap":
        urls = sources._sitemap_urls(p["sitemap"])
        jobs = [u for u, _ in urls if re.search(r"/job(s|detail)?/", u, re.I)]
        if not jobs:
            return 0, []
        page = sources.http(jobs[0], as_json=False, timeout=TIMEOUT, retries=1)
        d = sources.parse_job_page(page)
        if not d["structured"]:
            raise sources.ReaderError("job pages have no machine-readable details")
        return len(jobs), [d["title"]]
    raise sources.ReaderError("not a readable job board")


def _careers_links(base_url, page):
    links = re.findall(r'href=["\']([^"\'#]+)["\']', page, re.I)
    out = []
    for h in links:
        full = urllib.parse.urljoin(base_url, html.unescape(h))
        if re.search(r"career|jobs|join-?us|work-?with-?us|opportunit|employment", full, re.I) and \
                not re.search(r"\.(pdf|jpg|png|svg)$|linkedin|facebook|twitter|instagram|youtube|glassdoor|indeed", full, re.I):
            out.append(full.split("#")[0])
    seen, res = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            res.append(u)
    return res[:4]


def _registrable(host):
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def discover(name, website="", cfg=None):
    t0 = time.time()
    name = (name or "").strip()
    log, found, unsupported = [], {}, set()

    def left():
        return BUDGET - (time.time() - t0)

    def consider(url, kind, how):
        if url and url not in found:
            found[url] = {"url": url, "type": kind, "how": how}

    # 1. guess public job-board APIs from the name
    for s in slugs(name)[:3]:
        if left() < 5:
            break
        for kind, url in (("greenhouse", f"https://boards.greenhouse.io/{s}"), ("lever", f"https://jobs.lever.co/{s}"),
                          ("ashby", f"https://jobs.ashbyhq.com/{s}"), ("bamboohr", f"https://{s}.bamboohr.com/careers")):
            try:
                n, sample = count_jobs(url, kind)
                if n:
                    consider(url, kind, f"found a {sources.READER_LABELS[kind]} board named “{s}”")
            except Exception:  # noqa: BLE001 — a miss is normal
                pass
    log.append("Checked Greenhouse, Lever, Ashby and BambooHR for boards named after the company.")

    # 2. company website → careers page → hiring-system links
    sites = []
    if website:
        sites.append(website if website.startswith("http") else "https://" + website)
    for s in slugs(name)[:2]:
        sites += [f"https://www.{s.replace('-', '')}.com", f"https://www.{s}.com"]
    seen_sites, site_domain = set(), ""
    for site in sites:
        if left() < 8 or site in seen_sites:
            continue
        seen_sites.add(site)
        final, page = fetch(site)
        if not final or not isinstance(page, str):
            continue
        site_domain = _registrable(urllib.parse.urlparse(final).netloc)
        log.append(f"Opened {final}.")
        pages = [(final, page)]
        for cl in _careers_links(final, page):
            if left() < 6:
                break
            f2, p2 = fetch(cl)
            if f2 and isinstance(p2, str):
                pages.append((f2, p2))
                log.append(f"Followed the careers link {f2}.")
                for cl2 in _careers_links(f2, p2)[:2]:  # many sites have Careers → "Search jobs"
                    if left() < 6 or cl2 in [x[0] for x in pages]:
                        continue
                    f3, p3 = fetch(cl2)
                    if f3 and isinstance(p3, str):
                        pages.append((f3, p3))
        for url, text in pages:
            for m in ATS_HINT.finditer(text + " " + url):
                b, kind = board_from_url(m.group(0), site_domain)
                if b:
                    consider(b, kind, f"linked from {urllib.parse.urlparse(url).netloc}")
                elif kind:
                    unsupported.add(kind)
            if "eightfold" in text.lower() and "/careers" in url:  # Eightfold on the employer's own domain
                h = urllib.parse.urlparse(url).netloc
                consider(f"https://{h}/careers?domain={site_domain}", "eightfold", f"Eightfold site at {h}")
            if re.search(r"/job/|/jobs/|JobDetail", text) and urllib.parse.urlparse(url).netloc not in (
                    urllib.parse.urlparse(site).netloc,):
                consider(f"https://{urllib.parse.urlparse(url).netloc}/sitemap.xml", "sitemap",
                         f"careers site {urllib.parse.urlparse(url).netloc}")
        break  # first website that answered is the one we use

    # 3. JSearch: the employer's own apply links
    cfg = cfg or {}
    if (not found or all(v["type"] == "sitemap" for v in found.values())) and cfg.get("jsearch_api_key") and left() > 8:
        try:
            j = sources._jsearch_call(cfg, f"{name} jobs", False)
            log.append("Looked at the company's apply links in one JSearch result page (1 request).")
            for x in sources.jsearch_items(j):
                emp = (x.get("employer_name") or "").lower()
                if not any(w in emp for w in slugs(name)[-1:]):
                    continue
                for link in [x.get("job_apply_link")] + [o.get("apply_link") for o in x.get("apply_options") or []]:
                    b, kind = board_from_url(link or "", site_domain)
                    if b:
                        consider(b, kind, "found in the company's job postings")
                    elif kind:
                        unsupported.add(kind)
        except Exception as e:  # noqa: BLE001
            log.append(f"JSearch lookup failed: {e}")

    # 4. test-read every candidate
    cands = []
    for c in found.values():
        if left() < 3:
            break
        try:
            n, sample = count_jobs(c["url"], c["type"])
            if n:
                cands.append({**c, "jobs": n, "sample": [s for s in sample if s][:3], "label": sources.READER_LABELS[c["type"]]})
        except Exception as e:  # noqa: BLE001
            log.append(f"{c['url']} could not be read ({str(e)[:80]}).")
    order = ["workday", "eightfold", "greenhouse", "lever", "ashby", "smartrecruiters", "bamboohr", "sitemap"]
    cands.sort(key=lambda c: (order.index(c["type"]), -c["jobs"]))
    tip = ""
    if not cands:
        if unsupported:
            tip = (f"{name} uses {', '.join(sorted(unsupported))}, which this dashboard can't read. "
                   "Its jobs still reach you through the job-board search (JSearch).")
        else:
            tip = ("Couldn't find it automatically. Open the company's careers page, click the button that shows its job "
                   "list (often 'Search jobs'), copy the address from the browser bar, and use “Add employer manually”.")
    return {"ok": bool(cands), "name": name, "candidates": cands, "unsupported": sorted(unsupported),
            "log": log, "tip": tip, "seconds": round(time.time() - t0, 1)}
