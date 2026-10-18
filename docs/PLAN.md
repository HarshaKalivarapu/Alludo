# Alludo plan

A Contexto-style daily word game with better similarity rankings, for friends and clubs.
Non-commercial, and it must never cost money to run.

## How it works

- About 40k guessable base-form words (`data/vocab.json`, built by `pipeline/build_vocab.py`).
- For every secret word, the whole vocabulary is ranked ahead of time and stored as an answer
  key (one uint16 rank per word, ~80 KB). A guess is a lookup, so it is instant; no model runs
  while anyone plays.
- Ranking pipeline per secret word:
  1. Embeddings: ConceptNet Numberbatch + Qwen3-Embedding (word + WordNet definition), z-scored
     and averaged, with a penalty for rare words (`pipeline/compare.py`, engine `numberbatch+qwen3~f`).
  2. LLM brainstorm: ~400-550 words people associate with the secret.
  3. Pool: brainstorm + embedding top 2000 + GloVe 840B top 500 + the 1000 most common words.
  4. LLM scores every pool word 0-10 using `data/llm/rubric.txt` (everyday association: what it
     is made from, typical uses, companions, settings, traits; generic category siblings and
     process/technical words score lower).
  5. Order = LLM score, then embedding score. Pooled words scored 0 keep their embedding order.
  6. LLM reorders the top 500 by association strength (`top_reordered.txt`).
- Scripts: `pipeline/llm_score.py <run> <secrets...>` runs steps 2-6 against Gemini and resumes
  after quota errors; `pipeline/llm_rerank.py <run> <secrets...>` evaluates;
  `pipeline/build_games.py <run> <first_game> <secrets...>` writes `web/src/words.json` and
  `web/.data/kv.json`. Run outputs live in `data/llm/<run>/` (`haiku` = prototype, `gemini` = production).
- Evaluation: the user's own warm guesses in `data/llm/probes.json` ("you" is the fair test set;
  "claude" was written by the same model family that scores). Scores: green = share of guesses
  ranked <= 300; closeness = mean of 1 - ln(rank)/ln(40000).

## Results so far (user's 29 guesses on classroom, hockey, raisin)

| Version | green | closeness | median rank |
|---|---|---|---|
| Contexto-like (GloVe 840B) | 0.66 | 0.55 | 91 |
| Embeddings only | 0.66 | 0.57 | 81 |
| + Haiku scoring (old rubric) | 1.00 | 0.69 | 40 |
| + Haiku top-200 reorder | 1.00 | 0.72 | 22 |
| + Haiku top-500 reorder (current local games) | 1.00 | 0.74 | 19 |

Reorder lessons: a long listwise reorder made Haiku group words by theme (drought, arid, aridity
next to "dried" for raisin) and push broad categories (sport, food) out of green. Fixed by telling
it to judge each word on its own and by moving the broad category up to 6-7 in the rubric.
Remaining Haiku mistakes in the local games: "hokey" ranked #1 for hockey (look-alike spelling),
and generic fruits (peach, banana, kiwi) still at #33-46 for raisin. Planned: a look-alike guard
(drop words from the reordered top when they are spelling-close to the secret but scored low)
and the Gemini re-run.

User feedback after playing: hockey and classroom top lists now read well; raisin's top 60 still
has generic fruits (peach, persimmon) and drying-process words (desiccation, dehydrator), and is
missing cinnamon, bran, box, lunchbox. Cause: raisin was scored with the old rubric, so those words
never entered the top 200. Fix in progress: full re-run with the new rubric (see Next steps).

Other findings: Contexto's model is not any standard public one (best match GloVe 840B, rank
correlation ~0.6). Sentence models alone drift; Numberbatch alone surfaces obscure words.

## Decisions

- Name Alludo. Repo https://github.com/HarshaKalivarapu/Alludo (private).
- Hosting: Cloudflare **Pages** project `alludo` -> https://alludo.pages.dev (created, not yet
  deployed). Site in `web/public`, API in `web/functions/api/[[route]].ts` -> `web/src/api.ts`.
  KV namespace `GAMES` id `c53109b25c7f4167a3f1990ca7a67774`. Account: harshavk27@gmail.com.
  Wrangler is logged in on the user's laptop.
- LLM: Gemini 3.8 Flash, **free tier only** (never enable billing in AI Studio). Key in `.env`
  (gitignored) and later a GitHub secret. Free tier allows only ~20 requests/day, so requests are
  large (CHUNK = 1600 words; ~4 requests per secret word) and generation runs as a **daily**
  GitHub Actions job that resumes, starting ~5 weeks before the current batch ends. Days not
  scored in time fall back to embeddings-only rankings. No paid fallback.
