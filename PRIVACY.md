# Privacy: what happens to your personal data

Noble Search runs on your own computer or server. This page explains what personal data it holds, where that data is stored, what leaves your machine and where it goes, and how to delete it.

**Short version:** Noble Search keeps everything on your machine. The Noble Search author never receives any of your data. Data leaves your machine only to services *you* set up with your own keys: an AI model to rate jobs and write drafts, job sites to search, and your email or push service for the morning summary. Before anything goes to the AI, your name, street address, phone, email and LinkedIn link are replaced with placeholders, and your clearance is cut down to level and status.

## What Noble Search holds

| Data | Where it comes from | Stored in |
|---|---|---|
| Profile: name, town and state, phone, email, LinkedIn link, clearance, military background, target roles, location and radius | You type it in | `data/profile/profile.json` |
| Resumes you upload (.docx) | You upload them | `data/profile/resumes/` |
| Career inventory (a long summary of your career, built from your resumes) | Written by the AI from your resumes | `data/profile/inventory.md` |
| Jobs found, fit ratings, your notes and statuses | The daily search, the AI, and you | `data/jobs.db` |
| Resume drafts and application text | Written by the AI | `data/drafts/` |
| API keys and passwords (Anthropic, JSearch, USAJOBS, email) | You type them in | `data/settings.json` and `data/search.json`, readable by the file owner only |
| App log and troubleshooting files | The app | `data/logs/` |

Noble Search never asks for, and should never be given, your Social Security number, DD-214, VA disability rating, medical information or ID documents. None of them help a job search.

## What leaves your machine

Noble Search sends data out only for the features you turn on, and only to the service named in each row.

| Goes to | When | What is sent | What is never sent |
|---|---|---|---|
| **Anthropic (Claude API)**, using *your* API key | Fit ratings, resume drafts, application text, building your career inventory, search suggestions | The job posting, plus your profile, career inventory and resume text **with your personal details replaced by placeholders** (see below) | Your real name, street address, phone, email, LinkedIn link, polygraph or investigation details |
| **Your local model (Ollama)** | Same features, if you choose Ollama instead of Claude | Same as above, but it stays on your own network | Nothing leaves your network |
| **JSearch** (OpenWeb Ninja or RapidAPI), using *your* key | Daily search | Search words (job titles, keywords) and your town or search area | Your profile, resumes, notes |
| **USAJOBS**, using *your* key | Daily search | Search words and area. USAJOBS requires the email address you registered with them, sent with each request | Your profile, resumes, notes |
| **Employer job boards** (Workday, Greenhouse, Lever, Ashby, SmartRecruiters, BambooHR, Eightfold, company career pages) | Daily search, "Find job board", "Fetch posting" | A normal web request for the employer's public job list or posting | Anything about you |
| **Your email provider (SMTP)**, if you set it up | Morning summary | The summary, sent to you: job titles, companies, fit ratings and reasons | Your resumes |
| **ntfy**, if you set it up | Morning summary | A short push: the subject line and counts (e.g. "3 new · 1 High · 2 Med") | Job details, your profile, resumes |

Noble Search has no analytics, no tracking and no "phone home". It sends nothing to the Noble Search author or to GitHub. (Tips through GitHub Sponsors or Ko-fi are handled by those sites, not by the app.)

## How your personal details are hidden from the AI

The setting **Settings → Claude API → Hide my personal details from the AI** is on by default. With it on, Noble Search does the following on your machine before every AI request, for both Claude and Ollama:

1. **Swaps out your contact details.** Your name (including short forms such as "Ken Smith" for "Kenneth A. Smith"), street address, phone number in any format, email and LinkedIn link become placeholders such as `{{NAME}}`, `{{ADDRESS}}`, `{{PHONE}}`, `{{EMAIL}}` and `{{LINKEDIN}}`. If a job posting contains someone else's details, such as a recruiter's email, those get their own placeholders (`{{EMAIL_2}}`) so they are never confused with yours.
2. **Puts them back when the reply arrives.** The real values go back into the AI's answer on your machine. Your drafts show your real details, and the contact line at the top of a resume always comes from your profile, never from the AI.
3. **Keeps your town and state.** These are sent because the fit rating needs them to judge commute and relocation.
4. **Cuts clearance to level and status.** Your profile's clearance is sent as, for example, "Active TS/SCI". Polygraph type and dates and investigation details (SSBI, T5, reinvestigations, continuous vetting) are removed from your profile, resumes and career inventory before sending. A job posting's own requirement, such as "Full Scope Polygraph required", is sent unchanged so the fit rating can still tell you about it.

Noble Search uses only Anthropic's standard Messages API. Your resume content is never sent through Anthropic's Batch or Files APIs.

Turn the setting off only to troubleshoot a draft that goes wrong, and turn it back on afterwards.

## What Anthropic keeps

Noble Search uses **your** Anthropic API key, so the data you send is covered by **your** Anthropic account's terms. The Noble Search author has no access to it and no control over it. In summary, as of November 2026 (check Anthropic's current terms for the details):

- Anthropic keeps API requests and replies for **up to 30 days** by default, then deletes them.
- API data is **not used to train** Anthropic's models ([Anthropic commercial terms](https://www.anthropic.com/legal/commercial-terms)).
- Anthropic may keep data **longer if a request is flagged** for review under its usage policy.
- **Zero retention** is available only through a Zero Data Retention agreement that you make with Anthropic yourself.

If you want nothing to leave your network at all, use [Ollama mode](README.md#use-a-local-ai-model-instead-of-claude-ollama).

## Logs

- The app log (`data/logs/noble-search.log`) and the Problems log record what happened (for example "fit rating failed: timeout"), never the text of AI requests or replies.
- When an AI reply can't be read, Noble Search saves one copy of that reply in `data/logs/bad-reply-*.txt` so you can see what went wrong. Your personal details in that copy are replaced with placeholders too. You can delete these files at any time.

## Deleting your data

Everything Noble Search holds is in its `data` folder.

- To remove one item, use the app: delete a resume in Profile, a draft in its job's panel, or dismiss a job.
- To remove everything, stop Noble Search and delete the `data` folder. With Docker Compose: `docker compose down`, then delete the `data` folder next to `docker-compose.yml`.
- To clear what Anthropic holds, see your Anthropic account. Data there is deleted after the retention period above.
- Back up the `data` folder before deleting it if you might want it later. Noble Search keeps no other copy.

## Security

Keys and passwords are stored in files only the file owner can read. See [SECURITY.md](SECURITY.md) for how to report a problem and how to keep the app off the public internet.

## Questions

Open a [discussion](https://github.com/kghaynes/noble-search/discussions) on GitHub. Don't post personal data there.
