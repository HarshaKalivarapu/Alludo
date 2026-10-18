// Alludo game client. All progress lives in localStorage; the server only answers
// "what rank is this word" (plus hints, give-up, and the top-500 list).

const VOCAB_SIZE = 40000;
const GREEN_MAX = 300;
const YELLOW_MAX = 1500;
const HINT_MAX = 300;
const SETTINGS_KEY = "alludo:settings";

// Board colors for each stage, in light and dark. The board follows the best guess so far and
// never moves back to a colder stage.
const STAGES = {
  start: {
    dark:  { bg: "#1b2129", head: "#e4e8ee", "head-muted": "#9ba5b2", surface: "#252c36", ink: "#e8ebf0", muted: "#a0a9b5", line: "#333c48", accent: "#8f9bab" },
    light: { bg: "#cdd3db", head: "#222a35", "head-muted": "#56606c", surface: "#f5f6f8", ink: "#20262e", muted: "#68717c", line: "#bbc3cd", accent: "#5f6b7a" },
  },
  cold: {
    dark:  { bg: "#3a2827", head: "#f3ebe6", "head-muted": "#c2a9a5", surface: "#473332", ink: "#f5ece8", muted: "#c7aeaa", line: "#58403e", accent: "#dcc8c1" },
    light: { bg: "#e3d3cf", head: "#3a2423", "head-muted": "#7a5f5c", surface: "#f8f3f1", ink: "#2f2120", muted: "#7d6764", line: "#d4c1bc", accent: "#8a6762" },
  },
  warm: {
    dark:  { bg: "#36301f", head: "#f3eee2", "head-muted": "#c4b897", surface: "#433c2a", ink: "#f4efe3", muted: "#c8bd9e", line: "#544a35", accent: "#ddd2b2" },
    light: { bg: "#e2dcc6", head: "#36301f", "head-muted": "#71684f", surface: "#f7f4ea", ink: "#2c2719", muted: "#766d55", line: "#d2cab0", accent: "#83774f" },
  },
  close: {
    dark:  { bg: "#26352e", head: "#f1f0e6", "head-muted": "#a9b8ad", surface: "#30413a", ink: "#f1f0e6", muted: "#a9b8ad", line: "#3d5047", accent: "#cfcdbb" },
    light: { bg: "#d5dbd3", head: "#22302a", "head-muted": "#56645c", surface: "#f4f5f1", ink: "#1f2a25", muted: "#66716b", line: "#c2cac0", accent: "#56665d" },
  },
  won: {
    dark:  { bg: "#2b4235", head: "#f6f5ec", "head-muted": "#b6c7ba", surface: "#355045", ink: "#f6f5ec", muted: "#b6c7ba", line: "#46604f", accent: "#e8e5cf" },
    light: { bg: "#c9d8c8", head: "#1d2e24", "head-muted": "#4f6456", surface: "#f4f7f1", ink: "#1d2a22", muted: "#5f7065", line: "#b5c7b4", accent: "#4f6a58" },
  },
};
const STAGE_ORDER = ["start", "cold", "warm", "close", "won"];
// Closeness bars are a little brighter on the dark boards so they read on every tint. The logo's
// bar uses stronger versions of the same three colors.
const BANDS = {
  dark: { green: "#457050", yellow: "#7a6a31", red: "#7d433b", "logo-green": "#6f9f7b", "logo-yellow": "#c9b062", "logo-red": "#c4776b" },
  light: { green: "#b6d3b1", yellow: "#ead592", red: "#e5b2a6", "logo-green": "#5f9a6a", "logo-yellow": "#c49a2c", "logo-red": "#b9604f" },
};
const TOKENS = ["bg", "head", "head-muted", "surface", "ink", "muted", "line", "accent", "accent-ink"];

// The big-guess moment (ms): lift over the input, reveal, quiet pause, settle into the
// last-guess spot. The list copy starts fading in LEAD ms before landing.
const LIFT = 460, REVEAL = 650, PAUSE = 320, SETTLE = 560, LEAD = 170, LIST_FADE = 280;
// The softer single wave that changes the board color.
const WAVE = { ms: 1600, amp: 15, lambda: 130, feather: 65 };

const $ = (id) => document.getElementById(id);
const els = {
  stage: $("stage"), overlay: $("overlay"), gameLine: $("game-line"), counts: $("counts"), form: $("guess-form"),
  input: $("guess-input"), recent: $("recent"), list: $("guesses"), finish: $("finish"), finishTitle: $("finish-title"),
  finishText: $("finish-text"), shareNote: $("share-note"), nextGame: $("next-game"), menu: $("menu"), menuBtn: $("menu-btn"),
  warpMap: $("warp-map"), warpDisp: $("warp-disp"),
};
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
const darkScheme = matchMedia("(prefers-color-scheme: dark)");

let today = { game: 1, date: "" };
let gameNo = 1;
let game = null; // { guesses: [{word, rank, hint?, post?}], status, answer?, finishedOn? }
let lastWord = null;
let boardStage = "start";
let busy = false;
let settings = { hints: "easy", sort: "rank", theme: "system", ...readJSON(SETTINGS_KEY, {}) };

