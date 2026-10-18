"""Build the guessable vocabulary: common English base-form words.

Output: data/vocab.json  -> list of words ordered by frequency (most common first).
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NLTK_DIR = ROOT / "data" / "raw" / "nltk_data"
os.environ.setdefault("NLTK_DATA", str(NLTK_DIR))

import nltk
from lemminflect import getAllLemmas
from wordfreq import top_n_list, zipf_frequency

TARGET_SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else 40000
WORD_RE = re.compile(r"^[a-z]{2,}$")


def ensure_nltk():
    for pkg, path in [("wordnet", "corpora/wordnet"), ("stopwords", "corpora/stopwords")]:
        try:
            nltk.data.find(path)
        except LookupError:
            nltk.download(pkg, download_dir=str(NLTK_DIR), quiet=True)


def is_own_lemma(word):
    lemmas = getAllLemmas(word)
    return not lemmas or any(word in forms for forms in lemmas.values())


def main():
    ensure_nltk()
    from nltk.corpus import stopwords, wordnet as wn

    # Lemma names keep their case, so proper nouns ("Paris", "Witherspoon")
    # are excluded by only accepting entries that are already lowercase.
    wordnet_lemmas = {
        lemma.name()
        for synset in wn.all_synsets()
        for lemma in synset.lemmas()
        if lemma.name().islower() and "_" not in lemma.name()
    }
    stop = set(stopwords.words("english"))

    # Hunspell lists modern words WordNet lacks (quinoa, whiteboard, crunchy).
    # Its entries are stems, but irregular forms (children, ran) also appear,
    # so those are kept only when lemminflect agrees they are base forms.
    hunspell_path = ROOT / "data" / "raw" / "hunspell" / "en_US.dic"
    hunspell = set()
    for line in hunspell_path.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
        stem = line.split("/")[0].strip()
        if stem.islower() and is_own_lemma(stem):
            hunspell.add(stem)

    # Nationalities and languages (Japanese, Canadian) are capitalized in both
    # dictionaries but make natural guesses; WordNet tags them as adjectives.
    demonyms = {
        lemma.name().lower()
        for synset in wn.all_synsets(pos="a")
        for lemma in synset.lemmas()
        if lemma.name()[:1].isupper() and "_" not in lemma.name()
    }
    dictionary = wordnet_lemmas | hunspell | demonyms

    vocab = []
    for word in top_n_list("en", 400000):
        if not WORD_RE.match(word) or word in stop:
            continue
        if word not in dictionary:
            # Drops plurals/inflections as well as names, slang, and fragments.
            continue
        vocab.append(word)
        if len(vocab) >= TARGET_SIZE:
            break

    out = ROOT / "data" / "vocab.json"
    out.write_text(json.dumps(vocab))
    print(f"{len(vocab)} words; last word zipf={zipf_frequency(vocab[-1], 'en'):.2f} ({vocab[-1]})")
    print("sample:", vocab[:10], vocab[20000:20010])


if __name__ == "__main__":
    main()
