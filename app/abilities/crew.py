"""Crew Mode, phase 1: the chief of staff plans and reviews; the free and cheap brains do the work, in parallel.
Built by Claude, 8 Oct 2026. Shoestring budget: the chief (Claude) is called twice per job (plan, final review);
everything else runs on the free Gemini key, the free local Qwen, and Jarvis's own free web search.
Plan:
1. The chief splits the goal into small tasks on a task board, each given to the cheapest brain that can do it.
2. Workers run in parallel (Gemini queued at its free-key limit, Qwen one at a time on the GPU).
   A task can use the results of tasks it depends on. A failed task is retried once on another brain.
3. The chief merges everything into one answer; the board and the result are saved in notes/crew/.
Phase 1 workers think, read and write; mouse, keyboard and phone steps come back as a to-do list for Jarvis.
"""
import asyncio
import json
import logging
import re
import time

import httpx

from common import MEMORY, load_config, read_secret
from tools import tool, _say, errors_as_words

log = logging.getLogger("jarvis.crew")
SAFE = False

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
OLLAMA = "http://127.0.0.1:11434"
NOTES = MEMORY / "notes" / "crew"
WORKERS = {
    "flash-lite": "Gemini Flash-Lite: fastest, free. Sorting, extracting, summarising a chunk, titles, translating.",
    "flash": "Gemini Flash: free, reads a lot. Merging notes, long documents, careful writing, analysis.",
    "web": "Web researcher: searches the web and reads the top pages, returns facts with their sources. "
           "Give it a precise question.",
    "qwen": "Local Qwen on this PC: free and private. His own files and notes, drafts, anything private.",
    "haiku": "Claude Haiku: quick and sharp but uses his Claude limit; only when Gemini and Qwen won't do.",
}
FALLBACK = {"flash-lite": "flash", "flash": "qwen", "qwen": "flash", "haiku": "flash", "web": "flash"}
_GEM = asyncio.Semaphore(3)            # free key: a few calls at once (it also allows ~5 a minute per model)
_QWEN = asyncio.Semaphore(1)           # one GPU
_models: dict[str, str] = {}

PLAN_RULES = """You are the chief of staff of a crew of AI workers for Jarvis-Max, Dr Wolf's computer-using agent.
Split the goal into small, concrete tasks (2 to 8) and give each to the CHEAPEST worker that can do it well.
Workers:
{workers}
Rules: tasks that don't depend on each other run at the same time, so split work into parallel pieces when you can
(e.g. one web task per sub-question). Use "needs" to pass earlier results into a task. Workers can't use the mouse,
keyboard or phone: list any such steps in "hands" for Jarvis to do afterwards. Use haiku only if really needed.
Reply with JSON only:
{{"tasks": [{{"id": "t1", "worker": "web", "task": "...", "needs": []}}], "done_when": "one line", "hands": ["..."]}}"""

REVIEW_RULES = """You are the chief of staff reviewing your crew's work for Dr Wolf. Merge the task results into
one clear, correct answer to his goal. Drop anything unsupported or contradictory, keep sources the web tasks gave,
and say plainly if something is missing. Start with a 2-3 sentence spoken summary, then the full result."""


# ------------------------------------------------------------------ the brains ----
async def _claude(system: str, prompt: str, model: str) -> str:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query
    out = []
    opts = ClaudeAgentOptions(model=model, system_prompt=system, allowed_tools=[], max_turns=1,
                              setting_sources=[])
    async for m in query(prompt=prompt, options=opts):
        if isinstance(m, AssistantMessage):
            out += [b.text for b in m.content if isinstance(b, TextBlock)]
        elif isinstance(m, ResultMessage):
            break
    return "\n".join(out).strip()


