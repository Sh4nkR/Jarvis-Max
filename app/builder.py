"""Builder: Jarvis downloads, builds and installs GitHub projects, creates small new apps from
Dr Wolf's description (project_create), and publishes folders to GitHub.

Pen and paper: every build keeps a checklist (PLAN.md) in its folder under
Desktop/Jarvis-Builds. A stronger model, the consultant (Claude Sonnet on his Claude plan, or
Gemini when Claude is out), writes the steps. The brain on duty runs them one at a time (every
command needs Dr Wolf's ALLOW), ticks them off, and asks the consultant when a step fails or
it's unsure. After three consultations on one step it stops and tells him.

Publishing: a safety scan (keys, passwords, personal folders, huge files) runs first, the
consultant writes a README when there isn't a proper one, he sees a preview and clicks ALLOW,
then it pushes. It never force-pushes; the GitHub repo is created by him (one click).
"""
import asyncio
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path

import httpx

from common import IS_WIN, load_config, read_secret

log = logging.getLogger("jarvis.builder")

DESK = Path(os.environ.get("USERPROFILE") or Path.home()) / "Desktop"
BUILDS = DESK / "Jarvis-Builds"
CURRENT = BUILDS / ".current.json"
GATE = None                       # set by the server: async (tool_name, input) -> bool
GEMINI = None                     # set by the server: the Gemini brain (backup consultant)
STEP_TIMEOUT = 45 * 60                 # first Android/Gradle builds download a lot
MAX_ASKS = 3
GH_API = "https://api.github.com"
GH_WEB = "https://github.com"
_NOWIN = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

CONSULT_RULES = (
    "You are the senior engineer advising Jarvis, a small local AI that installs software on "
    "Dr Wolf's Windows 11 PC by running PowerShell 5.1 commands one at a time from the project "
    "folder, without administrator rights. Be exact and brief. Prefer per-user installs and the "
    "tools already on the PC. Never use admin/sudo, never change system settings, never ask for "
    "passwords or keys. EVERY STEP RUNS IN A FRESH PowerShell: variables and $env: settings do NOT "
    "carry over, so set $env:JAVA_HOME, $env:ANDROID_HOME, PATH etc. inside each command that needs "
    "them (or write them into files such as local.properties). Put downloaded toolkits (JDK, Gradle, "
    "Android command-line tools) under $env:USERPROFILE\\Jarvis-Tools as zip downloads "
    "(Invoke-WebRequest -UseBasicParsing, then Expand-Archive), never installers that need admin. "
    "In .properties files write Windows paths with forward slashes (sdk.dir=C:/Users/...). "
    "A step that accepts a licence must say so plainly in its text. A step may also write files: give "
    "them in its \"files\" list as {\"path\": relative to the project folder, \"content\": the WHOLE file}; "
    "they are written before its command runs, so never write files with PowerShell Set-Content or "
    "here-strings. Reply with JSON only, no prose around it.")


# ----------------------------------------------------------------- helpers ----
def _ps(cmd: str) -> list[str]:
    if not IS_WIN:
        return ["bash", "-c", cmd]
    wrapped = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
               f"$global:LASTEXITCODE=0; & {{ {cmd} }}; $ok=$?; "
               "if ($LASTEXITCODE) { exit $LASTEXITCODE }; if (-not $ok) { exit 1 }")
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", wrapped]


async def sh(cmd: str, cwd: Path, timeout: int = STEP_TIMEOUT) -> tuple[int, str]:
    def run():
        try:
            r = subprocess.run(_ps(cmd), capture_output=True, timeout=timeout, cwd=str(cwd),
                               creationflags=_NOWIN, env=_ENV)
            return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            return 124, f"(stopped: still running after {timeout // 60} minutes)"
        except OSError as e:
            return 127, str(e)
    return await asyncio.to_thread(run)


async def git(args: list[str], cwd: Path, timeout: int = 600) -> tuple[int, str]:
    def run():
        try:
            r = subprocess.run(["git", *args], capture_output=True, timeout=timeout, cwd=str(cwd),
                               creationflags=_NOWIN, env=_ENV)
            return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace").strip()
        except subprocess.TimeoutExpired:
            return 124, "git took too long"
        except OSError:
            return 127, "Git isn't installed on this PC"
    return await asyncio.to_thread(run)


