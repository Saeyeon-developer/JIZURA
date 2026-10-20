// Node side of the backdrop analysis tool (tools/backdrop/backdrop.py calls it; JSON on stdout):
//   node tools/backdrop/jz.js plan PROJECT.jizura.json [DURATION]
//       → { W, H, aspect, duration, lines: [{ line, text, start, end, visEnd }], cuts: [{ line, start, dur, layout, zone }] }
//         song seconds; interludes and the title card are left out of lines, cuts keep only lyric cuts
//   node tools/backdrop/jz.js normalize BACKDROP.json
//       → J.normalizeBackdrop of the file (exit 2 when nothing usable is left)
const fs = require('fs');
const J = require('./engine').loadEngine();
const [cmd, file, arg] = process.argv.slice(2);
const read = f => JSON.parse(fs.readFileSync(f, 'utf8').replace(/^﻿/, ''));
const out = o => process.stdout.write(JSON.stringify(o));

if (cmd === 'plan') {
  const project = Object.assign(J.defaultProject(), read(file));
  const duration = +arg > 0 ? +arg : 0;
  const plan = J.plan(project, duration ? { duration, beats: [] } : null);
  out({
    W: plan.W, H: plan.H, aspect: project.aspect, duration: plan.duration,
    lines: plan.lines.filter(l => !l.interlude && l.text).map(l => ({ line: l.index, text: l.text, start: l.start, end: l.end, visEnd: l.visEnd })),
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
