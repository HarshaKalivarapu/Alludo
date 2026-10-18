"""Append countries and big cities to the vocabulary so they are valid guesses.

build_vocab.py drops capitalized dictionary entries, so "canada" or "tokyo" can't be guessed.
Sources (GeoNames, https://download.geonames.org/export/dump/):
  data/raw/geonames/countryInfo.txt   every country and territory
  data/raw/geonames/cities15000.zip   cities with 15k+ people; kept if >= MIN_POPULATION
                                      or a national capital (feature code PPLC)
Only names that are one plain word once lowercased and stripped of accents are kept
("bogota", not "buenos aires" or "xi'an").

New words go at the END of data/vocab.json, so existing ids (and the embedding caches and
answer keys aligned to them) stay valid; embeddings.load() fills in the missing rows.
Safe to re-run: words already in the vocabulary are skipped.

Usage: python pipeline/add_places.py
Writes data/vocab.json (appended) and data/places.json ({"countries", "other", "cities"}: added words)
"""
import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path

from wordfreq import zipf_frequency

ROOT = Path(__file__).resolve().parent.parent
GEONAMES = ROOT / "data" / "raw" / "geonames"
VOCAB = ROOT / "data" / "vocab.json"
PLACES = ROOT / "data" / "places.json"
MIN_POPULATION = 1_000_000
WORD_RE = re.compile(r"^[a-z]{2,}$")
# One-word names people actually type that GeoNames lists under longer names (United
# States, The Netherlands) or not at all (UK nations, continents).
OTHER = [
    "america", "usa", "uk", "britain", "england", "scotland", "wales", "korea", "netherlands",
    "holland", "burma", "macedonia", "bosnia", "palestine", "europe", "africa", "asia", "oceania",
]


def plain(name):
    # Fold accents (Bogotá -> bogota) rather than using GeoNames' asciiname column,
    # which spells umlauts the German way (Köln -> koeln, Ürümqi -> uerumqi). Only accent
    # marks are dropped, so a curly apostrophe (Xi’an) still disqualifies the name.
    decomposed = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    return folded if WORD_RE.match(folded) else None


def countries():
    names = []
    for line in (GEONAMES / "countryInfo.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        names.append(line.split("\t")[4])
    return names


def cities():
    with zipfile.ZipFile(GEONAMES / "cities15000.zip") as z:
        lines = io.TextIOWrapper(z.open("cities15000.txt"), encoding="utf-8")
        rows = [line.rstrip("\n").split("\t") for line in lines]
    return [r[1] for r in rows if int(r[14] or 0) >= MIN_POPULATION or r[7] == "PPLC"]


def main():
    vocab = json.loads(VOCAB.read_text())
    have = set(vocab)
    places = json.loads(PLACES.read_text()) if PLACES.exists() else {}

    added = {}
    for kind, names in [("countries", countries()), ("other", OTHER), ("cities", cities())]:
        new = {w for w in map(plain, names) if w and w not in have}
        # Most familiar first, matching the frequency order of the rest of the vocabulary.
        new = sorted(new, key=lambda w: (-zipf_frequency(w, "en"), w))
        have.update(new)
        added[kind] = new
        places[kind] = places.get(kind, []) + new

    vocab += added["countries"] + added["other"] + added["cities"]
    VOCAB.write_text(json.dumps(vocab))
    PLACES.write_text(json.dumps(places, indent=1))
    print(
        f"added {len(added['countries'])} countries, {len(added['other'])} other place names "
        f"and {len(added['cities'])} cities; "
        f"vocab now {len(vocab)} words"
    )


if __name__ == "__main__":
    main()
