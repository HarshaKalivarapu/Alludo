"""Load each candidate model as a unit-normalized matrix aligned to data/vocab.json.

Rows for words a model doesn't know are all zeros; `known` marks which rows are real.
Results are cached in data/emb/<name>.npz so each model is only built once; words appended
to the vocabulary later get their rows built and added to the cache on the next load.

Usage: python pipeline/embeddings.py glove numberbatch bge qwen3
"""
import gzip
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CACHE = ROOT / "data" / "emb"
os.environ.setdefault("NLTK_DATA", str(RAW / "nltk_data"))


def load_vocab():
    return json.loads((ROOT / "data" / "vocab.json").read_text())


def _from_text_vectors(lines, vocab):
    index = {w: i for i, w in enumerate(vocab)}
    matrix, known = None, np.zeros(len(vocab), dtype=bool)
    for line in lines:
        word, _, rest = line.rstrip().partition(" ")
        i = index.get(word)
        if i is None or known[i]:
            continue
        vec = np.array(rest.split(" "), dtype=np.float32)
        if matrix is None:
            matrix = np.zeros((len(vocab), vec.size), dtype=np.float32)
        if vec.size != matrix.shape[1]:
            continue  # a few GloVe 840B lines are malformed
        matrix[i], known[i] = vec, True
    return matrix, known


def _from_zip(path, member, vocab, header=False):
    with zipfile.ZipFile(path) as z, z.open(member) as f:
        lines = (raw.decode("utf-8", errors="ignore") for raw in f)
        if header:
            next(lines)
        return _from_text_vectors(lines, vocab)


def build_glove(vocab):
    # GloVe 6B: Wikipedia + Gigaword news, 300 dims, lowercase. Classic static embeddings.
    return _from_zip(RAW / "glove" / "glove.6B.zip", "glove.6B.300d.txt", vocab)


def build_glove840(vocab):
    # GloVe 840B: Common Crawl web text, cased. Lowercase keys are used.
    return _from_zip(RAW / "glove" / "glove.840B.300d.zip", "glove.840B.300d.txt", vocab)


def build_fasttext(vocab):
    # fastText trained on Common Crawl (600B tokens), cased, "<rows> <dims>" header.
    return _from_zip(RAW / "fasttext" / "crawl-300d-2M.vec.zip", "crawl-300d-2M.vec", vocab, header=True)


def build_word2vec(vocab):
    # word2vec GoogleNews (Semantle's model), binary format: a "<rows> <dims>"
    # header, then each word followed by a space and <dims> float32 values.
    index = {w: i for i, w in enumerate(vocab)}
    with open(RAW / "word2vec" / "GoogleNews-vectors-negative300.bin", "rb") as f:
        rows, dims = map(int, f.readline().split())
        matrix = np.zeros((len(vocab), dims), dtype=np.float32)
        known = np.zeros(len(vocab), dtype=bool)
        for _ in range(rows):
            word = bytearray()
            while (ch := f.read(1)) != b" ":
                word += ch
            vec = np.frombuffer(f.read(4 * dims), dtype=np.float32)
            i = index.get(word.decode("utf-8", errors="ignore").strip())
            if i is not None and not known[i]:
                matrix[i], known[i] = vec, True
    return matrix, known


def build_numberbatch(vocab):
    # ConceptNet Numberbatch 19.08: word2vec + GloVe retrofitted onto the
    # ConceptNet knowledge graph. The first line is a "<rows> <dims>" header.
    with gzip.open(RAW / "numberbatch" / "numberbatch-en-19.08.txt.gz", "rt", encoding="utf-8") as f:
        next(f)
        return _from_text_vectors(f, vocab)


def _definition_texts(vocab):
    # A word on its own gives a sentence model little to go on, and it tends to
    # match on spelling. Adding the most common WordNet sense anchors meaning.
    from nltk.corpus import wordnet as wn

    texts = []
    for w in vocab:
        synsets = wn.synsets(w)
        texts.append(f"{w}: {synsets[0].definition()}" if synsets else w)
    return texts


def _build_sentence_model(model_id, vocab, use_definitions, **encode_kwargs):
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_id, device="cpu")
    texts = _definition_texts(vocab) if use_definitions else vocab
    matrix = model.encode(
        texts, batch_size=128, show_progress_bar=True, convert_to_numpy=True, **encode_kwargs
    )
    return matrix.astype(np.float32), np.ones(len(vocab), dtype=bool)


BUILDERS = {
    "glove": build_glove,
    "glove840": build_glove840,
    "fasttext": build_fasttext,
    "word2vec": build_word2vec,
    "numberbatch": build_numberbatch,
    "bge": lambda v: _build_sentence_model("BAAI/bge-base-en-v1.5", v, use_definitions=True),
    "bge_word": lambda v: _build_sentence_model("BAAI/bge-base-en-v1.5", v, use_definitions=False),
    "qwen3": lambda v: _build_sentence_model("Qwen/Qwen3-Embedding-0.6B", v, use_definitions=True),
}


def _build(name, words):
    matrix, known = BUILDERS[name](words)
    if matrix is None:  # a text-vector model that knows none of these words
        return None, known
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)
    return matrix, known


def _save(path, matrix, known):
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(path, matrix=matrix.astype(np.float16), known=known)


def load(name, vocab=None):
    vocab = vocab or load_vocab()
    path = CACHE / f"{name}.npz"
    if path.exists():
        data = np.load(path)
        matrix, known = data["matrix"].astype(np.float32), data["known"]
        if len(known) >= len(vocab):
            return matrix, known
        # The vocabulary only grows by appending (pipeline/add_places.py), so the cached rows
        # still line up and only the new words need building, not the whole model again.
        start = time.time()
        new = vocab[len(known):]
        extra, extra_known = _build(name, new)
        if extra is None:
            extra = np.zeros((len(new), matrix.shape[1]), dtype=np.float32)
        matrix = np.vstack([matrix, extra])
        known = np.concatenate([known, extra_known])
        _save(path, matrix, known)
        print(f"{name}: appended {len(new)} words ({extra_known.sum()} known), {time.time() - start:.0f}s")
        return matrix, known
    start = time.time()
    matrix, known = _build(name, vocab)
    _save(path, matrix, known)
    print(f"{name}: {known.sum()}/{len(vocab)} words, {matrix.shape[1]} dims, {time.time() - start:.0f}s")
    return matrix, known


if __name__ == "__main__":
    vocab = load_vocab()
    for name in sys.argv[1:]:
        load(name, vocab)
