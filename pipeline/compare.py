"""Rank the vocabulary for real Contexto answers under each model and compare.

Usage: python pipeline/compare.py glove numberbatch bge qwen3 numberbatch+qwen3
A name joined with "+" is an ensemble: the average of each model's z-scored similarity.
A "~f" suffix adds a penalty for rare words so obscure terms don't crowd the top.
Writes data/compare/results.json and prints overlap with Contexto's top lists.
"""
import json
import sys
from pathlib import Path

import numpy as np
from wordfreq import zipf_frequency

from embeddings import load, load_vocab

ROOT = Path(__file__).resolve().parent.parent
GAMES = [100, 400, 800, 1200]
TOP_N = 500
OVERLAP_AT = [50, 100, 300, 500]
# Zipf 3 is about one use per million words; below it, each Zipf point costs
# RARITY_WEIGHT standard deviations of similarity.
COMMON_ZIPF = 3.0
RARITY_WEIGHT = 0.5


def zscores(matrix, known, secret_index):
    sims = matrix @ matrix[secret_index]
    real = sims[known]
    z = (sims - real.mean()) / real.std()
    # Words the model has never seen sink to the bottom instead of the middle.
    z[~known] = z[known].min()
    return z


def score(name, models, secret_index, rarity):
    base, _, flags = name.partition("~")
    parts = base.split("+")
    total = sum(zscores(*models[p], secret_index) for p in parts) / len(parts)
    if "f" in flags:
        total = total - RARITY_WEIGHT * rarity
        total[secret_index] = np.inf
    return total


def main():
    names = sys.argv[1:]
    vocab = load_vocab()
    index = {w: i for i, w in enumerate(vocab)}
    models = {p: load(p, vocab) for name in names for p in name.partition("~")[0].split("+")}
    rarity = np.array([max(0.0, COMMON_ZIPF - zipf_frequency(w, "en")) for w in vocab])

    results = {"models": names, "games": []}
    for game in GAMES:
        contexto = json.loads((ROOT / "data" / "contexto" / f"top_{game}.json").read_text())["words"]
        secret = contexto[0]
        # Compare only on words both vocabularies share, so neither side is
        # penalized for words the other can't rank at all.
        reference = [w for w in contexto if w in index]
        entry = {"game": game, "secret": secret, "contexto": contexto, "rankings": {}, "overlap": {}}
        for name in names:
            order = np.argsort(-score(name, models, index[secret], rarity))
            ours = [vocab[i] for i in order[:TOP_N]]
            rank_of = {vocab[i]: r + 1 for r, i in enumerate(order)}
            entry["rankings"][name] = ours
            entry["overlap"][name] = {
                k: len(set(ours[:k]) & set(reference[:k])) / k for k in OVERLAP_AT
            }
            # Where Contexto's top-10 words land in our ranking.
            entry.setdefault("contexto_top10_in_ours", {})[name] = {
                w: rank_of[w] for w in reference[:10]
            }
        results["games"].append(entry)

    out = ROOT / "data" / "compare" / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1))

    header = f"{'model':<24}" + "".join(f"@{k:<6}" for k in OVERLAP_AT)
    for entry in results["games"]:
        print(f"\n#{entry['game']} {entry['secret']}  (overlap with Contexto's top-K)")
        print(header)
        for name in names:
            row = entry["overlap"][name]
            print(f"{name:<24}" + "".join(f"{row[k]:<7.0%}" for k in OVERLAP_AT))
    print("\nmean overlap across games")
    print(header)
    for name in names:
        means = [np.mean([e["overlap"][name][k] for e in results["games"]]) for k in OVERLAP_AT]
        print(f"{name:<24}" + "".join(f"{m:<7.0%}" for m in means))


if __name__ == "__main__":
    main()
