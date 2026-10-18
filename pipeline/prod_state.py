"""Production state shared by schedule.py and produce.py: site settings and the state files.

The state files (schedule.json, used.json, manifest.json) live in a state dir, by default
data/answers/, which is never committed. GitHub Actions runners start empty, so the durable
copy lives in the GAMES KV namespace next to the answer keys, under "state:<name>". The site
only ever reads "rank:<n>" keys, so these are never served. Values go through wrangler run
from web/ (it knows the binding) and are never echoed: the schedule holds the answers.
"""
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DEFAULT_STATE = ROOT / "data" / "answers"
POOL = DEFAULT_STATE / "pool.json"
STATE_FILES = {"schedule": "schedule.json", "used": "used.json", "manifest": "manifest.json"}
REMOTE = ["--remote"]


def site_config():
    """LAUNCH_DATE (game 1) and TIME_ZONE from web/wrangler.jsonc, as the site sees them."""
    text = (WEB / "wrangler.jsonc").read_text(encoding="utf-8")
    config = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.M))
    return date.fromisoformat(config["vars"]["LAUNCH_DATE"]), config["vars"]["TIME_ZONE"]


def game_number(day, launch):
    return (day - launch).days + 1


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def wrangler(*args):
    # shutil.which finds npx.cmd on Windows; output is captured so values never reach the log.
    npx = shutil.which("npx") or "npx"
    result = subprocess.run([npx, "wrangler", *args], cwd=WEB, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"wrangler {args[0]} {args[1] if len(args) > 1 else ''} failed:\n{result.stderr[-2000:]}")
    return result.stdout


def pull(state_dir, names, where=REMOTE):
    """Copy state:<name> from KV into the state dir. Returns the names KV doesn't have."""
    missing = []
    for name in names:
        try:
            out = wrangler("kv", "key", "get", f"state:{name}", "--binding", "GAMES", "--text", *where).strip()
        except RuntimeError as e:
            # The remote API answers a key that was never written with a 404.
            if "404" not in str(e):
                raise
            out = ""
        if not out or out == "Value not found":
            missing.append(name)
            continue
        write_json(Path(state_dir) / STATE_FILES[name], json.loads(out))
    return missing


def push(state_dir, names, where=REMOTE):
    for name in names:
        path = Path(state_dir) / STATE_FILES[name]
        # --path, not an inline value: wrangler echoes inline values.
        wrangler("kv", "key", "put", f"state:{name}", "--path", str(path.resolve()), "--binding", "GAMES", *where)
