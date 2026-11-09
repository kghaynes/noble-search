# Changelog

All notable changes are listed here. Versions follow [semantic versioning](https://semver.org/): 0.x.y while the project is young.

## [Unreleased]

### Fixed
- **Re-posting sites skipped:** jobleads.com, jobmesh.io and trabajos.univision.com are now on the default JSearch skip list. They pass JSearch only a ~500-character teaser and hide the full posting behind a JavaScript sign-up page, so fit ratings ran on almost nothing and "Fetch posting" failed with "loads its text with JavaScript". Existing installs keep their saved list: add these under **Search → Job-board searches (JSearch) → Skip these re-posting sites**.

### Changed
- **LinkedIn postings are off by default, and title-and-link only when on.** LinkedIn doesn't permit showing its job details in apps like this. Job-board results published by LinkedIn are now dropped inside the search, before anything is saved or shown. **Include LinkedIn postings** (Search page → Schedule) turns them on after a confirmation explaining the limits; your confirmation and its time are recorded. When on, a LinkedIn job shows only its title, its company and an **Also listed on LinkedIn ↗** link. Paste the description yourself into Resume & apply to draft a resume, because Noble Search never opens LinkedIn pages. Jobs that are also on an employer's site keep the employer's details and gain the LinkedIn link. LinkedIn jobs are still rated from their title and company, flagged **⚠ Title-only rating — paste the description for a full rating**; **Rate with this description** in Resume & apply saves your pasted description and re-rates the job in full. On upgrade, jobs known only from LinkedIn are removed once (watchlist ones are reduced to title, company and link). See the README's *LinkedIn postings* section.

## [0.4.0] — 2026-11-09
Know when postings come down — and which employers you need to apply to fast. Plus per-job resume choice and sturdier drafts.

### Added
- **Closed postings and the ⚡ apply-fast signal.** Every search notices postings that were taken down. A job gone from the employer's own hiring system (Workday, Greenhouse, Lever, Ashby, SmartRecruiters, BambooHR, Eightfold, sitemaps) or from USAJOBS is marked closed right away. Watchlist jobs also get a posting-link check. **Posting closed?** on any card covers LinkedIn, Indeed and anything else the app can't check, and **↺ Reopen** undoes it. Closed jobs stay in their tab, dimmed, at the bottom, labeled with how long they were up and how early they closed. A starred or applied job that closes says *likely filled — follow up or move on* and is listed in the morning email under **Closed since the last search**. After 5 closures, employers whose postings usually come down within 10 days get **⚡ Usually closes in ~N days** on new jobs, on the card and on morning-email top picks. Safety rules keep failed or partial reads, first reads, settings changes, title-rule changes and older jobs from closing anything by mistake, and a closed job that reappears on its employer's site reopens as a repost. A small closure record outlives the 30-day cleanup. New **Closed postings** box on the Search page. See [docs/closed-postings.md](docs/closed-postings.md).
- **Why a watchlist job is "Not for me".** Moving a starred or applied job to *Not for me* asks why: certifications, specific skills, salary, location, other, plus an optional note. It's optional and skippable, and Inbox triage never asks. Totals show in the Search page's *Closed postings* box.
- **Pick the resume to follow, per job.** The Resume & apply pane now has a **Follow** menu next to the model choice: keep your default template (Profile → Resumes) or pick another uploaded resume for this one job — say, a program-manager resume for a non-executive role. The pick sets the draft's layout, section names and wording, and which resume the application text leads with. Each draft shows which resume it followed.

### Upgrading
- Closures start the day after the first search on this version: that first search only learns each source's listings. ⚡ chips appear once an employer has 5 closed postings. No settings to change.

### Fixed
- **Drafts failing with "invalid JSON" when the reply was cut off.** A long resume could run past the reply limit (some models also use part of it to think first), and the half-finished reply looked like broken JSON. Drafts now get twice the room (16,000 tokens), a cut-off reply is detected and asked for again with 32,000, and the error says plainly when a reply was too long. Fit rating gets more room the same way.

## [0.3.1] — 2026-10-09
Drafts that look like your own resume, a problems log, and no more drafts failing on a stray comma.

### Added
- **Problems log** (Settings → Problems log): errors and notable events in one place — sources that failed, jobs that couldn't be rated, drafts that failed, email problems, and each search's result. Saved in your data folder as `logs/noble-search.log` (rotates at 1 MB), also shown by `docker compose logs`. Keys and passwords are never written to it.
- **Drafts look like your template resume.** The resume marked **Layout template** now sets the whole look of every draft, not just fonts and margins: section headings (lines, shading, color, caps), the name and contact line, employer and job-title lines, the scope line, bullets (including a labeled bullet like *Key Accomplishment:*), and the section names and their order. It also follows the template's shape: one line per job when the template does that (written in the template's own order and separators), a headline above the first heading, an unnamed bullets section such as *Selected Key Results*, bullets-only *Additional* sections, and as many Education lines as the template uses. No section is added that the template doesn't have (technology skills join the skills list). Symbol-font bullets (Wingdings arrows and squares) become a plain • so hiring software reads them. Templates with tables, text boxes or two columns can't be copied cleanly and use the built-in design; the draft's check list says which was used and why.