- Free-tier limits on the user's AI Studio project "Alludo" (from its Rate limits page):
  3.8/3.7/3.6/3/2.5 Flash: 5 RPM, 250K TPM, 20 RPD each. 3.5 and 3.1 Flash-Lite: 15 RPM, 500 RPD.
  Gemma 4 31B/26B: 30 RPM, 16K TPM, 14.4K RPD. Limits are per model, reset midnight Pacific.
  Flash-Lite 3.5 was tested and is clearly weaker (user guesses green 0.86 vs 1.00).
- Answer difficulty: mostly harder words. Mix ~10% easy-medium (classroom, hockey), ~20% medium
  (raisin, volcano), ~40% medium-hard (bonsai, lantern), ~30% hard (echo, horizon). "Hard" = long
  path from first guesses: less common, abstract-ish, scattered associations, or a misleading
  neighbor cluster. No weekday pattern needed.
- Answer pool is AI-curated, not computed: 4 theme agents brainstorm candidates
  (`data/answers/spec.txt` holds the rules), 2 independent agents rate tiers, disagreements are
  dropped. Pool and schedule live in `data/answers/` (gitignored) and are never shown to the user
  (they play the game too).
- Batch production: the daily job publishes puzzles as they finish. Launch once ~1 week is ready;
  later batches start generating a few weeks before the current batch ends.
- The user will rename the folder to Alludo only at the very end of the project, to keep this
  conversation's context.
- Answers: 3-month batches; a used answer cannot repeat for 18 months; answer pool >= ~2,000
  common concrete nouns. Answers never go in the repo; only into KV.
- Every game's key is kept forever; past games are frozen; the server never serves future games.
- Daily reset at midnight America/New_York. Game 1 = LAUNCH_DATE in `web/wrangler.jsonc`
  (currently 2026-10-16 for local testing with classroom/hockey/raisin as games 1-3).