async def _gemini_model(kind: str, key: str) -> str:
    if kind in _models:
        return _models[kind]
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{GEMINI_API}/models", params={"key": key, "pageSize": 200})
        r.raise_for_status()
    names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    lite = [n for n in names if "flash-lite" in n and "preview" not in n] or [n for n in names if "flash-lite" in n]
    full = [n for n in names if "flash" in n and "lite" not in n and "image" not in n and "tts" not in n
            and "preview" not in n] or [n for n in names if "flash" in n and "lite" not in n]
    ver = lambda n: tuple(int(x) for x in re.findall(r"\d+", n)[:3])  # noqa: E731
    pick = sorted(lite if kind == "flash-lite" else full, key=ver, reverse=True)
    if not pick:
        raise RuntimeError(f"the Gemini key has no {kind} model")
    _models[kind] = pick[0]
    return pick[0]


async def _gemini(kind: str, prompt: str) -> str:
    key = read_secret("gemini_key.txt")
    if not key:
        raise RuntimeError("no Gemini key")
    model = await _gemini_model(kind, key)
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    async with _GEM:
        for attempt in range(4):
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as c:
                r = await c.post(f"{GEMINI_API}/models/{model}:generateContent", params={"key": key}, json=body)
            if r.status_code in (429, 500, 503) and attempt < 3:
                await asyncio.sleep(10 * (attempt + 1))         # free key limit or Gemini busy: wait and retry
                continue
            r.raise_for_status()
            parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts).strip()
    raise RuntimeError("Gemini stayed busy")