### Fixed
- **Drafts failing with "Model returned invalid JSON".** Long resume and application drafts sometimes came back with one missing or extra comma, which failed the whole draft. Small slips like that are now repaired automatically, and if the reply still can't be read the draft is asked for once more. The unreadable reply is kept in `logs/` for troubleshooting.
- **Drafts followed an older resume's layout and wording.** The tagged template resume is now given to the writer first as the current resume; other resumes are used for facts only.

## [0.3.0] — 2026-10-08
Smarter, self-explaining search: plain-word matching, job-board searches built from your Profile, a settings check, and "Why didn't I see a job?".

### Added
- **"Why didn't I see a job?"** (Search page, and **Find out why** under the Jobs list): paste a job's link and/or title and it walks the job through every step — already in your list or dismissed, link (re-posting site?), title rules, location, and whether a recent search came across it — and says where it drops out, with one-click fixes. Each run now keeps a record of every title it checked and why it was kept or dropped (last 5 runs).
- **Settings check** on Save (or **Check my settings**): warnings about settings that work against each other (a skip word that blocks your own word, a field word already covered by another, no remote or near-home searches, a home town that can't be read), a test of the titles you want (examples from ✨ Suggest titles, your starred and High-fit jobs) against your rules, and how many titles from the last run would pass. One-click fixes.
- **How each search is doing:** for each job you look for on job boards, what the boards returned, what you kept and what was new over the last runs; searches that find nothing are flagged.
- **Federal jobs posted nationwide** (USAJOBS): optionally keep senior postings filled "anywhere" (Multiple locations, Location negotiable…), marked **relocation required** — for any agency, or only agencies with offices near you (✨ Suggest agencies).

### Changed
- **Job-board searches are now "jobs to look for".** List just the job (*director of IT*); the app searches it near home and/or remotely from your Profile work modes. No more `{home}` or "remote" in the lines. Existing search lines are converted automatically; lines naming another place move to **Advanced: exact searches**.
- **Monthly request limit:** set what your plan allows (200 on the free plan). If there are more searches than fit, they take turns from run to run instead of running out the month early. The page shows exactly what runs.
- **Plain-word matching** for seniority, field and skip words: any case, whole words only, singular and plural (*program* = *programs*), common endings (*engineer* = *engineering*, *intern* = *internship*) and *cyber…* compounds. Short words like *IT* and *AI* match only as whole words. Lines containing `\ | ( )` are still used as patterns, so existing lists keep working.
- ✨ Suggest titles now gives plain words, and ✨ Suggest jobs gives job titles only.

### Fixed
- The Jobs page **Near home** filter only recognized Florida. It now uses your home town and towns list, for any state.
- **Fit rating: "Model did not return JSON".** A long reason could run past the reply limit and cut the answer off. Rating now allows more room and retries twice, asking for JSON only.


## [0.2.1] — 2026-10-04
Security fixes from GitHub code scanning (10 high alerts). No change to how the search works.

### Security
- **Resume files stay in their folder.** Every resume upload, delete and lookup now checks that the file path is inside the resumes folder and refuses anything else. File names were already cleaned, so this is a second safeguard.
- **Exact website matching.** Checks for Greenhouse, ADP and Indeed links now accept only those sites and their subdomains, not look-alike names that merely contain them (e.g. `evilgreenhouse.io`).

### Upgrading
Run `./scripts/update.sh` (your data and settings are kept).

## [0.2.0] — 2026-10-04
Military background and AI title suggestions, plus the fixes and progress feedback from the first fresh-install test (these were prepared as 0.1.2, which was never published).

### Added
- **Military background on the Profile:** branch and rank / pay grade picked from lists (ranks change with the branch — Army, Marine Corps, Navy, Air Force, Space Force, Coast Guard, Guard/Reserve, federal civilian GS/SES — with "Other — type it"), occupation codes (MOS, AFSC, NEC, rating, designator) and skill identifiers (ASI, SQI, SI). Used by title suggestions, search suggestions and the fit rating.
- **✨ Suggest titles:** the AI translates your service and your whole career inventory (or, if you have none yet, your uploaded resumes) into civilian titles and proposes seniority words, field words and skip words to tick; add them to your lists or replace them, and edit freely.
- ✨ Suggest searches also reads the whole career inventory (it was cut at 3,000 characters) and falls back to your resumes.
- **Field words:** a new optional list — a job's title must contain a seniority word *and* a field word (e.g. "Director" + "Cyber"). Keeps out right-level, wrong-field jobs such as "Director of Nursing". Applies to senior federal jobs too.
- **Progress box** on the Jobs and Search pages while a search or rating runs: the current step (search sources → rate fit & gaps → ready), the source or job being worked on, a progress bar, an estimated time left, and a reminder that it keeps running if you close the page. First searches are flagged as the slow one (15–30+ minutes).
- **Stop search** button (progress box, Search page and Jobs header). Jobs found so far are kept and rated; no summary email is sent for a stopped run.
- **Warnings with one-click fixes:** fit rating turned off while jobs wait to be rated ("Turn rating on & rate them"), and job boards with a key but no searches ("Add searches").
- **Run your first search** button on an empty Jobs page (or **Choose where to search** if nothing is set up yet).
- **Back to Getting started** bar on every page until setup is finished, naming the next step.
- Profile page shows how the search reads your home town ("Search reads this as Melbourne, FL").
- The towns box says you can type or paste your own list; a ✨ Suggest error links straight to the Profile page.

### Fixed
- **USAJOBS searched the whole country.** If the Profile's home town couldn't be read (for example "Melbourne, Florida" or an address without commas), the "near home" federal search ran nationwide and kept hundreds of jobs. Home towns are now read in many more formats, and the near-home search is skipped — with a note in the run summary — until a home town is set.
- **Off-target senior federal jobs.** GS-14+ and SES jobs bypassed the "skip titles" list, so physician and similar jobs got through. The skip list now applies to them, and physician, medical officer, nurse, dentist, pharmacist, veterinarian, psychologist, chaplain, attorney and law clerk are on the default skip list.
- **Typing wiped during a search.** While a search ran, the Search page refreshed every 5 seconds and erased anything typed or pasted (towns, searches) and the ✨ Suggest result. Now only the "Daily search" box refreshes.
- **"Fill in your Profile" stayed unchecked** with no explanation. It now says exactly what is missing (your name, or a home town the search can read), and accepts the home town from either Home location or City, State.

### Upgrading
Run `./scripts/update.sh` (your data and settings are kept). If you saved your own "skip titles" list before, it keeps your version — add the new medical/legal entries yourself if you want them.

## [0.1.1] — 2026-10-03
- Morning email redesigned as a short briefing: top 5 picks with fit, pay and closing-date labels, a "closing within 7 days" box, and a source summary. Reads well on a phone.
- Job boards: links to copycat re-posting sites are never shown (the list is editable on the Search page). Internal-only postings are skipped.
- Sharper fit reasons: what the job is, why you fit it, then the gap.
- License: personal job searching, including contract and consulting work, is stated as a permitted noncommercial use.
- Repository housekeeping: contributing guide, security policy, issue forms, automatic tests, Dependabot, social preview, README badges.
- Docker image now runs Python 3.14. Updated tzdata and the GitHub Actions used by the tests.

## [0.1.0] — 2026-10-01
First public release.
- Daily search of employer hiring systems (Workday, Greenhouse, Lever, Ashby, SmartRecruiters, BambooHR, Eightfold, career-site sitemaps), USAJOBS and job boards via JSearch.
- "Add an employer by name" finder.
- AI fit rating (High / Med / Low) with a civilian-terms reason and gaps.
- Tailored resume drafts (.docx in your own layout, ATS-safe, fact-checked against your Profile).
- Application text for Workday-style forms, plus a keyword gap check.
- Morning email and ntfy push; Draft Library; 30-day retention with a permanent watchlist.
- Getting-started checklist; ✨ Suggest towns and searches; Claude API by default with optional local Ollama.

[Unreleased]: https://github.com/kghaynes/noble-search/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/kghaynes/noble-search/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/kghaynes/noble-search/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/kghaynes/noble-search/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/kghaynes/noble-search/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/kghaynes/noble-search/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/kghaynes/noble-search/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/kghaynes/noble-search/releases/tag/v0.1.0