// ---------- storage ----------
function readJSON(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function writeJSON(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
}
const gameKey = (n) => `alludo:game:${n}`;
const loadGame = (n) => readJSON(gameKey(n), null) ?? { guesses: [], status: "playing" };
const saveGame = () => writeJSON(gameKey(gameNo), game);

// ---------- helpers ----------
async function api(path) {
  try {
    const res = await fetch(`/api/${path}`);
    return await res.json();
  } catch {
    return { error: "network" };
  }
}
function dateOfGame(n) {
  const ms = Date.parse(today.date + "T00:00:00Z") - (today.game - n) * 86_400_000;
  return new Date(ms).toISOString().slice(0, 10);
}
function prettyDate(iso, long = false) {
  return new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", {
    timeZone: "UTC", month: "short", day: "numeric", ...(long ? { weekday: "long" } : {}),
  });
}
function band(rank) {
  return rank <= GREEN_MAX ? "green" : rank <= YELLOW_MAX ? "yellow" : "red";
}
// Exponential falloff so the bar visibly shrinks across the warm ranks.
function barWidth(rank) {
  const f = (x) => 0.5 * Math.exp(-0.5 * x);
  const pct = ((f(((rank - 1) / VOCAB_SIZE) * 100) - f(100)) / (f(0) - f(100))) * 100;
  return Math.max(1, pct);
}
const counted = () => game.guesses.filter((g) => !g.post);
const guessCount = () => counted().filter((g) => !g.hint).length;
const hintCount = () => counted().filter((g) => g.hint).length;
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const nextFrame = () => new Promise((r) => requestAnimationFrame(r));
const motion = () => !reducedMotion.matches;

// ---------- board stages ----------
function stageOf(entries) {
  if (!entries.length) return "start";
  const best = Math.min(...entries.map((g) => g.rank));
  return best === 1 ? "won" : best <= GREEN_MAX ? "close" : best <= YELLOW_MAX ? "warm" : "cold";
}
const isLater = (a, b) => STAGE_ORDER.indexOf(a) > STAGE_ORDER.indexOf(b);
function colorMode() {
  return settings.theme === "system" ? (darkScheme.matches ? "dark" : "light") : settings.theme;
}
// Paint the board for a stage, optionally fading over `ms`. keepBg leaves the background to the
// wave's overlay.
function setBoard(stage, ms = 0, keepBg = false) {
  const mode = colorMode();
  const palette = STAGES[stage][mode];
  const tokens = { ...palette, "accent-ink": mode === "dark" ? palette.bg : palette.surface, ...BANDS[mode] };
  const root = document.documentElement;
  root.style.transition = ms
    ? TOKENS.filter((n) => !(keepBg && n === "bg")).map((n) => `--b-${n} ${ms}ms ease`).join(", ")
    : "none";
  for (const [k, v] of Object.entries(tokens)) {
    if (keepBg && k === "bg") continue;
    root.style.setProperty(`--b-${k}`, v);
  }
  root.style.colorScheme = mode;
}

// ---------- rendering ----------
function rowEl(entry, { current = false, faded = false, note = "", fresh = false, where = "list" } = {}) {
  const li = document.createElement(where === "list" ? "li" : "div");
  li.className = `row ${band(entry.rank)}`;
  if (entry.rank === 1) li.classList.add("secret");
  if (current) li.classList.add("current");
  if (faded) li.classList.add("faded");
  if (fresh && motion()) li.classList.add("new");
  li.dataset.word = entry.word;
  li.style.setProperty("--w", barWidth(entry.rank).toFixed(1) + "%");
  const word = document.createElement("span");
  word.className = "word";
  word.textContent = entry.word;
  const tags = [entry.hint && "hint", entry.post && "after the game", note].filter(Boolean);
  if (tags.length) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = tags.join(" · ");
    word.append(tag);
  }
  const rank = document.createElement("span");
  rank.className = "rank";
  rank.textContent = entry.rank.toLocaleString();
  li.append(word, rank);
  return li;
}
function textNode(cls, text) {
  const p = document.createElement("p");
  p.className = cls;
  p.textContent = text;
  return p;
}
function sortedEntries(list = game.guesses) {
  const sorted = [...list];
  if (settings.sort === "rank") sorted.sort((a, b) => a.rank - b.rank);
  else sorted.reverse();
  return sorted;
}

// The last-guess spot shows the newest guess, or a short message in its place.
function showRecent(...nodes) {
  if (nodes.length) return els.recent.replaceChildren(...nodes);
  const last = game.guesses.find((g) => g.word === lastWord);
  els.recent.replaceChildren(last ? rowEl(last, { current: true, where: "recent" }) : textNode("empty", "Your latest guess shows here"));
}
const showMessage = (cls, text) => showRecent(textNode(cls, text));

