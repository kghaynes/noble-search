"""Morning summary: email (any SMTP server, e.g. Gmail with an app password) and optional ntfy push.

Sent after a search run once fit rating has finished, so the email shows the
ratings and gaps. Stdlib only (smtplib).
"""
import html
import json
import smtplib
import ssl
import threading
import time
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import fit

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


def build(run, cfg):
    """Return (subject, text, html) for a run."""
    jobs = _jobs(run.get("added_keys") or [])
    high = [j for j in jobs if j.get("fit") == "High"]
    med = [j for j in jobs if j.get("fit") == "Med"]
    low = [j for j in jobs if j.get("fit") == "Low"]
    unrated = [j for j in jobs if not j.get("fit")]
    bad = [d for d in run.get("detail") or [] if d.get("ok") is False]
    day = datetime.now().strftime("%a %b %-d")
    if jobs:
        parts = [f"{len(x)} {n}" for x, n in ((high, "High"), (med, "Med"), (low, "Low"), (unrated, "unrated")) if x]
        subject = f"Noble Search {day}: {len(jobs)} new — " + ", ".join(parts)
    else:
        subject = f"Noble Search {day}: no new matches"
    url = (cfg.get("dashboard_url") or "").rstrip("/")

    # ---- plain text
    t = [subject, ""]
    for label, group in (("HIGH FIT", high), ("MED FIT", med), ("NOT RATED YET", unrated)):
        if group:
            t.append(f"== {label} ==")
            for j in group:
                t += [f"- {j['title']} — {j['company']}", f"  {j.get('location') or ''} · {j.get('work_mode') or ''}"
                      + (f" · {j['salary']}" if j.get("salary") else "") + (f" · posted {j['posted_date']}" if j.get("posted_date") else ""),
                      *( [f"  {j['fit_reason']}"] if j.get("fit_reason") else [] ), f"  {j.get('link') or ''}", ""]
    if low:
        t += [f"== LOW FIT ({len(low)}) ==", *[f"- {j['title']} — {j['company']}" for j in low], ""]
    if bad:
        t += ["Sources with problems:", *[f"- {d.get('name')}: {'; '.join(d.get('errors') or [])[:200]}" for d in bad], ""]
    t.append(f"{run.get('found', 0)} matching jobs checked, {run.get('added', 0)} new, {run.get('updated', 0)} updated.")
    if url:
        t.append(f"Dashboard: {url}")
    text = "\n".join(t)

    # ---- html
    e = html.escape
    color = {"High": ("#0f7b4a", "#e3f5ec"), "Med": ("#8a5a00", "#fbf0d9"), "Low": ("#6b7280", "#eef0f3"), "": ("#6b7280", "#ffffff")}

    def card(j):
        fg, bg = color.get(j.get("fit") or "", color[""])
        pill = (f'<span style="font:600 12px Arial;color:{fg};background:{bg};border-radius:10px;padding:2px 8px">'
                f'{e(j["fit"] + " fit" if j.get("fit") else "Not rated")}</span>')
        meta = " · ".join(e(x) for x in [j.get("location"), j.get("work_mode"), j.get("salary"),
                                          f"posted {j['posted_date']}" if j.get("posted_date") else "",
                                          f"clearance: {j['clearance']}" if j.get("clearance") else ""] if x)
        return (f'<div style="border:1px solid #e3e6ea;border-radius:10px;padding:12px 14px;margin:0 0 10px">'
                f'<div>{pill} <a href="{e(j.get("link") or url)}" style="font:600 15px Arial;color:#1E3764;text-decoration:none">{e(j["title"])}</a></div>'
                f'<div style="font:600 13px Arial;color:#222;margin-top:4px">{e(j["company"])}</div>'
                f'<div style="font:12px Arial;color:#555;margin-top:2px">{meta}</div>'
                + (f'<div style="font:13px/1.45 Arial;color:#222;margin-top:6px">{e(j["fit_reason"])}</div>' if j.get("fit_reason") else "")
                + "</div>")

    h = [f'<div style="max-width:680px;margin:0 auto;font:14px Arial;color:#222">',
         f'<h2 style="font:700 18px Arial;color:#1E3764;margin:0 0 4px">{e(subject)}</h2>',
         f'<div style="font:12px Arial;color:#666;margin-bottom:14px">{run.get("found", 0)} matching jobs checked · '
         f'{run.get("added", 0)} new · {run.get("updated", 0)} updated'
         + (f' · <a href="{e(url)}" style="color:#1E3764">Open dashboard</a>' if url else "") + "</div>"]
    for label, group in (("High fit", high), ("Med fit", med), ("Not rated yet", unrated)):
        if group:
            h.append(f'<h3 style="font:700 14px Arial;margin:16px 0 8px">{label} ({len(group)})</h3>')
            h += [card(j) for j in group]
    if low:
        h.append(f'<h3 style="font:700 14px Arial;margin:16px 0 6px">Low fit ({len(low)})</h3><ul style="margin:0;padding-left:18px;font:13px Arial;color:#555">'
                 + "".join(f'<li><a href="{e(j.get("link") or url)}" style="color:#555">{e(j["title"])}</a> — {e(j["company"])}</li>' for j in low)
                 + "</ul>")
    if not jobs:
        h.append('<p style="font:14px Arial">No new matching jobs today. Your watchlist is unchanged.</p>')
    if bad:
        h.append('<h3 style="font:700 14px Arial;margin:16px 0 6px;color:#b42318">Sources with problems</h3><ul style="margin:0;padding-left:18px;font:12px Arial;color:#555">'
                 + "".join(f'<li>{e(d.get("name") or "")}: {e("; ".join(d.get("errors") or [])[:200])}</li>' for d in bad) + "</ul>")
    h.append('<p style="font:11px Arial;color:#888;margin-top:18px">Noble Search &copy; 2026 Kenneth Haynes and CyberCloudAI.tech. '
             'Licensed under the PolyForm Noncommercial License 1.0.0. Commercial use or reuse is prohibited without written permission.</p>')
    h.append("</div>")
    return subject, text, "".join(h), {"new": len(jobs), "high": len(high), "med": len(med)}


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
            print(f"notify: {ex}", flush=True)
    threading.Thread(target=go, daemon=True).start()
