# Backdrop: lyrics over a background video

JIZURA can be the lyric layer of a music video: the picture (a slow pan over a large illustration, or an AI video) is
made first and placed to the song, and JIZURA renders a transparent layer on top. For that it has to know where the
picture leaves room for text and what colour it is there. **The engine never looks at the video.** Whoever analysed
it — an MV director agent, the standalone analysis tool, or a person — writes the result into the project as data,
and the planner only reads it. Projects without this data plan exactly as before (checked by `dev/backdrop_test.js`).

Code: `src/08c_backdrop.js` (contract, resolution, colours), hooks in `src/08_planner.js` and `src/09_render.js`.

## Input contract

Two layers: **measurements** keyed by time (they survive lyric edits and retiming), and **decisions** keyed by lyric
line (they always win).

```jsonc
"backdrop": {
  "version": 1,
  "source": "bg_v3.mp4",          // informational
  "offset": 0,                    // track time = song time + offset (seconds)
  "legibility": "auto",           // default for every line: auto | none | outline | shadow | plate
  "clip": true,                   // false: drawing may run past the free areas
  "track": [
    { "t0": 0.0, "t1": 8.5,
      "free":  [[0.03, 0.05, 0.48, 0.50]],         // where text may go, best first — x, y, w, h in 0..1 of the frame
      "avoid": [[0.55, 0.20, 0.25, 0.80]],         // the subject; used to find free space when "free" is missing
      "tone":  { "color": "#1F2945", "busy": 0.15 } // the picture inside the free area: median colour, 0..1 clutter
    }
  ]
},
"overrides": {
  "3": { "zone": { "rect": [0.03, 0.70, 0.52, 0.28], "clip": true },
         "tone": { "color": "#E3D8CE", "busy": 0.1 },
         "legibility": "plate" }
}
```

Everything is validated on load (`J.normalizeBackdrop`, and strictly at use for overrides): rectangles are clipped to
the frame and dropped below 2 %, colours must be `#rrggbb`, unknown fields are discarded. Rectangles are normalised,
so changing the aspect or resolution keeps them meaningful — but they describe *that* framing, so re-run the
analysis when the aspect changes.

## How the planner uses it

For each lyric line shown over `[start, visEnd]`:

1. **Area.** `overrides[i].zone.rect` if present. Otherwise the track entry that overlaps the line most; its `free`
   list (best first), or areas derived from `avoid` (halves, thirds, quarters and the bands left above / below /
   beside the subjects, largest first). If the area the previous line used is still in the list it is kept, so the
   text does not jump around. Every cut of the line is laid out in that area (layouts are planned for its size) and,
   by default, clipped to it.
2. **Colours.** With a tone, each cut's colour scheme is copied with `bg` set to the tone colour and `fg` / `ink`
   brought to 4.5:1, `sub` / `accent` to 3:1 (`J.fitContrast` — lightness only, the style's hues stay). The copies
   are appended to `plan.style.schemes`; `cut.baseScheme` keeps the drawn one (ロック and 統一感 use it). Key
   backgrounds (green / black) ignore tones.
3. **Legibility.** `plate` → boxed, `outline` → outline / double outline, `shadow` → soft / hard shadow, `auto` →
   one of those when `tone.busy ≥ 0.45`, `none` → as drawn. A per-line `treat` override wins. It uses its own random
   stream, so the other picks of the line do not change.

`centerFree` (中央を空ける) still wins over backdrop areas when it is switched on.

## Rendering notes

* A cut with a zone is drawn translated into it and clipped (`zone.clip === false` turns the clip off). About a fifth
  of the layouts are full-frame designs (tile, marquee, splitScreen, the horror flashlight / door gap…) that spill
  out of an unclipped area — keep the clip unless you know the layouts in use.
* Layouts are scaled to the area, and use roughly half of it. Small areas give small text: aim for areas at least
  35–40 % of the frame's short side.
* Transparent output: transitions that paint the scheme background over the whole frame (11 of 27, solid or as a
  translucent veil) are replaced by a cross-fade when neither frame has anything at the corners. Before this they
  flashed a solid colour over the video under the layer.
* After Effects: `J.planForAE` turns `centerFree` on when cuts have zones, so the panel builds each zoned cut in its
  own stage comp — which always crops to the area (`clip: false` is not honoured there).

## Checking a result

* `node dev/backdrop_test.js [REF_SRC_DIR]` — planner checks in Node; with the `src/` of a reference commit
  (`git archive <commit> src | tar -x -C /tmp/ref`) it also proves plans without backdrop data are unchanged.
* `python3 build.py --dev`, then serve `dev/www` and open `backdrop_check.html` — renders the lyric layer over a
  synthetic picture and reports, per frame, the lyric pixels outside the area and over the subject
  (`window.RESULT`), with a contact sheet. First run: with a subject-avoiding track, lyric pixels on the subject went
  from ~25 % of all lyric pixels (no backdrop) to ~1 %.

Suggested pass criteria for an agent: no lyric pixels outside the area (clipped), < 2 % on the subject mask, text
over its local background ≥ 4.5:1, smallest glyph ≥ 3 % of the output height. Fix a failing line by re-rolling its
`seed`, then moving / enlarging its area, then `legibility: "plate"`.

## Where the data comes from (next step)

* **Director present** — the MV director agent already plans the shots and the subtitle space; it writes the
  `backdrop` block directly.
* **No director** — the standalone analysis (to be built) produces the same block from the finished video:
  * HyperFrames pans: analyse the source illustration once (subject mask) and move the boxes with the pan keyframes
    — no frame sampling needed.
  * AI video: sample frames at each line's start / middle / end, propose subject boxes with a local segmentation
    model, and let an agent confirm the ambiguous lines and the final contact sheet.
  * Free areas are scored by size, distance to the subject, clutter and closeness to the previous line's area; the
    tone is the median colour inside the chosen area.