function render({ holdFinish = false } = {}) {
  const isToday = gameNo === today.game;
  els.gameLine.textContent = `Game #${gameNo} · ${prettyDate(dateOfGame(gameNo), true)}${isToday ? "" : " (past game)"}`;
  const hints = hintCount();
  els.counts.innerHTML = `Guesses: <strong>${guessCount()}</strong>` + (hints ? ` · Hints: <strong>${hints}</strong>` : "");
  els.list.replaceChildren(...sortedEntries().map((g) => rowEl(g, { current: g.word === lastWord })));
  showRecent();

  const done = game.status !== "playing";
  els.finish.hidden = !done || holdFinish;
  if (done) {
    const g = guessCount(), h = hintCount();
    const hintText = h ? ` and ${plural(h, "hint")}` : "";
    const guesses = plural(g, "guess", "guesses");
    if (game.status === "won") {
      els.finishTitle.textContent = "You found it!";
      els.finishText.textContent = `The word was “${game.answer}”. You got it in ${guesses}${hintText}.`;
    } else {
      els.finishTitle.textContent = "Better luck next time";
      els.finishText.textContent = `The word was “${game.answer}”. You gave up after ${guesses}${hintText}.`;
    }
  }
  els.input.placeholder = done ? "keep guessing (won't count)" : "type a word";
  // The closest words give the answer away, so they only appear once the game is over; hints and
  // giving up only make sense while it's still going.
  const menuItem = (action) => els.menu.querySelector(`[data-action="${action}"]`);
  menuItem("closest").hidden = !done;
  menuItem("hint").hidden = done;
  menuItem("giveup").hidden = done;
  updateCountdown();
}

// ---------- guesses ----------
const ERRORS = {
  not_a_word: "Type one word, letters only.",
  common: "That word is too common to count.",
  unknown: "I don't know that word. Check the spelling?",
  blocked: "That word isn't allowed in Alludo.",
  no_game: "That game isn't available.",
  network: "Couldn't reach the server. Try again.",
};

async function submitGuess(raw) {
  const typed = raw.trim().toLowerCase();
  if (!typed) return;
  if (/\s/.test(typed)) {
    els.input.value = "";
    return showMessage("error", ERRORS.not_a_word);
  }
  const res = await api(`guess?game=${gameNo}&word=${encodeURIComponent(typed)}`);
  if (res.error) {
    els.input.value = "";
    return showMessage("error", ERRORS[res.error] ?? ERRORS.network);
  }
  const existing = game.guesses.find((g) => g.word === res.word);
  if (existing) {
    els.input.value = "";
    lastWord = existing.word;
    render();
    return showRecent(rowEl(existing, { current: true, faded: true, note: "already guessed", where: "recent" }));
  }
  const entry = { word: res.word, rank: res.rank };
  if (game.status !== "playing") entry.post = true;
  await addEntry(entry, res.word !== typed ? `counted as “${res.word}”` : "");
}

function commit(entry) {
  game.guesses.push(entry);
  lastWord = entry.word;
  if (entry.rank === 1 && game.status === "playing") finish("won", entry.word);
  saveGame();
}

// A guess that reaches a new board color gets the big moment; every other guess just appears.
async function addEntry(entry, note = "") {
  const next = entry.post ? boardStage : stageOf([...counted(), entry]);
  if (isLater(next, boardStage) && motion()) return bigGuess(entry, next);
  els.input.value = "";
  commit(entry);
  if (isLater(next, boardStage)) { boardStage = next; setBoard(next); }
  render();
  showRecent(rowEl(entry, { current: true, note, fresh: true, where: "recent" }));
  els.list.querySelector(`.row[data-word="${CSS.escape(entry.word)}"]`)?.classList.add("new");
}

function finish(status, answer) {
  game.status = status;
  game.answer = answer;
  game.finishedOn = today.date;
}

