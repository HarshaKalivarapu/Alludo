"""Check rankings against human relatedness ratings, across the whole range.

For each rated pair (a, b), b's in-game rank is computed with a as the secret word,
and the reverse; the Spearman correlation between human score and rank is reported.
"Middle" keeps only pairs humans rated as loosely related (the middle half of
scores), which is roughly where ranks 300-5000 live.

Usage: python pipeline/benchmark.py glove840 numberbatch~f "numberbatch+qwen3~f"
"""
import sys
from pathlib import Path

import numpy as np
from wordfreq import zipf_frequency

from compare import COMMON_ZIPF, score
from embeddings import load, load_vocab

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "data" / "raw" / "bench"
SETS = {
    "MEN (relatedness, 3000 pairs)": "EN-MEN-TR-3k.txt",
    "MTurk-771 (relatedness)": "EN-MTurk-771.txt",
    "WS-353-REL (relatedness)": "EN-WS-353-REL.txt",
    "SimLex-999 (strict similarity)": "EN-SIMLEX-999.txt",
}


def spearman(x, y):
    rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
    return np.corrcoef(rx, ry)[0, 1]


def main():
    names = sys.argv[1:]
    vocab = load_vocab()
    index = {w: i for i, w in enumerate(vocab)}
    models = {p: load(p, vocab) for name in names for p in name.partition("~")[0].split("+")}
    rarity = np.array([max(0.0, COMMON_ZIPF - zipf_frequency(w, "en")) for w in vocab])

    for title, filename in SETS.items():
        pairs = []
        for line in (BENCH / filename).read_text().splitlines():
            a, b, s = line.split()
            if a in index and b in index and a != b:
                pairs.append((index[a], index[b], float(s)))
        human = np.array([s for _, _, s in pairs])
        middle = (human > np.percentile(human, 25)) & (human < np.percentile(human, 75))
        print(f"\n{title}: {len(pairs)} pairs in vocab")
        print(f"{'model':<24}{'all':<8}{'middle':<8}{'median rank of top-quarter pairs'}")
        secrets = sorted({i for a, b, _ in pairs for i in (a, b)})
        for name in names:
            # Rank every vocabulary word for each secret involved, once.
            rank_of = {}
            for s in secrets:
                order = np.argsort(-score(name, models, s, rarity))
                ranks = np.empty(len(vocab), dtype=np.int32)
                ranks[order] = np.arange(1, len(vocab) + 1)
                rank_of[s] = ranks
            # Average the two directions; a lower rank means more related.
            ranks = np.array([(rank_of[a][b] + rank_of[b][a]) / 2 for a, b, _ in pairs])
            top_quarter = human >= np.percentile(human, 75)
            print(
                f"{name:<24}{spearman(human, -ranks):<8.2f}{spearman(human[middle], -ranks[middle]):<8.2f}"
                f"{np.median(ranks[top_quarter]):.0f}"
            )


if __name__ == "__main__":
    main()
