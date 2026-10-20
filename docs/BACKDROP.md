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
* Cut-to-cut transitions between two cuts that both have a clipped area play inside those two areas only. Outside the
  areas the two frames cross-fade. Before this, a transition drew over the whole frame, after each cut had been
  clipped into its area. Rules, bars and cells (`tyRuleWipe`, `tyGridCells`, …) then crossed the subject. This
  holds for every output (transparent, keyed, opaque), because keyed output is laid over the video too. When one of
  the two cuts has no area or `clip: false`, it draws on the whole frame anyway, so the transition does too, as
  before. 中央を空ける bands are not backdrop areas and keep the old full-frame transitions. Projects without
  backdrop data render exactly as before (`Renderer.transAreas` returns null).
* Transparent output: transitions that paint the scheme background over the whole frame (11 of 27, solid or as a
  translucent veil) are replaced by a cross-fade when neither frame has anything at the corners. Before this they
  flashed a solid colour over the video under the layer. With areas, the check looks at the corners of the areas,
  where the transition played.
* After Effects: `J.planForAE` turns `centerFree` on when cuts have zones, so the panel builds each zoned cut in its
  own stage comp — which always crops to the area (`clip: false` is not honoured there).

## Checking a result

* `node dev/backdrop_test.js [REF_SRC_DIR]` — planner checks in Node; with the `src/` of a reference commit
  (`git archive <commit> src | tar -x -C /tmp/ref`) it also proves plans without backdrop data are unchanged.
* `python tools/backdrop/backdrop.py verify …` — the same measurements on the real background, with a report (below).
* `python dev/backdrop_tool_test.py [OUT] [--verify]` — the analysis tool on a synthetic pan (no assets needed);
  `--verify` also runs `verify` and then `fix` (only `project.overrides` may change; every line passes or has a reason).
* `python3 build.py --dev`, then serve `dev/www` and open `backdrop_check.html` — renders the lyric layer over a
  synthetic picture and reports, per frame, the lyric pixels outside the area and over the subject
  (`window.RESULT`), with a contact sheet. First run: with a subject-avoiding track, lyric pixels on the subject went
  from ~25 % of all lyric pixels (no backdrop) to ~1 %.

Suggested pass criteria for an agent: no lyric pixels outside the area (clipped), < 2 % on the subject mask, text
over its local background ≥ 4.5:1, smallest glyph ≥ 3 % of the output height. Fix a failing line by re-rolling its
`seed`, then moving / enlarging its area, then `legibility: "plate"`. `backdrop.py fix` does this automatically
(see "The fix loop" below).

## Where the data comes from

* **Director present** — the MV director agent already plans the shots and the subtitle space; it writes the
  `backdrop` block directly.
* **No director** — the analysis tool below writes the same block from the background picture.

## The analysis tool

`tools/backdrop/` (kept apart from the upstream files; it only reads `src/`). It needs Python 3 with numpy and Pillow,
Node, and ffmpeg / ffprobe on PATH; person masks also need `npx` and HyperFrames (`npx hyperframes remove-background`,
the u2net_human_seg model, CPU, weights fetched once). Without HyperFrames it falls back to saliency only.

```sh
python tools/backdrop/backdrop.py analyze song.jizura.json --video bg.mp4          # an AI / finished video
python tools/backdrop/backdrop.py analyze song.jizura.json --pan pan.json          # a pan over an illustration
python tools/backdrop/backdrop.py verify  song.jizura.json --video bg.mp4          # (or --pan) check the result
python tools/backdrop/backdrop.py fix     song.jizura.json --video bg.mp4          # check, then fix what fails
```

