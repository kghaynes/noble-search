# Closed postings and the ⚡ apply-fast signal

Many postings come down within days of going up, often long before the closing date they list. Noble Search notices when a posting is taken down and keeps a small record of it. From those records it learns how long each employer's postings usually stay up, so it can tell you when to **apply fast**.

This page explains exactly how that works. For the short version, see the [README](../README.md#closed-postings-and-the--apply-fast-signal).

---

## 1. How a posting is marked closed

Each search reads its sources one after another. Right after each source is read, Noble Search compares what the source lists now with the jobs it found there before.

| Detected by | When | Label on the card |
|---|---|---|
| `employer` | The job is gone from the employer's own hiring system: Workday, Greenhouse, Lever, Ashby, SmartRecruiters, BambooHR, Eightfold, or the employer's job sitemap. | *Taken down from the employer's hiring site* |
| `usajobs` | The job dropped out of USAJOBS. The USAJOBS API lists only open announcements, so a job that disappears has closed. If that happens before its stated closing date, the card shows how many days early. | *Removed from USAJOBS* |
| `link` | A **watchlist** job's posting link now answers "not found" (HTTP 404 or 410, or Greenhouse's "job not found" redirect). Checked once per search, for up to 40 open watchlist jobs. | *The posting link no longer works* |
| `you` | You clicked **Posting closed?** on the job. | *You marked it closed* |

Closures by an employer system or USAJOBS take effect immediately. There is no "possibly closed" step to confirm.

### What is never closed automatically

**Job-board results (JSearch: LinkedIn, Indeed, Glassdoor, ZipRecruiter, …).** A job missing from today's job-board results proves nothing, for two reasons. Your searches take turns to stay within your monthly request limit, and each search returns only the top results. LinkedIn and Indeed also block automated checks, and Noble Search never tries to get around a bot check. For these, use **Posting closed?**.

The link check also skips job-board links, and any link that points into a private network.

---

## 2. Safety rules

The goal is that a bad day at a source, or a change you made, never closes a good job.

1. **Only after a read that worked.** Nothing is closed from a source whose read failed, was stopped, or is turned off.
2. **First read learns, it doesn't close.** The first time a source is read with this feature, Noble Search only records how many listings it has. The same applies to the first read after that source's settings change. Closures start with the next read. Settings that count as a change:
   - **Employers:** the hiring-system link or reader type, or the "posted within" window.
   - **USAJOBS:** the nationwide choice, the agencies list, the minimum grade, your home town, your work modes, or the window.
3. **Short reads close nothing.** If a source returns under half the listings it returned last time, nothing from it is closed that day. This catches a site that is half-down or answering with a short list.
4. **Still listed means still open.** A job counts as still listed if its title appears anywhere in what the source showed, not just among your matches. If you change your title rules and a job stops matching, it doesn't get marked closed. Titles are compared without regard to case or extra spaces, and the " (GS-15)" Noble Search adds to USAJOBS titles is ignored.
5. **Only jobs the reader actually scans.** Most readers only look at postings from the last 30 days (your window). A job older than the window minus 2 days is never closed by absence. If it's on your watchlist, the link check covers it.
6. **Reposts reopen.** If a closed job appears again on its employer's site or on USAJOBS, it reopens on its own and its record is marked as a repost. Reposts are left out of the speed statistics. There is one exception: if **you** marked a job closed, a job board still showing it won't reopen it, because boards keep stale listings. The employer's own site or USAJOBS will reopen it.

---

## 3. What you see

### On the job card
- **Closed jobs stay visible.** They stay in their tab, dimmed and sorted to the bottom, with a label such as *Closed Nov 7 · was up 4 days · 12 days before its deadline*. Hover the label to see how the closure was detected.
- **Watchlist alert:**
  - *Closed before you applied.* (when the job was ★ Interested)
  - *Posting closed — likely filled. Follow up or move on.* (when it was Applied, Interviewing or Offer)
