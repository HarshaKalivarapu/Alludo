"""Merge LLM relatedness scores into a final ranking and test it on warm guesses.

Final order: LLM score (0-10) first, embedding score as the tie-breaker, then (if present)
the LLM's own reordering of the top REORDER_TOP words. Words outside
the pool count as UNPOOLED_SCORE, and so do pooled words the LLM rated below it: a 0 means
"no boost", so those keep their embedding order instead of sinking to the very bottom.
Two guards run last: look-alikes of the secret that the LLM scored low leave the top
REORDER_TOP, and blocked/hidden words (data/filters/) go after every other word.

Each guess list gets two scores out of 1:
  green     - share of guesses ranked in the top 300 (Contexto's green zone)
  closeness - mean of 1 - ln(rank)/ln(vocab size); 1.0 = every guess at #1, random ~0.09

Usage: python pipeline/llm_rerank.py <run> classroom hockey raisin
Reads  data/llm/<run>/<secret>/scores_*.txt ("word<TAB>score" lines), data/llm/probes.json
Writes data/llm/<run>/<secret>/ranking.json
"""
import difflib
import json
import math
import sys
from pathlib import Path

import numpy as np

from llm_pool import ENGINE, LLM, Ranker
from words import read_filter, to_vocab

BASELINE = "glove840"
UNPOOLED_SCORE = 0.5
GREEN = 300
# Final pass: the LLM reorders the current top REORDER_TOP words by association strength.
REORDER_TOP = 500
REORDER_FILE = "top_reordered.txt"
# Look-alike guard: the listwise reorder (and spelling-sensitive embeddings) can mistake a
# word spelled like the secret for the secret itself ("hokey" came out #1 for hockey). Such
# words are moved to just after the top REORDER_TOP unless the LLM's own pointwise score
# says they are really related (teach for teacher).
LOOKALIKE_SCORE = 6
LOOKALIKE_RATIO = 0.8
# Slurs (blocked) and swears/sexual terms (hidden) must never surface as hints or in the
# closest-words list, so both go to the very end of every ranking.
FILTERED = read_filter("blocked") | read_filter("hidden")


