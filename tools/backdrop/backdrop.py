"""JIZURA backdrop analysis: write a project's `backdrop` block from its background picture, then verify the result.
For when no MV director agent supplies the block (contract: docs/BACKDROP.md).

  python tools/backdrop/backdrop.py analyze PROJECT.jizura.json (--video BG.mp4 | --pan PAN.json) [options]
  python tools/backdrop/backdrop.py verify  PROJECT.jizura.json (--video BG.mp4 | --pan PAN.json) [options]

analyze  plans the lyrics once (Node, src/ engine) for each line's [start, visEnd]; samples the background over that
         time (video: ffmpeg frames at start / middle / end and every ≤1.5 s; pan: the illustration through the pan
         keyframes every 0.25 s, its subject found once); detects the subject (local person segmentation + saliency,
         see detect.py); scores free areas (zones.py); writes OUT/backdrop.json, OUT/review.json + review.png (the
         agent hook) and the block into the project.
verify   renders the transparent lyric layer over background frames in headless Chrome / Edge (or a browser you
         open on the printed URL) and reports the pass criteria: OUT/verify/report.md, report.json, contact.png.

Needs: Python 3 with numpy + Pillow, Node, ffmpeg / ffprobe on PATH; for person masks npx + HyperFrames (local model).
"""
import argparse, hashlib, json, math, os, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import detect, zones
from sources import VideoSource, PanSource, stamp

AW = 320                 # analysis width (px); height follows the background's aspect
FRAME_W = 640            # sampled frames saved for review / person detection
VIDEO_STEP, PAN_STEP = 1.5, 0.25

def log(*a):
    print(*a, file=sys.stderr, flush=True)

def jz(*args):
    r = subprocess.run(['node', os.path.join(HERE, 'jz.js')] + [str(a) for a in args], capture_output=True)
    if r.returncode not in (0, 2):
        raise SystemExit('node jz.js %s failed:\n%s' % (args[0], r.stderr.decode('utf-8', 'replace')))
    return json.loads(r.stdout.decode('utf-8') or 'null')

def load_json(p):
    with open(p, encoding='utf-8-sig') as f:
        return json.load(f)

def save_json(p, o, indent=1):
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(o, f, ensure_ascii=False, indent=indent)
        f.write('\n')

def open_source(args):
    if bool(args.video) == bool(args.pan):
        raise SystemExit('give exactly one of --video or --pan')
    return VideoSource(args.video) if args.video else PanSource(args.pan)

def source_key(src):
    h = hashlib.sha1()
    if src.kind == 'video':
        st = os.stat(src.path)
        h.update(('%s|%d|%d' % (src.path, st.st_size, int(st.st_mtime))).encode())
    else:
        h.update(open(src.path, 'rb').read())
        h.update(open(src.image_path, 'rb').read(1 << 20))
    return src.kind + '_' + h.hexdigest()[:10]

def default_out(project):
    base = os.path.basename(project)
    for ext in ('.jizura.json', '.json'):
        if base.endswith(ext):
            base = base[:-len(ext)]
            break
    return os.path.join(os.path.dirname(os.path.abspath(project)), base + '.backdrop')

def sample_times(s, e, step):
    n = max(3, int(math.ceil((e - s) / step)) + 1)
    eps = min(0.08, (e - s) / 10)
    return [round(s + eps + (e - s - 2 * eps) * k / (n - 1), 3) for k in range(n)]

def raster(boxes, size):
    w, h = size
    m = np.zeros((h, w), bool)
    for x, y, bw, bh in boxes:
        m[max(0, int(y * h)):min(h, int(math.ceil((y + bh) * h))), max(0, int(x * w)):min(w, int(math.ceil((x + bw) * w)))] = True
    return m

def clip_rect(r, pad=0.0):
    x0, y0 = max(0.0, r[0] - pad), max(0.0, r[1] - pad)
    x1, y1 = min(1.0, r[0] + r[2] + pad), min(1.0, r[1] + r[3] + pad)
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]