async def _qwen(prompt: str) -> str:
    model = load_config().get("local_model") or "qwen3.5:9b"
    async with _QWEN:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as c:
            r = await c.post(f"{OLLAMA}/api/chat", json={"model": model, "stream": False, "think": False,
                                                         "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
    return (r.json().get("message") or {}).get("content", "").strip()


async def _web(task: str) -> str:
    import web
    q = re.sub(r"\s+", " ", task).strip()
    q = q[:q.find("?") + 1] if 20 < q.find("?") < 180 else q[:180]     # search engines want a short question
    found = await web.research(q, pages=3)
    if len(found or "") < 300:                                        # thin: plain search results instead
        rows = await web.search(q, n=8)
        found = (found or "") + "\n" + web.fmt_results(q, rows)
    if len(found.strip()) < 200:
        raise RuntimeError("the web search found nothing")
    prompt = (f"Task: {task}\n\nWeb findings:\n{found[:14000]}\n\nAnswer the task from these findings only, "
              "keeping each source's site name and link. Say plainly what the findings don't cover.")
    try:
        return await _gemini("flash-lite", prompt)
    except Exception:
        return await _qwen(prompt)


async def _run(worker: str, prompt: str, task: str = "") -> str:
    if worker == "web":
        return await _web(task or prompt)
    if worker in ("flash", "flash-lite"):
        return await _gemini(worker, prompt)
    if worker == "qwen":
        return await _qwen(prompt)
    if worker == "haiku":
        return await _claude("You are a careful worker on a crew. Do exactly the task.", prompt, "haiku")
    raise RuntimeError(f"unknown worker {worker}")


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    return json.loads(m.group(0)) if m else {}


# ------------------------------------------------------------------- the job ----
async def run_crew(goal: str) -> tuple[str, str]:
    chief = (load_config().get("crew_chief") or "opus").lower()
    t0 = time.time()
    workers = "\n".join(f"- {k}: {v}" for k, v in WORKERS.items())
    try:
        plan = _json(await _claude(PLAN_RULES.format(workers=workers), f"Goal: {goal}", chief))
    except Exception as e:                                       # Claude unavailable: Gemini plans instead
        log.warning("crew: chief couldn't plan (%s); Gemini Flash plans", e)
        chief = "flash"
        plan = _json(await _gemini("flash", PLAN_RULES.format(workers=workers) + f"\n\nGoal: {goal}"))
    tasks = {t["id"]: t for t in plan.get("tasks", []) if t.get("id") and t.get("task")}
    if not tasks:
        raise RuntimeError("the chief couldn't make a task board for that")
    for t in tasks.values():
        if t.get("worker") not in WORKERS:
            t["worker"] = "flash"
    results: dict[str, str] = {}
    who: dict[str, str] = {}

    async def do(tid: str):
        t = tasks[tid]
        ctx = "".join(f"\n\nResult of {n} ({tasks[n]['task'][:80]}):\n{results.get(n, '(missing)')[:6000]}"
                      for n in t.get("needs", []) if n in tasks)
        prompt = (f"Overall goal: {goal}\nYour task: {t['task']}{ctx}\n\nDo only your task. Be concrete and complete. "
                  "Use only the results given above and facts you are sure of: never invent prices, dates, links or "
                  "specs. If an input you need is missing or failed, say so plainly instead of filling it in.")
        for w in (t["worker"], FALLBACK.get(t["worker"], "flash")):
            try:
                results[tid], who[tid] = await _run(w, prompt, t["task"]), w
                return
            except Exception as e:
                log.warning("crew: %s failed on %s (%s)", tid, w, str(e)[:150])
        results[tid], who[tid] = "(this task failed on two brains)", "none"

    pending = set(tasks)
    while pending:                                               # run every task whose inputs are ready
        ready = [t for t in pending if all(n in results or n not in tasks for n in tasks[t].get("needs", []))]
        if not ready:
            ready = list(pending)                                # a broken dependency: run anyway
        await asyncio.gather(*(do(t) for t in ready))
        pending -= set(ready)

    board = "\n".join(f"- {tid} [{who[tid]}] {tasks[tid]['task']}" for tid in tasks)
    work = "\n\n".join(f"## {tid} ({who[tid]}): {tasks[tid]['task']}\n{results[tid]}" for tid in tasks)
    review = f"Goal: {goal}\nDone when: {plan.get('done_when', '')}\n\nCrew results:\n{work[:60000]}"
    try:
        final = await (_claude(REVIEW_RULES, review, chief) if chief != "flash" else _gemini("flash", REVIEW_RULES + "\n\n" + review))
    except Exception as e:
        log.warning("crew: chief couldn't review (%s); Gemini Flash merges", e)
        final = await _gemini("flash", REVIEW_RULES + "\n\n" + review)
    hands = plan.get("hands") or []
    NOTES.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower())[:50].strip("-") or "crew"
    note = NOTES / f"{time.strftime('%Y-%m-%d-%H%M')}-{slug}.md"
    note.write_text(f"# Crew: {goal}\n\nChief: {chief} · {len(tasks)} tasks · {int(time.time() - t0)} s\n\n"
                    f"## Task board\n{board}\n\n## Result\n{final}\n\n## Worker output\n{work}\n", encoding="utf-8")
    summary = (f"Crew finished in {int(time.time() - t0)} seconds: {len(tasks)} tasks, chief {chief}, workers "
               + ", ".join(sorted(set(who.values()))) + f". Saved as notes/crew/{note.name}.")
    if hands:
        summary += " Steps for your own hands afterwards: " + "; ".join(hands[:6])
    return final, summary


@tool("crew_job",
      "Crew Mode: hand a big job to the crew. The chief of staff splits it into tasks, the free brains (Gemini, "
      "local Qwen, web research) do them in parallel, and the chief merges one checked answer. Use it when Dr Wolf "
      "says 'crew: ...', 'get the crew on ...', or when a thinking/reading/writing job has 3 or more separate parts "
      "(research and compare, summarise a long transcript in parts, plan and draft). It takes from under a minute "
      "to a few minutes. goal: the whole job in his words, with any text or links he gave.",
      {"type": "object", "properties": {"goal": {"type": "string"}}, "required": ["goal"]})
@errors_as_words
async def crew_job(args):
    goal = (args.get("goal") or "").strip()
    if not goal:
        return _say("Ask him what the crew should do.", err=True)
    final, summary = await asyncio.wait_for(run_crew(goal), 15 * 60)
    return _say(f"{summary}\n\n{final[:8000]}\n\n(Tell him the short summary first; offer the rest only if he wants it. "
                "Don't start the steps for your own hands by yourself: tell him what they are and do them when he says "
                "so. If a website asks you to sign in, stop there and tell him.)")


TOOLS = [crew_job]


async def check() -> str:
    if not read_secret("gemini_key.txt"):
        raise RuntimeError("the crew needs the free Gemini key (BRAIN > Gemini asks for it once)")
    import web  # noqa: F401
    return "crew ready"
