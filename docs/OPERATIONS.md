# Alludo operations runbook

How the daily puzzles get made and published. Decisions and background are in `docs/PLAN.md`.
Nothing here prints answer words: logs name games as `#<game>`. Don't open `data/answers/`,
`data/llm/prod/`, or the KV `state:*` values if you want to keep playing unspoiled.

## The pieces

| Piece | What it does |
|---|---|
| `pipeline/schedule.py` | Picks the next batch (default 91 days) from `data/answers/pool.json`: 10/20/40/30 tier mix, no word used in the last 548 days, no theme two days running, at most two hard days in a row. Writes `schedule.json` and `used.json`. |
| `pipeline/produce.py` | The daily runner: LLM work for upcoming games, answer keys, fallback keys, upload. |
| `pipeline/prod_state.py` | Keeps `schedule.json`, `used.json`, `manifest.json` in KV as `state:schedule`, `state:used`, `state:manifest` (the site only reads `rank:<n>`). |
| `.github/workflows/daily.yml` | Runs `produce.py --remote-state --upload` every day at 09:15 UTC (after the midnight-Pacific quota reset), and on demand. |
| `.github/workflows/deploy.yml` | Deploys `web/` to Cloudflare Pages on every push to `main` that touches `web/**`. |

## One-time setup

1. **Push the repo** to https://github.com/HarshaKalivarapu/Alludo (a release needs at least one commit).
2. **Cloudflare API token**: dash.cloudflare.com > My Profile > API Tokens > Create Token > Custom,
   with *Account > Workers KV Storage > Edit* and *Account > Cloudflare Pages > Edit*.
   The account ID is shown by `npx wrangler whoami` (run in `web/`).
3. **Secrets** (each command asks for the value; nothing is echoed):
   ```
   gh secret set GEMINI_API_KEY
   gh secret set CLOUDFLARE_API_TOKEN
   gh secret set CLOUDFLARE_ACCOUNT_ID
   ```
4. **Embeddings release.** The runner only needs the three caches the ranker loads
   (about 130 MB; Qwen3 would take ~40 min to rebuild on a runner). In PowerShell, from the repo root:
   ```
   Compress-Archive -Force -Path data/emb/numberbatch.npz, data/emb/qwen3.npz, data/emb/glove840.npz -DestinationPath $env:TEMP/emb.zip
   gh release create embeddings-v1 $env:TEMP/emb.zip --title "Embedding caches v1" --notes "data/emb caches for the daily job"
   ```
   Releases of a private repo stay private. Whenever `data/vocab.json` or the caches change,
   upload a new release (`embeddings-v2`, ...) and bump `EMBEDDINGS_TAG` in `daily.yml`.
   `produce.py` refuses to run if the caches don't match the vocabulary.
5. **Launch date.** Set `LAUNCH_DATE` in `web/wrangler.jsonc` to the real game-1 date, make sure
   `web/src/words.json` matches `data/vocab.json` (`produce.py` refuses to upload otherwise), commit and
   push (`deploy.yml` deploys). Game numbers come from `LAUNCH_DATE`, so set it before scheduling.
6. Check the Pages project's production branch is `main` (Pages > alludo > Settings), or deploys
   land as previews.

## Starting the first batch

```
.venv/Scripts/python pipeline/schedule.py --start <LAUNCH_DATE> --remote-state
```
This pulls any state already in KV (none the first time), writes the batch, and pushes
`state:schedule` and `state:used`. It prints only counts (tier mix, themes, words still unused per
tier). Then start the job without waiting for the cron: `gh workflow run daily.yml`.

At about 4 requests per word and 40 free requests a day (20 on gemini-3.8-flash, then 20 on
gemini-3.6-flash), the job finishes about 10 words a day, so a 91-day batch takes ~10 days.
Launch once a week or so is ready (see "Checking progress").

**Next batches**: the status lines warn once fewer than 35 scheduled days are left. Then run
```
.venv/Scripts/python pipeline/schedule.py --remote-state
```
It continues the day after the last scheduled game. If it says a tier ran out, the pool needs
more words in that tier (the 1,044-word pool covers about 7 batches before the 18-month rule
starts recycling; `hard` and `medium-hard` run out first).

## What the daily job does

1. Downloads the embedding caches, restores yesterday's LLM files (`data/llm/prod/`, Actions cache),
   pulls `state:schedule` and `state:manifest` from KV.
2. For games from today to 120 days ahead without a finished LLM ranking, in date order: runs the
   LLM steps. Each word stays on the model that brainstormed it (`model.txt`); new words take
   gemini-38 until its daily quota is gone, then gemini-36. It stops when both are used up, after
   40 requests, or after 40 minutes.
3. Builds an answer key for every finished game, and an embeddings-only **fallback** key for any
   unfinished game due within 2 days. A fallback is replaced if the LLM ranking finishes before the
   game's day. Once a game's day has come and its key is in KV, it never changes.
4. Writes only new or changed keys to `web/.data/kv-prod.json`, uploads them with
   `wrangler kv bulk put --remote`, then records them in `state:manifest`.
5. Saves the LLM files back to the Actions cache, even if the run failed.

Expect ~30-45 minutes on days with LLM work while a batch is being filled, a few minutes
otherwise. That fits the 2,000 free minutes a month for a private repo.

## Checking progress

- The run page (Actions > Daily puzzles > a run) shows the status in its summary: games finished
  and in progress, how many days ahead have LLM keys published, any fallback keys still in play,
  and when the schedule ends. `gh run list --workflow daily.yml` and `gh run view <id> --log` work too.
- Locally (needs `npx wrangler login`): `.venv/Scripts/python pipeline/produce.py --remote-state --status`.

## When the quota runs out

That is the normal end of a run: the model's 429 "per day" reply makes the job switch models, and
when both are used up it builds keys from what is finished and stops. Words half-done resume the
next day on the same model. A model that keeps answering 503 "overloaded" gets two retries
(90 s, then 180 s) and is skipped for the rest of the run, since those replies also count against
the quota. If the LLM falls behind, games due within 2 days get fallback keys, so the site never
runs out of puzzles while the job keeps running.

## Pausing

- Stop the daily job: `gh workflow disable daily.yml` (resume with `gh workflow enable daily.yml`),
  or Actions > Daily puzzles > "..." > Disable workflow.
- Keys already uploaded keep being served. Fallbacks are only made 2 days ahead, so before a long
  pause cover the gap with keys that need no quota, e.g. locally:
  `.venv/Scripts/python pipeline/produce.py --remote-state --upload --max-requests 0 --fallback-days 30`
  (LLM keys can still replace these later, up to each game's day).
- Keys only, no LLM, from GitHub: `gh workflow run daily.yml -f max_requests=0`.

## Running it locally

`.venv/Scripts/python pipeline/produce.py --remote-state --upload` uses the key in `.env` and your
wrangler login. Don't run it while the Action is running, and note that local LLM files
(`data/llm/prod/`) and the Actions cache don't sync: a word started in one place starts over in
the other. Without `--upload` it only writes `web/.data/kv-prod.json` and leaves the manifest alone.

## Changing the vocabulary after launch

Past keys are frozen and sized to the vocabulary they were built with. Adding words means a new
`words.json`, a new embeddings release, and future keys rebuilt (`produce.py` rebuilds the unfrozen
ones automatically because their hashes change). Old games would not know the new words, so
decide how the site should handle that before changing the vocabulary after launch.
