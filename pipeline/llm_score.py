"""Run the LLM steps (brainstorm, score the pool, reorder the top) against an API model.

Every result is written to disk as soon as it arrives and finished steps are skipped,
so a run that hits a rate limit or daily quota can simply be started again later.

Usage: python pipeline/llm_score.py <run> <secrets...> [--max-requests 20]
       e.g. python pipeline/llm_score.py gemini-36 classroom hockey raisin
Needs  GEMINI_API_KEY in .env or the environment (never printed)
Writes data/llm/<run>/brainstorm_<secret>.txt, <secret>/scores_<n>.txt,
       <secret>/top_reordered.txt, usage.json
The production runner (pipeline/produce.py) calls run_secret() directly.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

from llm_pool import LLM, ROOT, Ranker, build_pool
from llm_rerank import REORDER_FILE, REORDER_TOP, final_order, read_scores

# run name -> (model id, seconds between requests, words per scoring request).
# Flash allows only 20 requests/day, so its requests are big; Gemma allows 14,400/day but
# generates slowly and only 16K input tokens/min, so its requests are small and spaced out.
# Flash models allow 5 requests/min, hence 12 s apart.
MODELS = {
    "gemini": ("gemini-3.8-flash", 5, 1600),
    "gemini-lite": ("gemini-3.5-flash-lite", 5, 1600),
    "gemma": ("gemma-4-31b-it", 10, 400),
    "gemini-36": ("gemini-3.6-flash", 12, 1600),
    "gemini-38": ("gemini-3.8-flash", 12, 1600),
}
# Per-minute 429s say how long to wait and cost nothing, so they get several tries.
RATE_RETRIES = 5
# A 503 "overloaded" reply still counts against the 20-requests/day quota, and overload
# tends to last minutes, so server errors get few tries with long waits in between.
SERVER_RETRIES = 2
SERVER_BACKOFF = 90  # seconds before the first retry; doubles after that
# Default cap on requests per CLI run: one model's whole free day.
MAX_REQUESTS = 20

BRAINSTORM_PROMPT = """You are generating data for a word-association guessing game (like Contexto). The secret word is "{secret}". Players guess single words and are told how close each guess is.

List about 400 single English words that a player might plausibly guess and that should feel at least somewhat "warm" for "{secret}". Cover a wide range of connection types, not just synonyms:
- synonyms and near-synonyms
- same category / sibling concepts
- parts, contents, materials, ingredients
- people, roles, and actions involved
- places, events, and processes connected to it
- things commonly found together with it, everyday scenes, routines, sounds, textures, tastes
- other meanings or senses of the word, idioms, cultural associations
- broad categories it belongs to (for example food, sport, room)
- looser associations a typical person would still consider related

Rules: single lowercase words only (no spaces or hyphens), base form (singular nouns, plain verbs), no proper nouns, team names, or brand names, no duplicates. Order from most to least related. Output one word per line and nothing else."""

SCORE_PROMPT = """{rubric}

SECRET word: "{secret}"

Score every word below. Output exactly one line per word, formatted as word<TAB>score with an integer score from 0 to 10, in the same order, and no other text.

{words}"""

REORDER_PROMPT = """{rubric}

SECRET word: "{secret}"

Below are the {count} words currently ranked closest to the secret word. Reorder ALL of them from most to least strongly associated with the secret word, the way an ordinary person thinks of it. Put first what it is or is made from and its most typical uses, companions, settings and traits. Put lower generic members of the same category with no special link, technical or process words, and rare words.

Judge each word on its own strength of association. Do not group words by theme or keep clusters of similar words together: a rare or technical word goes near the bottom even when it sits on the same theme as a top word. The broad category the secret belongs to (like "food" or "sport") is a natural early guess and belongs in the upper part of the list.

Output every word exactly once, one per line, and no other text.

{words}"""


def read_env_key(name):
    # Locally the key sits in .env; GitHub Actions passes it as an environment variable.
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name and value.strip():
                return value.strip().strip('"').strip("'")
    if os.environ.get(name):
        return os.environ[name]
    sys.exit(f"{name} is missing from .env and the environment")


class QuotaExhausted(Exception):
    """This model can't be used again today (its daily free quota is gone)."""


class ModelOverloaded(QuotaExhausted):
    """The model kept answering 5xx; try it again on a later run instead of burning quota."""


class BudgetReached(QuotaExhausted):
    """This run's own request or time limit is reached, for every model."""


class Budget:
    """Caps what one run may spend across all models: a safety net against retry loops."""

    def __init__(self, max_requests=None, deadline=None):
        self.max_requests = max_requests
        self.deadline = deadline  # time.time() value, or None
        self.sent = 0

    def take(self):
        if self.max_requests is not None and self.sent >= self.max_requests:
            raise BudgetReached(f"request budget of {self.max_requests} used")
        if self.deadline is not None and time.time() >= self.deadline:
            raise BudgetReached("time limit reached")
        self.sent += 1