async function bigGuess(entry, next) {
  const stageRect = els.stage.getBoundingClientRect();
  const inputRect = els.input.getBoundingClientRect();
  const recentRect = els.recent.getBoundingClientRect();
  const sampleRow = els.list.querySelector(".row");
  const rowH = sampleRow ? sampleRow.offsetHeight : recentRect.height;
  const slotTop = recentRect.top + (recentRect.height - rowH) / 2;

  // The card sits exactly over the last-guess spot; every step is a transform away from there.
  // While lifted it takes the input box's full height, so it covers the box completely.
  const fly = document.createElement("div");
  fly.className = `fly ${band(entry.rank)}`;
  fly.innerHTML = `<span class="fill"></span><span class="word"></span><span class="rank"></span>`;
  fly.querySelector(".word").textContent = entry.word;
  Object.assign(fly.style, {
    left: `${recentRect.left - stageRect.left}px`, top: `${slotTop - stageRect.top}px`,
    width: `${recentRect.width}px`, height: `${inputRect.height}px`,
  });
  els.stage.append(fly);
  const inputY = inputRect.top - slotTop;
  const resting = `translate(0px, ${inputY}px) scale(1)`;
  const lifted = `translate(0px, ${inputY}px) scale(1.08)`;
  const shadow = "0 14px 30px rgba(0,0,0,0.32)";

  // 1. Lift in place over the input box.
  const submitted = els.input.value;
  await fly.animate([{ transform: resting, boxShadow: "0 0 0 rgba(0,0,0,0)" }, { transform: lifted, boxShadow: shadow }],
    { duration: LIFT, easing: "cubic-bezier(.2,.8,.25,1)", fill: "forwards" }).finished;
  // Hidden under the card, so no one sees it clear. If the player already started typing their
  // next word, keep it: they can type during the whole sequence, and Enter works once it ends.
  if (els.input.value === submitted) els.input.value = "";

  // 2. Reveal: the rank counts down and the bar fills, while the highlight moves to the card.
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--b-accent").trim();
  fly.querySelector(".fill").animate([{ width: "0%" }, { width: `${barWidth(entry.rank).toFixed(1)}%` }],
    { duration: REVEAL, easing: "cubic-bezier(.3,.6,.3,1)", fill: "forwards" });
  document.querySelectorAll(".app .row.current").forEach((el) =>
    el.animate([{ outlineColor: accent }, { outlineColor: "transparent" }], { duration: REVEAL, easing: "ease-out", fill: "forwards" }));
  fly.animate([{ outlineColor: "transparent" }, { outlineColor: accent }], { duration: REVEAL, easing: "ease-out", fill: "forwards" });
  const rankEl = fly.querySelector(".rank");
  const t0 = performance.now();
  for (;;) {
    const p = Math.min(1, ((await nextFrame()) - t0) / REVEAL);
    const eased = 1 - Math.pow(1 - p, 3);
    rankEl.textContent = Math.round(VOCAB_SIZE - (VOCAB_SIZE - entry.rank) * eased).toLocaleString();
    if (p >= 1) break;
  }

  // Quiet pause: nothing moves, so draw the wave's frames now (it would stutter later).
  const pauseStart = performance.now();
  const wave = prepareWave(recentRect.left + recentRect.width / 2 - stageRect.left, slotTop + rowH / 2 - stageRect.top);
  await wait(Math.max(0, PAUSE - (performance.now() - pauseStart)));

  // 3. Settle: the card drops into the last-guess spot while the previous last guess fades out
  // and the list opens a gap where the new word belongs.
  const oldRows = [...els.list.children];
  const before = new Map(oldRows.map((el) => [el, el.getBoundingClientRect().top]));
  const gap = rowEl(entry, { current: true });
  gap.classList.add("placeholder");
  const index = sortedEntries([...game.guesses, entry]).findIndex((g) => g === entry);
  oldRows[index] ? els.list.insertBefore(gap, oldRows[index]) : els.list.append(gap);
  oldRows.forEach((el) => {
    const dy = before.get(el) - el.getBoundingClientRect().top;
    if (dy) el.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: SETTLE, easing: "cubic-bezier(.45,0,.2,1)" });
  });
  els.recent.firstElementChild?.animate([{ opacity: 1 }, { opacity: 0 }], { duration: SETTLE * 0.6, easing: "ease-out", fill: "forwards" });
  let gapFade = null;
  setTimeout(() => {
    gap.classList.remove("placeholder");
    gapFade = gap.animate([{ opacity: 0 }, { opacity: 1 }], { duration: LIST_FADE, easing: "ease-out", fill: "forwards" });
  }, SETTLE - LEAD);
  await fly.animate([
    { transform: lifted, boxShadow: shadow, height: `${inputRect.height}px` },
    { transform: "translate(0px, 0px) scale(1)", boxShadow: "0 0 0 rgba(0,0,0,0)", height: `${rowH}px` },
  ], { duration: SETTLE, easing: "cubic-bezier(.45,0,.2,1)", fill: "forwards" }).finished;

  // Landed: switch to the new state. The list copy carries on its fade from the same opacity.
  const startOpacity = gapFade ? Number(getComputedStyle(gap).opacity) : 0;
  const remaining = gapFade ? Math.max(0, LIST_FADE - gapFade.currentTime) : LIST_FADE;
  commit(entry);
  render({ holdFinish: true });
  fly.remove();
  const listed = els.list.querySelector(`.row[data-word="${CSS.escape(entry.word)}"]`);
  if (listed && remaining > 0) {
    await listed.animate([{ opacity: startOpacity }, { opacity: 1 }], { duration: remaining, easing: "ease-out" }).finished;
  }

  // 4. The wave carries the board into its new color.
  await singleWave(next, wave);
  boardStage = next;
  render();
  if (game.status !== "playing") els.finish.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 320, easing: "ease-out" });
}