def read_scores(folder, index):
    scores = {}
    for path in sorted(folder.glob("scores_*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.replace(",", "\t").split("\t")
            if len(parts) < 2:
                continue
            word = to_vocab(parts[0], index)
            try:
                value = float(parts[1])
            except ValueError:
                continue
            if word is not None:
                scores[word] = max(UNPOOLED_SCORE, min(10.0, value))
    return scores


def ranks_from(order):
    ranks = np.empty(len(order), dtype=np.int64)
    ranks[order] = np.arange(1, len(order) + 1)
    return ranks


def final_order(ranker, secret, scores, folder=None):
    s = ranker.index[secret]
    llm = np.full(len(ranker.vocab), UNPOOLED_SCORE)
    for w, v in scores.items():
        llm[ranker.index[w]] = v
    llm[s] = np.inf
    # lexsort sorts by the last key first: LLM score, then embedding score.
    order = np.lexsort((-ranker.scores(ENGINE, secret), -llm))
    reordered = folder / REORDER_FILE if folder else None
    if reordered and reordered.exists():
        order = apply_reorder(order, reordered.read_text(encoding="utf-8").split(), ranker.index)
    filtered = np.zeros(len(order), dtype=bool)
    filtered[[ranker.index[w] for w in FILTERED if w in ranker.index]] = True
    filtered[s] = False
    at_end = filtered[order]
    order = np.concatenate([order[~at_end], order[at_end]])
    if scores:
        # Without LLM scores (embeddings-only fallback) there is nothing to tell a real
        # relative from a look-alike, so the guard only runs on scored words.
        order = demote_lookalikes(order, secret, llm, ranker.vocab)
    return order


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def looks_alike(secret, word):
    if difflib.SequenceMatcher(None, secret, word).ratio() >= LOOKALIKE_RATIO:
        return True
    # The ratio misses one changed letter in a short word (cart/card is 0.75).
    return len(secret) <= 5 and edit_distance(secret, word) <= 1


def demote_lookalikes(order, secret, llm, vocab):
    """Move low-scored look-alikes of the secret out of the top REORDER_TOP to just after it.

    The next words in line move up, so the top REORDER_TOP stays full.
    """
    head, rest = order[1:REORDER_TOP + 1], order[REORDER_TOP + 1:]
    alike = np.array([llm[i] < LOOKALIKE_SCORE and looks_alike(secret, vocab[i]) for i in head], dtype=bool)
    n = int(alike.sum())
    return np.concatenate([order[:1], head[~alike], rest[:n], head[alike], rest[n:]])


def apply_reorder(order, words, index):
    """Replace the top of `order` (after the secret) with the LLM's own ordering of it.

    Only words that were already in that top slice are accepted; any the LLM dropped keep
    their previous relative order right after the ones it placed.
    """
    head = [int(i) for i in order[1:REORDER_TOP + 1]]
    allowed = set(head)
    placed = []
    for w in words:
        i = index.get(w.strip().lower())
        if i in allowed and i not in placed:
            placed.append(i)
    placed_set = set(placed)
    head = placed + [i for i in head if i not in placed_set]
    return np.concatenate([order[:1], head, order[REORDER_TOP + 1:]])


def summarize(ranks):
    n = len(ranks)
    return {
        "green": float(np.mean([r <= GREEN for r in ranks])),
        "closeness": float(np.mean([1 - math.log(r) / math.log(40000) for r in ranks])),
        "median": int(np.median(ranks)),
        "n": n,
    }


def main():
    run, secrets = sys.argv[1], sys.argv[2:]
    run_dir = LLM / run
    ranker = Ranker()
    vocab, index = ranker.vocab, ranker.index
    probes = json.loads((LLM / "probes.json").read_text())
    totals = {}

    for secret in secrets:
        folder = run_dir / secret
        scores = read_scores(folder, index) if folder.exists() else {}
        if not scores:
            print(f"\n=== {secret}: not scored yet, skipped")
            continue
        pool = json.loads((folder / "pool.json").read_text())["pool"]
        missing = [w for w in pool if w not in scores]
        order = final_order(ranker, secret, scores, folder)
        (folder / "ranking.json").write_text(json.dumps([vocab[i] for i in order[:2000]]))

        ranks = {
            "contexto-like": ranks_from(np.argsort(-ranker.scores(BASELINE, secret))),
            "embeddings": ranks_from(np.argsort(-ranker.scores(ENGINE, secret))),
            f"embeddings+{run}": ranks_from(order),
        }
        print(f"\n=== {secret}: {len(scores)} scored, {len(missing)} of {len(pool)} pool words unscored")
        for who, words in probes.get(secret, {}).items():
            known = [(w, to_vocab(w, index)) for w in words]
            unknown = [w for w, v in known if not v]
            known = [(w, v) for w, v in known if v]
            print(f"  guesses by {who}" + (f" (not in vocab: {', '.join(unknown)})" if unknown else ""))
            for name, r in ranks.items():
                rs = [int(r[index[v]]) for _, v in known]
                totals.setdefault((who, name), []).extend(rs)
                s = summarize(rs)
                print(f"    {name:<18} green {s['green']:.2f}  closeness {s['closeness']:.2f}  median {s['median']}")
                print("      " + " ".join(f"{w}:{x}" for (w, _), x in zip(known, rs)))

    print("\n=== all words combined")
    for (who, name), rs in totals.items():
        s = summarize(rs)
        print(f"  {who:<7} {name:<18} green {s['green']:.2f}  closeness {s['closeness']:.2f}  median {s['median']}  ({s['n']} guesses)")


if __name__ == "__main__":
    main()