def tail(text: str, n: int = 25, chars: int = 2000) -> str:
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    errs = [l for l in lines[:-n] if re.search(r"error|fail|not found|denied|exception|cannot|missing", l, re.I)]
    out = "\n".join(errs[-8:] + lines[-n:])
    return out[-chars:] or "(no output)"


def _json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        try:
            return json.loads(m.group(0)) if m else {}
        except ValueError:
            return {}


async def _gate(name: str, inp: dict) -> bool:
    return bool(GATE and await GATE(name, inp))


# -------------------------------------------------------------- consultant ----
async def _ask_claude(prompt: str) -> str:
    try:
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
        BUILDS.mkdir(parents=True, exist_ok=True)
        opts = ClaudeAgentOptions(model="sonnet", system_prompt=CONSULT_RULES, tools=[], max_turns=1,
                                  setting_sources=[], cwd=str(BUILDS))
        out: list[str] = []

        async def go():
            async for m in query(prompt=prompt, options=opts):
                if isinstance(m, AssistantMessage):
                    out.extend(b.text for b in m.content if isinstance(b, TextBlock))
        await asyncio.wait_for(go(), 420)               # writing a new app's files takes a while
        return "".join(out)
    except Exception as e:
        log.warning("consultant (Claude) unavailable: %s", str(e)[:200])
        return ""


