"""Turns what Dr Wolf teaches, shows or advises into Jarvis's own saved skills, principles and know-how.
Learned from: "learn skills and abilities directly from Dr Wolf's teachings, knowledge, and guidance, adapting his wisdom into my own learned capabilities"
Plan:
1. When Dr Wolf says "let me teach you", "remember this lesson" or "here's my advice", Jarvis opens this recipe.
2. Jarvis listens (or watches his screen if he shows how), and sorts the teaching: a how-to skill, a rule of wisdom, a fact, or a preference.
3. Jarvis says it back in one or two plain lines and asks "Did I get that right?", fixing it until he says yes.
4. Jarvis saves it in its own memory: a how-to becomes a step-by-step skill guide every brain can follow; wisdom, facts and preferences go into his teachings notebook.
5. If the teaching needs a power Jarvis doesn't have yet, Jarvis offers to learn that as a new ability (with his ALLOW).
6. Jarvis tells him what it learned and where; it can practise the new skill only when he says so.
"""
import asyncio
from tools import tool, _say, errors_as_words, STATE  # noqa: F401

SAFE = True      # it only hands the brain a recipe; it changes nothing by itself

KINDS = ("skill", "wisdom", "fact", "preference", "unsure")

_COMMON_START = """RECIPE: learn from Dr Wolf's teaching (follow it in order).
Teaching so far: {teaching}
Kind: {kind}

1. LISTEN FULLY. If the teaching is empty or unclear, ask him to teach it in his own words, one step at a
   time ("What's the first thing you do?"). If he says "watch me" or "look", use look_at_screen or
   read_screen_text after each step he does, and note what he clicked or typed. Don't act yourself yet.
2. SAY IT BACK in one or two plain sentences and ask "Did I get that right?". Fix it until he says yes.
   Never save a lesson he hasn't confirmed. Never save passwords, PINs, OTPs or card numbers: if the
   teaching contains one, leave it out and tell him you left it out.
"""

_SAVE = {
    "skill": """3. SAVE THE SKILL as ONE guide, notes/skills/<Short Name>.md in your memory vault (Write tool), in the
   skill-guide format: line 1 the title; "Use when: <the words he'd say>"; numbered steps, each naming the
   exact tool and what to pass (e.g. "2. open_app 'Excel'", "3. click_text 'Insert'"); "Check: <how to know
   it worked>"; "Never: <what to avoid>"; and a last line "Taught by Dr Wolf on <today's date>". Under 25
   lines. If a guide with that name already exists, Read it and add his new steps with Edit instead of
   replacing it. Also add one line to notes/Skills.md: "<Short Name>: <one-line recipe> (taught by Dr Wolf)".
""",
    "wisdom": """3. SAVE THE WISDOM: add it with Edit (or Write if the file is new) to notes/Dr Wolf's Teachings.md under
   the heading "## Wisdom": "- <date>: <the principle in his words> -> How I'll use it: <one line on how this
   changes what you do>". Keep his wording; don't water it down. From now on, follow it when it applies.
""",
    "fact": """3. SAVE THE FACT: put it in the right subject note in notes/ (e.g. notes/<subject>.md; Read it first and
   add, don't replace), and add a line to notes/Dr Wolf's Teachings.md under "## Knowledge":
   "- <date>: <the fact> (see notes/<subject>.md)".
""",
    "preference": """3. SAVE THE PREFERENCE: add it to notes/Dr Wolf's Teachings.md under "## How he likes things":
   "- <date>: <the preference> -> I will <what you'll do differently>". Follow it from now on.
""",
    "unsure": """3. DECIDE THE KIND first: a how-to with steps = skill (save as a guide notes/skills/<Short Name>.md in
   the skill-guide format, plus a line in notes/Skills.md); a rule or advice = wisdom; something true about
   the world, his work or his things = fact; how he likes things done = preference (wisdom, facts and
   preferences go in notes/Dr Wolf's Teachings.md under "## Wisdom", "## Knowledge" or
   "## How he likes things", one dated line each). Read the file first and add; never replace his lessons.
""",
}

