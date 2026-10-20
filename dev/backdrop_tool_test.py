"""Smoke test of the backdrop analysis tool (tools/backdrop) on a synthetic HyperFrames-style pan — no assets needed:
  python dev/backdrop_tool_test.py [OUT_DIR] [--verify]
Draws a 3840×2160 illustration (night sky, hills, a bright figure whose box is known), a pan description that slides
across it and pushes in, and a 9-line project; runs `backdrop.py analyze` and checks that
  * the block passes J.normalizeBackdrop with one entry per line, written into the project
  * the pan maths: the figure's known box moved by the pan keyframes lands where the rendered frame shows it
  * every chosen free area keeps clear of the figure's (known, moved) box during its line
  * the review hook exists (review.json + review.png) and a confirmed `free` is taken as given on a re-run
  * an area mixing a sunlit patch and shadow reads as busy (tone.busy ≥ 0.45: legibility 'auto' reacts), the sky not
--verify also runs `backdrop.py verify` (needs Chrome or Edge) and checks the report and contact sheet exist, then
`backdrop.py fix`: only project.overrides changes, and every line passes or has a reason in the report.
"""
import json, os, subprocess, sys, tempfile
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, 'tools', 'backdrop')
sys.path.insert(0, TOOL)
from sources import PanSource

FIGURE = (0.66, 0.30, 0.12, 0.62)        # x, y, w, h of the figure on the illustration (0..1)

