"""Build each secret word's candidate pool for LLM scoring.

Pool = embedding top EMBED_TOP (the chosen engine) + GloVe 840B top CONTEXT_TOP (catches
context links like "sun-dried") + the LLM's brainstormed associations + the COMMON_TOP most
frequent words (broad early guesses like "food" or "game" that neither source reliably has).
The pool is shuffled and split into chunks so one scoring pass sees a mix of strong and
weak words.

Usage: python pipeline/llm_pool.py <run> classroom hockey raisin
Reads  data/llm/<run>/brainstorm_<secret>.txt
Writes data/llm/<run>/<secret>/pool.json and data/llm/<run>/<secret>/chunk_<n>.txt
"""
import json
import random
import sys
from pathlib import Path

import numpy as np
from wordfreq import zipf_frequency

from compare import COMMON_ZIPF, score
from embeddings import load, load_vocab
from words import to_vocab

ROOT = Path(__file__).resolve().parent.parent
LLM = ROOT / "data" / "llm"
ENGINE = "numberbatch+qwen3~f"
EMBED_TOP = 2000
CONTEXT_TOP = 500
COMMON_TOP = 1000
CHUNK = 1600  # big requests: the free tier allows only ~20 requests a day


class Ranker:
    """Loads the embedding models once and ranks the vocabulary for any secret word."""

    def __init__(self):
        self.vocab = load_vocab()
        self.index = {w: i for i, w in enumerate(self.vocab)}
        self.models = {p: load(p, self.vocab) for p in ["numberbatch", "qwen3", "glove840"]}
        self.rarity = np.array([max(0.0, COMMON_ZIPF - zipf_frequency(w, "en")) for w in self.vocab])

    def scores(self, name, secret):
        return score(name, self.models, self.index[secret], self.rarity)


def build_pool(run_dir, secret, ranker, chunk=CHUNK):
    vocab, index = ranker.vocab, ranker.index
    engine_order = np.argsort(-ranker.scores(ENGINE, secret))
    context_order = np.argsort(-ranker.scores("glove840", secret))
    brainstorm_raw = (run_dir / f"brainstorm_{secret}.txt").read_text(encoding="utf-8").split()
    brainstorm = {w for w in (to_vocab(x, index) for x in brainstorm_raw) if w}

    pool = {vocab[i] for i in engine_order[:EMBED_TOP]} | {vocab[i] for i in context_order[:CONTEXT_TOP]}
    added = brainstorm - pool
    pool |= brainstorm
    pool |= set(vocab[:COMMON_TOP])  # vocab is ordered by frequency
    pool.discard(secret)

    out = run_dir / secret
    out.mkdir(parents=True, exist_ok=True)
    words = sorted(pool)
    random.Random(secret).shuffle(words)
    chunks = [words[i:i + chunk] for i in range(0, len(words), chunk)]
    for n, chunk in enumerate(chunks):
        (out / f"chunk_{n}.txt").write_text("\n".join(chunk), encoding="utf-8")
    (out / "pool.json").write_text(json.dumps({"brainstorm": sorted(brainstorm), "pool": words}))
    print(
        f"{secret}: brainstorm {len(brainstorm_raw)} -> {len(brainstorm)} in vocab, "
        f"{len(added)} not already in embedding pools; pool {len(words)} in {len(chunks)} chunks"
    )
    return chunks


def main():
    run, secrets = sys.argv[1], sys.argv[2:]
    ranker = Ranker()
    for secret in secrets:
        build_pool(LLM / run, secret, ranker)


if __name__ == "__main__":
    main()
