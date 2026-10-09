"""Morning summary: email (any SMTP server, e.g. Gmail with an app password) and optional ntfy push.

Sent after a search run once fit rating has finished, so the email shows the
ratings and gaps. Stdlib only (smtplib).
"""
import html
import json

import applog
import smtplib
import ssl
import threading
import time
import re
import urllib.parse
import urllib.request
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import fit
import sources

_db = None
_state = {"last_sent": None, "last_error": None}
FIT_ORDER = {"High": 0, "Med": 1, "Low": 2, "": 3}


def init(db_fn, db_lock):
    global _db
    _db = (db_fn, db_lock)
    with db_lock, db_fn() as conn:
        have = {r[1] for r in conn.execute("PRAGMA table_info(search_runs)")}
        for col in ("added_keys", "emailed"):
            if col not in have:
                conn.execute(f"ALTER TABLE search_runs ADD COLUMN {col} TEXT DEFAULT ''")


def status():
    return dict(_state)


# --------------------------------------------------------------------------- building the summary
def _run(run_id):
    db_fn, lock = _db
    with lock, db_fn() as conn:
        r = conn.execute("SELECT * FROM search_runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return None
    r = dict(r)
    for k, default in (("detail", []), ("added_keys", [])):
        try:
            r[k] = json.loads(r.get(k) or "null") or default
        except json.JSONDecodeError:
            r[k] = default
    return r


def _jobs(keys):
    if not keys:
        return []
    db_fn, lock = _db
    with lock, db_fn() as conn:
        rows = [dict(x) for x in conn.execute(
            f"SELECT * FROM jobs WHERE job_key IN ({','.join('?' * len(keys))})", list(keys))]
    rows = [r for r in rows if r.get("status") != "dismissed"]
    rows.sort(key=lambda r: r.get("posted_date") or "", reverse=True)   # newest first …
    rows.sort(key=lambda r: FIT_ORDER.get(r.get("fit") or "", 3))      # … within each fit level
    return rows


TOP_PICKS = 5
MORE_MAX = 15
_LEADS = re.compile(r"^\s*(strongest match|strong match|good match|match|fit)\s*[:\-—]\s*", re.I)
_GAP = re.compile(r"\s*\b(gaps?|risk)\s*:\s*", re.I)


def split_reason(reason):
    """('why it fits', 'gap') from a stored fit reason, tidied for reading."""
    r = _LEADS.sub("", (reason or "").strip())
    r = re.sub(r"\s*\(?Meets basic quals\.?\)?\s*$", "", r, flags=re.I).strip()
    parts = _GAP.split(r, maxsplit=1)
    why = parts[0].strip().rstrip(";,. ") if parts else ""
    gap = parts[-1].strip() if len(parts) == 3 else ""
    cap = lambda s: s[:1].upper() + s[1:] if s else s  # noqa: E731
    return (cap(why) + "." if why and why[-1] not in ".!?" else cap(why)), cap(gap)


def site(url):
    host = urllib.parse.urlparse(url or "").netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _closing(j, today=None):
    """Days until the closing date (0 = today), or None if unknown / past / far off."""
    today = today or date.today()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(j.get("closing_date") or ""))
    if not m:
        return None
    try:
        d = (date.fromisoformat(m.group(1)) - today).days
    except ValueError:
        return None
    return d if 0 <= d <= 7 else None


def _closes_text(d, j):
    if d is None:
        return ""
    when = "today" if d == 0 else "tomorrow" if d == 1 else datetime.fromisoformat(j["closing_date"][:10]).strftime("%a %b %-d")
    return f"closes {when}"


def _closing_soon(exclude=()):
    db_fn, lock = _db
    with lock, db_fn() as conn:
        rows = [dict(x) for x in conn.execute(
            "SELECT * FROM jobs WHERE closing_date != '' AND closing_date IS NOT NULL AND COALESCE(status,'') != 'dismissed' "
            "AND (fit IN ('High','Med') OR status IN ('interested','applied','interviewing','offer'))")]
    out = [(d, j) for j in rows for d in [_closing(j)] if d is not None and j["job_key"] not in exclude]
    out.sort(key=lambda x: x[0])
    return out[:8]


