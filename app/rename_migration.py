"""One-time move of an older install to the Roktar name.

Runs at every start and does nothing once there is nothing left to move. It never deletes
anything: files and folders are renamed only when the new name is still free, and the memory
notes are copied to memory-before-roktar/ before their text is changed.

This is the only file that still knows the old name. Delete it once Roktar has started once
on every PC that ran the old version.
"""
import json
import os
import shutil
from pathlib import Path

OLD = "Jar" + "vis"            # the previous name, kept in one place
NEW = "Roktar"


def _swap(text: str) -> str:
    """Old name -> Roktar, keeping the case style (OLD / Old / old)."""
    dotted = ((".".join(OLD.upper()), ".".join(NEW.upper())), (".".join(OLD.lower()), ".".join(NEW.lower())))
    for a, b in dotted + ((OLD.upper(), NEW.upper()), (OLD, NEW), (OLD.lower(), NEW.lower())):
        text = text.replace(a, b)
    return text


def _move(src: Path, dst: Path, done: list):
    if src.exists() and not dst.exists():
        try:
            src.rename(dst)
            done.append(f"{src.name} -> {dst.name}")
        except OSError as e:
            done.append(f"could not rename {src} ({e})")


def run(app: Path, root: Path) -> list:
    done = []
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    o, n = OLD.lower(), NEW.lower()

    # 1. settings: file name, and a saved display name that still says the old one
    _move(app / f"{o}.json", app / f"{n}.json", done)
    cfg = app / f"{n}.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            if str(data.get("name", "")).strip().lower() == o:
                data["name"] = NEW.upper()
                cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                done.append("saved name -> ROKTAR")
        except (OSError, ValueError):
            pass

    # 2. the phone's TLS certificate (same key, new file names, so the pinned phone app keeps working)
    sec = root / "secrets"
    for ext in ("crt", "key"):
        _move(sec / f"{o}-lan.{ext}", sec / f"{n}-lan.{ext}", done)
    _move(root / "logs" / f"{o}.log", root / "logs" / f"{n}.log", done)

    # 3. build folders: Desktop builds and the downloaded Android/JDK toolkits
    builds, tools = home / "Desktop" / f"{NEW}-Builds", home / f"{NEW}-Tools"
    _move(home / "Desktop" / f"{OLD}-Builds", builds, done)
    _move(home / f"{OLD}-Tools", tools, done)
    if builds.is_dir():
        for proj in builds.iterdir():
            if not proj.is_dir():
                continue
            _move(proj / f".{o}-job.json", proj / f".{n}-job.json", done)
            _move(proj / f".{o}-logs", proj / f".{n}-logs", done)
            lp = proj / "local.properties"
            try:
                t = lp.read_text(encoding="utf-8") if lp.exists() else ""
                if f"{OLD}-Tools" in t:
                    lp.write_text(t.replace(f"{OLD}-Tools", f"{NEW}-Tools"), encoding="utf-8")
                    done.append(f"{proj.name}/local.properties -> {NEW}-Tools")
            except OSError:
                pass

    # 4. memory notes: back up once, then swap the name inside every Markdown note
    mem, backup = root / "memory", root / "memory-before-roktar"
    if mem.is_dir():
        notes = [p for p in mem.rglob("*.md") if OLD.lower() in p.read_text(encoding="utf-8", errors="ignore").lower()]
        if notes:
            if not backup.exists():
                shutil.copytree(mem, backup)
                done.append("memory backed up to memory-before-roktar/")
            for p in notes:
                try:
                    p.write_text(_swap(p.read_text(encoding="utf-8")), encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
            done.append(f"name changed in {len(notes)} memory notes")
    return done
