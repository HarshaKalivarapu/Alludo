"""Daily production: turn the schedule into published answer keys.

Each run:
  1. LLM work. In date order, every scheduled game from today to --horizon days ahead whose
     LLM ranking isn't finished gets the LLM steps (llm_score.run_secret) in data/llm/prod/.
     One model per secret word, because scores from two models don't mix: the model is
     written to <secret>/model.txt once it has brainstormed. New words take the first model
     in ROTATION that still has quota today; a word bound to a used-up model waits for the
     next day. The run stops when every model is used up, or at --max-requests or
     --max-minutes.
  2. Keys. Every finished game gets its LLM answer key. Every unfinished game due within
     --fallback-days gets an embeddings-only key (engine ENGINE, no LLM), marked fallback
     so a finished LLM key can replace it before the game's day. A game whose day has come
     and whose key is in KV is frozen: people may be mid-game, and the site caches keys.
  3. Output. Only keys that differ from the manifest (what KV holds) go into
     web/.data/kv-prod.json. --upload sends them with wrangler and records them in
     <state-dir>/manifest.json; without it the manifest stays as is, so the file keeps
     listing everything not yet uploaded.

--remote-state pulls schedule and manifest from KV ("state:<name>") at the start and
pushes the manifest back after an upload: the Actions runner starts empty every day.

The schedule holds the answers and the owner, who plays too, reads the Actions log, so all
output goes through a filter that turns every scheduled word into "#<game>".

Usage: python pipeline/produce.py [--remote-state] [--upload] [--max-requests 40]
                                  [--max-minutes 40] [--horizon 120] [--status]
"""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

import prod_state
from llm_pool import ENGINE, LLM, Ranker
from llm_rerank import REORDER_FILE, final_order, read_scores
from llm_score import MODELS, Budget, BudgetReached, Gemini, QuotaExhausted, run_secret
from prod_state import DEFAULT_STATE, WEB, game_number, read_json, site_config, write_json

PROD = LLM / "prod"
ROTATION = ["gemini-38", "gemini-36"]
HORIZON = 120
FALLBACK_DAYS = 2
# Two models x 20 free requests a day.
MAX_REQUESTS = 40
# Batches should be scheduled ~5 weeks before the current one ends (docs/PLAN.md).
SCHEDULE_WARN_DAYS = 35
KV_OUT = WEB / ".data" / "kv-prod.json"


class Masked(io.TextIOBase):
    """A stdout/stderr wrapper that replaces scheduled words with their game number."""

    def __init__(self, stream, labels):
        self.stream = stream
        self.labels = labels
        words = sorted(labels, key=len, reverse=True)
        # Letters-only boundaries, so "brainstorm_<word>.txt" is masked too.
        self.pattern = re.compile(r"(?<![a-z])(" + "|".join(map(re.escape, words)) + r")(?![a-z])", re.I) if words else None

    def write(self, text):
        if self.pattern:
            text = self.pattern.sub(lambda m: self.labels[m.group(1).lower()], text)
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


RAW = sys.stdout


def say(*parts):
    # produce.py's own messages name games by number only, so they skip the mask: a
    # scheduled word can also be an ordinary word in a message ("#12 ends with ...").
    print(*parts, file=RAW, flush=True)


def today_in(tz):
    try:
        return datetime.now(ZoneInfo(tz)).date()
    except Exception:  # no tz database (Windows without tzdata): UTC-5 is close enough
        return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def bound_model(secret):
    path = PROD / secret / "model.txt"
    return path.read_text().strip() if path.exists() else None


def progress(secret):
    """(finished, scored chunks, total chunks, brainstormed) from the files on disk."""
    folder = PROD / secret
    chunks = len(list(folder.glob("chunk_*.txt")))
    scored = len(list(folder.glob("scores_*.txt")))
    return (folder / REORDER_FILE).exists(), scored, chunks, (PROD / f"brainstorm_{secret}.txt").exists()