class Gemini:
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, model, pause, usage, budget=None):
        self.model = model
        self.pause = pause
        self.key = read_env_key("GEMINI_API_KEY")
        self.usage = usage
        self.budget = budget or Budget(MAX_REQUESTS)
        self.last = 0.0

    def _post(self, body):
        # Every attempt, failed or not, may count against the daily quota, so each one
        # goes through the budget; only the time since the previous request is waited out.
        self.budget.take()
        time.sleep(max(0.0, self.last + self.pause - time.time()))
        self.last = time.time()
        return requests.post(
            self.URL.format(model=self.model),
            headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
            json=body,
            timeout=600,
        )

    def ask(self, prompt):
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        rate_tries = server_tries = 0
        while True:
            try:
                resp = self._post(body)
            except (requests.Timeout, requests.ConnectionError) as e:
                server_tries += 1
                if server_tries > SERVER_RETRIES:
                    raise ModelOverloaded(f"{self.model}: no answer ({type(e).__name__})")
                print(f"    {type(e).__name__}, retrying")
                time.sleep(30)
                continue
            self.usage["requests"] += 1
            if resp.status_code == 429:
                text = resp.text
                if "PerDay" in text or "per day" in text.lower():
                    raise QuotaExhausted(f"{self.model}: daily free-tier quota used up; rerun after it resets")
                rate_tries += 1
                if rate_tries > RATE_RETRIES:
                    raise ModelOverloaded(f"{self.model}: still rate limited after {RATE_RETRIES} waits")
                delay = re.search(r'"retryDelay":\s*"(\d+)', text)
                wait = int(delay.group(1)) + 1 if delay else 30 * rate_tries
                print(f"    rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                server_tries += 1
                if server_tries > SERVER_RETRIES:
                    raise ModelOverloaded(f"{self.model}: server error {resp.status_code}, giving up for this run")
                wait = SERVER_BACKOFF * 2 ** (server_tries - 1)
                print(f"    server error {resp.status_code}, waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            meta = data.get("usageMetadata", {})
            self.usage["input_tokens"] += meta.get("promptTokenCount", 0)
            self.usage["output_tokens"] += meta.get("candidatesTokenCount", 0) + meta.get("thoughtsTokenCount", 0)
            parts = data["candidates"][0]["content"].get("parts", [])
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def parse_scores(text, index):
    scores = {}
    for line in text.splitlines():
        parts = re.split(r"\t|\s{2,}|,|:", line.strip())
        if len(parts) < 2:
            continue
        word, value = parts[0].strip().lower(), parts[-1].strip()
        if re.fullmatch(r"\d+(\.\d+)?", value):
            scores[word] = float(value)
    return scores


def score_chunk(llm, secret, words, rubric, index, out_path):
    got = {}
    for _ in range(3):
        todo = [w for w in words if w not in got]
        if not todo:
            break
        text = llm.ask(SCORE_PROMPT.format(rubric=rubric, secret=secret, words="\n".join(todo)))
        got.update({w: v for w, v in parse_scores(text, index).items() if w in todo})
    missing = [w for w in words if w not in got]
    out_path.write_text("\n".join(f"{w}\t{int(got[w])}" for w in words if w in got), encoding="utf-8")
    return missing


def is_finished(run_dir, secret):
    # The reorder is the last step and only runs once every chunk is scored.
    return (run_dir / secret / REORDER_FILE).exists()


def run_secret(llm, run_dir, secret, rubric, ranker, chunk):
    """Run every missing step for one secret word. Raises QuotaExhausted (or a subclass)
    when the model, or this run's budget, can't take another request; finished steps are
    on disk, so calling it again later picks up where it stopped."""
    brainstorm_path = run_dir / f"brainstorm_{secret}.txt"
    if not brainstorm_path.exists():
        print(f"{secret}: brainstorming")
        words = [w.strip().lower() for w in llm.ask(BRAINSTORM_PROMPT.format(secret=secret)).splitlines()]
        words = [w for w in words if re.fullmatch(r"[a-z]+", w)]
        brainstorm_path.write_text("\n".join(words), encoding="utf-8")
        print(f"  {len(words)} words")
    folder = run_dir / secret
    if not (folder / "pool.json").exists():
        build_pool(run_dir, secret, ranker, chunk)
    chunks = sorted(folder.glob("chunk_*.txt"), key=lambda p: int(p.stem.split("_")[1]))
    for chunk_path in chunks:
        out_path = folder / chunk_path.name.replace("chunk_", "scores_")
        if out_path.exists():
            continue
        words = chunk_path.read_text(encoding="utf-8").split()
        missing = score_chunk(llm, secret, words, rubric, ranker.index, out_path)
        print(f"  {secret} {chunk_path.stem}: {len(words) - len(missing)}/{len(words)} scored")
    reorder_path = folder / REORDER_FILE
    if not reorder_path.exists():
        order = final_order(ranker, secret, read_scores(folder, ranker.index))
        top = [ranker.vocab[i] for i in order[1:REORDER_TOP + 1]]
        prompt = REORDER_PROMPT.format(rubric=rubric, secret=secret, count=len(top), words="\n".join(top))
        text = llm.ask(prompt)
        reorder_path.write_text(text.strip(), encoding="utf-8")
        print(f"  {secret}: top {len(top)} reordered")


def load_usage(path):
    return json.loads(path.read_text()) if path.exists() else {"requests": 0, "input_tokens": 0, "output_tokens": 0}


def main():
    parser = argparse.ArgumentParser(description="Run the LLM steps for some secret words.")
    parser.add_argument("run", choices=sorted(MODELS))
    parser.add_argument("secrets", nargs="+")
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS)
    args = parser.parse_args()
    run_dir = LLM / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    usage_path = run_dir / "usage.json"
    usage = load_usage(usage_path)
    model, pause, chunk = MODELS[args.run]
    llm = Gemini(model, pause, usage, Budget(args.max_requests))
    rubric = (LLM / "rubric.txt").read_text(encoding="utf-8")
    ranker = Ranker()

    try:
        for secret in args.secrets:
            run_secret(llm, run_dir, secret, rubric, ranker, chunk)
            usage_path.write_text(json.dumps(usage))
    except QuotaExhausted as e:
        print(f"Stopped: {e}")
    finally:
        usage_path.write_text(json.dumps(usage))
        print(f"usage so far: {usage}")


if __name__ == "__main__":
    main()
