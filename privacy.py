"""Keep personal details out of what is sent to the AI model. Stdlib only.

Before a prompt goes out, your name, street address, phone, email and LinkedIn link are swapped for
placeholders like {{NAME}} and {{EMAIL}}. When the reply comes back, the real values are put back in
on this computer. The model never needs them: drafts get their contact line from your profile, here.

Clearance details are cut down to level and status (e.g. "Active TS/SCI"). Polygraph and investigation
details are dropped.

Nothing here is logged. See README → Privacy and security.
"""
import json
import re

TOKENS = ("NAME", "ADDRESS", "PHONE", "EMAIL", "LINKEDIN")

# what to tell the model about the placeholders (added to the system prompt when anything was swapped)
NOTICE = ("\n\nPRIVACY PLACEHOLDERS: some personal details in this request were replaced with placeholders such as "
          "{{NAME}}, {{EMAIL}}, {{PHONE}}, {{ADDRESS}} and {{LINKEDIN}}. If you need one in your answer, copy the "
          "placeholder exactly as written. Never guess or invent the real value.")

EMAIL_RE = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
LINKEDIN_RE = re.compile(r"(?i)\b(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|pub)/[^\s,;|)]+")
# US-style phone numbers: (321) 555-0123, 321-555-0123, 321.555.0123, +1 321 555 0123
PHONE_RE = re.compile(r"(?<![\w-])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\w-])")
# street lines: "2895 Main St", "100 N. Harbor City Blvd, Apt 4"
STREET_RE = re.compile(
    r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][A-Za-z0-9'.-]*\s+){1,4}"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Blvd|Boulevard|Ct|Court|Cir|Circle|Way|Pl|Place|"
    r"Ter|Terrace|Pkwy|Parkway|Hwy|Highway|Trl|Trail)\b\.?(?:,?\s+(?:Apt|Unit|Suite|Ste|#)\s*[\w-]+)?")

# --- clearance ---------------------------------------------------------------------------------------
_LEVELS = (  # (pattern, label), most specific first
    (r"\bts\s*/\s*sci\b|\btop\s+secret\s*/\s*sci\b|\btop\s+secret\s*/\s*sensitive\s+compartmented", "TS/SCI"),
    (r"\btop\s+secret\b|\bts\b", "Top Secret"),
    (r"\bq\s+clearance\b|\bdoe\s+q\b", "DOE Q"),
    (r"\bl\s+clearance\b|\bdoe\s+l\b", "DOE L"),
    (r"\bsecret\b", "Secret"),
    (r"\bconfidential\b", "Confidential"),
    (r"\bpublic\s+trust\b", "Public Trust"),
)
_STATUS = (
    (r"\b(?:active|current)\b", "Active"),
    (r"\b(?:inactive|lapsed|expired)\b", "Inactive"),
    (r"\beligib", "Eligible"),
    (r"\binterim\b", "Interim"),
)
# polygraph and investigation details to drop from free text (resumes, inventory)
_POLY_RE = re.compile(
    r"(?i)\s*(?:,|;|\bwith\b|\band\b|\bw/)?\s*(?:an?\s+)?"
    r"(?:(?:full[\s-]*scope|fs|ci|counter[\s-]*intelligence|lifestyle)[\s-]*(?:poly(?:graph)?)|polygraph)\b"
    r"(?:\s*\([^)]*\))?(?:\s*(?:completed|passed|dated|in|on)?\s*(?:[A-Za-z]{3,9}\.?\s+)?(?:19|20)\d{2})?")
_INVESTIGATION_RE = re.compile(
    r"(?i)\s*[,;(]?\s*(?:ssbi|t5|tier\s*5|t3|tier\s*3|sbpr|ppr|periodic\s+reinvestigation|"
    r"continuous\s+(?:evaluation|vetting))\b[^.;\n)|]*\)?")


def generalize_clearance(text):
    """'TOP SECRET with CI polygraph, SSBI 2019' -> 'Active TS/SCI'-style level + status, or '' if none."""
    t = str(text or "").strip()
    if not t:
        return ""
    low = t.lower()
    level = next((lbl for pat, lbl in _LEVELS if re.search(pat, low)), "")
    if not level:
        return "Clearance: see resume" if re.search(r"clear", low) else ""
    status = next((lbl for pat, lbl in _STATUS if re.search(pat, low)), "")
    return f"{status} {level}".strip()


def scrub_clearance_details(text):
    """Remove polygraph and investigation details from free text, keeping the clearance level itself."""
    t = _POLY_RE.sub("", str(text or ""))
    return _INVESTIGATION_RE.sub("", t)


