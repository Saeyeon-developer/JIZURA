"""`backdrop.py fix` (or `verify --fix`): verify, then fix the failing lines and verify them again.

Fixes are decisions, so they go into project.overrides[line] only (the backdrop block and src/ stay as they are).
Every failing line gets the fixes of docs/BACKDROP.md, in order:
  1. seed        re-roll the line (overrides.seed: the planner draws a line from hash(project seed, line, seed)).
                 SEEDS candidates, TRANS_SEEDS when every failing frame is a transition (⇐name) frame.
  2. area        on "subject" / "glyph": the track entry's other free areas (for "glyph" only larger ones), then for
                 "glyph" larger grid areas clear of the subject (zones.larger). Written as overrides.zone.rect with
                 the tone measured inside it (overrides.tone): the entry's tone describes its first area.
     legibility  on "contrast": overrides.legibility "plate", then "outline" (with the areas when both fail).
  3. seed        once more (SEEDS further) when step 2 has nothing that applies or helps: what is left is mostly the
                 layouts the seed drew (small type, glow / stickers around the glyphs).
Lines are fixed from left to right, because a line's picks depend on the lines before it (the planner avoids what
was used recently): re-rolling line 3 redraws lines 4, 5 … too, and those are only judged afterwards. The line
being fixed (the frontier) gets one round per step: all candidates of the step are rendered side by side as variants
of the project in one job (only that line and the one before it, whose last cut the line's first transition
replaces), the first in the order above that passes is kept — or else the best one, when it is better — and then
every line whose drawing changed (jz.js `sig`) is verified again. A line whose steps run out keeps its best version
and the report says why (no larger area, the subject detection, a locked line …).
Writes OUT/verify/report.md / .json (merged, with a "Fix loop" section), contact.png, fix.json.
"""
import copy, json, os, shutil
import numpy as np

import verify as V
import zones

SEEDS, TRANS_SEEDS = 3, 5
LEGIBILITY = ('plate', 'outline')
LARGE = 0.6            # an area with this short side (of the frame's) is not why the text is small
MAX_AREAS = 3          # area candidates per round

def badness(rep_line):
    """to compare versions of a line: ink outside the area or on the subject first, then failing frames, then kinds"""
    if not rep_line:
        return (0, 0, 0)
    frames = rep_line['failingFrames']
    return (sum(1 for f in frames if {'outside', 'subject'} & set(f['fails'])), len(frames), len(rep_line['fails']))

def same_rect(a, b, tol=2e-3):
    return a is not None and b is not None and all(abs(x - y) < tol for x, y in zip(a, b))

def short_side(r, W, H):
    return min(r[2] * W, r[3] * H) / min(W, H)

def overrides_of(proj, li):
    return ((proj.get('overrides') or {}).get(str(li))) or {}

def apply(proj, li, patch):
    ovs = proj.setdefault('overrides', {})
    ov = ovs.setdefault(str(li), {})
    ov.update(patch)

def describe(patch):
    parts = []
    if 'seed' in patch:
        parts.append('seed %d' % patch['seed'])
    if 'zone' in patch:
        parts.append('area [%s] (busy %.2f)' % (', '.join('%.2f' % v for v in patch['zone']['rect']), patch['tone'].get('busy', 0)))
    if 'legibility' in patch:
        parts.append('legibility ' + patch['legibility'])
    return ' + '.join(parts)

def entry_of(project, pl):
    """the backdrop track entry the planner uses for a line: the one overlapping it most (J.lineBackdrop)"""
    bd = project.get('backdrop') or {}
    off = float(bd.get('offset') or 0)
    best, entry = 0, None
    for it in bd.get('track') or []:
        o = min(pl['visEnd'], it['t1'] - off) - max(pl['start'], it['t0'] - off)
        if o > best:
            best, entry = o, it
    return entry

def rect_of(info, li):
    z = next((c['zone'] for c in info['cuts'] if c['line'] == li and c.get('zone')), None)
    if not z:
        return None
    return [round(z['x'] / info['W'], 4), round(z['y'] / info['H'], 4), round(z['w'] / info['W'], 4), round(z['h'] / info['H'], 4)]

class Line:
    """what the loop tried for one lyric line"""

    def __init__(self, li, ov, fails):
        self.li, self.turn_fails = li, fails      # what it failed on when its turn came
        self.base_seed = int(ov.get('seed') or 0)
        self.phase = 'seed'                       # seed → fix (while it helps) → seed2 → done
        self.next_seed = self.base_seed + 1
        self.rects, self.legs = [], []            # areas / legibility values tried
        self.had_areas = False
        self.rounds = []
        self.reasons = {}
        self.samples = None