def build(run, cfg):
    """Return (subject, text, html, counts) for a run."""
    jobs = _jobs(run.get("added_keys") or [])
    high = [j for j in jobs if j.get("fit") == "High"]
    med = [j for j in jobs if j.get("fit") == "Med"]
    low = [j for j in jobs if j.get("fit") == "Low"]
    unrated = [j for j in jobs if not j.get("fit")]
    detail = run.get("detail") or []
    bad = [d for d in detail if d.get("ok") is False]
    scanned = sum(int(d.get("scanned") or 0) for d in detail)
    nsrc = len([d for d in detail if not d.get("skipped")])
    day = datetime.now().strftime("%a %b %-d")
    if jobs:
        parts = [f"{len(x)} {n}" for x, n in ((high, "High"), (med, "Med"), (low, "Low"), (unrated, "unrated")) if x]
        subject = f"Noble Search {day}: {len(jobs)} new — " + ", ".join(parts)
    else:
        subject = f"Noble Search {day}: no new matches"
    url = (cfg.get("dashboard_url") or "").rstrip("/")
    skip = sources._skip_list(cfg)

    def rank(j):  # within a fit level: real employer/board link first, then ones with a reason, then has pay
        return (sources._skipped_site(j.get("link"), skip), not j.get("fit_reason"), not j.get("salary"))
    ranked = sorted(high, key=rank) + sorted(med, key=rank)
    picks = ranked[:TOP_PICKS]
    more_all = ranked[TOP_PICKS:] + unrated
    more, extra = more_all[:MORE_MAX], len(more_all) - MORE_MAX
    soon = _closing_soon()
    by_source = [f"{d.get('name')} {d.get('added')}" for d in detail if int(d.get("added") or 0) > 0]
    s1 = f"{len(jobs)} new today ({', '.join(parts)})." if jobs else "No new matching jobs today."
    s2 = [f"{scanned:,} listings checked across {nsrc} sources"] if scanned else []
    if run.get("updated"):
        s2.append(f"{run['updated']} existing jobs updated")
    summary = s1 + (" " + "; ".join(s2)[:1].upper() + "; ".join(s2)[1:] + "." if s2 else "")

    def meta(j):
        return " · ".join(x for x in [j.get("location"), j.get("work_mode"), j.get("salary")] if x)

    # ---- plain text (reads like a short briefing)
    t = [summary, ""]
    if by_source:
        t += ["New jobs by source: " + ", ".join(by_source) + ".", ""]
    if picks:
        t.append("Top picks")
        for i, j in enumerate(picks, 1):
            why, gap = split_reason(j.get("fit_reason"))
            closes = _closes_text(_closing(j), j)
            t.append(f"{i}. {j['title']} — {j['company']} — {meta(j)} — {j['fit']}."
                     + (f" {why}" if why else "") + (f" Gap: {gap}" if gap else "") + (f" ({closes})" if closes else ""))
            t.append(f"   {j.get('link') or url}")
        t.append("")
    if more:
        t.append("Also new")
        t += [f"- {j.get('fit') or 'Not rated'}: {j['title']} — {j['company']} — {meta(j)}" for j in more]
        if extra > 0:
            t.append(f"…and {extra} more in your dashboard.")
        t.append("")
    if low:
        t += [f"{len(low)} Low-fit job{'s' if len(low) > 1 else ''} added to your dashboard.", ""]
    if soon:
        t.append("Closing within 7 days: " + "; ".join(f"{j['title']} — {j['company']} ({_closes_text(d, j)})" for d, j in soon) + ".")
        t.append("")
    if bad:
        t.append("Couldn't read: " + "; ".join(f"{d.get('name')} ({'; '.join(d.get('errors') or [])[:120]})" for d in bad) + ".")
        t.append("")
    if url:
        t.append(f"Open your dashboard: {url}")
    text = "\n".join(t)

    # ---- html (table layout and inline styles, so Gmail/Outlook/phones all render it)
    e = html.escape
    F = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    NAVY, GOLD, INK, MUTED, LINE = "#14213d", "#c99a2e", "#1d2433", "#5b6475", "#e6e8ee"
    pill = {"High": ("#0f7b4a", "#e3f5ec"), "Med": ("#8a5a00", "#fbf0d9"), "Low": ("#5b6475", "#eef0f3"), "": ("#5b6475", "#eef0f3")}

    def chip(txt, fg, bg):
        return (f'<span style="display:inline-block;{F};font-size:12px;font-weight:700;color:{fg};background:{bg};'
                f'border-radius:999px;padding:2px 9px;margin:0 6px 4px 0;white-space:nowrap">{e(txt)}</span>')

    def pick(i, j):
        why, gap = split_reason(j.get("fit_reason"))
        fg, bg = pill.get(j.get("fit") or "", pill[""])
        chips = chip(f'{j["fit"]} fit', fg, bg)
        if j.get("salary"):
            chips += chip(j["salary"], INK, "#f1f3f7")
        d = _closing(j)
        if d is not None:
            chips += chip(_closes_text(d, j), "#b42318", "#fde8e7")
        link = j.get("link") or url
        loc = " · ".join(e(x) for x in [j.get("location"), j.get("work_mode")] if x)
        return f"""<tr><td style="padding:16px 0;border-top:1px solid {LINE}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td width="34" valign="top"><div style="{F};width:26px;height:26px;line-height:26px;border-radius:50%;background:{NAVY};color:#fff;font-size:13px;font-weight:700;text-align:center">{i}</div></td>
<td valign="top">
<a href="{e(link)}" style="{F};font-size:16px;font-weight:700;color:{NAVY};text-decoration:none;line-height:1.3">{e(j["title"])}</a>
<div style="{F};font-size:14px;color:{INK};margin:3px 0 8px"><b>{e(j["company"])}</b>{' · ' + loc if loc else ''}</div>
<div>{chips}</div>
{f'<div style="{F};font-size:14px;line-height:1.5;color:{INK};margin-top:6px">{e(why)}</div>' if why else ''}
{f'<div style="{F};font-size:14px;line-height:1.5;color:#8a3b12;margin-top:4px"><b>Gap:</b> {e(gap)}</div>' if gap else ''}
<div style="{F};font-size:13px;margin-top:8px"><a href="{e(link)}" style="color:#1f5eff;text-decoration:none">View posting on {e(site(link) or "the site")} &rarr;</a></div>
</td></tr></table></td></tr>"""

    def section(title, inner, color=INK):
        return (f'<tr><td style="padding:22px 0 6px"><div style="{F};font-size:12px;font-weight:800;letter-spacing:.08em;'
                f'text-transform:uppercase;color:{color}">{title}</div></td></tr>{inner}')

    rows = []
    if picks:
        rows.append(section("Today's top picks", "".join(pick(i, j) for i, j in enumerate(picks, 1))))
    if more:
        li = "".join(
            f'<tr><td style="padding:9px 0;border-top:1px solid {LINE};{F};font-size:14px;line-height:1.4;color:{INK}">'
            f'{chip((j.get("fit") or "Not rated") + (" fit" if j.get("fit") else ""), *pill.get(j.get("fit") or "", pill[""]))}'
            f'<a href="{e(j.get("link") or url)}" style="color:{NAVY};font-weight:600;text-decoration:none">{e(j["title"])}</a>'
            f' <span style="color:{MUTED}">— {e(j["company"])}{" · " + e(meta(j)) if meta(j) else ""}</span></td></tr>' for j in more)
        if extra > 0:
            li += (f'<tr><td style="padding:9px 0;border-top:1px solid {LINE};{F};font-size:14px;color:{MUTED}">'
                   f'…and {extra} more High/Med job{"s" if extra > 1 else ""} in your dashboard.</td></tr>')
        rows.append(section("Also new", li))
    if low:
        rows.append(f'<tr><td style="padding:12px 0 0;{F};font-size:14px;color:{MUTED}">{len(low)} Low-fit job{"s" if len(low) > 1 else ""} '
                    f'added to your dashboard{" (" + ", ".join(e(c) for c in list(dict.fromkeys(j["company"] for j in low))[:4]) + ("…" if len(set(j["company"] for j in low)) > 4 else "") + ")" if low else ""}.</td></tr>')
    if not jobs:
        rows.append(f'<tr><td style="padding:18px 0;{F};font-size:15px;color:{INK}">No new matching jobs today. Your watchlist is unchanged.</td></tr>')
    if soon:
        li = "".join(f'<div style="margin:4px 0"><b>{e(_closes_text(d, j)[:1].upper() + _closes_text(d, j)[1:])}:</b> '
                     f'<a href="{e(j.get("link") or url)}" style="color:{NAVY};text-decoration:none">{e(j["title"])}</a> — {e(j["company"])}</div>' for d, j in soon)
        rows.append(f'<tr><td style="padding:20px 0 0"><div style="background:#fff7e6;border:1px solid #f3dca6;border-radius:10px;padding:12px 14px;{F};font-size:14px;line-height:1.45;color:{INK}">'
                    f'<div style="font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#8a5a00;margin-bottom:4px">Closing within 7 days</div>{li}</div></td></tr>')
    foot = []
    if by_source:
        foot.append("New jobs by source: " + e(", ".join(by_source)) + ".")
    if bad:
        foot.append("Couldn't read: " + e("; ".join(f"{d.get('name')} ({'; '.join(d.get('errors') or [])[:100]})" for d in bad)) + ".")
    if foot:
        rows.append(f'<tr><td style="padding:20px 0 0;{F};font-size:13px;line-height:1.5;color:{MUTED}">{"<br>".join(foot)}</td></tr>')
    if url:
        rows.append(f'<tr><td align="center" style="padding:24px 0 6px"><a href="{e(url)}" style="{F};display:inline-block;background:{NAVY};color:#fff;'
                    f'font-size:15px;font-weight:700;text-decoration:none;border-radius:8px;padding:12px 22px">Open your dashboard</a></td></tr>')

    stats = "".join(
        f'<td align="center" style="padding:0 12px 0 0"><div style="{F};font-size:24px;font-weight:800;color:{c}">{n}</div>'
        f'<div style="{F};font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:#aeb8d0;white-space:nowrap">{lbl}</div></td>'
        for n, lbl, c in ((len(jobs), "New", "#fff"), (len(high), "High fit", "#7ee2b0"), (len(med), "Med fit", "#f6d58b"),
                          (f"{scanned:,}" if scanned else run.get("found", 0), "Checked", "#fff")))
    preheader = e(summary)
    h = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light only"></head>