def illustration(path):
    W, H = 3840, 2160
    yy = np.linspace(0, 1, H)[:, None, None]
    sky = (np.array([14, 20, 48]) * (1 - yy) + np.array([58, 52, 104]) * yy).repeat(W, 1).astype(np.uint8)
    im = Image.fromarray(sky)
    d = ImageDraw.Draw(im)
    rng = np.random.default_rng(3)
    for x, y in rng.random((260, 2)) * [W, H * 0.6]:
        d.ellipse([x, y, x + 5, y + 5], fill=(220, 225, 255))
    d.polygon([(0, H * 0.86), (W * 0.25, H * 0.76), (W * 0.5, H * 0.88), (W * 0.8, H * 0.8), (W, H * 0.9), (W, H), (0, H)], fill=(24, 30, 44))
    x, y, w, h = FIGURE[0] * W, FIGURE[1] * H, FIGURE[2] * W, FIGURE[3] * H
    d.ellipse([x + w * 0.28, y, x + w * 0.72, y + h * 0.2], fill=(247, 217, 196))                   # head
    d.rounded_rectangle([x, y + h * 0.2, x + w, y + h], radius=w * 0.25, fill=(232, 238, 247))       # body
    d.ellipse([W * 0.12, H * 0.10, W * 0.17, H * 0.19], fill=(255, 244, 214))                       # moon
    im.filter(ImageFilter.GaussianBlur(1.2)).save(path)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    out = os.path.abspath(args[0]) if args else tempfile.mkdtemp(prefix='jz_backdrop_')
    os.makedirs(out, exist_ok=True)
    illustration(os.path.join(out, 'night.png'))
    pan = {'image': 'night.png', 'frame': [1920, 1080], 'box': [0, -540, 3840, 2160], 'origin': '50% 50%',
           'keys': [{'t': 0, 'x': 0, 'y': 0, 'scale': 1},
                    {'t': 12, 'x': -1100, 'y': -120, 'scale': 1.1, 'ease': 'sine.inOut'},
                    {'t': 24, 'x': -1700, 'y': -200, 'scale': 1.25, 'ease': 'power1.inOut'}]}
    json.dump(pan, open(os.path.join(out, 'pan.json'), 'w'), indent=1)
    lyrics = ['[00:00.50]새벽의 색을 기억하고 있어', '[00:03.00]*투명*한 채로 끝낼 수 없어!', '[00:05.50]夜明けの色を覚えてる',
              '[00:08.00]Hello world, still here', '[00:10.50]아직 늦지 않았을까', '[00:13.00]손을 뻗으면 닿을 것 같아',
              '[00:15.50]星の数だけ', '[00:18.00]we keep on running', '[00:20.50]새벽이 오기 전에']
    proj_path = os.path.join(out, 'pan.jizura.json')
    json.dump({'lyrics': '\n'.join(lyrics), 'title': '', 'style': 'noir', 'aspect': '16:9', 'seed': 5}, open(proj_path, 'w', encoding='utf-8'), ensure_ascii=False)
    bd = os.path.join(out, 'pan.backdrop')
    cli = [sys.executable, os.path.join(TOOL, 'backdrop.py')]
    run = lambda *a: subprocess.run(cli + list(a), check=True, capture_output=True, text=True, encoding='utf-8')
    n = 0
    def ok(c, msg):
        nonlocal n
        n += 1
        if not c:
            raise AssertionError(msg)

    # the pan maths, two ways: the figure's box drawn on a mask and rendered through the pan (the affine path the
    # analysis uses) vs the box moved analytically through the keyframes
    src = PanSource(os.path.join(out, 'pan.json'))
    fmask = Image.new('L', (960, 540))
    ImageDraw.Draw(fmask).rectangle([FIGURE[0] * 960, FIGURE[1] * 540, (FIGURE[0] + FIGURE[2]) * 960 - 1, (FIGURE[1] + FIGURE[3]) * 540 - 1], fill=255)
    for t in (0, 3, 6, 12, 18, 24):
        b = src.box_at(FIGURE, t)
        want = [max(0, b[0]), max(0, b[1]), min(1, b[0] + b[2]), min(1, b[1] + b[3])]
        ys, xs = np.nonzero(np.asarray(src.render(fmask, t, (480, 270))) > 127)
        if want[2] - want[0] > 0.02 and want[3] - want[1] > 0.02:
            seen = [xs.min() / 480, ys.min() / 270, (xs.max() + 1) / 480, (ys.max() + 1) / 270]
            ok(all(abs(a - c) < 0.01 for a, c in zip(seen, want)), 'pan transform at %s s: rendered %s vs moved box %s' % (t, seen, want))
        else:
            ok(len(xs) == 0, 'figure off frame at %s s' % t)

    r = run('analyze', proj_path, '--pan', os.path.join(out, 'pan.json'), '--out', bd, '--duration', '24')
    res = json.loads(r.stdout.strip().splitlines()[-1])
    proj = json.load(open(proj_path, encoding='utf-8'))
    block = proj.get('backdrop')
    ok(block and len(block['track']) == 9 == res['entries'], 'one entry per line written into the project')
    norm = subprocess.run(['node', os.path.join(TOOL, 'jz.js'), 'normalize', os.path.join(bd, 'backdrop.json')], capture_output=True, text=True)
    ok(norm.returncode == 0 and len(json.loads(norm.stdout)['track']) == 9, 'J.normalizeBackdrop keeps every entry')
    for e in block['track']:
        ok(all(k in e for k in ('free', 'tone')) and e['tone']['color'].startswith('#'), 'entry has free + tone')
        r0 = e['free'][0]
        for t in np.linspace(e['t0'], e['t1'], 6):
            b = src.box_at(FIGURE, t)
            inter = max(0, min(r0[0] + r0[2], b[0] + b[2]) - max(r0[0], b[0])) * max(0, min(r0[1] + r0[3], b[1] + b[3]) - max(r0[1], b[1]))
            ok(inter <= 0.01 * r0[2] * r0[3], 'free area %s overlaps the figure %s at %.2f s' % (r0, [round(v, 3) for v in b], t))
    ok(os.path.exists(os.path.join(bd, 'review.json')) and os.path.exists(os.path.join(bd, 'review.png')), 'review hook files')

    # the agent hook: confirm a free area for one line, re-run, the area is used as given
    rv = json.load(open(os.path.join(bd, 'review.json'), encoding='utf-8'))
    rv['lines'][2]['confirm'] = {'free': [[0.05, 0.1, 0.4, 0.45]]}
    json.dump(rv, open(os.path.join(bd, 'review.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    run('analyze', proj_path, '--pan', os.path.join(out, 'pan.json'), '--out', bd, '--duration', '24', '--review', os.path.join(bd, 'review.json'))
    e2 = json.load(open(proj_path, encoding='utf-8'))['backdrop']['track'][2]
    ok(e2['free'] == [[0.05, 0.1, 0.4, 0.45]], 'confirmed free area used: %s' % e2['free'])
    ok(json.load(open(os.path.join(bd, 'review.json'), encoding='utf-8'))['lines'][2]['confirm'], 'confirm kept in the new review.json')

    # light / shadow: an area half in a sunlit patch reads as busy (legibility 'auto' turns a treatment on) although its
    # clutter is low; the even night sky does not
    sys.path.insert(0, TOOL)
    import zones
    dark = np.ones((90, 160, 3)) * np.array([0.1, 0.1, 0.18])
    mixed = dark.copy()
    mixed[:, :60] = [0.75, 0.7, 0.6]
    d = {}
    ok(zones.tone_of([0, 0, 1, 1], [mixed], d)['busy'] >= 0.45 and d['clutter'] < 0.45, 'light / shadow mix reads as busy: %s' % d)
    ok(zones.tone_of([0, 0, 1, 1], [dark])['busy'] < 0.1, 'an even area stays calm')
    ok(all(e['tone']['busy'] < 0.45 for e in proj['backdrop']['track']), 'the night sky stays below the auto threshold')

    if '--verify' in sys.argv:
        r = run('verify', proj_path, '--pan', os.path.join(out, 'pan.json'), '--out', bd, '--duration', '24', '--timeout', '600')
        rep = json.loads(r.stdout.strip().splitlines()[-1])
        ok(not rep['errors'], 'verify errors: %s' % rep['errors'])
        for f in ('report.json', 'report.md', 'contact.png'):
            ok(os.path.exists(os.path.join(bd, 'verify', f)), 'verify wrote ' + f)
        print('verify:', 'PASS' if rep['pass'] else 'FAIL (lines %s)' % rep['failing'], '->', os.path.join(bd, 'verify', 'report.md'))
        # the fix loop: only project.overrides changes, and every line passes or the report says why not
        before = json.load(open(proj_path, encoding='utf-8'))
        r = run('fix', proj_path, '--pan', os.path.join(out, 'pan.json'), '--out', bd, '--duration', '24', '--timeout', '600')
        fx = json.loads(r.stdout.strip().splitlines()[-1])
        after = json.load(open(proj_path, encoding='utf-8'))
        ok(not fx['errors'], 'fix errors: %s' % fx['errors'])
        ok(all(before.get(k) == after.get(k) for k in set(before) | set(after) if k != 'overrides'), 'fix writes project.overrides only')
        ok(all(set(o) <= {'seed', 'zone', 'tone', 'legibility'} for o in after.get('overrides', {}).values()), 'fix writes its own fields only')
        ok(set(fx['failing']) == set(int(k) for k in fx['unresolved']) and all(fx['unresolved'].values()), 'a reason for every failing line: %s' % fx)
        rep = json.load(open(os.path.join(bd, 'verify', 'report.json'), encoding='utf-8'))
        ok(len(rep['lines']) == 9 and all(l['frames'] for l in rep['lines']), 'merged report covers every line')
        ok(os.path.exists(os.path.join(bd, 'verify', 'fix.json')) and not os.path.exists(os.path.join(bd, 'verify', 'variants')), 'fix.json, variants cleaned up')
        print('fix: %d round(s), fixed lines %s, left %s -> %s' % (fx['rounds'], fx['fixed'], fx['unresolved'] or 'none', os.path.join(bd, 'verify', 'report.md')))
    print('backdrop tool: %d checks passed (%s)' % (n, out))

if __name__ == '__main__':
    main()