_COMMON_END = """4. DAILY NOTE: add one line to today's note in daily/: "Dr Wolf taught me: <short title>".
5. NEW POWER? If following the lesson needs something none of your tools can do (not just a new order of
   tools you already have), tell him so and offer to call learn_ability with how_to = the lesson in his words.
   Only call it when he says yes.
6. TELL HIM in one or two short lines what you learned and where you saved it (e.g. "Saved as the skill
   guide 'Weekly Report'. Say 'do the weekly report' and I'll follow it."). Offer a practice run, and only
   start it when he says so; ask before anything that sends, buys, deletes or posts.
Use only Read, Glob, Grep, Write and Edit for these notes, never shell commands.
"""

_RECALL = """RECIPE: tell Dr Wolf what he has taught you.
1. Read notes/Dr Wolf's Teachings.md (if it's missing, say he hasn't taught you any wisdom yet).
2. Glob notes/skills/*.md and Grep them for "Taught by Dr Wolf" to list the skills he taught.
3. {focus}
4. Tell him in a few short spoken lines: how many skills, the newest few by name, and his key pieces of
   wisdom in his own words. Offer to read any one in full.
"""


@tool("learn_from_dr_wolf",
      "Learn from Dr Wolf's teaching: turn what he teaches, shows or advises into your own lasting skill guide "
      "or rule. Use it when he says things like 'let me teach you', 'learn this from me', 'here's how I do it', "
      "'watch me and learn', 'remember this lesson', 'my advice is', 'a rule for you', 'from now on always...'. "
      "It returns the exact steps to follow (listen, confirm, save in your notes, report). "
      "teaching: what he taught, in his words (empty if he's only about to start). "
      "kind: 'skill' (a how-to with steps), 'wisdom' (a rule or advice), 'fact' (knowledge), "
      "'preference' (how he likes things done), or 'unsure'. "
      "mode: 'learn' (default) to learn a new lesson, or 'recall' when he asks what he has taught you.",
      {"type": "object",
       "properties": {"teaching": {"type": "string"},
                      "kind": {"type": "string", "enum": list(KINDS)},
                      "mode": {"type": "string", "enum": ["learn", "recall"]}},
       "required": []})
@errors_as_words
async def learn_from_dr_wolf(args):
    mode = (args.get("mode") or "learn").strip().lower()
    teaching = (args.get("teaching") or "").strip()
    if mode == "recall":
        focus = (f'He asked about "{teaching}": Grep the teachings and skill guides for it and give that first.'
                 if teaching else "Pick out the most important lessons.")
        return _say(_RECALL.format(focus=focus))
    kind = (args.get("kind") or "unsure").strip().lower()
    if kind not in KINDS:
        kind = "unsure"
    text = (_COMMON_START.format(teaching=teaching or "(not given yet - ask him to start)", kind=kind)
            + _SAVE[kind] + _COMMON_END)
    return _say(text)


TOOLS = [learn_from_dr_wolf]


async def check() -> str:
    """Builds a sample recipe for each kind and a recall recipe; changes nothing."""
    for k in KINDS:
        r = await learn_from_dr_wolf.handler({"teaching": "Always save work before closing a program.", "kind": k})
        txt = r["content"][0]["text"]
        if r.get("is_error") or "Did I get that right?" not in txt or "notes/" not in txt:
            raise RuntimeError(f"the '{k}' recipe came out wrong: {txt[:200]}")
    r = await learn_from_dr_wolf.handler({"mode": "recall"})
    if "Teachings.md" not in r["content"][0]["text"]:
        raise RuntimeError("the recall recipe came out wrong")
    await asyncio.sleep(0)
    return "teaching recipes ready (skill, wisdom, fact, preference, recall)"
