"""Jarvis's two Android build skills.

1. android_build: build a debug APK HERE on the PC with the Android Studio tools already
   installed (its own Java "jbr", the Android SDK in %LOCALAPPDATA%\\Android\\Sdk, and the
   project's Gradle wrapper). Dr Wolf clicks ALLOW first, because a Gradle build runs the
   project's own build scripts.
2. github_build + github_build_status: push the project to his GitHub (through github_publish:
   safety scan, his ALLOW, never a force-push) with a build recipe that makes GitHub's servers
   build the APK, then later check the build and download the APK to Desktop\\Jarvis-Builds\\APKs.

Debug builds only: a release build needs a signing key, and Jarvis never handles keys.
"""
import asyncio
import glob
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx

import builder
from common import IS_WIN

APKS = builder.BUILDS / "APKs"
LAST = builder.BUILDS / ".last-github-build.json"
LOGDIR = Path(__file__).resolve().parent.parent / "logs" / "builds"
_NOWIN = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
_PF = os.environ.get("ProgramFiles", r"C:\Program Files")
_LOCAL = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))


# ------------------------------------------------------------- finding tools ----
def _project_root(d: Path) -> Path | None:
    """The folder with settings.gradle(.kts): the folder itself or up to two levels down."""
    for depth in ("", "*/", "*/*/"):
        for n in ("settings.gradle", "settings.gradle.kts"):
            hits = sorted(d.glob(depth + n))
            hits = [h for h in hits if "node_modules" not in h.parts and "build" not in h.parts]
            if hits:
                return hits[0].parent
    return None


def _jdk() -> str | None:
    c = [os.environ.get("JAVA_HOME", "")]
    c += glob.glob(os.path.join(_PF, "Android", "Android Studio*", "jbr"))
    c += glob.glob(os.path.join(_PF, "Android", "Android Studio*", "jre"))
    for vendor in ("Eclipse Adoptium", "AdoptOpenJDK", "Java", "Microsoft", "Zulu"):
        c += sorted(glob.glob(os.path.join(_PF, vendor, "*")), reverse=True)
    for p in c:
        if p and Path(p, "bin", "java.exe" if IS_WIN else "java").exists():
            return p
    return None


def _sdk() -> str | None:
    for p in (os.environ.get("ANDROID_HOME", ""), os.environ.get("ANDROID_SDK_ROOT", ""),
              os.path.join(_LOCAL, "Android", "Sdk")):
        if p and Path(p, "platforms").is_dir():
            return p
    return None


def _gradle(root: Path) -> str | None:
    if (root / ("gradlew.bat" if IS_WIN else "gradlew")).exists():
        return r".\gradlew.bat" if IS_WIN else "./gradlew"
    g = shutil.which("gradle")
    if g:
        return g
    home = Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")
    hits = sorted(home.glob("wrapper/dists/*/*/gradle-*/bin/" + ("gradle.bat" if IS_WIN else "gradle")),
                  reverse=True)             # a Gradle that Android Studio downloaded earlier
    return str(hits[0]) if hits else None


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"   # PowerShell single-quoted string


def _write_local_properties(root: Path, sdk: str):
    lp = root / "local.properties"
    text = lp.read_text(encoding="utf-8", errors="replace") if lp.exists() else ""
    if "sdk.dir" in text:
        return
    esc = sdk.replace("\\", "\\\\").replace(":", "\\:")
    lp.write_text(text.rstrip() + ("\n" if text.strip() else "") + f"sdk.dir={esc}\n", encoding="utf-8")


def _save_apk(src: Path, label: str) -> Path:
    APKS.mkdir(parents=True, exist_ok=True)
    dst = APKS / f"{label}-{time.strftime('%Y%m%d-%H%M')}.apk"
    shutil.copy2(src, dst)
    return dst


