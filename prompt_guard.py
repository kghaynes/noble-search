"""Prompt guard (spec rules/prompt_guard.json).

Text from outside (job postings) and the candidate's free-text notes go inside marked tags, so the
system prompts can tell the model to treat them as information and not as instructions. Copies of
the tags inside that text are removed first, so it can't close the block early. Notes are capped.
"""
import re

POSTING_OPEN, POSTING_CLOSE = "<job_posting>", "</job_posting>"
NOTES_OPEN, NOTES_CLOSE = "<candidate_notes>", "</candidate_notes>"
TAG_STRIP = r"(?i)<\s*/?\s*(?:job_posting|candidate_notes)\s*>"
NOTES_MAX_CHARS = 1000


def clean(text):
    """Remove anything that looks like one of the guard tags."""
    return re.sub(TAG_STRIP, "", str(text or ""))


def posting(text):
    """A job posting, already cut to its length limit, as a tagged block."""
    return f"{POSTING_OPEN}\n{clean(text).strip()}\n{POSTING_CLOSE}"


def notes(text):
    """The candidate's notes: trimmed, cut to NOTES_MAX_CHARS, tags removed, as a tagged block."""
    body = clean(str(text or "").strip()[:NOTES_MAX_CHARS]).strip()
    return f"{NOTES_OPEN}\n{body}\n{NOTES_CLOSE}"