<body style="margin:0;padding:0;background:#f2f4f8">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f4f8"><tr><td align="center" style="padding:16px 6px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:620px;background:#ffffff;border-radius:14px;overflow:hidden">
<tr><td style="background:{NAVY};padding:20px 18px 18px">
<div style="{F};font-size:12px;font-weight:800;letter-spacing:.18em;color:{GOLD}">&#9733; NOBLE SEARCH</div>
<div style="{F};font-size:22px;font-weight:800;color:#fff;margin:6px 0 2px">{e(datetime.now().strftime("%A, %B %-d"))}</div>
<div style="{F};font-size:14px;color:#c9d2e8;line-height:1.45">{e(summary)}</div>
<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:14px"><tr>{stats}</tr></table>
</td></tr>
<tr><td style="padding:4px 18px 22px;word-break:break-word"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(rows)}</table></td></tr>
</table>
<div style="{F};font-size:11px;line-height:1.5;color:#8a93a6;max-width:560px;margin:14px auto 0">Noble Search &copy; 2026 Kenneth Haynes and CyberCloudAI.tech. Licensed under the PolyForm Noncommercial License 1.0.0. Commercial use or reuse is prohibited without written permission.</div>
</td></tr></table></body></html>"""
    return subject, text, h, {"new": len(jobs), "high": len(high), "med": len(med)}


# --------------------------------------------------------------------------- sending
def send_email(cfg, subject, text, html_body):
    host = (cfg.get("smtp_host") or "").strip()
    user = (cfg.get("smtp_user") or "").strip()
    pw = cfg.get("smtp_password") or ""
    to = [a.strip() for a in str(cfg.get("email_to") or user).replace(";", ",").split(",") if a.strip()]
    if not host or not user or not pw or not to:
        raise ValueError("Email is not set up: fill in the mail server, account, app password and recipient.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.get("email_from_name") or "Noble Search", user))
    msg["To"] = ", ".join(to)
    msg["Message-ID"] = make_msgid(domain=user.split("@")[-1] if "@" in user else None)
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    port = int(cfg.get("smtp_port") or 587)
    tries = [port] + ([465] if port == 587 else [587] if port == 465 else [])
    errors = []
    for p in tries:
        stage = "connecting"
        try:
            ctx = ssl.create_default_context()
            if p == 465:
                s = smtplib.SMTP_SSL(host, p, context=ctx, timeout=30)
            else:
                s = smtplib.SMTP(host, p, timeout=30)
            with s:
                stage = "greeting"
                s.ehlo()
                if p != 465:
                    stage = "starting encryption (STARTTLS)"
                    s.starttls(context=ctx)
                    s.ehlo()
                stage = "logging in"
                s.login(user, pw)
                stage = "sending"
                s.send_message(msg)
            return p
        except smtplib.SMTPAuthenticationError:
            raise ValueError("The mail server rejected the login. For Gmail, use a 16-character app password "
                             "(myaccount.google.com/apppasswords), not your normal password.") from None
        except (smtplib.SMTPException, OSError) as ex:
            errors.append(f"port {p}: failed while {stage}: {type(ex).__name__}: {ex}")
    raise ValueError(f"Could not send email via {host} — " + " | ".join(errors))


def send_push(cfg, subject, counts):
    base, topic = (cfg.get("ntfy_url") or "").rstrip("/"), (cfg.get("ntfy_topic") or "").strip()
    if not base or not topic:
        return
    body = f"{counts['new']} new · {counts['high']} High · {counts['med']} Med" if counts["new"] else "No new matches today"
    hdr = {"Title": subject.encode("ascii", "ignore").decode(), "Tags": "briefcase"}
    if cfg.get("dashboard_url"):
        hdr["Click"] = cfg["dashboard_url"]
    if cfg.get("ntfy_token"):
        hdr["Authorization"] = f"Bearer {cfg['ntfy_token']}"
    req = urllib.request.Request(f"{base}/{topic}", data=body.encode(), headers=hdr, method="POST")
    with urllib.request.urlopen(req, timeout=20):
        pass


def send_for_run(run_id, cfg):
    run = _run(run_id)
    if not run:
        raise ValueError("No such search run")
    subject, text, html_body, counts = build(run, cfg)
    errors = []
    if cfg.get("email_enabled"):
        try:
            send_email(cfg, subject, text, html_body)
        except ValueError as ex:
            errors.append(str(ex))
    try:
        send_push(cfg, subject, counts)
    except Exception as ex:  # noqa: BLE001
        errors.append(f"Push notification failed: {ex}")
    db_fn, lock = _db
    stamp = datetime.now().isoformat(timespec="minutes")
    with lock, db_fn() as conn:
        conn.execute("UPDATE search_runs SET emailed=? WHERE id=?", (("failed: " + " | ".join(errors))[:400] if errors else stamp, run_id))
    _state.update(last_sent=None if errors else stamp, last_error=" | ".join(errors) or None)
    if errors:
        applog.error("email", "Couldn't send: " + " | ".join(errors))
    else:
        applog.info("email", f"Summary sent ({counts.get('new', 0)} new)" if isinstance(counts, dict) else "Summary sent")
    if errors:
        raise ValueError(" | ".join(errors))
    return subject


def send_test(cfg):
    """Send the summary of the most recent run (or an empty one) as a test."""
    db_fn, lock = _db
    with lock, db_fn() as conn:
        r = conn.execute("SELECT id FROM search_runs WHERE status!='running' ORDER BY id DESC LIMIT 1").fetchone()
    if r:
        run = _run(r[0])
    else:
        run = {"found": 0, "added": 0, "updated": 0, "detail": [], "added_keys": []}
    subject, text, html_body, counts = build(run, cfg)
    subject = "[Test] " + subject
    send_email(cfg, subject, text, html_body)
    try:
        send_push(cfg, subject, counts)
    except Exception as ex:  # noqa: BLE001
        return {"ok": True, "detail": f"Email sent to {cfg.get('email_to') or cfg.get('smtp_user')}, but the push failed: {ex}"}
    return {"ok": True, "detail": f"Test email sent to {cfg.get('email_to') or cfg.get('smtp_user')} — check your inbox (and spam)."}


# --------------------------------------------------------------------------- after a search run
def after_search(run_id, trigger, cfg):
    """Rate the new jobs, then send the summary. Runs in its own thread."""
    def go():
        try:
            if cfg.get("fit_provider", "ollama") != "off":
                waited = 0
                while waited < 4 * 3600:
                    if fit.status()["running"]:
                        time.sleep(15)
                        waited += 15
                        continue
                    try:
                        fit.run()
                        break
                    except RuntimeError:  # another rating run just started
                        time.sleep(15)
                        waited += 15
            wants = cfg.get("email_enabled") or (cfg.get("ntfy_url") and cfg.get("ntfy_topic"))
            if wants and (trigger == "scheduled" or cfg.get("email_manual_runs")):
                send_for_run(run_id, cfg)
        except Exception as ex:  # noqa: BLE001
            _state["last_error"] = str(ex)[:400]
            applog.error("email", f"Morning email/alert failed: {ex}")
    threading.Thread(target=go, daemon=True).start()
