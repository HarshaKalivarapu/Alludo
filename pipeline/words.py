"""Map any typed or generated word onto the vocabulary (plurals and inflections to base form),
and read the word filter lists in data/filters/."""
from pathlib import Path

from lemminflect import getAllLemmas


def to_vocab(word, index):
    """Return the vocabulary form of `word`, or None if it has none."""
    word = word.strip().lower()
    if word in index:
        return word
    for forms in getAllLemmas(word).values():
        for lemma in forms:
            if lemma in index:
                return lemma
    return None


FILTERS = Path(__file__).resolve().parent.parent / "data" / "filters"


def read_filter(name):
    """Words in data/filters/<name>.txt (blocked = slurs, hidden = swears and sexual terms)."""
    path = FILTERS / f"{name}.txt"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return {w.strip() for w in lines if w.strip() and not w.startswith("#")}
