/* ============================================================
   JIZURA — backdrop: where the lyrics may go over a background video, and the colours that read on it

   The engine never looks at the video. Whoever analysed it (an MV director agent, the standalone analysis tool,
   or a person) writes what it found into the project as data; the planner only reads it:

   project.backdrop = {                     time-based measurements (survive lyric edits)
     version: 1, source: 'bg.mp4', offset: 0,          track time = song time + offset
     legibility: 'auto',                               default for every line (see below)
     clip: true,                                       false: let the drawing run past the free areas
     track: [{ t0, t1,                                 seconds
               free:  [[x, y, w, h], …],               where text may go, best first (0..1 of the frame)
               avoid: [[x, y, w, h], …],               the subject; used to find free space when free is missing
               tone:  { color: '#rrggbb', busy: 0..1 } the background inside the free area }]
   }
   project.overrides[line] = {               decisions (always win over the track)
     zone: { rect: [x, y, w, h], clip: true },         clip: cut the drawing at the zone edge (default on)
     tone: { color, busy }, legibility: 'auto' | 'none' | 'outline' | 'shadow' | 'plate'
   }

   Projects without any of this plan exactly as before.
   ============================================================ */
(() => {
'use strict';

const HEX = /^#[0-9a-f]{6}$/i;
const LEGIBILITY = ['auto', 'none', 'outline', 'shadow', 'plate'];
// text treatments that keep a lyric readable on a busy picture (all exist in the core / style packs)
const LEGIBLE_TREAT = { outline: ['outline', 'doubleOutline'], shadow: ['softShadow', 'hardShadow'], plate: ['boxed'] };
const BUSY_AUTO = 0.45;                  // 'auto': from this busy value up, a readable treatment is picked
const num = (v, lo, hi) => { v = +v; return isFinite(v) ? J.clamp(v, lo, hi) : null; };

/* [x, y, w, h] in 0..1, clipped to the frame; null when it is not a usable rectangle */
J.normRect = r => {
  if (!Array.isArray(r) || r.length !== 4) return null;
  let [x, y, w, h] = r.map(v => num(v, -1, 2));
  if ([x, y, w, h].some(v => v == null)) return null;
  const x1 = J.clamp(x + w), y1 = J.clamp(y + h); x = J.clamp(x); y = J.clamp(y);
  w = x1 - x; h = y1 - y;
  return w > 0.02 && h > 0.02 ? [x, y, w, h].map(v => Math.round(v * 1e4) / 1e4) : null;
};
J.normTone = t => {
  if (!t || typeof t !== 'object' || !HEX.test(t.color || '')) return null;
  const o = { color: t.color.toUpperCase() };
  const busy = num(t.busy, 0, 1); if (busy != null) o.busy = busy;
  return o;
};

/* project files are untrusted: keep only well-formed numbers, rectangles and colours */
J.normalizeBackdrop = b => {
  if (!b || typeof b !== 'object' || !Array.isArray(b.track)) return null;
  const o = { version: 1, offset: num(b.offset, -36000, 36000) || 0, track: [] };
  if (typeof b.source === 'string') o.source = b.source.slice(0, 260);
  if (LEGIBILITY.includes(b.legibility)) o.legibility = b.legibility;
  if (b.clip === false) o.clip = false;
  for (const e of b.track.slice(0, 5000)) {
    if (!e || typeof e !== 'object') continue;
    const t0 = num(e.t0, -36000, 36000), t1 = num(e.t1, -36000, 36000);
    if (t0 == null || t1 == null || t1 <= t0) continue;
    const it = { t0, t1 };
    const free = (Array.isArray(e.free) ? e.free : []).map(J.normRect).filter(Boolean).slice(0, 16);
    const avoid = (Array.isArray(e.avoid) ? e.avoid : []).map(J.normRect).filter(Boolean).slice(0, 16);
    if (free.length) it.free = free;
    if (avoid.length) it.avoid = avoid;
    const tone = J.normTone(e.tone); if (tone) it.tone = tone;
    if (it.free || it.avoid || it.tone) o.track.push(it);
  }
  o.track.sort((a, c) => a.t0 - c.t0);
  return o.track.length ? o : null;
};

/* free space from the subject boxes: the largest of a few compositional candidates that keeps clear of them */
const CANDIDATES = [
  [0, 0, 0.5, 1], [0.5, 0, 0.5, 1], [0, 0, 1, 0.5], [0, 0.5, 1, 0.5],
  [0, 0, 0.36, 1], [0.64, 0, 0.36, 1], [0, 0, 1, 0.36], [0, 0.64, 1, 0.36],
  [0, 0, 0.5, 0.5], [0.5, 0, 0.5, 0.5], [0, 0.5, 0.5, 0.5], [0.5, 0.5, 0.5, 0.5],
].map(([x, y, w, h]) => [x + 0.03, y + 0.04, w - 0.06, h - 0.08]);
const hits = (a, b, m = 0.02) => a[0] < b[0] + b[2] + m && b[0] - m < a[0] + a[2] && a[1] < b[1] + b[3] + m && b[1] - m < a[1] + a[3];
J.freeFromAvoid = avoid => {
  // plus the bands left over above / below / beside the subjects (a wide subject in a tall frame leaves only those)
  const x0 = Math.min(...avoid.map(a => a[0])), y0 = Math.min(...avoid.map(a => a[1]));
  const x1 = Math.max(...avoid.map(a => a[0] + a[2])), y1 = Math.max(...avoid.map(a => a[1] + a[3])), M = 0.03;
  const bands = [[M, M, 1 - 2 * M, y0 - 2 * M], [M, y1 + M, 1 - 2 * M, 1 - y1 - 2 * M], [M, M, x0 - 2 * M, 1 - 2 * M], [x1 + M, M, 1 - x1 - 2 * M, 1 - 2 * M]]
    .filter(b => b[2] >= 0.2 && b[3] >= 0.15);
  return CANDIDATES.concat(bands).filter(c => !avoid.some(a => hits(c, a))).sort((a, b) => b[2] * b[3] - a[2] * a[3]);
};

const sameRect = (a, b) => a && b && a.every((v, i) => Math.abs(v - b[i]) < 1e-3);

/* what the backdrop says about one lyric line shown over [s, e] (song seconds):
   { rect, zone, tone, legibility } or null. prevRect keeps the text where it was when that place is still free. */
J.lineBackdrop = (project, li, s, e, W, H, prevRect) => {
  const ov = ((project && project.overrides) || {})[li] || {};
  const B = project && project.backdrop && Array.isArray(project.backdrop.track) ? project.backdrop : null;
  let entry = null;
  if (B) {
    const off = +B.offset || 0;
    let best = 0;
    for (const it of B.track) {
      const o = Math.min(e, it.t1 - off) - Math.max(s, it.t0 - off);
      if (o > best) { best = o; entry = it; }
    }
  }
  const ovRect = ov.zone && J.normRect(ov.zone.rect);
  let rect = ovRect;
  if (!rect && entry) {
    const cands = entry.free && entry.free.length ? entry.free : entry.avoid ? J.freeFromAvoid(entry.avoid) : [];
    rect = cands.find(c => sameRect(c, prevRect)) || cands[0] || null;
  }
  const tone = J.normTone(ov.tone) || (entry && entry.tone) || null;
  if (!rect && !tone) return null;
  const legibility = LEGIBILITY.includes(ov.legibility) ? ov.legibility : B && B.legibility ? B.legibility : 'auto';
  const out = { rect, tone, legibility };
  // clipped unless switched off: about a fifth of the layouts are full-frame designs that would spill out of the area
  const clip = ovRect ? ov.zone.clip !== false : !(B && B.clip === false);
  if (rect) out.zone = { x: Math.round(rect[0] * W), y: Math.round(rect[1] * H), w: Math.round(rect[2] * W), h: Math.round(rect[3] * H), clip, side: 'free' };
  return out;
};

/* a copy of scheme `base` re-lit for this background colour; returns its index in st.schemes (one per base + colour) */
J.toneScheme = (st, base, tone) => {
  if (!tone) return base;
  if (!st._toneCache) {
    Object.defineProperty(st, '_toneCache', { value: new Map() });
    Object.defineProperty(st, '_nBase', { value: st.schemes.length });
  }
  const b = ((base % st._nBase) + st._nBase) % st._nBase, key = b + tone.color;
  if (st._toneCache.has(key)) return st._toneCache.get(key);
  const S = st.schemes[b], bg = tone.color, o = Object.assign({}, S, { bg });
  for (const k of ['fg', 'ink']) if (S[k]) o[k] = J.fitContrast(S[k], bg, 4.5);
  // accents 4.5:1 as well: many layouts lay the text on an accent plate (ticket, marker, sticker) and write it in the
  // bg colour (src/11p_layouts*.js onCol takes any colour from 2.4:1), so the plate and its text read only at this
  for (const k of ['accent', 'accent2']) if (S[k]) o[k] = J.fitContrast(S[k], bg, 4.5);
  if (S.sub) o.sub = J.fitContrast(S.sub, bg, 3);
  for (const k of ['ghostA', 'ghostB']) if (S[k]) o[k] = J.fitContrast(S[k], bg, 1.6);
  if (S.dim) o.dim = J.mix(bg, o.fg, 0.14);
  if (S.grad) o.grad = S.grad.map(c => J.fitContrast(c, bg, 3));
  st.schemes.push(o);
  const idx = st.schemes.length - 1;
  st._toneCache.set(key, idx);
  return idx;
};

/* the treatment a line needs to read on its background (null: keep what was drawn) */
J.legibleTreat = (BD, treat, rng) => {
  if (!BD) return null;
  const want = BD.legibility === 'auto' ? (BD.tone && BD.tone.busy >= BUSY_AUTO ? 'any' : null) : LEGIBLE_TREAT[BD.legibility] ? BD.legibility : null;
  if (!want) return null;
  const pool = (want === 'any' ? [].concat(...Object.values(LEGIBLE_TREAT)) : LEGIBLE_TREAT[want]).filter(k => J.TREAT[k]);
  if (!pool.length || pool.includes(treat)) return null;
  return rng.pick(pool);
};
})();
