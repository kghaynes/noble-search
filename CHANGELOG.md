# Changelog

All notable changes are listed here. Versions follow [semantic versioning](https://semver.org/): 0.x.y while the project is young.

## [Unreleased]

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

[Unreleased]: https://github.com/kghaynes/noble-search/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/kghaynes/noble-search/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/kghaynes/noble-search/releases/tag/v0.1.0
