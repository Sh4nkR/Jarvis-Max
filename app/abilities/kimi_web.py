"""Ask Kimi (and Kimi's Agent Swarm) on kimi.com in Dr Wolf's own signed-in browser: uses his Kimi plan, no API key.
Built by Claude, 8 Oct 2026.
Plan:
1. Open kimi.com (or kimi.com/agent-swarm for a swarm job) in his normal browser, where he is signed in.
2. Paste the question into Kimi's message box and send it.
3. Normal question: wait for the answer, read all of it, save it as a note, return it.
4. Swarm job: it can run for many minutes, so it returns at once; kimi_result reads and saves it later.
"""
import asyncio
import re
import time

import tools
from common import MEMORY
from tools import tool, _say, errors_as_words, STATE

SAFE = False                     # it types into a website and uses his Kimi quota: ALLOW on first use

HOME = "https://www.kimi.com/"
SWARM = "https://www.kimi.com/agent-swarm"
BOX_HINTS = ["Ask Anything", "Ask anything", "Ask Kimi", "Ask", "Message"]
NOTES = MEMORY / "notes" / "kimi"
LAST = {"q": "", "swarm": False, "at": 0.0}


def _text(res: dict) -> str:
    return "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")


def _save(question: str, answer: str, swarm: bool) -> str:
    NOTES.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower())[:50].strip("-") or "kimi"
    p = NOTES / f"{time.strftime('%Y-%m-%d-%H%M')}-{slug}.md"
    p.write_text(f"# Kimi {'Agent Swarm' if swarm else ''}: {question}\n\n{answer.strip()}\n", encoding="utf-8")
    return f"notes/kimi/{p.name}"


async def _send(question: str, swarm: bool) -> str:
    """Open Kimi and send the question. '' if sent, otherwise what went wrong."""
    if not STATE.hands:
        return "My hands are switched off (the HANDS button), so I can't use Kimi's website."
    await tools.open_url.handler({"url": SWARM if swarm else HOME})
    await asyncio.sleep(7)                                   # the page and his sign-in load
    last = ""
    for hint in BOX_HINTS + [None]:
        args = {"text": question, "enter": True}
        if hint:
            args["into"] = hint
        res = await tools.type_text.handler(args)
        last = _text(res)
        if not res.get("is_error") and not re.search(r"didn'?t|not (appear|find|found)|couldn'?t|no box", last, re.I):
            return ""
    return f"I couldn't get the question into Kimi's message box ({last[:160]}). Is he signed in to kimi.com?"


@tool("ask_kimi",
      "Ask Kimi (Moonshot AI's K3, through Dr Wolf's own Kimi plan on kimi.com, no API key) and get its full "
      "answer back. Use it when he says 'ask Kimi ...', wants a second opinion from Kimi, or wants Kimi's "
      "Agent Swarm for a big job ('use the swarm', 'swarm it', 'Kimi swarm'). question: exactly what to ask. "
      "swarm: true for Agent Swarm (many Kimi agents working in parallel on a big research, data or document "
      "job; it takes minutes, so it only starts the job: call kimi_result later to collect it).",
      {"type": "object", "properties": {"question": {"type": "string"}, "swarm": {"type": "boolean"}},
       "required": ["question"]})
@errors_as_words
async def ask_kimi(args):
    q = (args.get("question") or "").strip()
    swarm = bool(args.get("swarm"))
    if not q:
        return _say("Ask him what to ask Kimi.", err=True)
    problem = await _send(q, swarm)
    if problem:
        return _say(problem, err=True)
    LAST.update(q=q, swarm=swarm, at=time.time())
    if swarm:
        return _say("Kimi's Agent Swarm has the job and is working in his browser (it can take several minutes; "
                    "his plan must include Agent Swarm, or Kimi will say so on the page). Tell him it has started; "
                    "when he asks for it, or in a few minutes, call kimi_result to read and save the result.")
    res = await tools.read_whole_page.handler({"start_after": q, "wait": True})
    answer = _text(res)
    if res.get("is_error") or len(answer) < 20:
        return _say(f"Kimi got the question, but I couldn't read its answer yet ({answer[:160]}). "
                    "Call kimi_result in a moment.", err=True)
    where = _save(q, answer, False)
    return _say(f"Kimi's answer (saved as {where}):\n{answer[:6000]}")


@tool("kimi_result",
      "Read and save the result Kimi (or Kimi's Agent Swarm) has written on kimi.com for the last question sent "
      "with ask_kimi. Use it when he asks for the swarm's result, or after a swarm job has had a few minutes.",
      {"type": "object", "properties": {}})
@errors_as_words
async def kimi_result(args):
    if not LAST["q"]:
        return _say("Nothing has been sent to Kimi since I started.", err=True)
    await tools.focus_window.handler({"title": "Kimi"})
    res = await tools.read_whole_page.handler({"start_after": LAST["q"], "wait": True})
    answer = _text(res)
    if res.get("is_error") or len(answer) < 20:
        return _say(f"Kimi isn't finished, or its page isn't in front ({answer[:160]}). Try again in a minute.", err=True)
    where = _save(LAST["q"], answer, LAST["swarm"])
    files = " If Kimi made files (documents, spreadsheets, code), they are on its page to preview or download." \
        if LAST["swarm"] else ""
    return _say(f"Kimi's result (saved as {where}):{files}\n{answer[:6000]}")


TOOLS = [ask_kimi, kimi_result]


async def check() -> str:
    for name in ("open_url", "type_text", "read_whole_page", "focus_window"):
        if not hasattr(tools, name):
            raise RuntimeError(f"Jarvis has no {name} tool")
    NOTES.mkdir(parents=True, exist_ok=True)
    return "Kimi website tools ready"
