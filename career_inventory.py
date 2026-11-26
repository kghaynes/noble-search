"""The career inventory's structure, shared by the fit rating and the drafts (spec inventory.json and fit.json
"certifications"; SPEC sections 5, 7 and 16): its section headings, finding a section, and the
"Certifications held" line every prompt about the candidate carries. No imports from the app, so any
module can use it."""
import re

# Inventory section headings (the inventory builder's prompt writes them; the prompts look for them).
INVENTORY_CERTS_HEADING = "Certifications"
INVENTORY_GAPS_HEADING = "Gaps / to fill in"

_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t#]*$")


def _heading_key(text):
    return re.sub(r"\s+", "", text.replace("*", "").replace("_", "")).casefold()


def inventory_section(lines, heading):
    """Spec section 16: (start, end) line indexes of the first Markdown section whose heading text starts with
    `heading` (ignoring case, whitespace, * and _): from its heading line up to the next heading of the same
    or a higher level, or the end. None when there is no such heading."""
    want = _heading_key(heading)
    start = level = None
    for i, line in enumerate(lines):
        mt = _HEADING_RE.match(line)
        if not mt:
            continue
        if start is None:
            if _heading_key(mt.group(2)).startswith(want):
                start, level = i, len(mt.group(1))
        elif len(mt.group(1)) <= level:
            return start, i
    return None if start is None else (start, len(lines))


# "Certifications held" line (spec fit.json "certifications"; SPEC section 5): the certifications the candidate
# lists, in one place near the top, so a small model doesn't miss them deep in a long inventory.
CERTS_LABEL = "Certifications held"
CERTS_MAX_ITEMS = 20
CERTS_ITEM_MAX_CHARS = 120
CERTS_NOTE_MAX_WORDS = 12          # a line with more words than this is a note, not a certification
CERTS_NOTE_PERIOD_MIN_WORDS = 3    # a line ending in "." with at least this many words is a note too
CERTS_NONE = ("none", "none listed", "not listed", "none provided", "n/a")
CERTS_SEPARATOR = "; "
_BULLET_RE = re.compile(r"^(?:[-*+•]|\d+[.)])\s+")


def _cert_item(line):
    """One certification from one line, or '' for a blank, sub-heading, note or 'none' line."""
    t = line.strip()
    if not t or t.startswith("#"):
        return ""
    t = _BULLET_RE.sub("", t).replace("**", "").replace("__", "")
    t = " ".join(t.split())
    words = len(t.split())
    if (not t or words > CERTS_NOTE_MAX_WORDS or (t.endswith(".") and words >= CERTS_NOTE_PERIOD_MIN_WORDS)
            or t.rstrip(".").casefold() in CERTS_NONE):
        return ""
    return t[:CERTS_ITEM_MAX_CHARS].strip()


def certifications_held(inventory, profile):
    """Spec section 5: certifications from the inventory's Certifications section, then the profile's
    military-earned certifications (one per line). Bullets and bold removed, notes skipped, duplicates
    (ignoring case) dropped keeping the first spelling, at most CERTS_MAX_ITEMS."""
    lines = str(inventory or "").split("\n")
    sec = inventory_section(lines, INVENTORY_CERTS_HEADING)
    src = (lines[sec[0] + 1:sec[1]] if sec else []) + str((profile or {}).get("mil_certs") or "").splitlines()
    out, seen = [], set()
    for line in src:
        item = _cert_item(line)
        if item and item.casefold() not in seen:
            seen.add(item.casefold())
            out.append(item)
            if len(out) >= CERTS_MAX_ITEMS:
                break
    return out


def certifications_line(inventory, profile):
    """The "Certifications held: A; B" prompt line as a list of 0 or 1 lines (none when nothing is held).
    Fit, resume and application prompts put it right after the candidate's Clearance line."""
    certs = certifications_held(inventory, profile)
    return [f"{CERTS_LABEL}: " + CERTS_SEPARATOR.join(certs)] if certs else []