class Subjects:
    """subject masks at analysis size for track times, for either kind of source"""

    def __init__(self, src, cache, detector):
        self.src, self.cache, self.detector = src, cache, detector
        self.size = (AW, round(AW * src.h / src.w / 2) * 2)
        if src.kind == 'pan':
            self._prepare_pan()

    def _prepare_pan(self):
        # the illustration is analysed once; its subject mask then moves with the pan
        src, cache = self.src, self.cache
        os.makedirs(cache, exist_ok=True)
        im = src.image
        k = min(1.0, 1280 / max(im.size))
        small = os.path.join(cache, 'illustration.png')
        if not os.path.exists(small):
            im.resize((round(im.size[0] * k), round(im.size[1] * k)), Image.LANCZOS).save(small)
        person = None
        if self.detector in ('auto', 'person'):
            pm = detect.person_masks([small], os.path.join(cache, 'masks'), log).get(small)
            if pm:
                person = detect.load_grey(pm, Image.open(small).size)
        aw = 480
        ash = round(aw * im.size[1] / im.size[0])
        rgb = detect.load_rgb(small, (aw, ash))
        if person is not None:
            person = np.asarray(Image.fromarray((person * 255).astype(np.uint8)).resize((aw, ash)), np.float32) / 255
        mask, self.pan_source, self.pan_reasons = detect.subject(rgb, person if self.detector != 'saliency' else None)
        self.pan_mask = Image.fromarray((mask * 255).astype(np.uint8))
        self.pan_mask.save(os.path.join(cache, 'subject.png'))
        self.small = Image.open(small).convert('RGB')
        boxes, _ = detect.components(mask, 0.004)
        self.pan_boxes = boxes

    def sample(self, times):
        """{t: (rgb, mask, source, reasons, frame_path or None)}"""
        src, out = self.src, {}
        if src.kind == 'pan':
            for t in times:
                rgb = np.asarray(src.render(self.small, t, self.size), np.float32) / 255
                mask = np.asarray(src.render(self.pan_mask, t, self.size), np.float32) > 127
                out[t] = (rgb, mask, self.pan_source, self.pan_reasons, None)
            return out
        frames = src.frames(times, os.path.join(self.cache, 'frames'), FRAME_W)
        pm = {}
        if self.detector in ('auto', 'person'):
            pm = detect.person_masks(sorted(set(frames.values())), os.path.join(self.cache, 'masks'), log)
        for t in times:
            rgb = detect.load_rgb(frames[t], self.size)
            person = detect.load_grey(pm[frames[t]], self.size) if frames[t] in pm else None
            mask, source, reasons = detect.subject(rgb, person if self.detector != 'saliency' else None)
            out[t] = (rgb, mask, source, reasons, frames[t])
        return out

