"""Pick the next batch of secret words from the answer pool and give each a game and date.

A batch is usually 91 days (one quarter). Tier mix: 10% easy-medium, 20% medium, 40%
medium-hard, 30% hard, rounded by largest remainder so the counts add up to the batch length.
A word used within REPEAT_DAYS (18 months) of the batch start can't be picked. The order is a
seeded shuffle with two rules: no theme two days running (a run of foods feels samey), and
never more than two hard days in a row. The rules also hold across the join with the
previous batch.

Usage: python pipeline/schedule.py [--start 2026-11-01] [--days 91] [--first-game 1]
                                   [--seed s] [--state-dir data/answers] [--remote-state] [--dry-run]
                                   [--if-fewer-than 35]
  --start       defaults to the day after the last scheduled game, or LAUNCH_DATE if none
  --first-game  defaults to the game number the site gives --start (from LAUNCH_DATE)
  --remote-state  pull schedule/used (and the pool, if there's no local copy) from KV first and
                  push them back after (needs wrangler login, or CLOUDFLARE_API_TOKEN in CI)
  --if-fewer-than N  do nothing unless fewer than N scheduled days are left from today; the daily
                  job runs this so the next batch is scheduled without anyone remembering to
Reads  data/answers/pool.json, <state-dir>/schedule.json and used.json if present
Writes <state-dir>/schedule.json  [{game, date, word, tier}, ...] in game order
       <state-dir>/used.json      {word: [dates used]}
Prints counts only, never words: the owner plays the game too.
"""
import argparse
import random
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import prod_state
from prod_state import DEFAULT_STATE, POOL, game_number, read_json, site_config, write_json

TIERS = ["easy-medium", "medium", "medium-hard", "hard"]
MIX = {"easy-medium": 0.10, "medium": 0.20, "medium-hard": 0.40, "hard": 0.30}
REPEAT_DAYS = 548
MAX_HARD_RUN = 2
ATTEMPTS = 5000


def tier_counts(days):
    exact = {t: MIX[t] * days for t in TIERS}
    counts = {t: int(exact[t]) for t in TIERS}
    # Hand the leftover days to the tiers that lost the most to rounding down.
    for t in sorted(TIERS, key=lambda t: exact[t] - counts[t], reverse=True)[: days - sum(counts.values())]:
        counts[t] += 1
    return counts


def fits(seq, item):
    if seq and seq[-1]["theme"] == item["theme"]:
        return False
    if item["tier"] == "hard" and len(seq) >= MAX_HARD_RUN and all(s["tier"] == "hard" for s in seq[-MAX_HARD_RUN:]):
        return False
    return True


def arrange(picks, history, rng):
    """Order `picks` so every day fits the rules, continuing from `history` (earlier days).

    Greedy: each day takes a random word among those that still fit. A dead end (only
    words that break a rule are left) just restarts with a fresh shuffle; with 4 themes
    and 30% hard days that is rare, and the restarts stay seeded, so results repeat.
    """
    for _ in range(ATTEMPTS):
        left = picks[:]
        rng.shuffle(left)
        seq = list(history)
        while left:
            options = [i for i, p in enumerate(left) if fits(seq, p)]
            if not options:
                break
            seq.append(left.pop(rng.choice(options)))
        if not left:
            return seq[len(history):]
    sys.exit(f"could not arrange the batch under the theme/hard-day rules after {ATTEMPTS} tries")


