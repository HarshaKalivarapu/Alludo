"""Turn rankings into what the site's API serves.

web/src/words.json  vocabulary (ids are list positions), inflection -> base-form map,
                    the stopword list ("too common" replies), and the blocked list
                    (slurs the API rejects; data/filters/blocked.txt)
web/.data/kv.json   `wrangler kv bulk put` input: one key per game, "rank:<n>", holding
                    a little-endian uint16 array where entry i is vocab word i's rank
                    (1 = the secret word)

Usage: python pipeline/build_games.py <run> <first_game> secret1 secret2 ...
Games are numbered consecutively from <first_game> in the order given.
"""
import base64
import json
import os
import sys
from pathlib import Path

import numpy as np
from lemminflect import getAllInflections

from llm_pool import LLM, ROOT, Ranker
from llm_rerank import final_order, read_scores
from words import read_filter

os.environ.setdefault("NLTK_DATA", str(ROOT / "data" / "raw" / "nltk_data"))
WEB = ROOT / "web"


def build_words(vocab):
    from nltk.corpus import stopwords

    index = {w: i for i, w in enumerate(vocab)}
    forms = {}
    for word in vocab:
        for inflections in getAllInflections(word).values():
            for form in inflections:
                form = form.lower()
                # A form that is itself a vocabulary word keeps its own meaning.
                if form.isalpha() and form not in index and form not in forms:
                    forms[form] = word
    stop = sorted(set(stopwords.words("english")) - set(index))
    # Blocked words stay in the vocabulary so ids don't shift; the API refuses them (and
    # their inflected forms, which map to them through `forms`).
    blocked = sorted(read_filter("blocked") & set(index))
    out = WEB / "src" / "words.json"
    words = {"vocab": vocab, "forms": forms, "stop": stop, "blocked": blocked}
    out.write_text(json.dumps(words, separators=(",", ":")))
    print(f"words.json: {len(vocab)} words, {len(forms)} inflected forms, {len(stop)} stopwords, {len(blocked)} blocked")


def main():
    run, first_game, secrets = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
    ranker = Ranker()
    build_words(ranker.vocab)

    entries = []
    for offset, secret in enumerate(secrets):
        game = first_game + offset
        scores = read_scores(LLM / run / secret, ranker.index)
        order = final_order(ranker, secret, scores, LLM / run / secret)
        ranks = np.empty(len(order), dtype="<u2")
        ranks[order] = np.arange(1, len(order) + 1)
        assert ranks[ranker.index[secret]] == 1
        entries.append({"key": f"rank:{game}", "value": base64.b64encode(ranks.tobytes()).decode(), "base64": True})
        print(f"game {game}: {secret} ({len(scores)} LLM scores)")

    out = WEB / ".data" / "kv.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entries))
    print(f"wrote {out} ({len(entries)} games)")


if __name__ == "__main__":
    main()