# --- placeholders ------------------------------------------------------------------------------------
def _name_patterns(full_name):
    """Full name, plus 'First Last' if a middle name/initial is present. Case-insensitive, whole words."""
    parts = [p for p in re.split(r"\s+", str(full_name or "").strip()) if p]
    if len(parts) < 2:
        return []
    names = {" ".join(parts)}
    core = [p for p in parts if not re.fullmatch(r"(?i)(jr|sr|ii|iii|iv)\.?,?", p)]
    if len(core) >= 2:
        names.add(f"{core[0]} {core[-1]}")
    names = sorted(names, key=len, reverse=True)
    rxs = [re.compile(r"(?i)(?<![\w])" + r"\s+".join(re.escape(w) for w in n.split()) + r"(?![\w])") for n in names]
    if len(core) >= 2 and len(core[-1]) >= 3:
        # nicknames: any capitalized first name (+ optional initial) before the surname, e.g. "Ken Haynes"
        rxs.append(re.compile(r"(?<![\w])[A-Z][a-z]+\.?(?:\s+[A-Z]\.)?\s+" + re.escape(core[-1]) + r"(?![\w])"))
    return rxs


def build_rules(profile):
    """List of (token, compiled regex). Profile values first (exact), then generic patterns."""
    p = profile or {}
    rules = []
    for rx in _name_patterns(p.get("full_name")):
        rules.append(("NAME!", rx))   # '!' = always the user's own value
    for key, tok in (("email", "EMAIL"), ("linkedin", "LINKEDIN"), ("phone", "PHONE")):
        v = str(p.get(key) or "").strip()
        if len(v) >= 5:
            if tok == "PHONE":   # match the same digits in any format
                d = re.sub(r"\D", "", v)[-10:]
                if len(d) == 10:
                    rules.append((tok, re.compile(
                        r"(?<!\d)(?:\+?1[\s.-]?)?\(?" + d[:3] + r"\)?[\s.-]?" + d[3:6] + r"[\s.-]?" + d[6:] + r"(?!\d)")))
            else:
                bare = re.sub(r"(?i)^https?://(www\.)?", "", v).rstrip("/")
                rules.append((tok, re.compile(r"(?i)(?:https?://)?(?:www\.)?" + re.escape(bare) + r"/?")))
    rules += [("LINKEDIN", LINKEDIN_RE), ("EMAIL", EMAIL_RE), ("PHONE", PHONE_RE), ("ADDRESS", STREET_RE)]
    return rules


def _norm(v):
    """Compare values loosely: case, scheme, www, punctuation and a leading US country code don't matter."""
    v = re.sub(r"(?i)^\s*https?://", "", str(v or "").strip())
    v = re.sub(r"(?i)^www\.", "", v).rstrip("/").lower()
    v = re.sub(r"\W", "", v)
    return v[1:] if len(v) == 11 and v.isdigit() and v.startswith("1") else v


class Redactor:
    """Swap personal details for placeholders, remembering the originals so a reply can be restored."""

    def __init__(self, profile):
        self.rules = build_rules(profile)
        self.values = {}      # placeholder -> original text (first seen)
        p = profile or {}
        # prefer the profile's own spelling when putting values back
        for key, tok in (("full_name", "NAME"), ("email", "EMAIL"), ("phone", "PHONE"), ("linkedin", "LINKEDIN")):
            if str(p.get(key) or "").strip():
                self.values["{{" + tok + "}}"] = str(p[key]).strip()

    def redact(self, text):
        t = str(text or "")
        for tok, rx in self.rules:
            def sub(m, tok=tok):
                if tok.endswith("!"):
                    return "{{" + tok[:-1] + "}}"
                return self._token_for(tok, m.group(0).strip())
            t = rx.sub(sub, t)
        return t

    def _token_for(self, tok, found):
        """{{EMAIL}} for the user's own value; {{EMAIL_2}}, {{EMAIL_3}}... for any other one (e.g. a recruiter's)."""
        key = _norm(found)
        for ph, val in self.values.items():
            if ph.startswith("{{" + tok) and _norm(val) == key:
                return ph
        ph = "{{" + tok + "}}"
        if ph in self.values:
            n = 2
            while "{{%s_%d}}" % (tok, n) in self.values:
                n += 1
            ph = "{{%s_%d}}" % (tok, n)
        self.values[ph] = found
        return ph

    @property
    def used(self):
        return bool(self.values)

    def restore(self, text, json_safe=False):
        """Put real values back. json_safe escapes them for use inside JSON strings."""
        t = str(text or "")
        for ph, val in sorted(self.values.items(), key=lambda kv: -len(kv[0])):
            if json_safe:
                val = json.dumps(val)[1:-1]
            t = t.replace(ph, val)
        return t


def prepare(profile, system, user):
    """Redact a prompt and cut the profile's clearance down to level + status. Returns (system, user, redactor).
    Polygraph/investigation details in resumes and the inventory are removed where they are loaded
    (scrub_candidate), so a job posting's own clearance requirements reach the model unchanged."""
    r = Redactor(profile)
    s, u = r.redact(system), r.redact(user)
    raw = str((profile or {}).get("clearance") or "").strip()
    if raw:
        gen = generalize_clearance(raw) or "not specified"
        s, u = s.replace(raw, gen), u.replace(raw, gen)
    if "{{" in s + u:
        s += NOTICE
    return s, u, r


def scrub_candidate(inventory, resume_texts):
    """Drop polygraph and investigation details from the candidate's own material."""
    return scrub_clearance_details(inventory), [(n, scrub_clearance_details(t)) for n, t in resume_texts]


def clean_for_log(text, profile=None):
    """Redact a model reply before it is written to the troubleshooting folder."""
    return Redactor(profile).redact(text)
