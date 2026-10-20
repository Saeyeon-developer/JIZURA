// Node side of the backdrop analysis tool (tools/backdrop/backdrop.py calls it; JSON on stdout):
//   node tools/backdrop/jz.js plan PROJECT.jizura.json [DURATION]
//       → { W, H, aspect, duration, lines: [{ line, text, start, end, visEnd, sig }], cuts: [{ line, start, dur, layout, zone }] }
//         song seconds; interludes and the title card are left out of lines, cuts keep only lyric cuts.
//         sig: hash of everything that draws the line (its cuts with their colours, the cut before it, which a
//         transition into the line shows, and the screen effects over it): the fix loop re-verifies a line only when
//         its sig changed
//   node tools/backdrop/jz.js normalize BACKDROP.json
//       → J.normalizeBackdrop of the file (exit 2 when nothing usable is left)
const fs = require('fs'), crypto = require('crypto');
const J = require('./engine').loadEngine();
const [cmd, file, arg] = process.argv.slice(2);
const read = f => JSON.parse(fs.readFileSync(f, 'utf8').replace(/^﻿/, ''));
const out = o => process.stdout.write(JSON.stringify(o));

if (cmd === 'plan') {
  const project = Object.assign(J.defaultProject(), read(file));
  const duration = +arg > 0 ? +arg : 0;
  const plan = J.plan(project, duration ? { duration, beats: [] } : null);
  const sig = li => {
    const own = plan.cuts.filter(c => c.line === li && c.layout !== 'interlude');
    if (!own.length) return null;
    const s = Math.min(...own.map(c => c.start)), e = Math.max(...own.map(c => c.end)), k = plan.cuts.indexOf(own[0]);
    const cuts = (k > 0 ? [plan.cuts[k - 1]] : []).concat(own);
    const body = JSON.stringify({ cuts, schemes: cuts.map(c => plan.style.schemes[c.scheme]), events: plan.events.filter(v => v.t + (v.dur || 0) >= s && v.t <= e) });
    return crypto.createHash('sha1').update(body).digest('hex').slice(0, 12);
  };
  out({
    W: plan.W, H: plan.H, aspect: project.aspect, duration: plan.duration,
    lines: plan.lines.filter(l => !l.interlude && l.text).map(l => ({ line: l.index, text: l.text, start: l.start, end: l.end, visEnd: l.visEnd, sig: sig(l.index) })),
    cuts: plan.cuts.filter(c => c.line >= 0 && c.layout !== 'interlude').map(c => ({ line: c.line, start: c.start, dur: c.dur, layout: c.layout, zone: c.zone || null })),
  });
} else if (cmd === 'normalize') {
  const b = J.normalizeBackdrop(read(file));
  out(b);
  if (!b) process.exitCode = 2;
} else {
  console.error('usage: node jz.js plan PROJECT [DURATION] | normalize BACKDROP.json');
  process.exitCode = 1;
}
