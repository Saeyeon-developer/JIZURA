// Backdrop (src/08c_backdrop.js) checks on the planner, in Node (no browser):
//   node dev/backdrop_test.js [REF_SRC_DIR]
// 1. with REF_SRC_DIR (e.g. the src/ of the upstream commit, extracted with git archive): projects without
//    backdrop data must plan exactly as that engine does (every style × aspect × a few seeds and switches)
// 2. backdrop behaviour: zones from overrides / free areas / avoid boxes, continuity, re-lit schemes, legibility,
//    untrusted input, ロック snapshots, the After Effects export flag
const fs = require('fs'), vm = require('vm'), path = require('path'), assert = require('assert');
const ROOT = path.join(__dirname, '..');

function loadEngine(srcDir) {
  const noop = () => {};
  const ctx2d = new Proxy({}, { get: (t, k) => k === 'measureText' ? (() => ({ width: 100, actualBoundingBoxAscent: 80, actualBoundingBoxDescent: 10 }))
    : (k === 'getImageData' || k === 'createImageData') ? (() => ({ data: new Uint8ClampedArray(4) })) : (typeof k === 'string' && /^create/.test(k)) ? (() => ({ addColorStop: noop })) : noop, set: () => true });
  const el = () => ({ getContext: () => ctx2d, style: {}, width: 0, height: 0, appendChild: noop, addEventListener: noop, setAttribute: noop, classList: { add: noop, remove: noop, toggle: noop } });
  const g = { console, Math, JSON, Date, Intl, Map, Set, WeakMap, Promise, Proxy, Symbol, Uint8ClampedArray, Uint32Array, Float32Array, ArrayBuffer, DataView, TextEncoder, setTimeout, clearTimeout };
  g.window = g;
  g.document = { createElement: el, getElementById: () => null, querySelectorAll: () => [], head: { appendChild: noop }, fonts: { load: async () => [], ready: Promise.resolve() }, addEventListener: noop };
  g.localStorage = { getItem: () => null, setItem: noop };
  g.OffscreenCanvas = function () { return el(); };
  g.Path2D = function () { return new Proxy({}, { get: () => noop }); };
  g.DOMMatrix = function () { return { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }; };
  g.requestAnimationFrame = noop;
  vm.createContext(g);
  for (const f of fs.readdirSync(srcDir).filter(f => f.endsWith('.js') && f !== '12_ui.js').sort())
    vm.runInContext(fs.readFileSync(path.join(srcDir, f), 'utf8'), g, { filename: f });
  return g.J;
}
const strip = plan => JSON.stringify(plan, (k, v) => (k === 'appVersion' ? undefined : v));

const J = loadEngine(path.join(ROOT, 'src'));
const LYRICS = ['[00:01.00]새벽의 색을/기억하고 있어', '[00:05.00]*투명*한 채로 끝낼 수 없어!', '', '[00:10.00]夜明けの色を覚えてる', '[00:14.00]Hello world, still here', '[間奏 6]', '[00:24.00]아직 늦지 않았을까'].join('\n');
const project = o => Object.assign(J.defaultProject(), { lyrics: LYRICS, seed: 7 }, o);
let checks = 0;
const ok = (c, msg) => { checks++; assert.ok(c, msg); };

/* ---------- 1. invariance ---------- */
const REF = process.argv[2];
if (REF) {
  const R = loadEngine(path.resolve(REF));
  let n = 0;
  for (const style of J.STYLE_ORDER) for (const aspect of ['16:9', '9:16', '1:1', '21:9']) for (const seed of [1, 20260922])
    for (const sw of [{}, { unify: true }, { centerFree: true }, { typeset: true, extra: true }]) {
      const mk = E => Object.assign(E.defaultProject(), { lyrics: LYRICS, style, aspect, seed }, sw);
      const a = strip(R.plan(mk(R), { duration: 40, beats: [] })), b = strip(J.plan(mk(J), { duration: 40, beats: [] }));
      if (a !== b) throw new Error(`plan differs from the reference: ${style} ${aspect} seed ${seed} ${JSON.stringify(sw)}`);
      n++;
    }
  console.log('invariance: identical plans for', n, 'projects without backdrop data');
}