class Fixer:
    def __init__(self, ctx, args, B):
        self.ctx, self.args, self.B = ctx, args, B
        self.subjects = None
        self.confirms = {}
        review = args.review or os.path.join(ctx.out, 'review.json')
        if os.path.exists(review):
            for ln in B.load_json(review).get('lines', []):
                if ln.get('confirm'):
                    self.confirms[ln['line']] = ln['confirm']

    def line_samples(self, st, pl):
        """the background and subject over the line, as analyze sampled them: rgbs at analysis size, subject union"""
        if st.samples is None:
            B, ctx = self.B, self.ctx
            if self.subjects is None:
                self.subjects = B.Subjects(ctx.src, ctx.cache, self.args.detector)
            step = B.PAN_STEP if ctx.src.kind == 'pan' else B.VIDEO_STEP
            ts = [round(t + ctx.offset, 3) for t in B.sample_times(pl['start'], pl['visEnd'], step)]
            got = self.subjects.sample(ts)
            conf = self.confirms.get(st.li) or {}
            subj = B.raster(conf['avoid'], self.subjects.size) if conf.get('avoid') else np.logical_or.reduce([got[t][1] for t in ts])
            st.samples = ([got[t][0] for t in ts], subj)
        return st.samples

    def areas(self, st, pl, entry, cur, fails):
        W, H = self.ctx.src.w, self.ctx.src.h
        glyph = 'glyph' in fails
        opts = [r for r in (entry or {}).get('free') or [] if not glyph or cur is None or short_side(r, W, H) >= 1.12 * short_side(cur, W, H)]
        if glyph and cur is not None:
            rgbs, subj = self.line_samples(st, pl)
            opts += zones.larger(subj, rgbs, (W, H), cur)
        out = []
        for r in opts:
            if not same_rect(r, cur) and not any(same_rect(r, t) for t in st.rects + out):
                out.append(r)
        return out[:MAX_AREAS]

    def candidates(self, st, rep_line, ov, pl, entry, info):
        """[(patch, description)] for the line's next step, in the order of docs/BACKDROP.md"""
        if st.phase in ('seed', 'seed2'):
            frames = rep_line['failingFrames']
            trans = bool(frames) and all('⇐' in f['cuts'] for f in frames)
            seeds = list(range(st.next_seed, st.next_seed + (TRANS_SEEDS if trans else SEEDS)))
            st.next_seed = seeds[-1] + 1
            return [({'seed': n}, 'seed %d%s' % (n, ' (transition frames)' if trans else '')) for n in seeds]
        fails = set(rep_line['fails'])
        conf = self.confirms.get(st.li) or {}
        areas, legs = [], []
        if fails & {'subject', 'glyph'} and not conf.get('free'):
            areas = self.areas(st, pl, entry, rect_of(info, st.li), fails)
            st.had_areas |= bool(areas)
            st.rects += areas
        if 'contrast' in fails and not ov.get('treat'):
            legs = [l for l in LEGIBILITY if l not in st.legs and l != ov.get('legibility')]
            st.legs += legs
        patches = []
        for r in areas:
            rgbs, _ = self.line_samples(st, pl)
            zone = dict(ov.get('zone') or {}, rect=r)
            zone.setdefault('clip', True)
            patches.append({'zone': zone, 'tone': zones.tone_of(r, rgbs)})
        patches += [{'legibility': l} for l in legs]
        if areas and legs:
            patches += [dict(a, legibility=legs[0]) for a in patches[:2]]
        return [(p, describe(p)) for p in patches]

    def why(self, st, rep_line, ov, info):
        """why each remaining failure is left as it is"""
        fails = rep_line['fails']
        W, H = self.ctx.src.w, self.ctx.src.h
        conf = self.confirms.get(st.li) or {}
        out = []
        for f in fails:
            if f in ('glyph', 'subject') and conf.get('free'):
                out.append('%s: the area was confirmed in review.json (confirm.free); change it there' % f)
            elif f == 'glyph':
                cur = rect_of(info, st.li)
                sh = short_side(cur, W, H) if cur else 0
                if not cur:
                    out.append('glyph: the line has no area')
                elif sh >= LARGE:
                    out.append('glyph: the area is already large (short side %.0f %% of the frame) and the seeds tried gave small glyphs: the '
                               'layouts drawn here set small text or shrink it on the way out; pick a layout (overrides.layout / cutTech) '
                               'or split the cut' % (sh * 100))
                elif st.had_areas:
                    out.append('glyph: larger areas tried (%d), glyphs still small (short side now %.0f %% of the frame): re-frame '
                               'the shot or split the cut' % (len(st.rects), sh * 100))
                else:
                    out.append('glyph: no larger area available (short side %.0f %% of the frame; nothing larger in the free list or on '
                               'the grid clear of the subject): re-frame the shot or split the cut' % (sh * 100))
            elif f == 'subject':
                out.append('subject: %s; the subject detection or the framing needs a look (review.json confirm.avoid / confirm.free, '
                           'or a director\'s call)' % ('the other free areas did not help' if st.had_areas else 'no other free area'))
            elif f == 'contrast':
                bare = rep_line.get('bgContrast')
                if ov.get('treat'):
                    out.append('contrast: the line\'s own treat override (%s) wins over legibility' % ov['treat'])
                elif bare is not None and bare >= V.CRITERIA['contrast']:
                    out.append('contrast: plate and outline tried; the text reads on the bare picture (%s:1), so it is the line\'s own '
                               'drawing around the glyphs (glow, stickers, panels in light colours): pick a layout or treat for the line '
                               '(overrides.layout / treat / cutTech)' % bare)
                else:
                    out.append('contrast: plate and outline tried: a calmer area, or a tone confirm')
            elif f == 'outside':
                out.append('outside: the seeds tried did not keep the drawing in the area; give the neighbouring line an area '
                           '(a transition next to a line without one draws on the whole frame) or check clip')
        return out

