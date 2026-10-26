"""Claude sign-in: check it, and open the sign-in page when it's missing.

Roktar's brain is Claude Code, the same one jaredrhod's backtalk uses. It runs
on your Claude plan, so there is no API key. The CLI ships inside the
claude-agent-sdk package; no separate install is needed.

Run directly (the launcher does this):  python signin.py
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"


def cli_path() -> str | None:
    """The Claude Code program bundled with the SDK, else one on PATH."""
    try:
        import claude_agent_sdk
        b = Path(claude_agent_sdk.__file__).parent / "_bundled" / ("claude.exe" if IS_WIN else "claude")
        if b.exists():
            return str(b)
    except ImportError:
        pass
    return shutil.which("claude")


def status() -> dict:
    cli = cli_path()
    if not cli:
        return {"loggedIn": False, "error": "Claude Code program not found"}
    try:
        r = subprocess.run([cli, "auth", "status", "--json"], capture_output=True,
                           text=True, timeout=90)
        out = (r.stdout or "").strip()
        data = json.loads(out[out.find("{"):]) if "{" in out else {}
        if not data:
            data = {"loggedIn": False, "error": (r.stderr or out or "no answer")[:300]}
        return data
    except Exception as e:  # never crash the launcher over a status check
        return {"loggedIn": False, "error": str(e)[:300]}


def login(new_window: bool = False):
    """Opens the Claude sign-in page in the browser. You click Authorize."""
    cli = cli_path()
    if not cli:
        raise RuntimeError("Claude Code program not found")
    args = [cli, "auth", "login", "--claudeai"]
    if new_window and IS_WIN:
        return subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_CONSOLE)
    return subprocess.call(args)


if __name__ == "__main__":
    s = status()
    if s.get("loggedIn"):
        print("  Claude sign-in ............ OK")
        sys.exit(0)
    print()
    print("  ONE-TIME STEP: sign in to Claude.")
    print("  Your browser will open a Claude page. Sign in and click Authorize.")
    print("  (This is your own Claude account. Roktar never sees your password.)")
    print()
    try:
        login()
    except Exception as e:
        print(f"  Could not start the sign-in: {e}")
        sys.exit(1)
    s = status()
    if s.get("loggedIn"):
        print("  Claude sign-in ............ OK")
        sys.exit(0)
    print("  Sign-in did not complete. Roktar will still open and show a SIGN IN button.")
    sys.exit(1)