def llm_work(todo, ranker, args):
    """Run the LLM steps for `todo` (schedule entries, date order) until quota or budget runs out."""
    PROD.mkdir(parents=True, exist_ok=True)
    usage_path = PROD / "usage.json"
    usage = read_json(usage_path, {})
    budget = Budget(args.max_requests, time.time() + 60 * args.max_minutes)
    rubric = (LLM / "rubric.txt").read_text(encoding="utf-8")
    clients, spent = {}, set()
    try:
        for entry in todo:
            secret = entry["word"]
            run = bound_model(secret) or next((m for m in ROTATION if m not in spent), None)
            if run is None:
                say("every model is used up for today")
                break
            if run in spent:
                say(f"#{entry['game']}: waits for {run} (used up today)")
                continue
            if run not in clients:
                model, pause, _ = MODELS[run]
                clients[run] = Gemini(model, pause, usage.setdefault(run, {"requests": 0, "input_tokens": 0, "output_tokens": 0}), budget)
            say(f"#{entry['game']} ({entry['date']}, {entry['tier']}): {run}")
            try:
                run_secret(clients[run], PROD, secret, rubric, ranker, MODELS[run][2])
            except BudgetReached as e:
                print(f"  stopped: {e}")
                break
            except QuotaExhausted as e:  # also ModelOverloaded: leave that model for today
                print(f"  {e}")
                spent.add(run)
            finally:
                if progress(secret)[3] and not bound_model(secret):
                    (PROD / secret).mkdir(parents=True, exist_ok=True)
                    (PROD / secret / "model.txt").write_text(run)
                write_json(usage_path, usage)
    finally:
        write_json(usage_path, usage)
        say(f"LLM requests sent this run: {budget.sent}")


def answer_key(ranker, secret, llm):
    folder = PROD / secret
    order = final_order(ranker, secret, read_scores(folder, ranker.index), folder) if llm else final_order(ranker, secret, {})
    ranks = np.empty(len(order), dtype="<u2")
    ranks[order] = np.arange(1, len(order) + 1)
    assert ranks[ranker.index[secret]] == 1
    return ranks.tobytes()


