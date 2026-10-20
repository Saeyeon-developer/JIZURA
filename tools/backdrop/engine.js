// The JIZURA engine (src/*.js) in Node, without a browser: canvas, fonts and the DOM are stubbed, so planning works
// (layout measuring sees fixed text metrics) but nothing is drawn. Used by dev/backdrop_test.js and tools/backdrop/jz.js.
//   const J = require('./engine').loadEngine(srcDir)
const fs = require('fs'), vm = require('vm'), path = require('path');

function loadEngine(srcDir = path.join(__dirname, '..', '..', 'src')) {
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

module.exports = { loadEngine };
