"""Samasa: compound codewords for Jarvis's repeated multi-step jobs.

Like a Sanskrit samasa (compound word), one codeword stands for a whole sequence of tool calls,
e.g. OPN-VRF = open_app, then look_at_screen to verify. The brain calls the `samasa` tool once
instead of several tools one by one: fewer round trips, fewer tokens, and the small local brain
gets proven recipes it can't fumble.

- The codebook lives in memory/samasa.json (seeded below the first time).
- Every brain sees a short legend of the codewords in its instructions (growth.prompt_addon).
- Each night, mine() looks through logs/turns.jsonl for tool sequences that keep repeating and
  writes suggestions to memory/samasa-proposals.md. New codewords are added only by Opus or
  Dr Wolf editing samasa.json, never automatically.
- Only safe eyes/hands/web/phone-reading tools can appear in a codeword: no calls, builds,
  publishing or brain switching (those keep their own ALLOW cards).
"""
import collections
import json
import re
import time

from common import LOGS, MEMORY

BOOK = MEMORY / "samasa.json"
PROPOSALS = MEMORY / "samasa-proposals.md"
TURNS = LOGS / "turns.jsonl"

SAFE = {"look_at_screen", "read_screen_text", "see_buttons", "look_closer", "read_whole_page",
        "click_text", "click_button", "click_at", "type_text", "press_keys", "scroll",
        "open_app", "open_url", "list_windows", "focus_window",
        "phone_read_screen", "phone_open_app", "phone_tap_text", "phone_type", "phone_scroll",
        "phone_button", "_wait"}

SEED = {
    "OPN-VRF": {"what": "open a PC app, then look to check it opened", "needs": ["name"],
                "steps": [{"tool": "open_app", "args": {"name": "{name}"}},
                          {"tool": "_wait", "args": {"seconds": 2.5}},
                          {"tool": "look_at_screen", "args": {}}]},
    "URL-RD": {"what": "open a web page, then read all of it", "needs": ["url"],
               "steps": [{"tool": "open_url", "args": {"url": "{url}"}},
                         {"tool": "read_whole_page", "args": {"wait": True}}]},
    "CLK-VRF": {"what": "click words on screen, then read the screen to confirm", "needs": ["target"],
                "steps": [{"tool": "click_text", "args": {"text": "{target}"}},
                          {"tool": "read_screen_text", "args": {}}]},
    "TYP-ENT": {"what": "type into the box labelled 'box', press Enter, read the result", "needs": ["box", "text"],
                "steps": [{"tool": "type_text", "args": {"text": "{text}", "into": "{box}", "enter": True}},
                          {"tool": "read_screen_text", "args": {}}]},
    "PH-OPN": {"what": "open a PHONE app, then read the phone screen", "needs": ["name"],
               "steps": [{"tool": "phone_open_app", "args": {"name": "{name}"}},
                         {"tool": "_wait", "args": {"seconds": 2}},
                         {"tool": "phone_read_screen", "args": {}}]},
    "PH-TAP": {"what": "tap words on the PHONE, then read the phone screen", "needs": ["target"],
               "steps": [{"tool": "phone_tap_text", "args": {"text": "{target}"}},
                         {"tool": "phone_read_screen", "args": {}}]},
    "PH-TYP": {"what": "tap a PHONE text box, type into it (does NOT send), read the screen", "needs": ["box", "text"],
               "steps": [{"tool": "phone_tap_text", "args": {"text": "{box}"}},
                         {"tool": "phone_type", "args": {"text": "{text}"}},
                         {"tool": "phone_read_screen", "args": {}}]},
}


def load() -> dict:
    try:
        book = json.loads(BOOK.read_text(encoding="utf-8"))
        if isinstance(book, dict) and book:
            return book
    except (OSError, ValueError):
        pass
    BOOK.parent.mkdir(parents=True, exist_ok=True)
    BOOK.write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    return dict(SEED)


def legend() -> str:
    """One short line per codeword, for the brains' instructions."""
    lines = []
    for code, c in load().items():
        if all(s.get("tool") in SAFE for s in c.get("steps", [])):
            needs = ", ".join(c.get("needs", []))
            lines.append(f"- {code}({needs}): {c.get('what', '')}")
    return "\n".join(lines)


def plan(code: str, args: dict) -> tuple[list[dict] | None, str]:
    """The concrete tool calls for a codeword, or (None, why not)."""
    book = load()
    c = book.get((code or "").strip().upper())
    if not c:
        return None, f"There's no Samasa codeword '{code}'. Known: {', '.join(book)}."
    missing = [k for k in c.get("needs", []) if not str(args.get(k, "")).strip()]
    if missing:
        return None, f"{code} needs: {', '.join(missing)}."
    out = []
    for s in c.get("steps", []):
        if s.get("tool") not in SAFE:
            return None, f"{code} uses {s.get('tool')}, which isn't allowed in a codeword."
        a = {}
        for k, v in (s.get("args") or {}).items():
            a[k] = re.sub(r"\{(\w+)\}", lambda m: str(args.get(m.group(1), "")), v) if isinstance(v, str) else v
        out.append({"tool": s["tool"], "args": a})
    return out, ""


def count_use(code: str):
    book = load()
    c = book.get(code.upper())
    if c is not None:
        c["uses"] = int(c.get("uses", 0)) + 1
        try:
            BOOK.write_text(json.dumps(book, indent=1), encoding="utf-8")
        except OSError:
            pass


def mine(min_count: int = 5) -> int:
    """Find tool sequences that keep repeating; write them as suggestions. Returns how many."""
    seqs = collections.Counter()
    try:
        lines = TURNS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in lines[-2000:]:
        try:
            t = json.loads(line)
        except ValueError:
            continue
        names = [(x if isinstance(x, str) else (x.get("name") or x.get("tool") or "")).split("__")[-1]
                 for x in t.get("tools", [])]
        for k in (2, 3):
            for i in range(len(names) - k + 1):
                g = tuple(names[i:i + k])
                if len(set(g)) > 1 and all(n in SAFE for n in g):
                    seqs[g] += 1
    known = {tuple(s["tool"] for s in c.get("steps", [])) for c in load().values()}
    rows = [(n, g) for g, n in seqs.most_common(30) if n >= min_count and g not in known][:12]
    PROPOSALS.write_text(
        "# Samasa proposals\n\n_Tool sequences Jarvis keeps repeating (from logs/turns.jsonl), "
        f"mined {time.strftime('%Y-%m-%d %H:%M')}. Opus or Dr Wolf may turn one into a codeword "
        "in memory/samasa.json._\n\n"
        + "\n".join(f"- {n} times: {' > '.join(g)}" for n, g in rows) + "\n", encoding="utf-8")
    return len(rows)