// ---------- the logo ----------
// Two soft arcs rise from the logo's final O. They're drawn after the font loads, centered on the
// letter's actual glyph, and pulse outward whenever the board changes color.
const measure = document.createElement("canvas").getContext("2d");
function drawLogoRipple() {
  const el = $("logo-rip");
  el.querySelector("svg")?.remove();
  el.querySelector(".bl")?.remove();
  const style = getComputedStyle(el);
  measure.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  const m = measure.measureText("O");
  const marker = document.createElement("i");
  marker.className = "bl";
  el.append(marker);
  const box = el.getBoundingClientRect();
  const baseline = marker.getBoundingClientRect().top - box.top;
  const cx = (m.actualBoundingBoxRight - m.actualBoundingBoxLeft) / 2;
  const cy = baseline - (m.actualBoundingBoxAscent - m.actualBoundingBoxDescent) / 2;
  const r = (m.actualBoundingBoxAscent + m.actualBoundingBoxDescent) / 2;
  const size = parseFloat(style.fontSize);
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width", box.width);
  svg.setAttribute("height", box.height);
  svg.setAttribute("aria-hidden", "true");
  // Arcs cover only the top of a circle, from 205 to 335 degrees (SVG y points down).
  const point = (radius, deg) => {
    const a = (deg * Math.PI) / 180;
    return `${(cx + radius * Math.cos(a)).toFixed(2)} ${(cy + radius * Math.sin(a)).toFixed(2)}`;
  };
  [{ k: 1.65, w: 0.052, o: 0.8 }, { k: 2.35, w: 0.042, o: 0.42 }].forEach((ring, i) => {
    const radius = r * ring.k;
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("d", `M ${point(radius, 205)} A ${radius} ${radius} 0 0 1 ${point(radius, 335)}`);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", (size * ring.w).toFixed(2));
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("opacity", ring.o);
    path.style.transformBox = "view-box";
    path.style.transformOrigin = `${cx}px ${cy}px`;
    path.dataset.i = i;
    svg.append(path);
  });
  el.append(svg);
}
function pulseLogo() {
  if (!motion()) return;
  $("logo-rip").querySelectorAll("path").forEach((path) => {
    const o = Number(path.getAttribute("opacity"));
    path.animate(
      [{ opacity: o, transform: "scale(1)" }, { opacity: 0, transform: "scale(1.35)", offset: 0.5 },
        { opacity: 0, transform: "scale(0.85)", offset: 0.51 }, { opacity: o, transform: "scale(1)" }],
      { duration: 1400, delay: Number(path.dataset.i) * 160, easing: "ease-out" },
    );
  });
}
document.fonts.ready.then(drawLogoRipple);
addEventListener("resize", drawLogoRipple);

