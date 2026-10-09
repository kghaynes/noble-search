"""Problems log: one place for errors and notable events, in plain language.

Written to data/logs/noble-search.log (rotates at 1 MB, keeps 3 old files) and to the console, so
`docker compose logs` shows it too. Settings → Problems log shows the latest lines. Never log keys,
passwords or resume text here.
"""
import logging
import os
import re
from logging.handlers import RotatingFileHandler

import profile_store as ps

LOG_DIR = os.path.join(ps.DATA_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "noble-search.log")
_log = logging.getLogger("noble")
_ready = False
SECRET = re.compile(r"(?i)(key|token|password|secret)=([^&\s]+)")


def _setup():
    global _ready
    if _ready:
        return
    _ready = True
    _log.setLevel(logging.INFO)
    _log.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        _log.addHandler(fh)
    except OSError:
        pass   # read-only data folder: console only
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    _log.addHandler(sh)


def _clean(area, msg):
    msg = SECRET.sub(r"\1=•••", " ".join(str(msg).split()))[:1000]
    return f"[{area}] {msg}"


def info(area, msg):
    _setup()
    _log.info(_clean(area, msg))


def warn(area, msg):
    _setup()
    _log.warning(_clean(area, msg))


def error(area, msg):
    _setup()
    _log.error(_clean(area, msg))


def tail(n=200):
    """The last n lines of the log, newest last."""
    try:
        with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-max(1, min(int(n), 2000)):]
    except FileNotFoundError:
        return []


def save_bad_reply(name, text):
    """Keep the latest unreadable model reply for troubleshooting (stays in your data folder)."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, f"bad-reply-{re.sub(r'[^a-z0-9-]', '', name.lower())[:40]}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text or "")
        return os.path.basename(path)
    except OSError:
        return ""