def run(args, B):
    ctx = V.Ctx(args, B)
    fx = Fixer(ctx, args, B)
    proj = B.load_json(args.project)
    V.check_offset(ctx, proj, B)
    ses = V.Session(ctx, args, B)
    try:
        loop(ctx, fx, ses, args, B, proj)
    finally:
        ses.drop()
        ses.close()
        shutil.rmtree(os.path.join(ctx.vdir, 'variants'), ignore_errors=True)

def loop(ctx, fx, ses, args, B, proj):
    vardir = os.path.join(ctx.vdir, 'variants')
    os.makedirs(vardir, exist_ok=True)
    info = ctx.plan(args, B)
    res = ses.render([{'id': 'base', 'project': proj, 'info': info}])['base']
    rows, errors = {}, list(res.get('errors', []))
    for r in res.get('rows', []):
        rows.setdefault(r['line'], []).append(r)
    merged = lambda: {'rows': [r for li in sorted(rows) for r in rows[li]], 'errors': errors}
    report = V.summarize(merged(), info)
    sigs = {l['line']: l['sig'] for l in info['lines']}
    first = {l['line']: l['fails'] for l in report['lines'] if not l['pass']}
    B.log('fix: %d line(s) failing: %s' % (len(first), sorted(first)))
    states, rounds, rendered = {}, 0, len(sigs)
    for rnd in range(1, args.rounds + 1):
        byline = {l['line']: l for l in report['lines']}
        front = next((l for l in report['lines'] if not l['pass'] and (l['line'] not in states or states[l['line']].phase != 'done')), None)
        if front is None:
            break
        li = front['line']
        pl = next(l for l in info['lines'] if l['line'] == li)
        ov = overrides_of(proj, li)
        st = states.get(li)
        if st is None:
            st = states[li] = Line(li, ov, front['fails'])
            if ov.get('lock'):
                st.phase, st.reasons['lock'] = 'done', 'the line is locked (ロック): the loop leaves it as it is'
                continue
        cands = fx.candidates(st, front, ov, pl, entry_of(proj, pl), info)
        if not cands and st.phase == 'fix':            # no area or legibility step applies: the seeds once more
            st.phase = 'seed2'
            cands = fx.candidates(st, front, ov, pl, entry_of(proj, pl), info)
        if not cands:
            st.phase = 'done'
            continue
        rounds = rnd
        prev = max((x for x in sigs if x < li), default=None)
        watch = [x for x in (prev, li) if x is not None]
        runs, variants = [], []
        for k, (patch, desc) in enumerate(cands):
            pk = copy.deepcopy(proj)
            apply(pk, li, patch)
            path = os.path.join(vardir, 'v%d.jizura.json' % k)
            B.save_json(path, pk)
            ik = B.jz('plan', path, ctx.duration)
            sk = {l['line']: l['sig'] for l in ik['lines']}
            changed = {x for x in watch if sk.get(x) != sigs.get(x)}
            v = {'k': k, 'patch': patch, 'desc': desc, 'project': pk, 'info': ik, 'sigs': sk, 'lines': changed, 'prefix': 'v%dx' % k}
            variants.append(v)
            if li in changed:
                runs.append({'id': 'v%d' % k, 'prefix': v['prefix'], 'project': pk, 'info': ik, 'lines': changed})
        got = ses.render(runs) if runs else {}
        rendered += sum(len(r['lines']) for r in runs)
        cur_bad = {x: badness(byline.get(x)) for x in watch}
        tried, chosen, best = [], None, None
        for v in variants:
            vrows = {x: ([r for r in got.get('v%d' % v['k'], {}).get('rows', []) if r['line'] == x] if x in v['lines'] else rows.get(x, []))
                     for x in watch}
            vrep = {l['line']: l for l in V.summarize({'rows': [r for x in watch for r in vrows[x]]}, v['info'])['lines']}
            v['rows'] = vrows
            ok_prev = prev is None or badness(vrep.get(prev)) <= cur_bad[prev]
            mine = vrep.get(li)
            v['bad'] = badness(mine)
            res_txt = ('ok' if mine and mine['pass'] else ', '.join(mine['fails']) if mine else '-') + ('' if li in v['lines'] else ' (drawing unchanged)')
            if not ok_prev:
                res_txt += '; line %d gets worse' % prev
            tried.append({'step': v['desc'], 'result': res_txt})
            if not ok_prev:
                continue
            if mine and mine['pass'] and chosen is None:
                chosen = v
            if best is None or v['bad'] < best['bad']:
                best = v
        if chosen is None and best is not None and best['bad'] < cur_bad[li]:
            chosen = best
        st.rounds.append({'round': rnd, 'tried': tried, 'kept': chosen['desc'] if chosen else None})
        B.log('fix round %d, line %d: %s → %s' % (rnd, li, '; '.join('%s: %s' % (t['step'], t['result']) for t in tried),
                                                'kept ' + chosen['desc'] if chosen else 'nothing better'))
        if st.phase == 'seed':
            st.phase = 'fix'
        elif st.phase == 'seed2':
            st.phase = 'done'
        elif chosen is None:
            st.phase = 'seed2'
        if chosen:
            proj = chosen['project']
            B.save_json(args.project, proj)
            info = chosen['info']
            for x in chosen['lines']:
                rows[x] = chosen['rows'][x]
            ses.adopt(chosen['prefix'], chosen['lines'])
            redo = {x for x, s in chosen['sigs'].items() if s != sigs.get(x)} - chosen['lines']
            sigs = chosen['sigs']
            for x in list(rows):
                if x not in sigs:
                    del rows[x]
            if redo:
                r2 = ses.render([{'id': 'base', 'project': proj, 'info': info, 'lines': redo}])['base']
                errors += r2.get('errors', [])
                rendered += len(redo)
                for x in redo:
                    rows[x] = [r for r in r2.get('rows', []) if r['line'] == x]
            report = V.summarize(merged(), info)
        ses.drop()
    # what is left, and why
    byline = {l['line']: l for l in report['lines']}
    summary = []
    for li in sorted(set(states) | set(first) | {l['line'] for l in report['lines'] if not l['pass']}):
        st, l = states.get(li), byline.get(li)
        ok = bool(l and l['pass'])
        why = []
        if not ok and l:
            if st and 'lock' in st.reasons:
                why = [st.reasons['lock']]
            elif st and st.phase == 'done':
                why = fx.why(st, l, overrides_of(proj, li), info)
            else:
                why = ['not reached within %d round(s): run fix again (or with more --rounds)' % args.rounds]
        summary.append({'line': li, 'failedFirst': first.get(li, []), 'failedOnItsTurn': st.turn_fails if st else [],
                        'rounds': st.rounds if st else [], 'pass': ok, 'now': [] if ok or not l else l['fails'], 'why': why,
                        'overrides': {k: v for k, v in overrides_of(proj, li).items() if k in ('seed', 'zone', 'tone', 'legibility')}})
    md = ['', '## Fix loop', '',
          '%d round(s), %d line renders in all (the song has %d lines). Fixes are in `project.overrides` of %s, in the order '
          'seed → area / legibility (docs/BACKDROP.md, "The fix loop"). Lines are fixed left to right: a fix redraws the '
          'lines after it, which are judged afterwards.' % (rounds, rendered, len(sigs), os.path.basename(args.project)), '',
          '| line | failed first | on its turn | tried (round: step → result) | now |', '|---|---|---|---|---|']
    for s in summary:
        steps = '<br>'.join('%d: %s%s' % (r['round'], '; '.join('%s → %s' % (t['step'], t['result']) for t in r['tried']),
                                          ' — **kept %s**' % r['kept'] if r['kept'] else ' — nothing better') for r in s['rounds'])
        md.append('| %d | %s | %s | %s | %s |' % (s['line'], ', '.join(s['failedFirst']) or 'ok', ', '.join(s['failedOnItsTurn']) or '-',
                                                (steps or '-').replace('|', '/'),
                                                'ok' if s['pass'] else '**%s** — %s' % (', '.join(s['now']), ' '.join(s['why']).replace('|', '/'))))
    report = V.write_report(ctx, args, B, info, merged(), '\n'.join(md) + '\n')
    B.save_json(os.path.join(ctx.vdir, 'fix.json'), {'rounds': rounds, 'renderedLines': rendered, 'lines': summary})
    out = V.brief(report, ctx.vdir)
    out.update(rounds=rounds, fixed=[s['line'] for s in summary if s['pass'] and s['failedFirst']],
               unresolved={s['line']: s['why'] for s in summary if not s['pass']})
    print(json.dumps(out, ensure_ascii=False))