def main():
    parser = argparse.ArgumentParser(description="Produce and publish answer keys.")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--horizon", type=int, default=HORIZON, help="days ahead to do LLM work for")
    parser.add_argument("--fallback-days", type=int, default=FALLBACK_DAYS)
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS)
    parser.add_argument("--max-minutes", type=float, default=40, help="stop starting LLM requests after this")
    parser.add_argument("--kv-out", type=Path, default=KV_OUT)
    parser.add_argument("--today", type=date.fromisoformat, help="pretend it is this date (testing)")
    parser.add_argument("--upload", action="store_true", help="put the keys into the remote KV")
    parser.add_argument("--remote-state", action="store_true", help="pull/push state files from/to KV")
    parser.add_argument("--status", action="store_true", help="only print progress")
    args = parser.parse_args()

    if args.remote_state:
        missing = prod_state.pull(args.state_dir, ["schedule", "manifest"])
        if "schedule" in missing:
            sys.exit("no state:schedule in KV yet: run schedule.py --remote-state first")
    schedule = read_json(args.state_dir / "schedule.json", None)
    if not schedule:
        sys.exit(f"no schedule in {args.state_dir}: run pipeline/schedule.py first")
    manifest = read_json(args.state_dir / "manifest.json", {})
    labels = {}
    for e in schedule:
        labels.setdefault(e["word"], f"#{e['game']}")
    sys.stdout, sys.stderr = Masked(sys.stdout, labels), Masked(sys.stderr, labels)

    launch, tz = site_config()
    today = args.today or today_in(tz)
    wrong = [e["game"] for e in schedule if game_number(date.fromisoformat(e["date"]), launch) != e["game"]]
    if wrong:
        say(f"warning: {len(wrong)} scheduled games don't match LAUNCH_DATE {launch} (first: #{wrong[0]})")
        if args.upload:
            sys.exit("refusing to upload: the site would serve these keys on the wrong days")
    games = sorted(schedule, key=lambda e: e["date"])
    day = lambda e: date.fromisoformat(e["date"])
    frozen = lambda e: day(e) <= today and str(e["game"]) in manifest

    if args.status:
        print_status(games, manifest, today, args.horizon)
        return

    ranker = Ranker()
    site_vocab = json.loads((WEB / "src" / "words.json").read_text(encoding="utf-8"))["vocab"]
    if site_vocab != ranker.vocab:
        msg = f"data/vocab.json ({len(ranker.vocab)} words) differs from web/src/words.json ({len(site_vocab)})"
        if args.upload:
            sys.exit(f"refusing to upload: {msg}; rebuild words.json and deploy the site first")
        say(f"warning: {msg}; keys built now won't match the deployed site")
    if len(ranker.vocab) >= 2 ** 16:
        sys.exit("vocabulary too large for uint16 ranks")
    # The embedding caches must line up with the vocabulary, or every ranking is garbage.
    if any(m.shape[0] != len(ranker.vocab) for m, _ in ranker.models.values()):
        sys.exit("embedding caches don't match data/vocab.json: rebuild them (and the embeddings release)")

    # A game whose LLM key is already in KV is done, even if its LLM files live on another machine
    # (a local run vs. the Actions cache): redoing it would only spend quota.
    published = lambda e: str(e["game"]) in manifest and not manifest[str(e["game"])].get("fallback")
    todo = [
        e for e in games
        if today <= day(e) <= today + timedelta(days=args.horizon)
        and not frozen(e) and not published(e) and not progress(e["word"])[0]
    ]
    say(f"today {today}: {len(todo)} games within {args.horizon} days still need LLM work")
    if todo and args.max_requests > 0:
        llm_work(todo, ranker, args)

    entries, records = [], {}
    for e in games:
        g = str(e["game"])
        finished = progress(e["word"])[0]
        if frozen(e):
            continue
        if finished:
            engine = f"llm:{bound_model(e['word']) or 'unknown'}"
        elif day(e) <= today + timedelta(days=args.fallback_days):
            if g in manifest and not manifest[g].get("fallback"):
                continue  # an LLM key is in KV but its files are gone (cache lost): keep it
            engine = ENGINE
        else:
            continue
        key = answer_key(ranker, e["word"], finished)
        digest = hashlib.sha256(key).hexdigest()
        if manifest.get(g, {}).get("sha256") == digest:
            continue
        entries.append({"key": f"rank:{g}", "value": base64.b64encode(key).decode(), "base64": True})
        records[g] = {"date": e["date"], "engine": engine, "fallback": not finished, "sha256": digest, "vocab": len(ranker.vocab)}
        say(f"key #{g} ({e['date']}): {'fallback ' + engine if not finished else engine}")

    args.kv_out.parent.mkdir(parents=True, exist_ok=True)
    args.kv_out.write_text(json.dumps(entries))
    say(f"wrote {args.kv_out} ({len(entries)} new or changed keys)")

    if args.upload and entries:
        try:
            path = args.kv_out.resolve().relative_to(WEB.resolve()).as_posix()
        except ValueError:
            path = str(args.kv_out.resolve())
        prod_state.wrangler("kv", "bulk", "put", path, "--binding", "GAMES", "--remote")
        manifest.update(records)
        write_json(args.state_dir / "manifest.json", manifest)
        say(f"uploaded {len(entries)} keys")
        if args.remote_state:
            prod_state.push(args.state_dir, ["manifest"])
            say("pushed manifest to KV")
    elif entries:
        say("not uploaded (no --upload): the manifest is unchanged")
    print_status(games, manifest, today, args.horizon)


def print_status(games, manifest, today, horizon):
    """Counts plus the runway: how many days from today already have an LLM key published."""
    ahead = [e for e in games if today <= date.fromisoformat(e["date"]) <= today + timedelta(days=horizon)]
    done = sum(progress(e["word"])[0] for e in ahead)
    started = sum(1 for e in ahead if not progress(e["word"])[0] and progress(e["word"])[3])
    runway = 0
    for e in (e for e in games if date.fromisoformat(e["date"]) >= today):
        m = manifest.get(str(e["game"]))
        if not m or m.get("fallback"):
            break
        runway += 1
    # Past fallback games are frozen for good; only today's and later ones can still improve.
    fallbacks = sorted(int(g) for g, m in manifest.items() if m.get("fallback") and date.fromisoformat(m["date"]) >= today)
    last = games[-1]
    lines = [
        f"status {today}: {len(ahead)} games in the next {horizon} days, {done} LLM-finished, {started} in progress",
        f"  LLM keys published for the next {runway} days; fallback keys in KV: {fallbacks or 'none'}",
        f"  schedule ends with #{last['game']} on {last['date']}",
    ]
    left = (date.fromisoformat(last["date"]) - today).days
    if left < SCHEDULE_WARN_DAYS:
        lines.append(f"  only {left} scheduled days left: run schedule.py for the next batch")
    say("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("```\n" + "\n".join(lines) + "\n```\n")


if __name__ == "__main__":
    main()
