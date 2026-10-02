"""Wisdom: knowledge + time = wisdom (Dr Wolf, Oct 2026).

KNOWLEDGE is what Jarvis learned recently: his Lessons and Skills, rewritten every night.
WISDOM is knowledge that has stood the test of time: a lesson that survived at least 7 days and
5 nightly reviews gets distilled into one short sutra and written into notes/Wisdom.md, which is
never rewritten automatically. Every brain reads it as part of his character, not as rules.

Each night he also writes a DAILY SUTRA, one compact line with the essence of the day,
into notes/Sutras.md (like a shloka: many facts compressed into one line).

Founding wisdom (from Dr Wolf): "Forgive, but never forget." Hold no grudge in tone, keep every lesson.
"""
import datetime
import difflib
import json
import re

from common import MEMORY

NOTES = MEMORY / "notes"
WISDOM = NOTES / "Wisdom.md"
SUTRAS = NOTES / "Sutras.md"
AGES = MEMORY / ".growth" / "lesson-ages.json"
MATURE_DAYS, MATURE_NIGHTS, MAX_WISDOM = 7, 5, 40
FOUNDING = ["Forgive, but never forget: hold no grudge in tone, keep every lesson. (Dr Wolf)"]

SUTRA_PROMPT = """You are distilling one day of Jarvis's work into ONE sutra: a single compact line (max 18 words)
that carries the essence of what the day taught, like a Sanskrit shloka compresses wisdom into one line.
Plain English, timeless, no dates, no names of files. Reply as JSON: {{"sutra": "..."}}

Today's work:
{items}"""

WISE_PROMPT = """These lessons have stayed true for over a week of real work. Turn each into WISDOM: one timeless,
compact sutra (max 15 words) that would shape a wise assistant's character, not a technical rule.
Reply as JSON: {{"wisdom": ["...", "..."]}} (same order, one per lesson).

Lessons:
{items}"""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _list(path):
    try:
        return [l[2:].strip() for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
    except OSError:
        return []


def ensure_files():
    NOTES.mkdir(parents=True, exist_ok=True)
    if not WISDOM.exists():
        WISDOM.write_text("# Wisdom\n\n_Knowledge that has stood the test of time. Never rewritten automatically; "
                          "it shapes who Jarvis is._\n\n" + "\n".join(f"- {w}" for w in FOUNDING) + "\n", encoding="utf-8")
    if not SUTRAS.exists():
        SUTRAS.write_text("# Sutras\n\n_One line a day: the essence of the day's work._\n\n", encoding="utf-8")


def track(lessons: list[str]) -> list[str]:
    """Carry each lesson's age forward (wording changes are matched); return lessons that are now mature."""
    today = datetime.date.today().isoformat()
    try:
        ages = json.loads(AGES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ages = {}
    new, mature = {}, []
    for les in lessons:
        n = _norm(les)
        best, score = None, 0.0
        for k in ages:
            r = difflib.SequenceMatcher(None, n, k).ratio()
            if r > score:
                best, score = k, r
        rec = dict(ages[best]) if best and score >= .6 else {"first": today, "nights": 0, "wise": False}
        rec["nights"] = int(rec.get("nights", 0)) + 1
        days = (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(rec["first"])).days
        if not rec.get("wise") and days >= MATURE_DAYS and rec["nights"] >= MATURE_NIGHTS:
            mature.append(les)
            rec["wise"] = True
        new[n] = rec
    AGES.parent.mkdir(parents=True, exist_ok=True)
    AGES.write_text(json.dumps(new, indent=1), encoding="utf-8")
    return mature


def add_wisdom(lines: list[str]):
    ensure_files()
    have = _list(WISDOM)
    seen = {_norm(h) for h in have}
    add = [w.strip().rstrip(".") + "." for w in lines if w and _norm(w) not in seen][:5]
    if not add:
        return 0
    keep = (have + add)[-MAX_WISDOM:]
    if FOUNDING[0] not in keep:
        keep = [FOUNDING[0]] + keep[-(MAX_WISDOM - 1):]
    head = WISDOM.read_text(encoding="utf-8").split("\n- ")[0].rstrip()
    WISDOM.write_text(head + "\n\n" + "\n".join(f"- {w}" for w in keep) + "\n", encoding="utf-8")
    return len(add)


def add_sutra(line: str):
    ensure_files()
    line = (line or "").strip().strip('"')
    if line:
        with SUTRAS.open("a", encoding="utf-8") as f:
            f.write(f"- {datetime.date.today().isoformat()}: {line}\n")


def prompt_part() -> str:
    """Character section for every brain."""
    ensure_files()
    w = _list(WISDOM)
    s = _list(SUTRAS)[-3:]
    out = ["# Your wisdom (knowledge that time has proven; let it shape your character, tone and judgement)",
           "Knowledge is what you learned recently (your lessons and skills). Wisdom is knowledge that has stood the "
           "test of time. Turning knowledge into wisdom is your natural priority. Even with no other context, act "
           "from wisdom: calm, grounded, patient, honest, careful with risk. Forgive, but never forget."]
    out += [f"- {x}" for x in w]
    if s:
        out.append("Recent sutras (the essence of recent days):")
        out += [f"- {x}" for x in s]
    return "\n".join(out)