def font(size):
    for f in ('C:/Windows/Fonts/malgun.ttf', '/System/Library/Fonts/AppleSDGothicNeo.ttc', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()

def tile(frame_rgb, mask, avoid, free, label, flagged):
    """one review tile: the frame, subject tinted red, avoid boxes red, free areas green (chosen one thick)"""
    im = Image.fromarray((frame_rgb * 255).astype(np.uint8)).resize((400, round(400 * frame_rgb.shape[0] / frame_rgb.shape[1])))
    w, h = im.size
    red = Image.new('RGB', im.size, (255, 40, 40))
    m = Image.fromarray((mask * 110).astype(np.uint8)).resize(im.size)
    im = Image.composite(red, im, m)
    d = ImageDraw.Draw(im)
    R = lambda r: [r[0] * w, r[1] * h, (r[0] + r[2]) * w - 1, (r[1] + r[3]) * h - 1]
    for r in avoid:
        d.rectangle(R(r), outline=(255, 60, 60), width=2)
    for k, r in enumerate(free):
        d.rectangle(R(r), outline=(60, 255, 90), width=4 if k == 0 else 1)
    bar = Image.new('RGB', (w, 24), (230, 140, 20) if flagged else (40, 40, 40))
    ImageDraw.Draw(bar).text((6, 3), label, fill=(255, 255, 255), font=font(15))
    out = Image.new('RGB', (w, h + 24))
    out.paste(bar, (0, 0)); out.paste(im, (0, 24))
    return out

def sheet(tiles, cols=4):
    if not tiles:
        return None
    w, h = tiles[0].size
    rows = math.ceil(len(tiles) / cols)
    s = Image.new('RGB', (cols * (w + 6), rows * (h + 6)), (17, 17, 17))
    for i, t in enumerate(tiles):
        s.paste(t, ((i % cols) * (w + 6), (i // cols) * (h + 6)))
    return s

def analyze(args):
    src = open_source(args)
    offset = args.offset if args.offset is not None else getattr(src, 'offset', 0.0)
    duration = args.duration or max(1.0, src.duration - offset)
    out = os.path.abspath(args.out or default_out(args.project))
    cache = os.path.join(out, 'cache', source_key(src))
    os.makedirs(out, exist_ok=True)
    info = jz('plan', os.path.abspath(args.project), duration)
    if abs(info['W'] / info['H'] - src.w / src.h) > 0.02:
        log('warning: the project is %s (%dx%d) but the background is %dx%d — areas are fractions of the background frame'
            % (info['aspect'], info['W'], info['H'], src.w, src.h))
    confirms = {}
    review_path = os.path.join(out, 'review.json')
    if args.review:
        for ln in load_json(args.review).get('lines', []):
            if ln.get('confirm'):
                confirms[ln['line']] = ln['confirm']
    subjects = Subjects(src, cache, args.detector)
    step = PAN_STEP if src.kind == 'pan' else VIDEO_STEP
    plan_lines = [l for l in info['lines'] if l['visEnd'] > l['start']]
    times = {l['line']: [round(t + offset, 3) for t in sample_times(l['start'], l['visEnd'], step)] for l in plan_lines}
    if src.kind == 'video':
        late = [l['line'] for l in plan_lines if l['visEnd'] + offset > src.duration + 0.05]
        if late:
            log('warning: lines %s run past the end of the video (%.2f s); their last samples use its last frame' % (late, src.duration))
    log('%d lines, %d samples (%s)' % (len(plan_lines), sum(len(v) for v in times.values()), src.kind))
    samples = subjects.sample(sorted({t for v in times.values() for t in v}))
    track, review, tiles, prev = [], [], [], None
    for l in plan_lines:
        li, ts = l['line'], times[l['line']]
        conf = confirms.get(li) or {}
        t0, t1 = round(l['start'] + offset, 3), round(l['visEnd'] + offset, 3)
        rec = {'line': li, 'text': l['text'], 't0': t0, 't1': t1}
        if conf.get('skip'):
            rec.update(confirm=conf, ambiguous=False, reasons=['skipped by confirm'])
            review.append(rec)
            continue
        rgbs = [samples[t][0] for t in ts]
        sources = sorted({p for t in ts for p in samples[t][2].split('+')})
        reasons = []
        for t in ts:
            for r in samples[t][3]:
                if r not in reasons:
                    reasons.append(r)
        masks = [samples[t][1] for t in ts]
        if conf.get('avoid'):
            subj = raster(conf['avoid'], subjects.size)
            avoid = [clip_rect(r) for r in conf['avoid']]
        else:
            subj = np.logical_or.reduce(masks)
            boxes, _ = detect.components(subj, 0.004)
            avoid = [clip_rect(b, 0.01) for b in boxes]
            areas = [m.mean() for m in masks]
            if subj.mean() > 1.6 * max(max(areas), 1e-6) and max(areas) > 0.02:
                reasons.append('subject moves or the shot changes during the line (areas are the union)')
        if conf.get('free'):
            free = [clip_rect(r) for r in conf['free']]
            tone, detail = zones.tone_of(free[0], rgbs), {'confirmed': True}
        else:
            res = zones.choose(subj, rgbs, (src.w, src.h), prev)
            free, tone, detail = res['free'], res['tone'], res['detail']
            reasons += res['problems']
        if conf.get('tone'):
            tone = conf['tone']
        prev = free[0]
        entry = {'t0': t0, 't1': t1, 'free': free, 'tone': tone}
        if avoid:
            entry['avoid'] = avoid[:16]
        track.append(entry)
        flagged = bool(reasons) and not conf
        mid = ts[len(ts) // 2]
        frame = samples[mid][4]
        if frame is None:                                   # pan: render the middle sample for the agent to look at
            frame = src.frames([mid], os.path.join(cache, 'frames'), FRAME_W)[mid]
        rec.update(detector='+'.join(sources), ambiguous=flagged, reasons=reasons, proposed={'avoid': avoid, 'free': free, 'tone': tone},
                   detail=detail, samples=[{'t': t, 'source': samples[t][2]} for t in ts],
                   frame=os.path.relpath(frame, out).replace('\\', '/'), confirm=conf or None)
        mpath = os.path.join(out, 'masks', 'L%03d.png' % li)
        os.makedirs(os.path.dirname(mpath), exist_ok=True)
        Image.fromarray((subj * 255).astype(np.uint8)).save(mpath)
        rec['mask'] = os.path.relpath(mpath, out).replace('\\', '/')
        review.append(rec)
        label = 'L%d  %.1f–%.1fs  %s%s' % (li, t0, t1, rec['detector'], '  CHECK' if flagged else '  ok' if conf else '')
        tiles.append(tile(detect.load_rgb(frame, (640, round(640 * src.h / src.w))), subj, avoid, free, label, flagged))
    block = {'version': 1, 'source': src.name, 'offset': offset, 'legibility': args.legibility, 'track': track}
    if src.kind == 'pan':
        block['source'] = os.path.basename(src.path)
    save_json(os.path.join(out, 'backdrop.json'), block)
    norm = jz('normalize', os.path.join(out, 'backdrop.json'))
    if not norm or len(norm['track']) != len(track):
        raise SystemExit('J.normalizeBackdrop rejected entries: %d of %d kept' % (len(norm['track']) if norm else 0, len(track)))
    s = sheet(tiles)
    if s:
        s.save(os.path.join(out, 'review.png'))
    flagged = [r['line'] for r in review if r.get('ambiguous')]
    save_json(review_path, {
        'version': 1, 'project': os.path.abspath(args.project), 'source': src.path, 'kind': src.kind, 'frame': [src.w, src.h], 'offset': offset,
        'how': 'Look at review.png (red: subject, green: chosen free area). For a wrong line set "confirm" to any of '
               '{"avoid": [[x,y,w,h]...]} (the subject, 0..1 of the frame), {"free": [[x,y,w,h]...]} (areas to use, best '
               'first), {"tone": {"color": "#rrggbb", "busy": 0..1}}, {"skip": true}; or {} to accept the proposal. '
               'Then re-run analyze with --review this file (frames and masks are cached).',
        'flagged': flagged, 'lines': review})
    if not args.no_write:
        proj = load_json(args.project)
        proj['backdrop'] = norm
        save_json(args.project, proj)
    log('backdrop: %d entries%s → %s' % (len(track), '' if args.no_write else ' written into ' + args.project, out))
    log('review: %s (%d line(s) to check: %s)' % (os.path.join(out, 'review.png'), len(flagged), flagged))
    print(json.dumps({'out': out, 'entries': len(track), 'flagged': flagged}))

def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog='backdrop.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('analyze', 'verify'):
        p = sub.add_parser(name)
        p.add_argument('project', help='the .jizura.json project')
        p.add_argument('--video', help='background video')
        p.add_argument('--pan', help='pan description JSON (see sources.py)')
        p.add_argument('--out', help='output folder (default: PROJECT.backdrop next to the project)')
        p.add_argument('--offset', type=float, help='background time = song time + offset (default 0, or the pan\'s offset)')
        p.add_argument('--duration', type=float, help='song length in seconds (default: the background\'s)')
        p.add_argument('--detector', choices=['auto', 'person', 'saliency'], default='auto')
        p.add_argument('--review', help='analyze: a review.json with confirmed lines; verify: subject boxes from its confirms')
        if name == 'analyze':
            p.add_argument('--legibility', choices=['auto', 'none', 'outline', 'shadow', 'plate'], default='auto')
            p.add_argument('--no-write', action='store_true', help='do not write the block into the project')
        else:
            p.add_argument('--browser', default='auto', help='auto (Chrome / Edge headless), a browser executable, or none (open the printed URL yourself)')
            p.add_argument('--scale', type=float, default=0.5, help='render scale of the lyric layer (default 0.5)')
            p.add_argument('--timeout', type=float, default=900)
            p.add_argument('--port', type=int, default=0)
    args = ap.parse_args()
    if args.cmd == 'analyze':
        analyze(args)
    else:
        import verify
        verify.run(args, sys.modules[__name__])

if __name__ == '__main__':
    main()