// ---------- the wave ----------
// One water ripple from the last-guess spot: it bends the board slightly as it passes, and the
// new color's edge rides on it. Only the visible part of the page is mapped.
function waveFrames(W, H, x0, y0, count, reach, lambda) {
  const s = W > 900 ? 6 : 4, cw = Math.ceil(W / s), ch = Math.ceil(H / s);
  const canvas = document.createElement("canvas");
  canvas.width = cw; canvas.height = ch;
  const ctx = canvas.getContext("2d");
  const frames = [], radii = [];
  for (let f = 0; f < count; f++) {
    const p = f / (count - 1);
    const radius = reach * (1 - Math.pow(1 - p, 1.7));
    const strength = (1 - 0.55 * p) * Math.min(1, p / 0.08);
    const img = ctx.createImageData(cw, ch);
    for (let y = 0; y < ch; y++) {
      for (let x = 0; x < cw; x++) {
        const dx = x * s - x0, dy = y * s - y0, r = Math.hypot(dx, dy) || 1;
        const d = radius - r;
        const w = Math.abs(d) < lambda / 2
          ? Math.sin((2 * Math.PI * d) / lambda) * Math.pow(Math.cos((Math.PI * d) / lambda), 2) * strength
          : 0;
        const i = (y * cw + x) * 4;
        img.data[i] = Math.round(128 + 127 * w * (dx / r));
        img.data[i + 1] = Math.round(128 + 127 * w * (dy / r));
        img.data[i + 2] = 128; img.data[i + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    frames.push(canvas.toDataURL());
    radii.push(radius);
  }
  return { frames, radii };
}

function prepareWave(x, y) {
  const stageRect = els.stage.getBoundingClientRect();
  const W = stageRect.width;
  const top = Math.max(0, -stageRect.top); // first visible row of the stage
  const H = Math.min(innerHeight, stageRect.height - top + 80);
  const far = Math.max(Math.hypot(x, y - top), Math.hypot(W - x, y - top), Math.hypot(x, top + H - y), Math.hypot(W - x, top + H - y));
  const { frames, radii } = waveFrames(W, H, x, y - top, 40, far + 60 + WAVE.feather, WAVE.lambda);
  frames.forEach((url) => { const im = new Image(); im.src = url; }); // decode ahead of time
  return { x, y, top, W, H, frames, radii };
}

async function singleWave(next, wave) {
  if (!motion()) { setBoard(next); return; }
  const { x, y, top, W, H, frames, radii } = wave;
  pulseLogo();
  els.overlay.style.background = STAGES[next][colorMode()].bg;
  els.overlay.style.opacity = 1;
  setBoard(next, WAVE.ms, true);
  els.warpMap.setAttribute("x", 0);
  els.warpMap.setAttribute("y", top);
  els.warpMap.setAttribute("width", W);
  els.warpMap.setAttribute("height", H);
  els.warpMap.setAttribute("href", frames[0]);
  els.warpDisp.setAttribute("scale", WAVE.amp);
  els.stage.style.filter = "url(#warp)";
  const mask = `radial-gradient(circle at ${x}px ${y}px, #000 calc(var(--r) - ${WAVE.feather}px), transparent var(--r))`;
  els.overlay.style.maskImage = mask;
  els.overlay.style.webkitMaskImage = mask;
  const start = performance.now();
  for (;;) {
    const p = Math.min(1, ((await nextFrame()) - start) / WAVE.ms);
    const f = Math.round(p * (frames.length - 1));
    els.warpMap.setAttribute("href", frames[f]);
    els.overlay.style.setProperty("--r", `${radii[f] + WAVE.feather * 0.5}px`);
    if (p >= 1) break;
  }
  setBoard(next);
  els.overlay.style.opacity = 0;
  els.overlay.style.maskImage = els.overlay.style.webkitMaskImage = "none";
  els.stage.style.filter = "";
  els.warpDisp.setAttribute("scale", 0);
}

// ---------- hints, giving up, lists ----------
function pickHintRank() {
  const taken = new Set(game.guesses.map((g) => g.rank));
  const best = game.guesses.length ? Math.min(...game.guesses.map((g) => g.rank)) : null;
  let target;
  if (settings.hints === "hard") target = 2 + Math.floor(Math.random() * (HINT_MAX - 1));
  else if (best === null) target = HINT_MAX;
  else if (settings.hints === "medium") target = Math.min(best, HINT_MAX + 1) - 1;
  else target = Math.floor(Math.min(best, HINT_MAX * 2) / 2);
  target = Math.max(2, Math.min(HINT_MAX, target));
  // Nearest rank not already on the list, preferring worse ranks over better ones.
  for (let d = 0; d < HINT_MAX; d++) {
    for (const r of [target + d, target - d]) {
      if (r >= 2 && r <= HINT_MAX && !taken.has(r)) return r;
    }
  }
  return null;
}

async function getHint() {
  if (game.status !== "playing") return showMessage("note", "The game is over, so hints are off.");
  const rank = pickHintRank();
  if (!rank) return showMessage("note", "No more hints left for this word.");
  const res = await api(`hint?game=${gameNo}&rank=${rank}`);
  if (res.error) return showMessage("error", ERRORS.network);
  await addEntry({ word: res.word, rank: res.rank, hint: true });
}

async function giveUp() {
  const res = await api(`giveup?game=${gameNo}`);
  if (res.error) return showMessage("error", ERRORS.network);
  finish("gave_up", res.word);
  saveGame();
  render();
  showRecent(rowEl({ word: res.word, rank: 1 }, { note: "the secret word", where: "recent" }));
}

async function showClosest() {
  const note = $("closest-note"), list = $("closest-list");
  if (game.status === "playing") {
    note.textContent = "Finish or give up first. This list shows the answer.";
    list.replaceChildren();
  } else {
    note.textContent = `For game #${gameNo}. Words you guessed are outlined.`;
    const res = await api(`top?game=${gameNo}`);
    const mine = new Set(game.guesses.map((g) => g.word));
    list.replaceChildren(...(res.words ?? []).map((w, i) => rowEl({ word: w, rank: i + 1 }, { current: mine.has(w) && i > 0 })));
  }
  $("dlg-closest").showModal();
}

// state: "none" | "playing" | "won" | "gave_up"
function statusOf(n) {
  const g = readJSON(gameKey(n), null);
  if (!g || !g.guesses.length) return { state: "none" };
  const count = g.guesses.filter((x) => !x.post && !x.hint).length;
  if (g.status === "won") return { state: "won", count, onTime: g.finishedOn === dateOfGame(n) };
  return { state: g.status === "gave_up" ? "gave_up" : "playing", count };
}

function playRandom() {
  const past = Array.from({ length: Math.max(0, today.game - 1) }, (_, i) => i + 1);
  const unplayed = past.filter((n) => statusOf(n).state === "none");
  const pool = unplayed.length ? unplayed : past;
  if (pool.length) go(`/play/${pool[Math.floor(Math.random() * pool.length)]}`);
}

function statsCells() {
  let played = 0, won = 0, totalGuesses = 0, streak = 0;
  for (let n = 1; n <= today.game; n++) {
    const s = statusOf(n);
    if (s.state !== "none") played++;
    if (s.state === "won") { won++; totalGuesses += s.count; }
    // A streak counts daily games won on their own day. Today's game only breaks it once given up.
    if (s.state === "won" && s.onTime) streak++;
    else if (n !== today.game || s.state === "gave_up") streak = 0;
  }
  const cells = [
    [played, "played"], [played ? Math.round((won / played) * 100) + "%" : "–", "won"],
    [streak, "current streak"], [won ? Math.round(totalGuesses / won) : "–", "avg guesses"],
  ];
  return cells.map(([v, l]) => `<div><strong>${v}</strong><span>${l}</span></div>`).join("");
}
function showStats() {
  $("stats").innerHTML = statsCells();
  $("dlg-stats").showModal();
}

// ---------- home and archive ----------
function renderHome() {
  const card = $("today-card");
  if (today.game < 1) {
    card.innerHTML = `<p class="eyebrow">Coming soon</p><h2>The first word arrives at midnight Eastern</h2><p class="countdown"></p>`;
  } else {
    const s = statusOf(today.game);
    const date = new Date(today.date + "T00:00:00Z").toLocaleDateString("en-US", { timeZone: "UTC", weekday: "long", month: "long", day: "numeric" });
    const action = { none: "Play", playing: "Continue", won: "View", gave_up: "View" }[s.state];
    const line = {
      none: "",
      playing: `In progress · ${plural(s.count, "guess", "guesses")}`,
      won: `Solved in ${plural(s.count, "guess", "guesses")}`,
      gave_up: `Gave up after ${plural(s.count, "guess", "guesses")}`,
    }[s.state];
    card.innerHTML = `<p class="eyebrow">Game #${today.game}</p><h2>${date}</h2>${line ? `<p>${line}</p>` : ""}
      <a class="btn primary" href="/play" data-link>${action}</a><p class="countdown"></p>`;
  }
  renderWeek();
  updateCountdown();
}

// This calendar week as small tiles, Sunday to Saturday. Days still to come are greyed out.
function renderWeek() {
  const sunday = today.game - new Date(today.date + "T00:00:00Z").getUTCDay();
  const tiles = [];
  for (let n = sunday; n < sunday + 7; n++) tiles.push(dayTile(n, dateOfGame(n), "weekday"));
  $("week").innerHTML = tiles.join("");
}

// One calendar tile. label "weekday" shows Mon/Tue; "number" shows the game number.
function dayTile(n, iso, label) {
  const day = Number(iso.slice(8, 10));
  const weekday = new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { timeZone: "UTC", weekday: "short" });
  const caption = label === "weekday" ? weekday : `#${n}`;
  if (n < 1 || n > today.game) return `<div class="cal-day off" aria-disabled="true">${label === "weekday" ? `<small>${weekday}</small>` : ""}${day}</div>`;
  const s = statusOf(n);
  const name = { none: "not played", playing: "in progress", won: "solved", gave_up: "gave up" }[s.state];
  const dot = `<i class="dot ${s.state === "none" ? "" : s.state}"></i>`;
  const body = label === "weekday" ? `<small>${caption}</small>${day}${dot}` : `${day}${dot}<small>${caption}</small>`;
  return `<a class="cal-day${n === today.game ? " today" : ""}" href="/play/${n}" data-link aria-label="Game ${n}, ${prettyDate(iso)}, ${name}">${body}</a>`;
}

// The calendar shows one month at a time, from the launch month to the current month.
let calMonth = null; // first day of the shown month, as "YYYY-MM"
const monthKey = (iso) => iso.slice(0, 7);
function shiftMonth(key, delta) {
  const [y, m] = key.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return d.toISOString().slice(0, 7);
}
function renderArchive() {
  const first = monthKey(dateOfGame(1)), last = monthKey(today.date || dateOfGame(1));
  calMonth = calMonth && calMonth >= first && calMonth <= last ? calMonth : last;
  const [y, m] = calMonth.split("-").map(Number);
  $("cal-title").textContent = new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-US", { timeZone: "UTC", month: "long", year: "numeric" });
  $("cal-prev").disabled = calMonth <= first;
  $("cal-next").disabled = calMonth >= last;
  const note = $("cal-note");
  note.hidden = today.game >= 1;
  note.textContent = `Past games will show up here once the first one goes live on ${prettyDate(dateOfGame(1), true)}.`;
  $("random-game").hidden = today.game < 2;
  const cells = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => `<div class="cal-dow">${d}</div>`);
  const startDay = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  for (let i = 0; i < startDay; i++) cells.push(`<div class="cal-day blank" aria-hidden="true"></div>`);
  const launch = Date.parse(dateOfGame(1) + "T00:00:00Z");
  for (let d = 1; d <= days; d++) {
    const iso = `${calMonth}-${String(d).padStart(2, "0")}`;
    const n = Math.round((Date.parse(iso + "T00:00:00Z") - launch) / 86_400_000) + 1;
    cells.push(dayTile(n, iso, "number"));
  }
  $("cal-grid").innerHTML = cells.join("");
}
$("cal-prev").onclick = () => { calMonth = shiftMonth(calMonth, -1); renderArchive(); };
$("cal-next").onclick = () => { calMonth = shiftMonth(calMonth, 1); renderArchive(); };

// ---------- pages ----------
// "/" home, "/play" today's game, "/play/12" game 12, "/archive" the calendar.
function showView(name) {
  document.body.dataset.view = name;
  for (const v of ["home", "play", "archive"]) $(`view-${v}`).hidden = v !== name;
  // The back arrow and some menu items only belong on certain pages.
  document.querySelectorAll("[data-show]").forEach((el) => (el.hidden = !el.dataset.show.split(" ").includes(name)));
}
function route() {
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const play = path.match(/^\/play(?:\/(\d+))?$/);
  if (play && today.game >= 1) {
    const n = play[1] ? Number(play[1]) : today.game;
    if (n >= 1 && n <= today.game) {
      showView("play");
      return openGame(n);
    }
  }
  if (path !== "/" && path !== "/archive") history.replaceState(null, "", "/");
  game = null;
  setBoard("start", motion() ? 450 : 0);
  if (path === "/archive") {
    showView("archive");
    renderArchive();
  } else {
    showView("home");
    renderHome();
  }
  scrollTo(0, 0);
}
function go(path) {
  if (busy) return; // never interrupt a big-guess moment
  if (path !== location.pathname) history.pushState(null, "", path);
  route();
}
addEventListener("popstate", () => { if (!busy) route(); });
document.addEventListener("click", (e) => {
  const link = e.target.closest("a[data-link]");
  if (!link || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  closeMenu();
  go(link.getAttribute("href"));
});

function shareText() {
  const entries = counted();
  const counts = { green: 0, yellow: 0, red: 0 };
  entries.forEach((g) => counts[band(g.rank)]++);
  const total = entries.length || 1;
  const biggest = Math.max(...Object.values(counts));
  const scale = biggest <= 25 ? 5 : biggest <= 50 ? 10 : biggest <= 100 ? 15 : 20;
  const squares = { green: "🟩", yellow: "🟨", red: "🟥" };
  const lines = Object.entries(counts).map(([k, c]) => {
    const n = c ? Math.min(c, Math.max(1, Math.round((c / total) * scale))) : 0;
    return `${squares[k].repeat(n)} ${c}`.trim();
  });
  const g = guessCount(), h = hintCount();
  const hintText = h ? ` and ${plural(h, "hint")}` : "";
  const guesses = plural(g, "guess", "guesses");
  const what = game.status === "won" ? `got it in ${guesses}${hintText}` : `gave up after ${guesses}${hintText}`;
  return `Alludo #${gameNo}: I ${what}\n\n${lines.join("\n")}\n\n${location.origin}`;
}

async function share() {
  const text = shareText();
  const touch = matchMedia("(pointer: coarse)").matches;
  try {
    if (touch && navigator.share) { await navigator.share({ text }); return; }
    await navigator.clipboard.writeText(text);
    els.shareNote.textContent = "Copied. Paste it anywhere.";
  } catch {
    els.shareNote.textContent = text;
  }
}

function updateCountdown() {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "numeric", hourCycle: "h23" })
      .formatToParts(new Date()).map((p) => [p.type, p.value]),
  );
  const left = 24 * 60 - (Number(parts.hour) * 60 + Number(parts.minute));
  const text = `Next word in ${Math.floor(left / 60)}h ${left % 60}m (midnight Eastern).`;
  els.nextGame.textContent = text;
  document.querySelectorAll(".today-card .countdown").forEach((el) => (el.textContent = text));
}