# ------------------------------------------------------- 1. local PC build ----
async def android_build(folder: str) -> str:
    d = builder._folder(folder)
    if not d:
        return "That isn't a project folder I can build (give me the app's folder, not the whole Desktop)."
    root = _project_root(d)
    if not root:
        return f"There's no Android project in {d} (no settings.gradle). Is it the right folder?"
    jdk, sdk, gradle = _jdk(), _sdk(), _gradle(root)
    if not sdk:
        return ("I can't find the Android SDK. Ask him to open Android Studio once and let it finish its "
                "setup (it downloads the SDK), then try again. Or use github_build instead.")
    if not jdk:
        return "I can't find Java (Android Studio's own 'jbr' folder). Android Studio may need reinstalling."
    if not gradle:
        return ("This project has no Gradle wrapper and Gradle isn't on the PC. Ask him to open the project "
                "in Android Studio once (it sets Gradle up), then try again. Or use github_build instead.")
    _write_local_properties(root, sdk)
    shown = f"{Path(gradle).name} assembleDebug   (in {root})"
    if not await builder._gate("android_build", {"folder": str(root), "command": shown, "java": jdk, "sdk": sdk}):
        return "He didn't allow the build. Nothing was run."
    cmd = (f"$env:JAVA_HOME={_q(jdk)}; $env:ANDROID_HOME={_q(sdk)}; $env:ANDROID_SDK_ROOT={_q(sdk)}; "
           f"$env:Path=$env:JAVA_HOME + '\\bin;' + $env:Path; "
           f"& {_q(gradle) if not gradle.startswith('.') else gradle} assembleDebug --no-daemon --console=plain --stacktrace")
    start = time.time()
    code, out = await builder.sh(cmd, root)
    LOGDIR.mkdir(parents=True, exist_ok=True)
    log = LOGDIR / f"{root.name}-local-{time.strftime('%Y%m%d-%H%M%S')}.log"
    log.write_text(out, encoding="utf-8")
    if code:
        return (f"The build FAILED (log: {log}). The important lines:\n{builder.tail(out, 20)}\n"
                "Tell him in one short line, then use project_ask with these lines if he wants it fixed.")
    apks = [p for p in root.glob("**/build/outputs/apk/**/*.apk") if p.stat().st_mtime >= start - 5]
    if not apks:
        return f"Gradle said it finished, but I can't find a new APK. Log: {log}"
    apk = max(apks, key=lambda p: p.stat().st_mtime)
    saved = _save_apk(apk, root.name)
    mins = max(1, round((time.time() - start) / 60))
    return (f"Built in about {mins} min. The APK is {saved} ({apk.stat().st_size // 1024} KB). "
            "It's a debug build: copy it to the phone and tap it to install.")


# ------------------------------------------------------ 2. GitHub cloud build ----
WORKFLOW = """name: Jarvis APK build
on:
  push:
    branches: [ main ]
  workflow_dispatch: {}
permissions:
  contents: write
jobs:
  apk:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: __DIR__
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: '17'
      - name: Accept Android SDK licences
        run: yes | "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null 2>&1 || true
      - uses: gradle/actions/setup-gradle@v4
        with:
          gradle-version: '8.7'
      - name: Build debug APK
        run: |
          if [ -f ./gradlew ]; then chmod +x ./gradlew; ./gradlew assembleDebug --no-daemon --stacktrace; else gradle assembleDebug --no-daemon --stacktrace; fi
      - uses: actions/upload-artifact@v4
        with:
          name: apk
          path: '__PRE__**/build/outputs/apk/debug/*.apk'
      - uses: softprops/action-gh-release@v2
        with:
          tag_name: build-${{ github.run_number }}
          name: Build ${{ github.run_number }}
          files: '__PRE__**/build/outputs/apk/debug/*.apk'
"""


def _ensure_workflow(d: Path, root: Path) -> str:
    wf_dir = d / ".github" / "workflows"
    for f in list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml")):
        if "assemble" in f.read_text(encoding="utf-8", errors="replace"):
            return f"it already has a build recipe ({f.name})"
    rel = root.relative_to(d).as_posix()
    rel = "." if rel in ("", ".") else rel
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "jarvis-apk.yml").write_text(
        WORKFLOW.replace("__DIR__", rel).replace("__PRE__", "" if rel == "." else rel + "/"), encoding="utf-8")
    return "I added a build recipe (.github/workflows/jarvis-apk.yml)"


async def github_build(folder: str, repo: str, private: bool = False, message: str = "") -> str:
    d = builder._folder(folder)
    if not d:
        return "That isn't a project folder I can build (give me the app's folder, not the whole Desktop)."
    root = _project_root(d)
    if not root:
        return f"There's no Android project in {d} (no settings.gradle). Is it the right folder?"
    note = _ensure_workflow(d, root)
    msg = await builder.github_publish(str(d), repo, private, message)
    if not msg.startswith("Published"):
        return msg
    sha = (await builder.git(["rev-parse", "HEAD"], d))[1].strip()
    owner, name = builder._repo_of(repo if "/" in repo else
                                   f"{builder.load_config().get('github_user') or ''}/{repo}")
    LAST.write_text(json.dumps({"owner": owner, "name": name, "sha": sha, "label": root.name,
                                "private": bool(private), "at": time.time()}), encoding="utf-8")
    return (f"{msg} {note}. GitHub is now building the APK on its own servers; that usually takes "
            "3 to 8 minutes. Tell him, and when he asks, call github_build_status to check and download it.")