async def _ask_gemini(prompt: str) -> str:
    key = read_secret("gemini_key.txt")
    model = getattr(GEMINI, "model_id", "") if GEMINI else ""
    if not key or not model:
        return ""
    from gemini_brain import API
    body = {"systemInstruction": {"parts": [{"text": CONSULT_RULES}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{API}/models/{model}:generateContent", json=body,
                             headers={"x-goog-api-key": key})
            r.raise_for_status()
            parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
    except Exception as e:
        log.warning("consultant (Gemini) unavailable: %s", str(e)[:200])
        return ""


async def consult(prompt: str) -> tuple[dict, str]:
    """(answer, who). The answer is {} when no consultant could be reached."""
    for who, fn in (("Claude", _ask_claude), ("Gemini", _ask_gemini)):
        got = _json(await fn(prompt))
        if got:
            return got, who
    return {}, ""


# ---------------------------------------------------------------- the job ----
def _job_dir() -> Path | None:
    try:
        return Path(json.loads(CURRENT.read_text(encoding="utf-8"))["dir"])
    except (OSError, ValueError, KeyError):
        return None


def _load() -> tuple[Path | None, dict]:
    d = _job_dir()
    try:
        return d, json.loads((d / ".jarvis-job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, {}


def _save(d: Path, job: dict):
    for i, s in enumerate(job["steps"], 1):
        s["n"] = i
    (d / ".jarvis-job.json").write_text(json.dumps(job, indent=1, ensure_ascii=False), encoding="utf-8")
    mark = {"done": "x", "failed": "!", "todo": " "}
    lines = [f"# Build plan: {job['name']}", "", f"Source: {job['url']}", "", job.get("summary", ""), ""]
    for s in job["steps"]:
        files = ", ".join(f["path"] for f in s.get("files") or [])
        lines.append(f"- [{mark[s['status']]}] {s['n']}. {s['text']}"
                     + (f"  (writes {files})" if files else "") + (f"  `{s['cmd']}`" if s["cmd"] else "")
                     + (f"  -> {s['note']}" if s.get("note") else ""))
    if job.get("finish"):
        lines += ["", f"When it's done: {job['finish']}"]
    (d / "PLAN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    CURRENT.write_text(json.dumps({"dir": str(d)}), encoding="utf-8")


def _clean_rel(path: str) -> str | None:
    """A file path the consultant gave, if it stays inside the project folder (no .., no drive, no .git)."""
    p = str(path or "").strip().replace("\\", "/")
    parts = [x for x in p.split("/") if x and x != "."]
    if not parts or re.match(r"^[A-Za-z]:", p) or ".." in parts or parts[0] in (".git", ".jarvis-logs") \
            or p in ("PLAN.md", ".jarvis-job.json"):
        return None
    return "/".join(parts)


def _files(raw) -> list[dict]:
    out = []
    for f in raw or []:
        rel = _clean_rel(f.get("path")) if isinstance(f, dict) else None
        if rel and isinstance(f.get("content"), str) and len(f["content"]) <= 200_000:
            out.append({"path": rel, "content": f["content"]})
    return out[:30]


def _steps(raw) -> list[dict]:
    out = []
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        cmd, files = str(s.get("cmd") or "").strip(), _files(s.get("files"))
        if cmd or files:
            text = s.get("text") or cmd or "Write " + ", ".join(f["path"] for f in files)
            out.append({"text": str(text)[:200], "cmd": cmd, "files": files,
                        "check": str(s.get("check") or "").strip(), "status": "todo", "note": ""})
    return out[:25]


def _plan_lines(job: dict) -> str:
    mark = {"done": "done", "failed": "FAILED", "todo": "to do"}
    return "\n".join(f"{s['n']}. [{mark[s['status']]}] {s['text']}" for s in job["steps"])


def _repo_of(source: str) -> tuple[str, str] | None:
    s = source.strip().rstrip("/")
    m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+)", s) or re.fullmatch(r"([\w.-]+)/([\w.-]+)", s)
    return (m.group(1), re.sub(r"\.git$", "", m.group(2))) if m else None


async def _facts(owner: str, name: str) -> dict | None:
    """The repo's details from GitHub's API. That API allows only 60 lookups an hour per internet
    address (shared by everyone on the same connection), so when it says no, git itself checks
    that the repo exists (git has no such limit)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{GH_API}/repos/{owner}/{name}", headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 200:
            return r.json()
        log.info("GitHub API said %s for %s/%s; checking with git instead", r.status_code, owner, name)
        if r.status_code == 404:
            return None
    except Exception as e:
        log.info("GitHub API unreachable (%s); checking with git instead", str(e)[:120])
    code, out = await git(["ls-remote", "--heads", f"{GH_WEB}/{owner}/{name}.git"], BUILDS if BUILDS.exists() else DESK,
                          timeout=60)
    if code:                                            # an empty new repo is fine: exit 0, no branches
        return None
    return {"html_url": f"{GH_WEB}/{owner}/{name}", "description": "", "limited": True}


KEY_FILES = ("package.json", "pyproject.toml", "requirements.txt", "setup.py", "Cargo.toml", "go.mod",
             "build.gradle", "build.gradle.kts", "CMakeLists.txt", "Makefile", "pom.xml", "install.bat",
             "install.ps1", "setup.bat", "setup.ps1")
PC_TOOLS = ("git", "python", "py", "uv", "pip", "node", "npm", "pnpm", "yarn", "java", "dotnet", "cargo",
            "go", "cmake", "winget", "choco", "ollama", "gradle", "adb", "sdkmanager", "code")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "models", "logs", "memory", "secrets"}


def _pc_tools() -> str:
    have = [t for t in PC_TOOLS if shutil.which(t)]
    for var in ("JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            have.append(f"{var}={os.environ[var]}")
    for label, p in (("Android SDK folder", Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk"),
                     ("Jarvis-Tools folder", Path(os.environ.get("USERPROFILE") or Path.home()) / "Jarvis-Tools")):
        if os.environ.get("LOCALAPPDATA" if "SDK" in label else "USERPROFILE") and p.is_dir():
            have.append(f"{label} {p} contains: {', '.join(sorted(x.name for x in p.iterdir())[:20])}")
    return f"ALREADY ON THE PC: {', '.join(have) or 'nothing detected'}"


def _file_list(d: Path, limit: int = 200) -> list[str]:
    files = []
    for root, dirs, names in os.walk(d):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and x not in (".jarvis-logs", "build", "dist", ".gradle"))
        rel = Path(root).relative_to(d).as_posix()
        files += [(f"{rel}/" if rel != "." else "") + x for x in sorted(names)]
        if len(files) >= limit:
            break
    return files[:limit]


def _context(d: Path) -> str:
    readme = next((p for p in d.iterdir() if p.is_file() and p.name.lower().startswith("readme")), None)
    heads = []
    for k in KEY_FILES:
        if (d / k).is_file():
            heads.append(f"--- {k} ---\n" + (d / k).read_text(encoding="utf-8", errors="replace")[:1500])
    return ("README:\n" + (readme.read_text(encoding="utf-8", errors="replace")[:7000] if readme else "(none)")
            + "\n\nFILES:\n" + "\n".join(_file_list(d)) + "\n\n" + "\n".join(heads) + "\n\n" + _pc_tools())


SOURCE_EXT = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".css", ".json", ".toml", ".txt", ".md",
              ".ps1", ".bat", ".cmd", ".java", ".kt", ".kts", ".gradle", ".xml", ".properties", ".cs",
              ".csproj", ".go", ".rs", ".c", ".cpp", ".h", ".yml", ".yaml", ".ini", ".cfg", ".sh"}


def _source(d: Path, budget: int = 45_000) -> str:
    """The project's own text files, so the consultant can fix the code itself (small apps only)."""
    out, used = [], 0
    for rel in _file_list(d, 400):
        p = d / rel
        if rel in ("PLAN.md", ".jarvis-job.json") or p.suffix.lower() not in SOURCE_EXT \
                or rel.endswith(("package-lock.json", "yarn.lock")):
            continue
        try:
            if p.stat().st_size > 60_000:
                out.append(f"--- {rel} --- (too big to show)")
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if used + len(text) > budget:
            out.append(f"--- {rel} --- (not shown: out of room)")
            continue
        out.append(f"--- {rel} ---\n{text}")
        used += len(text)
    return "\n".join(out) or "(no source files yet)"


# ------------------------------------------------------------- build tools ----
async def project_start(source: str) -> str:
    rid = _repo_of(source or "")
    if not rid:
        return "Give me a GitHub link or owner/name, like github.com/owner/project."
    owner, name = rid
    info = await _facts(owner, name)
    if not info:
        return (f"I couldn't find github.com/{owner}/{name} (it may be private or misspelled). Tell him; "
                "don't download or build it any other way.")
    url = info.get("html_url") or f"https://github.com/{owner}/{name}"
    facts = ("GitHub's details unavailable right now (its hourly lookup limit is used up), but the project exists"
             if info.get("limited") else
             f"{info.get('stargazers_count', 0)} stars, last updated {str(info.get('pushed_at', ''))[:10]}, "
             f"license {((info.get('license') or {}).get('spdx_id')) or 'none'}, "
             f"{round((info.get('size') or 0) / 1024)} MB" + (", ARCHIVED" if info.get("archived") else ""))
    BUILDS.mkdir(parents=True, exist_ok=True)
    d = BUILDS / name
    if not (d / ".git").is_dir():
        if not await _gate("project_download", {"url": url, "facts": facts, "about": info.get("description") or ""}):
            return "He didn't allow the download. Stop and ask him what he wants."
        code, out = await git(["clone", "--depth", "1", url + ".git", str(d)], BUILDS, timeout=1800)
        if code:
            return f"The download failed: {tail(out, 6)}. Tell him; don't download it any other way."
    ctx = await asyncio.to_thread(_context, d)
    plan, who = await consult(
        "Write the install plan for this GitHub project on Windows 11.\n"
        f"Project: {url} ({info.get('description') or 'no description'}; {facts})\n"
        f"It is ALREADY downloaded to {d}, and every command runs with that folder as the current "
        "directory. Don't download or clone the project again.\n\n{ctx}\n\n"
        'Reply as JSON: {"summary": "one sentence: what it is and how it gets installed", '
        '"steps": [{"text": "short plain-English step", "cmd": "exact PowerShell command", '
        '"check": "PowerShell command that exits 0 only if the step worked, or empty"}], '
        '"finish": "how Dr Wolf starts the program afterwards", "warnings": ["anything he must do himself"]}. '
        "At most 15 steps. Use the tools already on the PC where possible. If a release installer "
        "exists, downloading and running it is fine. Last step: prove it runs (e.g. --version).")
    steps = _steps(plan.get("steps"))
    if not steps:
        return ("Downloaded to " + str(d) + ", but no consultant could write a plan (Claude and Gemini "
                "both unreachable, or the reply was unusable). Tell him.")
    job = {"url": url, "name": name, "summary": plan.get("summary", ""), "steps": steps,
           "finish": plan.get("finish", ""), "warnings": plan.get("warnings") or [], "asks": 0,
           "planner": who, "started": datetime.datetime.now().isoformat(timespec="seconds")}
    _save(d, job)
    warn = "".join(f"\n- He must: {w}" for w in job["warnings"][:5])
    return (f"Downloaded {url} ({facts}) to {d}. {who} wrote the plan (PLAN.md in that folder):\n"
            f"{job['summary']}\n{_plan_lines(job)}{warn}\n\nRead him the plan in short lines and wait "
            "for his go. Then call project_next, once per step.")


def _slug(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text or "")[:4]
    return "-".join(w.capitalize() for w in words)[:40] or "New-App"


async def project_create(idea: str, name: str = "") -> str:
    """A brand-new small app from his description: the consultant writes the plan and the first
    files; the brain on duty runs, tests and fixes it step by step, like any other build."""
    idea = (idea or "").strip()
    if len(idea) < 8:
        return "Ask him what the app should do (a sentence or two), then call project_create again."
    BUILDS.mkdir(parents=True, exist_ok=True)
    base = _slug(name or idea)
    d, i = BUILDS / base, 2
    while d.exists() and any(d.iterdir()):
        d, i = BUILDS / f"{base}-{i}", i + 1
    plan, who = await consult(
        "Dr Wolf wants a NEW small app built from scratch on his Windows 11 PC. His words:\n"
        f"\"{idea}\"\n\nThe project folder is {d} (empty); every command runs there.\n{_pc_tools()}\n\n"
        "Design the simplest thing that does what he asked: prefer Python (standard library, or a "
        "project-local .venv for anything else) or plain HTML/JS, unless he named a platform. Keep "
        "it to a few short, readable files with a README.md saying how to run it. Put all code in "
        "the steps' \"files\" lists. Plan it so a small local AI can carry it out one step at a "
        "time: write the files, install what's needed, then TEST it with a command that exits 0 "
        "only if it works (a small test script or a --selftest flag; never a command that waits "
        "for a window to be closed or runs forever). The last step proves it runs.\n\n"
        'Reply as JSON: {"name": "short app name", "summary": "one sentence: what it does and how '
        'it is built", "steps": [{"text": "short plain-English step", "files": [{"path": "...", '
        '"content": "..."}], "cmd": "exact PowerShell command, or empty", "check": "PowerShell '
        'command that exits 0 only if the step worked, or empty"}], "finish": "how Dr Wolf starts '
        'it", "warnings": ["anything he must do himself"]}. At most 12 steps.')
    steps = _steps(plan.get("steps"))
    if not steps:
        return ("No consultant could plan the app (Claude and Gemini both unreachable, or the reply "
                "was unusable). Tell him; don't write it yourself.")
    d.mkdir(parents=True, exist_ok=True)
    job = {"url": f"new app: {idea[:300]}", "name": str(plan.get("name") or d.name)[:60], "kind": "create",
           "idea": idea, "summary": plan.get("summary", ""), "steps": steps, "finish": plan.get("finish", ""),
           "warnings": plan.get("warnings") or [], "asks": 0, "planner": who,
           "started": datetime.datetime.now().isoformat(timespec="seconds")}
    _save(d, job)
    warn = "".join(f"\n- He must: {w}" for w in job["warnings"][:5])
    return (f"{who} designed {job['name']} in {d} (plan in PLAN.md):\n{job['summary']}\n"
            f"{_plan_lines(job)}{warn}\n\nRead him the plan in short lines and wait for his go. "
            "Then call project_next, once per step.")


async def project_next() -> str:
    d, job = _load()
    if not job:
        return "There's no build in progress. Start one with project_start."
    step = next((s for s in job["steps"] if s["status"] != "done"), None)
    if not step:
        return f"All {len(job['steps'])} steps are done. {job.get('finish', '')}"
    shown = (step["cmd"] + (f"   [then check: {step['check']}]" if step["check"] else "")).strip()
    files = step.get("files") or []
    if files:
        ok = await _gate("project_write", {"folder": str(d), "files": [f["path"] for f in files], "command": shown})
    else:
        ok = await _gate("run_command", {"command": shown, "folder": str(d)})
    if not ok:
        return "He didn't allow that step. Stop and ask him what he wants."
    wrote = []
    for f in files:
        p = (d / f["path"]).resolve()
        if d.resolve() not in p.parents:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f["content"], encoding="utf-8")
        wrote.append(f["path"])
    code, out = await sh(step["cmd"], d) if step["cmd"] else (0, "")
    if wrote:
        out = f"(wrote {', '.join(wrote)})\n{out}"
    logs = d / ".jarvis-logs"
    logs.mkdir(exist_ok=True)
    (logs / f"step-{step['n']}.log").write_text(f"$ {step['cmd']}\nexit {code}\n{out}", encoding="utf-8")
    ok = code == 0
    if ok and step["check"]:
        ccode, cout = await sh(step["check"], d, timeout=300)
        ok = ccode == 0
        if not ok:
            out, code = out + f"\n[check failed: {step['check']} -> exit {ccode}]\n{cout}", ccode
    step["status"], step["note"] = ("done", "") if ok else ("failed", f"exit {code}")
    if ok:
        job["asks"] = 0
    _save(d, job)
    left = sum(s["status"] != "done" for s in job["steps"])
    if ok:
        if not left:
            return (f"Step {step['n']} done. All {len(job['steps'])} steps are done: it's built and "
                    f"installed in {d}. {job.get('finish', '')}")
        nxt = next(s for s in job["steps"] if s["status"] != "done")
        return f"Step {step['n']} done ({left} left). Next: {nxt['n']}. {nxt['text']}. Call project_next."
    return (f"Step {step['n']} FAILED ({step['text']}), exit code {code}. Last lines:\n{tail(out)}\n\n"
            "Call project_ask (say what you think went wrong, or just ask) to get the consultant's fix.")


async def project_ask(question: str = "") -> str:
    d, job = _load()
    if not job:
        return "There's no build in progress."
    step = next((s for s in job["steps"] if s["status"] != "done"), None)
    if not step:
        return "Every step is already done."
    if job.get("asks", 0) >= MAX_ASKS:
        return (f"The consultant has already been asked {MAX_ASKS} times about step {step['n']}. Stop now "
                f"and tell him plainly what's stuck: {step['text']} ({step.get('note', '')}).")
    try:
        last = (d / ".jarvis-logs" / f"step-{step['n']}.log").read_text(encoding="utf-8")
    except OSError:
        last = "(this step hasn't run yet)"
    new_app = job.get("kind") == "create"
    what = (f"building a NEW app for Dr Wolf (his words: \"{job.get('idea', '')}\") in {d}" if new_app
            else f"installing {job['url']} on Windows 11")
    code_now = (f"\n\nTHE APP'S FILES RIGHT NOW (you wrote them; fix the code itself when that's the problem):\n"
                f"{await asyncio.to_thread(_source, d)}\n\n{_pc_tools()}" if new_app else "")
    fix, who = await consult(
        f"Jarvis is {what} and is stuck.\nPlan so far:\n{_plan_lines(job)}\n\n"
        f"Current step {step['n']}: {step['text']}\nFiles it writes: "
        f"{', '.join(f['path'] for f in step.get('files') or []) or '-'}\nCommand: {step['cmd'] or '-'}\n"
        f"Check: {step['check'] or '-'}\nLast output:\n{tail(last, 40, 3500)}{code_now}\n\n"
        f"Jarvis says: {question or '(no comment)'}\n\n"
        'Reply as JSON: {"answer": "one or two plain sentences: what went wrong and the fix", '
        '"replace_step": true if the new steps replace the current step, false to insert them before it, '
        '"steps": [{"text": "...", "files": [{"path": "...", "content": "the whole corrected file"}], '
        '"cmd": "exact PowerShell command, or empty", "check": "..."}], '
        '"give_up": true only if it can\'t work on this PC, "reason": "why, if give_up"}. '
        "To fix code, give a step that rewrites the files and re-runs the test.")
    if not fix:
        return "No consultant could be reached (Claude and Gemini both unavailable). Tell him."
    job["asks"] = job.get("asks", 0) + 1
    if fix.get("give_up"):
        _save(d, job)
        return f"{who} says this can't work here: {fix.get('reason') or fix.get('answer', '')}. Tell him."
    new = _steps(fix.get("steps"))
    i = job["steps"].index(step)
    if new:
        job["steps"][i:i + (1 if fix.get("replace_step") else 0)] = new
    elif step["status"] == "failed":
        step["status"] = "todo"
    _save(d, job)
    return (f"{who}: {fix.get('answer', '')}\nUpdated plan:\n{_plan_lines(job)}\n"
            f"(Consultations on this step: {job['asks']} of {MAX_ASKS}.) Call project_next.")


async def project_status() -> str:
    d, job = _load()
    if not job:
        return "There's no build in progress."
    done = sum(s["status"] == "done" for s in job["steps"])
    return f"{job['name']} in {d}: {done} of {len(job['steps'])} steps done.\n{_plan_lines(job)}"


# ----------------------------------------------------------------- publish ----
KEEP_OUT = ["secrets/", "memory/", "logs/", ".env", "*.env", "*.pem", "*.key", "*.pfx", "id_rsa*",
            "*.kdbx", ".venv/", "venv/", "node_modules/", "__pycache__/", "*.pyc", ".jarvis-logs/"]
SECRET_TEXT = [
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "a Google/Gemini API key"),
    (re.compile(r"\bAQ\.[A-Za-z0-9_-]{30,}"), "a Gemini API key"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "an Anthropic API key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}"), "an API key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}"), "a GitHub token"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "a Slack token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "a private key"),
    (re.compile(r"(?i)\b(password|passwd|pwd)\s*[:=]\s*['\"][^'\"\s]{4,}['\"]"), "a password"),
]
BAD_NAMES = re.compile(r"(^|/)(\.env[^/]*|id_rsa[^/]*|[^/]*\.(pem|key|pfx|kdbx)|credentials[^/]*\.json|secrets/.*)$", re.I)


def _scan(d: Path, files: list[str]) -> list[str]:
    found = []
    for f in files:
        p = d / f
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if BAD_NAMES.search(f):
            found.append(f"{f} (looks like a key or secrets file)")
        elif size > 50 * 1024 * 1024:
            found.append(f"{f} ({size // (1024 * 1024)} MB: too big for GitHub)")
        elif size < 2 * 1024 * 1024:
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for pat, what in SECRET_TEXT:
                if pat.search(text):
                    found.append(f"{f} (contains {what})")
                    break
        if len(found) >= 10:
            break
    return found


def _folder(path: str) -> Path | None:
    p = Path(os.path.expandvars(os.path.expanduser((path or "").strip().strip('"'))))
    if not p.is_absolute():
        p = DESK / p
    try:
        p = p.resolve()
    except OSError:
        return None
    home = Path(os.environ.get("USERPROFILE") or Path.home()).resolve()
    if not p.is_dir() or p in (home, DESK.resolve(), Path(p.anchor)):
        return None
    return p


async def github_publish(folder: str, repo: str, private: bool = False, message: str = "") -> str:
    d = _folder(folder)
    if not d:
        return "That isn't a folder I can publish (it must be one project folder, not the whole Desktop or home)."
    cfg = load_config()
    rid = _repo_of(repo if "/" in (repo or "") else f"{cfg.get('github_user') or ''}/{repo}")
    if not rid or not rid[0]:
        return "I need his GitHub username: add \"github_user\" to jarvis.json, or give the repo as owner/name."
    owner, name = rid
    url = f"{GH_WEB}/{owner}/{name}"
    code, _ = await git(["--version"], d)
    if code:
        return "Git isn't installed on this PC, so I can't publish."
    if not private and not await _facts(owner, name):
        webbrowser.open(f"https://github.com/new?name={name}&visibility=public")
        return (f"The repo {url} doesn't exist yet. I've opened GitHub's new-repo page with the name filled "
                "in. Ask him to click 'Create repository' (leave 'Add a README' off), then call "
                "github_publish again.")
    gi = d / ".gitignore"
    have = gi.read_text(encoding="utf-8", errors="replace") if gi.exists() else ""
    missing = [k for k in KEEP_OUT if k not in have.split()]
    if missing:
        gi.write_text(have.rstrip() + ("\n\n" if have.strip() else "")
                      + "# Kept out of GitHub by Jarvis (keys, personal notes, logs, installs)\n"
                      + "\n".join(missing) + "\n", encoding="utf-8")
    if not (d / ".git").is_dir():
        code, out = await git(["init", "-b", "main"], d)
        if code:
            return f"git init failed: {out[:300]}"
    # files committed earlier that .gitignore now keeps out (personal settings, old secrets) stop being published
    code, out = await git(["ls-files", "-ci", "--exclude-standard"], d)
    stale = [f for f in out.splitlines() if f.strip()]
    if stale:
        await git(["rm", "--cached", "--quiet", "--", *stale], d)
    code, out = await git(["ls-files", "--cached", "--others", "--exclude-standard"], d)
    files = [f for f in out.splitlines() if f.strip() and (d / f).exists()]
    if not files:
        return "There's nothing in that folder to publish."
    found = await asyncio.to_thread(_scan, d, files)
    if found:
        return ("Nothing was published. The safety scan found:\n- " + "\n- ".join(found)
                + "\nTell him; these must be removed or added to .gitignore first.")
    readme = next((d / n for n in ("README.md", "readme.md", "Readme.md") if (d / n).exists()), None)
    if not readme or len(readme.read_text(encoding="utf-8", errors="replace").strip()) < 300:
        got, who = await consult(
            f"Write a README.md for the GitHub repo {owner}/{name}, for GitHub users.\n\n"
            + await asyncio.to_thread(_context, d)
            + '\n\nReply as JSON: {"readme": "the full Markdown"}. Cover what it is, features, '
              "requirements, install, usage and license (only if a LICENSE file exists). No invented facts.")
        if got.get("readme"):
            (d / "README.md").write_text(got["readme"].strip() + "\n", encoding="utf-8")
            files = sorted(set(files) | {"README.md"})
    first = next((l.strip("# ").strip() for l in (d / "README.md").read_text(encoding="utf-8", errors="replace")
                  .splitlines() if l.strip()), "") if (d / "README.md").exists() else ""
    top = sorted({f.split("/")[0] + ("/" if "/" in f else "") for f in files})
    if not await _gate("github_publish", {"repo": url, "folder": str(d), "files": len(files),
                                          "visibility": "private" if private else "PUBLIC", "readme": first,
                                          "top": ", ".join(top[:15]) + (" ..." if len(top) > 15 else "")}):
        return "He didn't allow publishing. Nothing was pushed."
    for k, v in (("user.name", owner), ("user.email", f"{owner}@users.noreply.github.com")):
        if (await git(["config", k], d))[0]:
            await git(["config", k, v], d)              # only when Git has no name set at all
    await git(["add", "-A"], d)
    code, out = await git(["commit", "-m", message or f"Update {name} (published by Jarvis)"], d)
    if code and "nothing to commit" not in out:
        return f"The commit failed: {out[:300]}"
    code, remotes = await git(["remote", "-v"], d)
    remote = next((l.split()[0] for l in remotes.splitlines() if f"{owner}/{name}".lower() in l.lower()), None)
    if not remote:
        remote = "origin" if "origin" not in remotes.split() else "github"
        await git(["remote", "add", remote, url + ".git"], d)
    code, out = await git(["push", "-u", remote, "HEAD:main"], d)
    if code and re.search(r"rejected|fetch first|non-fast-forward", out):
        await git(["fetch", remote, "main"], d)
        n = (await git(["rev-list", "--count", f"{remote}/main"], d))[1]
        related = (await git(["merge-base", "HEAD", f"{remote}/main"], d))[0] == 0
        if related:
            # GitHub has commits this PC hasn't seen yet (made on the website, or from another PC):
            # merge them in the normal way, which keeps both sides. Any clash is undone at once and
            # nothing is pushed, so work on GitHub is never overwritten.
            mcode, mout = await git(["merge", "--no-edit", f"{remote}/main"], d)
            if mcode:
                await git(["merge", "--abort"], d)
                return (f"{url} has changes made elsewhere that clash with the files on this PC, so I "
                        "stopped: nothing was pushed or overwritten. Tell him.")
            code, out = await git(["push", "-u", remote, "HEAD:main"], d)
        elif n.isdigit() and int(n) <= 3:               # a new repo with just GitHub's starter README/LICENSE
            await git(["merge", "--allow-unrelated-histories", "-X", "ours", "--no-edit", f"{remote}/main"], d)
            code, out = await git(["push", "-u", remote, "HEAD:main"], d)
        else:
            return (f"{url} already has other work on it ({n} commits), so I won't overwrite it. "
                    "Nothing was pushed. Tell him.")
    if code:
        if re.search(r"could not read Username|Authentication failed|403|terminal prompts disabled", out):
            return ("GitHub refused the login. Git on this PC needs signing in to GitHub once "
                    "(a sign-in window appears on the first push from Git). Tell him.")
        if "not found" in out.lower():
            webbrowser.open(f"https://github.com/new?name={name}&visibility={'private' if private else 'public'}")
            return ("GitHub says the repo doesn't exist. I've opened the new-repo page; ask him to click "
                    "'Create repository', then call github_publish again.")
        return f"The push failed: {tail(out, 8)}"
    local = (await git(["rev-parse", "HEAD"], d))[1]
    remote_head = (await git(["ls-remote", url + ".git", "refs/heads/main"], d))[1].split("\t")[0]
    ok = remote_head and remote_head == local
    return (f"Published {len(files)} files to {url}. "
            + ("GitHub shows the same commit, so it's confirmed." if ok else
               "The push finished, but I couldn't confirm it on GitHub yet."))