/* ---------- 2. backdrop behaviour ---------- */
const A = [[0.04, 0.06, 0.48, 0.5]], B = [[0.03, 0.7, 0.52, 0.28]];
{ // zone from a line override: every cut of that line is laid out in it, other lines untouched
  const p = project({ overrides: { 1: { zone: { rect: B[0] } } } });
  const plan = J.plan(p, { duration: 40, beats: [] });
  const ref = J.plan(project({}), { duration: 40, beats: [] });
  const z = plan.cuts.filter(c => c.line === 1);
  ok(z.length && z.every(c => c.zone && c.zone.w === Math.round(0.52 * plan.W) && c.zone.h === Math.round(0.28 * plan.H) && c.zone.clip === true), 'override zone on line 1 (clipped by default)');
  const open = J.plan(project({ overrides: { 1: { zone: { rect: B[0], clip: false } } } }), { duration: 40, beats: [] });
  ok(open.cuts.filter(c => c.line === 1).every(c => c.zone.clip === false), 'zone.clip false is kept');
  const openTrack = J.plan(project({ backdrop: J.normalizeBackdrop({ clip: false, track: [{ t0: 0, t1: 60, free: B }] }) }), { duration: 40, beats: [] });
  ok(openTrack.cuts.filter(c => c.zone).every(c => c.zone.clip === false), 'backdrop.clip false applies to track areas');
  ok(plan.cuts.filter(c => c.line !== 1 && c.line >= 0).every(c => !c.zone), 'other lines have no zone');
  ok(strip(plan.cuts.filter(c => c.line === 0)) === strip(ref.cuts.filter(c => c.line === 0)), 'line before is unchanged');
  ok(plan.centerFree === false, 'centerFree flag untouched in the browser plan');
  ok(J.planForAE(plan, p).centerFree === true, 'AE export turns the zone flag on');
  ok(J.planForAE(ref, project({})).centerFree === false, 'AE export unchanged without zones');
}
{ // track: free areas by time, continuity, tone → re-lit scheme
  const bd = { track: [{ t0: 0, t1: 9, free: [A[0], B[0]], tone: { color: '#E3D8CE', busy: 0.1 } }, { t0: 9, t1: 60, free: [B[0], A[0]] }] };
  const p = project({ backdrop: J.normalizeBackdrop(bd) });
  const plan = J.plan(p, { duration: 40, beats: [] });
  const first = plan.cuts.find(c => c.line === 0), later = plan.cuts.find(c => c.line === 3);
  ok(first.zone && first.zone.y === Math.round(0.06 * plan.H), 'line 0 uses the first free area');
  ok(later.zone && later.zone.y === Math.round(0.06 * plan.H), 'line 3 keeps the area the previous line used (still free)');
  ok(first.baseScheme != null && first.scheme >= plan.style.schemes.length - 2 && plan.style.schemes[first.scheme].bg === '#E3D8CE', 'tone gives a re-lit scheme');
  const sc = plan.style.schemes[first.scheme];
  ok(J.contrast(sc.fg, sc.bg) >= 4.5 && J.contrast(sc.accent, sc.bg) >= 2.9, 'fg / accent read on the backdrop colour');
  ok(later.baseScheme == null, 'no tone after 9 s → style colours');
  ok(!JSON.stringify(plan.style).includes('_toneCache'), 'scheme cache stays out of the exported plan');
  const snap = J.lineSnapshot(plan, 0);
  ok(snap.every((s, k) => s.scheme === plan.cuts.filter(c => c.line === 0 && c.utext != null)[k].baseScheme), 'ロック keeps the drawn scheme, not the re-lit one');
}
{ // avoid boxes only: the largest compositional area clear of the subject
  const p = project({ backdrop: J.normalizeBackdrop({ track: [{ t0: 0, t1: 60, avoid: [[0.55, 0.2, 0.25, 0.8]] }] }) });
  const plan = J.plan(p, { duration: 40, beats: [] });
  const c = plan.cuts.find(c => c.line === 0), Z = c.zone;
  ok(Z && Z.x + Z.w <= 0.55 * plan.W, 'free area from avoid stays left of the subject: ' + JSON.stringify(Z));
}
{ // legibility: busy picture → a readable treatment; explicit plate; none
  const busy = J.plan(project({ backdrop: J.normalizeBackdrop({ track: [{ t0: 0, t1: 60, free: A, tone: { color: '#808080', busy: 0.8 } }] }) }), { duration: 40, beats: [] });
  ok(busy.cuts.filter(c => c.line >= 0 && c.layout !== 'interlude').every(c => ['outline', 'doubleOutline', 'softShadow', 'hardShadow', 'boxed'].includes(c.treat) || c.treat === 'none' && busy.typeset), 'busy → readable treatments');
  const plate = J.plan(project({ overrides: { 0: { zone: { rect: A[0] }, legibility: 'plate' } } }), { duration: 40, beats: [] });
  ok(plate.cuts.filter(c => c.line === 0).every(c => c.treat === 'boxed'), 'plate → boxed');
  const none = J.plan(project({ backdrop: J.normalizeBackdrop({ legibility: 'none', track: [{ t0: 0, t1: 60, free: A, tone: { color: '#808080', busy: 0.9 } }] }) }), { duration: 40, beats: [] });
  const ref = J.plan(project({}), { duration: 40, beats: [] });
  ok(none.cuts.filter(c => c.line === 0).map(c => c.treat).join() === ref.cuts.filter(c => c.line === 0).map(c => c.treat).join(), 'none → treatments as drawn');
}
{ // key backgrounds stay white-on-black
  const p = project({ keyBg: 'green', backdrop: J.normalizeBackdrop({ track: [{ t0: 0, t1: 60, free: A, tone: { color: '#E3D8CE' } }] }) });
  const plan = J.plan(p, { duration: 40, beats: [] });
  ok(plan.cuts.every(c => c.baseScheme == null), 'no re-lit schemes with a key background');
}
{ // untrusted input
  ok(J.normalizeBackdrop({ track: [{ t0: 'x', t1: 2, free: [[0, 0, 1, 1]] }] }) === null, 'bad times dropped');
  ok(J.normalizeBackdrop({ track: [{ t0: 0, t1: 2, free: [[0, 0, 0.001, 1], 'a', [0, 0, 1]], tone: { color: 'red;}' } }] }) === null, 'bad rects / colours dropped');
  const n = J.normalizeBackdrop({ offset: '1.5', legibility: 'evil', source: 7, extra: '<script>', track: [{ t0: 3, t1: 4, free: [[-0.2, 0.5, 0.7, 0.9]], tone: { color: '#abcdef', busy: 9 }, x: 1 }, { t0: 0, t1: 1, avoid: [[0.1, 0.1, 0.2, 0.2]] }] });
  ok(n.offset === 1.5 && !n.legibility && !n.source && !n.extra && n.track[0].t0 === 0, 'normalized fields / sorted track');
  ok(JSON.stringify(n.track[1].free[0]) === JSON.stringify([0, 0.5, 0.5, 0.5]) && n.track[1].tone.busy === 1 && n.track[1].tone.color === '#ABCDEF', 'rect clipped, busy clamped');
  const p = project({ overrides: { 0: { zone: { rect: 'nope' }, tone: { color: 'url(x)' } } } });
  ok(J.plan(p, { duration: 40, beats: [] }).cuts.every(c => !c.zone && c.baseScheme == null), 'bad override zone / tone ignored');
}
console.log('backdrop:', checks, 'checks passed');