def _gh() -> str | None:
    g = shutil.which("gh")
    if g:
        return g
    p = Path(_PF, "GitHub CLI", "gh.exe")
    return str(p) if p.exists() else None


async def _run(args: list[str], cwd: Path, timeout: int = 300) -> tuple[int, str]:
    def run():
        try:
            r = subprocess.run(args, capture_output=True, timeout=timeout, cwd=str(cwd),
                               creationflags=_NOWIN, env=builder._ENV)
            return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace").strip()
        except subprocess.TimeoutExpired:
            return 124, "took too long"
        except OSError as e:
            return 127, str(e)
    return await asyncio.to_thread(run)


async def github_build_status() -> str:
    if not LAST.exists():
        return "There's no GitHub build I started. Use github_build first."
    job = json.loads(LAST.read_text(encoding="utf-8"))
    o, n, sha, label = job["owner"], job["name"], job["sha"], job.get("label") or job["name"]
    gh = _gh()
    authed = bool(gh) and (await _run([gh, "auth", "status"], builder.BUILDS, 30))[0] == 0
    runs_url = f"https://github.com/{o}/{n}/actions"
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as cx:
        if authed:
            code, out = await _run([gh, "run", "list", "--repo", f"{o}/{n}", "--limit", "10", "--json",
                                    "databaseId,status,conclusion,headSha,number,url"], builder.BUILDS, 60)
            runs = json.loads(out) if code == 0 and out.startswith("[") else []
            runs = [{"id": r["databaseId"], "status": r["status"], "conclusion": r["conclusion"],
                     "head_sha": r["headSha"], "run_number": r["number"], "html_url": r["url"]} for r in runs]
        else:
            if job.get("private"):
                return ("That repo is private, and GitHub's command-line tool isn't signed in on the PC, so I "
                        f"can't check it. Ask him to look at {runs_url}, or sign in to 'gh' once.")
            r = await cx.get(f"{builder.GH_API}/repos/{o}/{n}/actions/runs", params={"per_page": 10})
            runs = r.json().get("workflow_runs", []) if r.status_code == 200 else []
        run = next((r for r in runs if r.get("head_sha") == sha), None)
        if not run:
            return f"GitHub hasn't started building that push yet (or I can't see it). Check again in a minute: {runs_url}"
        if run["status"] != "completed":
            return f"Still building on GitHub ({run['status'].replace('_', ' ')}). Check again in a couple of minutes."
        if run["conclusion"] != "success":
            why = ""
            if authed:
                why = builder.tail((await _run([gh, "run", "view", str(run["id"]), "--repo", f"{o}/{n}",
                                                "--log-failed"], builder.BUILDS, 120))[1], 20)
            return (f"The GitHub build FAILED ({run['conclusion']}). {run['html_url']}\n{why}\n"
                    "Tell him in one short line; use project_ask with the error lines if he wants it fixed.")
        # success: fetch the APK from the release the recipe made (public repos), else via gh
        tag = f"build-{run['run_number']}"
        r = await cx.get(f"{builder.GH_API}/repos/{o}/{n}/releases/tags/{tag}")
        assets = [a for a in (r.json().get("assets", []) if r.status_code == 200 else [])
                  if a.get("name", "").endswith(".apk")]
        tmp = builder.BUILDS / ".download"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        if assets:
            got = await cx.get(assets[0]["browser_download_url"])
            if got.status_code == 200 and got.content[:2] == b"PK":
                (tmp / assets[0]["name"]).write_bytes(got.content)
        if not list(tmp.glob("**/*.apk")) and authed:
            await _run([gh, "run", "download", str(run["id"]), "--repo", f"{o}/{n}", "--dir", str(tmp)],
                       builder.BUILDS, 300)
    apks = list(tmp.glob("**/*.apk"))
    if not apks:
        return (f"GitHub built it, but I couldn't download the APK. He can get it from {run['html_url']} "
                "(Artifacts at the bottom).")
    saved = _save_apk(apks[0], label)
    shutil.rmtree(tmp, ignore_errors=True)
    return f"GitHub built it. The APK is downloaded to {saved} ({saved.stat().st_size // 1024} KB)."