// ---------- settings ----------
// Home and the archive use the starting board; a game uses its own stage.
const currentBoard = () => (document.body.dataset.view === "play" ? boardStage : "start");
function applyThemeSetting() {
  if (settings.theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = settings.theme;
  setBoard(currentBoard());
}
function bindSettings() {
  for (const name of ["hints", "sort", "theme"]) {
    document.querySelectorAll(`input[name="${name}"]`).forEach((input) => {
      input.checked = input.value === settings[name];
      input.onchange = () => {
        settings[name] = input.value;
        writeJSON(SETTINGS_KEY, settings);
        applyThemeSetting();
        if (game) render();
      };
    });
  }
}
darkScheme.addEventListener("change", () => { if (settings.theme === "system") setBoard(currentBoard()); });

// ---------- menu and controls ----------
function closeMenu() { els.menu.hidden = true; els.menuBtn.setAttribute("aria-expanded", "false"); }
els.menuBtn.onclick = (e) => {
  e.stopPropagation();
  els.menu.hidden = !els.menu.hidden;
  els.menuBtn.setAttribute("aria-expanded", String(!els.menu.hidden));
};
document.addEventListener("click", (e) => { if (!els.menu.contains(e.target)) closeMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });

// Guesses and hints run one at a time, so a big-guess moment is never interrupted: typing stays
// open, but Enter does nothing until the sequence (including the wave) has finished.
async function exclusive(task) {
  if (busy) return;
  busy = true;
  try { await task(); } finally { busy = false; }
}
document.addEventListener("click", (e) => {
  const action = e.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  closeMenu();
  ({
    how: () => $("dlg-how").showModal(),
    hint: () => exclusive(getHint),
    giveup: () => (game.status === "playing" ? $("dlg-giveup").showModal() : showMessage("note", "This game is already over.")),
    closest: showClosest,
    stats: showStats,
    settings: () => $("dlg-settings").showModal(),
  })[action]?.();
});
$("giveup-confirm").onclick = () => { $("dlg-giveup").close(); exclusive(giveUp); };
$("random-game").onclick = playRandom;
$("share-btn").onclick = share;
els.form.onsubmit = (e) => {
  e.preventDefault();
  exclusive(() => submitGuess(els.input.value)).then(() => els.input.focus());
};

// ---------- start ----------
function openGame(n) {
  gameNo = n;
  game = loadGame(n);
  lastWord = game.guesses.at(-1)?.word ?? null;
  boardStage = stageOf(counted());
  setBoard(boardStage, motion() ? 450 : 0);
  els.shareNote.textContent = "";
  render();
  scrollTo(0, 0);
  els.input.focus();
}

async function start() {
  applyThemeSetting();
  bindSettings();
  const res = await api("today");
  if (res.error) {
    showView("home");
    $("today-card").innerHTML = `<h2>Couldn't load today's game</h2><p>Refresh the page to try again.</p>`;
    return;
  }
  today = res;
  // Old links used ?g=12; send them to the new address.
  const legacy = Number(new URL(location.href).searchParams.get("g"));
  if (Number.isInteger(legacy) && legacy >= 1) history.replaceState(null, "", `/play/${legacy}`);
  if (!readJSON("alludo:seen-help", false)) { writeJSON("alludo:seen-help", true); $("dlg-how").showModal(); }
  route();
  setInterval(updateCountdown, 30_000);
}

start();