Common options: `--out DIR` (default `song.backdrop/` next to the project), `--offset S` (background time = song time
+ offset), `--duration S` (song length; default the background's), `--detector auto|person|saliency`; analyze:
`--legibility`, `--no-write`, `--review FILE`; verify / fix: `--browser auto|none|PATH`, `--scale 0.5`, `--timeout`
(per job); fix: `--rounds N` (default 30); `verify --fix` is the same as `fix`.

### analyze

1. **Plan once** (`tools/backdrop/jz.js`, the engine in Node via `tools/backdrop/engine.js`, which
   `dev/backdrop_test.js` also uses): each lyric line's `[start, visEnd]`.
2. **Sample the background** over each line. Video: frames at the start, middle and end and at most 1.5 s apart
   (ffmpeg, 640 px, cached). Pan: no video. The subject is found **once** on the illustration and its mask moves with
   the pan keyframes; frames are rendered from the illustration every 0.25 s for clutter and colour.
3. **Find the subject** in each frame (`detect.py`). The person mask (HyperFrames, all frames in one run) is used when
   it is solid. Otherwise a saliency guess (colour distance from the frame border's palette, centre prior; numpy
   only) is used, alone or added to a faint person mask, and the line is **flagged**. A line's subject is the union
   over its samples; `avoid` holds its boxes.
4. **Score free areas** (`zones.py`): every rectangle on a grid (0.03 margin; sizes from 0.28 × 0.18 of the frame)
   that keeps clear of the subject (≤ 0.5 % overlap, subject dilated by 0.02) scores
   `size + 0.25 · clearance − 0.6 · clutter + 0.15 · overlap with the previous line's area (+0.08 for the exact same
   area)`. *size* weighs the short side against 45 % of the frame's short side, plus √area. *clutter* is the
   luminance stddev and edge density inside, averaged over the samples. `free` lists the best four that don't
   overlap much. The previous line's area is listed only when it is the choice, because the planner keeps it wherever
   it appears. `tone.color` is the median colour inside the chosen area (all samples). `tone.busy` is the larger of
   its clutter and its **light / shadow spread**: the share of the area at least 2:1 (WCAG) away from the median on
   the side the text is on (the engine makes the text lighter when the median's `J.lum` < 0.5), with 30 % of the area
   counting as 1. The engine fits the text to 4.5:1 against the median only, so over those pixels it can fall to
   about 2:1. Clutter misses this case: a soft sunlit patch or a shadow across half the area has few edges and little
   local variation. From 13.5 % of the area, busy reaches 0.45 and `legibility: auto` turns a treatment on before
   verify has to find the failure. review.json's `detail` lists `clutter`, `spread` and `offTone` (the raw share).
   The contract is unchanged: busy is still one 0..1 number.
5. **Write** `OUT/backdrop.json`, check it with `J.normalizeBackdrop` (every entry must survive), and put it into the
   project (unless `--no-write`). One track entry per line, `t0`/`t1` = the line's `[start, visEnd]` + offset.

A line is flagged when the subject is a saliency guess or a faint person mask, when no area is clear of it, when the
best area is small (short side < 30 % of the frame), or when it is very busy (≥ 0.8).

**Pan description** — mirrors the GSAP tween on the illustration's wrapper in a HyperFrames composition (HyperFrames
pans are `x` / `y` / `scale` keyframes on a wrapper; there is no separate pan file to read):

```jsonc
{
  "image": "city.png",                   // relative to this file
  "frame": [1920, 1080],                 // composition size, px
  "box": [0, -540, 3840, 2160],          // the wrapper's left, top, width, height before the transform (default: cover)
  "origin": "50% 50%",                   // its transformOrigin (GSAP default)
  "offset": 0,                           // composition time = song time + offset
  "keys": [                              // poses at composition seconds; missing values carry over
    { "t": 0,  "x": 0, "y": 0, "scale": 1 },
    { "t": 24, "x": -1700, "y": -200, "scale": 1.25, "ease": "power1.inOut" }   // ease of the move into this key
  ]
}
```

`xPercent` / `yPercent` work as in GSAP. Eases: `none`, `power0–4`, `quad`, `cubic`, `quart`, `quint`, `sine`,
`expo`, `circ`, each `.in` / `.out` / `.inOut`.

### The agent hook: review.json

`OUT/review.png` shows each line's middle frame with the subject tinted red, the `avoid` boxes in red, the chosen
area thick green and the alternatives thin; flagged lines carry an orange **CHECK**. `OUT/review.json` has, per line,
the detector, the reasons, the proposal, the frame and mask paths, and `"confirm": null`. An agent (or a person) looks
at the flagged lines and sets `confirm` on any line:

| `confirm` | effect on the next run |
|---|---|
| `{}` | accept the proposal (the line is no longer flagged) |
| `{"avoid": [[x, y, w, h], …]}` | the subject, as boxes: replaces the detection; areas are scored again |
| `{"free": [[x, y, w, h], …]}` | the areas to use, best first (the tone is measured inside the first) |
| `{"tone": {"color": "#rrggbb", "busy": 0.3}}` | the tone |
| `{"skip": true}` | no entry for the line |

Then run `analyze … --review OUT/review.json`. Frames and person masks are cached, the confirms are kept in the new
review.json, and `verify` uses confirmed `avoid` boxes as the subject mask. The tool never calls a paid API.
Decisions that are not measurements (a line's `seed`, `legibility`, a fixed `zone`) go into `project.overrides`.

### verify

Plans the project with its backdrop and takes three samples per lyric cut (35 %, 60 % and 92 % into it). For each
sample it writes the background frame and subject mask at half the output size, then serves those files together
with `verify.html` and the engine (`src/*.js` joined, as `build.py --dev` does, without running the build) on
127.0.0.1. Headless Chrome or Edge renders the transparent layer, measures it, and posts the results back. With
`--browser none` the tool prints the URL instead, for any browser, such as the Claude desktop browser pane. Before
measuring, the page loads fonts as the app's export does: the plan's faces, for the lyrics, title and HUD text. It
then draws every sample once, so that faces requested along the way (unicode-range subsets, decoration text) are
loaded too. Without this, the first frames of a fresh page measured fallback glyphs, and the same frame could pass or
fail depending on what had been drawn before it. The page stays open for further jobs (the fix loop sends one per
round), and a job can hold several variants of the project, each limited to some lines. Output goes to
`OUT/verify/`:

* `report.md` / `report.json` — per line: worst value of each measure, pass / fail, what to try
  (seed → area → plate), and the failing frames with the cuts drawn at the time. A cut still in its transition is
  marked `⇐name`; while it lasts, the previous cut's area also counts as inside.
* `contact.png` — one tile per cut (its 60 % sample): background, subject tinted red, the lyric layer, area dashed.
  The page posts the tiles one by one (`tiles/`), and the tool lays them out, so a fix round only re-renders its lines.
* `fails/*.png` — the composite of every failing frame.

| measure | how | pass |
|---|---|---|
| outside | ink (alpha > 40) more than 1.5 px outside every active cut's area | 0 px |
| on subject | ink on the subject mask / ink | < 2 % |
| contrast | median glyph luminance (pixels in the scheme's `fg` / `ink`) vs. the composited ring 1–3 px around the glyphs (the video, or the plate / outline / shadow when there is one); `bgContrast` is vs. the bare background | ≥ 4.5 : 1 |
| glyph | 25th percentile height of the main glyph shapes (≥ 0.3 × the tallest), with the parts of a character joined first | ≥ 3 % of H |

The glyph height is approximate: stacked vertical text merges into columns and measures too tall.

### The fix loop

`backdrop.py fix` (or `verify --fix`) verifies, then works through the failing lines with the fixes above, in order,
until each passes or its fixes run out. Fixes are decisions, so they go into `project.overrides[line]` only:
`seed`, `zone` + `tone`, and `legibility`. The `backdrop` block, the rest of the project and `src/` stay as they are.

1. **Seed.** Three re-rolls (`overrides.seed` = the current one + 1, + 2, + 3). The planner draws a line from
   `hash(project seed, line, seed)`. When every failing frame is a transition frame (`⇐name`), it tries five, because
   the seed picks the transition.
2. **Area** for "subject" / "glyph": the track entry's other `free` areas (for "glyph" only those with a short side at
   least 12 % longer), then for "glyph" larger grid areas that stay clear of the line's subject (`zones.larger`; the
   subject is analyze's union over the line, or the confirmed `avoid`). The area goes into `overrides.zone.rect` with
   the tone measured inside it (`overrides.tone`), because the entry's tone describes its first area.
   **Legibility** for "contrast": `"plate"`, then `"outline"`. When a line fails both, area + plate combinations are
   tried too.
3. **Seeds once more** (three more) when the area / legibility step has nothing that applies or nothing that helps.
   The remaining glyph and contrast failures are mostly the layouts the seed picked: small type, text that shrinks
   on its way out, glow or stickers in light colours right around the glyphs.

**Left to right.** A line's picks depend on the lines before it, because the planner avoids what was used recently.
Re-rolling line 3 redraws lines 4, 5 … as well, and can break lines that passed. Fixing several lines at once
therefore churned on the synthetic pan, ending with more failing lines than it started with. So the loop fixes the
earliest failing line first (the frontier) and judges the lines after it only afterwards. Lines before the frontier
never change again. The exception is the line just before it, whose last cut the frontier's first transition
replaces, so it is checked as well.

**One round per step.** All candidates of a step (three seeds, or the areas and legibility values) are rendered
side by side, as variants of the project in one job, and only for the frontier line and the line before it. The
first passing candidate, in the order above, is kept. If none passes, the best one is kept when it is better than
what the line had. "Better" counts frames with ink outside the area or on the subject first, then failing frames,
then kinds of failure. A candidate that makes the line before it worse is never kept. Then every line whose drawing
changed is verified again. `jz.js plan` gives each line a `sig`: a hash of its cuts with their colours, the cut
before it and the screen effects over it. Lines whose `sig` did not change are not rendered again. A ロック line is
left alone.

**Report.** `report.md` / `report.json` are the merged result: each line's rows come from the last render of its
current drawing, which is the same as a fresh `verify` would give. A **Fix loop** section lists, per line, what it
failed on first and when its turn came, every candidate with its result, what was kept, and why a line is left
failing. Reasons include: the area was confirmed in review.json; no larger area clear of the subject; the area is
already large and the layouts set small text (pick a layout or split the cut); the text reads on the bare picture,
so the line's own drawing is too light around the glyphs; the other free areas did not help with the subject
(detection or framing: confirm `avoid` / `free`, or a director's call); or the line was not reached within
`--rounds`. `fix.json` holds the same data, plus the overrides each line ended with.

On the synthetic pan, a fix run takes about 45 s for 9 lines, mostly 13 rounds of ~3 s. Re-rolls cascade, so it
renders about 100 lines in all, compared with 9 for one verify. Lines 1, 6, 7 and 8 pass after it. Line 2 is left
on glyph height because its area was confirmed in review.json (the test does that on purpose). Line 5 is left on
glyph height because its area is already 90 % of the frame and six seeds all drew small type.

### First results

* **Synthetic pan** (`dev/backdrop_tool_test.py`: 9 lines, a figure sliding into the frame). Every chosen area stays
  clear of the figure's true moving box. In verification, 0.2 % of all lyric ink landed on the subject. The failures
  were small or low-contrast cuts, plus the transition frames below. With transitions kept inside the areas, no
  frame has ink outside its area, and ink on the subject is 0.0 %. Lines 1, 5, 7 and 8 still failed, on contrast or
  glyph height only. With fonts loaded before measuring (see verify), line 6 fails on contrast as well: it had passed
  on fallback glyphs. None of these contrast failures is a light / shadow mix, since the sky in the areas is even
  (busy ≤ 0.05). They come from the line's own drawing (glow, stickers, halftone around the glyphs), or from exit
  frames where no glyph colour is found and solid shapes are measured instead. `fix` passes 1, 6, 7 and 8 (above).
* **Game footage** (14 s: a sandcastle, legs, two close-up faces). The person mask is solid on the legs and faint on
  the stylised faces (now noted). The sandcastle falls back to saliency, which is weak on photo-like scenes, and is
  flagged. Close-ups leave only small areas, so they are flagged and fail on glyph height; a plate or a director's
  framing is the answer there.
* **Engine finding (fixed):** transitions (`tyRuleWipe`, `tyGridCells`, …) drew over the whole frame and were not
  clipped to the areas. Mid-transition frames put rules and bars over the subject; the corner check from stage 1 only
  caught full-frame fills. On the synthetic pan, lines 2, 3, 4 and 8 failed "outside" on ⇐tyGridCells / ⇐tyRuleWipe
  frames only (1.2k–8.4k px; lines 3 and 4 also had 2.7 % and 3.8 % of their ink on the subject). Now transitions
  play inside the two cuts' areas (see Rendering notes), and those frames have 0 px outside and 0 % on the subject.
