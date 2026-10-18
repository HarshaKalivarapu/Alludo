import words from "./words.json";

export interface Env {
  GAMES: KVNamespace;
  LAUNCH_DATE: string;
  TIME_ZONE: string;
}

const vocab: string[] = words.vocab;
const idOf = new Map(vocab.map((w, i) => [w, i]));
const forms: Record<string, string> = words.forms;
const stopwords = new Set<string>(words.stop);
// Slurs: never accepted as guesses, whether typed directly or as an inflected form.
const blocked = new Set<string>(words.blocked);

const TOP_COUNT = 500;
const HINT_MAX_RANK = 300;
const DAY_MS = 86_400_000;

// Answer keys never change once published, so each isolate keeps the ones it has read.
const rankCache = new Map<number, Uint16Array>();
const byRankCache = new Map<number, Uint32Array>();

function localDate(env: Env, when = new Date()): string {
  // en-CA formats as YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", { timeZone: env.TIME_ZONE }).format(when);
}

function todayGame(env: Env): number {
  const days = (Date.parse(localDate(env) + "T00:00:00Z") - Date.parse(env.LAUNCH_DATE + "T00:00:00Z")) / DAY_MS;
  return Math.round(days) + 1;
}

function gameDate(env: Env, game: number): string {
  return new Date(Date.parse(env.LAUNCH_DATE + "T00:00:00Z") + (game - 1) * DAY_MS).toISOString().slice(0, 10);
}

async function ranksFor(env: Env, game: number): Promise<Uint16Array | null> {
  // Future games are refused even though their keys are already uploaded.
  if (!Number.isInteger(game) || game < 1 || game > todayGame(env)) return null;
  const cached = rankCache.get(game);
  if (cached) return cached;
  const buf = await env.GAMES.get(`rank:${game}`, "arrayBuffer");
  if (!buf) return null;
  const ranks = new Uint16Array(buf);
  rankCache.set(game, ranks);
  return ranks;
}

function wordsByRank(game: number, ranks: Uint16Array): Uint32Array {
  let byRank = byRankCache.get(game);
  if (!byRank) {
    byRank = new Uint32Array(ranks.length);
    ranks.forEach((rank, id) => (byRank![rank - 1] = id));
    byRankCache.set(game, byRank);
  }
  return byRank;
}

type Normalized = { word: string } | { error: "not_a_word" | "common" | "unknown" | "blocked" };

function normalize(raw: string): Normalized {
  const typed = raw.trim().toLowerCase();
  if (!/^[a-z]+$/.test(typed)) return { error: "not_a_word" };
  const word = idOf.has(typed) ? typed : forms[typed];
  if (word) return blocked.has(word) ? { error: "blocked" } : { word };
  if (stopwords.has(typed)) return { error: "common" };
  return { error: "unknown" };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

export async function handleApi(url: URL, env: Env): Promise<Response> {
  const path = url.pathname.replace(/^\/api\//, "");
  if (path === "today") {
    const game = todayGame(env);
    return json({ game, date: gameDate(env, game) });
  }

  const game = Number(url.searchParams.get("game"));
  const ranks = await ranksFor(env, game);
  if (!ranks) return json({ error: "no_game" }, 404);

  switch (path) {
    case "guess": {
      const result = normalize(url.searchParams.get("word") ?? "");
      if ("error" in result) return json(result, 400);
      // Keys built before a word joined the vocabulary are shorter; those games don't know it.
      const rank = ranks[idOf.get(result.word)!];
      if (rank === undefined) return json({ error: "unknown" }, 400);
      return json({ word: result.word, rank });
    }
    case "hint": {
      // Rank 1 is the answer itself, so hints never reach it.
      const rank = Number(url.searchParams.get("rank"));
      if (!Number.isInteger(rank) || rank < 2 || rank > HINT_MAX_RANK) return json({ error: "bad_rank" }, 400);
      return json({ word: vocab[wordsByRank(game, ranks)[rank - 1]], rank });
    }
    case "giveup":
      return json({ word: vocab[wordsByRank(game, ranks)[0]] });
    case "top": {
      const byRank = wordsByRank(game, ranks);
      return json({ words: Array.from(byRank.subarray(0, TOP_COUNT), (id) => vocab[id]) });
    }
    default:
      return json({ error: "not_found" }, 404);
  }
}