- **⚡ Usually closes in ~N days** appears on open jobs from employers that take postings down fast (see section 4).
- **Posting closed?** marks a job closed yourself. **↺ Reopen — it's still up** undoes any closure.

### In the morning email
- **Top picks** from fast-closing employers get the same ⚡ chip: *⚡ Fastco usually closes in ~4 days — apply soon.*
- **Closed since the last search** lists watchlist jobs closed by that search, each with *likely filled — follow up or move on* or *closed before you applied*. A count of other closed postings follows.
- Closed jobs are left out of **Closing within 7 days**.

### On the Search page
The **Closed postings** box shows:
- how many postings have closed, by how they were detected, plus reposts
- how fast each employer's postings come down (median days, after 5 closures)
- why watchlist jobs became *Not for me*

---

## 4. The ⚡ apply-fast signal

For each employer, Noble Search takes the **median** number of days its postings stayed up. "Up" runs from the posted date (or the day Noble Search first saw the job, if there's no posted date) to the day it closed. Reposts don't count.

The chip appears when:
- the employer has **at least 5** closed postings, and
- the median is **10 days or less**.

The median is used instead of the average so that one posting left up for months doesn't hide the usual pattern. Employers are matched by company name, without regard to case.

---

## 5. "Not for me" reasons (watchlist only)

When you move a job **from your watchlist** (Interested, Applied, Interviewing or Offer) to **Not for me**, a short picker asks why:

**Certifications · Specific skills · Salary · Location · Other**, plus an optional note of up to 300 characters.

- **Save & move to Not for me** records your picks. **Skip** moves the job without reasons. **Cancel** leaves it where it was.
- **Dismissing from the Inbox never asks.** Triage stays one click.
- Totals appear in the Search page's *Closed postings* box. A later version may use them to tune fit ratings, for example a salary floor or a recurring certification gap.

---

## 6. What is stored

Two small tables in your database (`data/jobs.db`). Both are kept after the 30-day cleanup removes the job itself, so the employer speeds keep learning.

**`closures`**: one row per closed posting:
company, title, source label, lane, fit, posted date, first seen, stated closing date, closed date, how it was detected, your status at the time, days it was up, days before its deadline, reopened date (if reposted), and the search run.

**`passes`**: one row per watchlist job moved to *Not for me*:
company, title, lane, fit, the status it had, your reasons, your note, and the time.

No notes, drafts, job descriptions or resume text are kept in either table. Each job also has two fields, `closed_on` and `closed_by`. These are removed with the job and are included in **Export CSV**.

---

## 7. For developers

- **Code:** `closures.py`.
  - `detect()` runs after each source read, called from `search.run()`.
  - `link_check()` runs once at the end of a search.
  - `should_reopen()` and `reopen_in()` are called from `search.merge_rows()`.
  - `employer_speed()` and `fast_days()` feed the job list and the email.
  - `record_pass()` is called from `app.update_job()` when a watchlist job is dismissed with `reasons`.
- **"What the source showed":** the titles recorded in `sources.Context.seen` for that source. These are recorded before your title rules are applied, and the same list powers *Why didn't I see a job?*. Recording stops at 8,000 titles per source; a source at that cap is never used for closures.
- **Per-source memory:** stored in the `meta` table under `closures:<source name>` as `{"fp": settings fingerprint, "seen": listing count}`.
- **Endpoint:** `POST /jobs/close {job_key, closed: true|false}`. `POST /jobs/update` accepts `reasons` (list) and `reason_note` with `status: "dismissed"`.
- **Tuning:** constants at the top of `closures.py`: `MIN_CLOSURES` (5), `FAST_DAYS` (10), `MIN_SEEN_SHARE` (0.5), `AGE_MARGIN` (2), `LINK_CHECK_MAX` (40).
- **Tests:** `tests/test_closures.py`.