def main():
    parser = argparse.ArgumentParser(description="Schedule the next batch of answers.")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--days", type=int, default=91)
    parser.add_argument("--first-game", type=int)
    parser.add_argument("--seed")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--pool", type=Path, default=POOL)
    parser.add_argument("--remote-state", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--if-fewer-than", type=int)
    args = parser.parse_args()

    if args.remote_state:
        # A local pool is the master copy (it's curated here); only fetch it where there's none,
        # as on a fresh GitHub Actions runner.
        names = ["schedule", "used"] + ([] if args.pool.exists() else ["pool"])
        missing = prod_state.pull(args.state_dir, names)
        print(f"pulled state from KV ({'none yet: ' + ', '.join(missing) if missing else ', '.join(names)})")
    pool = read_json(args.pool, None)
    if pool is None:
        sys.exit(f"answer pool not found: {args.pool}")
    schedule = read_json(args.state_dir / "schedule.json", [])
    used = read_json(args.state_dir / "used.json", {})
    for entry in schedule:  # a schedule entry always counts as a use, even if used.json lags
        if entry["date"] not in used.setdefault(entry["word"], []):
            used[entry["word"]].append(entry["date"])

    launch, tz = site_config()
    last = max(schedule, key=lambda e: e["game"]) if schedule else None
    if args.if_fewer_than is not None and last:
        today = datetime.now(ZoneInfo(tz)).date()
        left = (date.fromisoformat(last["date"]) - today).days + 1
        if left >= args.if_fewer_than:
            print(f"{left} scheduled days left (through game {last['game']}); no new batch needed yet")
            return
        print(f"only {left} scheduled days left: scheduling the next batch")
    start = args.start or (date.fromisoformat(last["date"]) + timedelta(days=1) if last else launch)
    first = args.first_game or (last["game"] + 1 if last and not args.start else game_number(start, launch))
    if first != game_number(start, launch):
        print(f"warning: the site's LAUNCH_DATE ({launch}) makes {start} game {game_number(start, launch)}, not {first}")
    taken_games = {e["game"] for e in schedule}
    taken_dates = {e["date"] for e in schedule}
    dates = [start + timedelta(days=d) for d in range(args.days)]
    games = list(range(first, first + args.days))
    if taken_games & set(games) or taken_dates & {d.isoformat() for d in dates}:
        sys.exit("this batch overlaps games or dates already in the schedule")

    cutoff = start - timedelta(days=REPEAT_DAYS)
    blocked = {w for w, ds in used.items() if any(date.fromisoformat(d) > cutoff for d in ds)}
    counts = tier_counts(args.days)
    rng = random.Random(args.seed or f"alludo:{start.isoformat()}")
    picks = []
    for tier in TIERS:
        eligible = sorted(w for w, v in pool.items() if v["tier"] == tier and w not in blocked)
        if len(eligible) < counts[tier]:
            sys.exit(
                f"tier '{tier}' ran out: the batch needs {counts[tier]} but only {len(eligible)} are "
                f"unused in the last {REPEAT_DAYS} days. Add words to the pool or shorten the batch."
            )
        picks += [{"word": w, "tier": tier, "theme": pool[w]["theme"]} for w in rng.sample(eligible, counts[tier])]

    history = [{"tier": e["tier"], "theme": pool.get(e["word"], {}).get("theme")} for e in sorted(schedule, key=lambda e: e["game"])[-MAX_HARD_RUN:]]
    ordered = arrange(picks, history, rng)
    batch = [
        {"game": g, "date": d.isoformat(), "word": p["word"], "tier": p["tier"]}
        for g, d, p in zip(games, dates, ordered)
    ]

    hard_run = run = 0
    for p in ordered:
        run = run + 1 if p["tier"] == "hard" else 0
        hard_run = max(hard_run, run)
    print(f"batch: games {games[0]}-{games[-1]}, {dates[0]} to {dates[-1]}")
    print("  tiers  " + ", ".join(f"{t} {counts[t]}" for t in TIERS))
    print("  themes " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(p["theme"] for p in ordered).items())))
    print(f"  longest hard run {hard_run}; words blocked by the {REPEAT_DAYS}-day rule: {len(blocked)}")
    chosen = {p["word"] for p in ordered}
    left = Counter(v["tier"] for w, v in pool.items() if w not in blocked and w not in chosen)
    print("  still unused per tier " + ", ".join(f"{t} {left[t]}" for t in TIERS))
    if args.dry_run:
        print("dry run: nothing written")
        return

    for entry in batch:
        used.setdefault(entry["word"], []).append(entry["date"])
    schedule = sorted(schedule + batch, key=lambda e: e["game"])
    write_json(args.state_dir / "schedule.json", schedule)
    write_json(args.state_dir / "used.json", used)
    print(f"wrote {args.state_dir / 'schedule.json'} ({len(schedule)} games) and used.json")
    if args.remote_state:
        prod_state.push(args.state_dir, ["schedule", "used"])
        # Keep KV's copy of the pool current so the daily job schedules from the same words.
        if args.pool.resolve() == (args.state_dir / "pool.json").resolve():
            prod_state.push(args.state_dir, ["pool"])
        print("pushed schedule, used and pool to KV")


if __name__ == "__main__":
    main()