- Place names: countries and big cities are valid guesses; sports teams and brands are not.
- Look: Chalkboard palette (user's pick; Slate Dusk second), keeping green/yellow/red closeness
  colors and soft accents. Row outlines must use `outline` so they wrap the fill bar. Proposed
  (demo at https://claude.ai/artifact/9mZYDAQaPRtKBb5vDQghSq): the board starts as Slate Dusk and
  shifts with the player's BEST guess to a brick-red, amber, then green chalkboard, plus a solved
  state; ~1s fade, instant with reduced motion. Daily theme rotation was rejected as overkill.
  The board never moves back to a colder color. Chosen transition: "Single wave (softer)", a
  single water ripple from the guess's row that bends the board slightly (SVG displacement,
  amp 15, wavelength 130, 1.6s) with the new color's edge riding the ripple. Code lives in the
  demo https://claude.ai/artifact/UDfzSqrVhuP6QPPmVsGaCd. Approved "big guess" moment for the
  guess that reaches a new color (reference demo https://claude.ai/artifact/5ukGkKiJXR6BGmNfDUhXRs):
  the card lifts in place over the input box (scale 1.08, soft shadow, no border, input box's full
  height so it covers it completely); during the reveal the old guess's outline fades out while
  the card's fades in; it shrinks to row height as it settles; once it covers the input,
  the input quietly resets to its placeholder; the rank counts down from 40,000 while the card
  fills with the zone color; nothing else on the board moves until it settles (560ms) into the
  last-guess spot under the input, while the previous last guess fades out and the ranked list
  slides open; the list copy starts fading in 170ms before landing (280ms fade); then the wave.
  Ordinary guesses just appear. The game needs a "last guess" spot under the input (like Contexto).
- No login for v1; progress, stats and streaks live in localStorage.

## Running locally

- Python: `.venv` (Python 3.14, sentence-transformers, wordfreq, lemminflect, nltk, requests).
  After moving the folder, recreate it: `python -m venv --system-site-packages .venv` then
  `.venv/Scripts/python -m pip install sentence-transformers wordfreq lemminflect nltk requests`.
- Embedding caches are in `data/emb/*.npz` (gitignored; Qwen3 takes ~40 min on CPU to rebuild).
- Game: `cd web`, `npx wrangler kv bulk put .data/kv.json --binding GAMES --local`, then
  `npx wrangler pages dev --port 8788` and open http://localhost:8788.

## Logo

Spaced capitals "ALLUDO" (Unbounded 700, 0.26em spacing) centered over a small red/yellow/green
bar, with two soft arcs rising from the final O (drawn by app.js on the glyph's real center). The
arcs pulse once each time the board changes color. Options page:
https://claude.ai/artifact/17umdknpCtwETt1uoW5HUg

## Launch

- Game 1 = Monday 2026-10-19 (LAUNCH_DATE in web/wrangler.jsonc). First 91-day batch scheduled
  (games 1-91, through 2027-01-17) and stored in KV `state:schedule`/`state:used`.
- Continents stay valid guesses and are in the answer pool (europe, africa, asia, oceania).
- The owner wants no swear or sexual words at all: everything formerly "hidden" is now blocked
  (178 blocked words; hidden.txt is empty on purpose).
- Site pages: `/` home (today's card, stats), `/play` and `/play/<n>`, `/archive` (month calendar
  with solved / gave up / in progress / not played). Progress is per browser (localStorage).
- Local testing with past games: `npx wrangler pages dev --port 8788 --binding LAUNCH_DATE=2026-10-16`.
  On localhost only, the menu adds "Reset this game" (in a game) and "Reset all games" (home and
  calendar); they never appear on the live site.

## Launch-eve status (2026-10-18, evening)

- Live at https://alludo.pages.dev (deployed by GitHub Actions on push; secrets are set).
- Games #1-#3 have LLM answer keys in KV; #4 is half done. The daily job continues from
  2026-10-19 09:15 UTC once the `embeddings-v1` release exists.
- UI done: Chalkboard stages, big-guess moment, softer single wave, last-guess spot, logo with
  pulsing ripple, home page (today + Sunday-first week strip + dividers), calendar archive,
  back arrow, page-specific menu with Lucide icons, closest words only after a game ends.
- Model decision final: 3.8 + 3.6 Flash. Gemma matched quality but was too slow and crashed.
- Tab icon: chalk "A" (Unbounded Bold traced to a path) over the three-color bar; link preview
  og.png with the full wordmark. Both built by `pipeline/brand_assets.py` (fonts in data/raw/fonts).
- The daily job schedules the next batch itself when fewer than 35 days are left (pool in KV
  `state:pool`), and both workflows skip cleanly when setup is missing.

## Status (2026-10-18)

- Vocabulary 40,764 (764 places added by `pipeline/add_places.py`); 22 blocked words (API
  rejects them) and 156 hidden words (always ranked last); look-alike guard and team/brand rule
  are in. User still to confirm: keep continents (europe, africa, asia, oceania), and the
  blocked/hidden policy.
- Production is built: `pipeline/schedule.py`, `pipeline/produce.py`, `pipeline/prod_state.py`,
  `.github/workflows/daily.yml` and `deploy.yml`; runbook in `docs/OPERATIONS.md`. Not run for
  real yet: needs secrets, the embeddings release, and the real LAUNCH_DATE.

- Game look and effects are built into `web/public` (Chalkboard stages, big-guess moment,
  softer single wave, last-guess spot, outlines). Awaiting the user's play-test.
- Answer pool done: 1,044 words in `data/answers/pool.json` (gitignored).
- Model comparison: Haiku (prototype), Flash-Lite 3.5 (weaker, rejected), 3.6 Flash (good on
  classroom + hockey; raisin running), Gemma 4 31B (classroom + hockey scored; slow). 3.8 Flash's
  only run was deleted; the user chose not to re-run it.

## Next steps

1. Finish the model comparison (Gemma, 3.6 Flash raisin). User's decision: production uses
   3.8 + 3.6 Flash (40 requests/day, ~10 puzzles/day) unless Gemma clearly wins; Flash-Lite is
   dropped entirely. 3.7 Flash (another 20/day) is available if more speed is ever needed.
2. Look-alike guard (drop spelling-similar but unrelated words like "hokey" for hockey).
3. Vocabulary: add countries and big cities; add a slur/profanity filter; keep team and brand
   names out (the LLM leaked "whaler", "wings" for hockey). New words need embeddings appended.
4. Schedule generator from the pool (tier mix 10/20/40/30, 18-month no-repeat), stored in KV.
5. Daily GitHub Actions job + secrets (CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, GEMINI_API_KEY),
   embeddings stored as a GitHub Release asset.
6. First deploy to alludo.pages.dev with real games; upload keys with `--remote`.
7. Later ideas: class/club rooms with a host-chosen word, "why is this close?" notes after the game.
