"""`backdrop.py verify`: the lyric layer over the background, measured against the pass criteria of docs/BACKDROP.md.

Prepares OUT/verify/ (job.json, background frames and subject masks at each sample time), serves it with the engine
(src/*.js joined, like build.py --dev) and verify.html on 127.0.0.1, and lets a browser render and measure:
headless Chrome / Edge by default, or any browser opened on the printed URL (--browser none — e.g. the Claude
desktop browser pane). The page posts its numbers and a contact tile per cut back; this writes report.json /
report.md and contact.png. A Session keeps the page open for more jobs, each with one or more runs (variants of the
project) limited to some lines: the fix loop (fix.py) renders only what changed, and its candidate fixes side by side.
"""
import glob, http.server, json, os, shutil, subprocess, tempfile, threading
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CRITERIA = {'outside': 0, 'onSubject': 0.02, 'contrast': 4.5, 'glyph': 0.03}
FRACS = (0.35, 0.6, 0.92)              # where in each cut to look: during the hold and into the exit (0.6 → contact sheet)

def engine_js():
    return '\n'.join(open(f, encoding='utf-8').read() for f in sorted(glob.glob(os.path.join(ROOT, 'src', '*.js')))).encode('utf-8')

def find_browser(want):
    if want not in ('auto', None):
        return want
    cands = [r'C:\Program Files\Google\Chrome\Application\chrome.exe', r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
             r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
             '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
    for c in cands:
        if os.path.exists(c):
            return c
    for n in ('google-chrome', 'chromium', 'chromium-browser', 'chrome', 'msedge'):
        p = shutil.which(n)
        if p:
            return p
    return None

class Server:
    def __init__(self, vdir, port):
        self.vdir, self.got, self.done = vdir, {}, threading.Event()
        self.seq, self.stop, self.cv = 0, False, threading.Condition()      # job number the page waits for (/next)
        js, page = engine_js(), open(os.path.join(HERE, 'verify.html'), 'rb').read()
        srv = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body=b'', ctype='text/plain'):
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split('?')[0]
                if path == '/next':                    # the page waits here for its next job
                    want = int((self.path.split('seq=') + ['0'])[1] or 0)
                    with srv.cv:
                        srv.cv.wait_for(lambda: srv.stop or srv.seq > want, 50)
                        reply = 'stop' if srv.stop else str(srv.seq) if srv.seq > want else 'wait'
                    return self.send(200, reply.encode())
                if path in ('/', '/verify.html'):
                    return self.send(200, page, 'text/html; charset=utf-8')
                if path == '/jizura.js':
                    return self.send(200, js, 'text/javascript; charset=utf-8')
                if path.startswith('/job/'):
                    f = os.path.realpath(os.path.join(srv.vdir, path[5:]))
                    if f.startswith(os.path.realpath(srv.vdir) + os.sep) and os.path.isfile(f):
                        ctype = {'.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg'}.get(os.path.splitext(f)[1], 'application/octet-stream')
                        return self.send(200, open(f, 'rb').read(), ctype)
                self.send(404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                name = self.path.strip('/')
                kind, _, base = name.partition('/')
                if kind in ('frame', 'tile') and base.replace('_', '').isalnum():
                    d = os.path.join(srv.vdir, 'fails' if kind == 'frame' else 'tiles')
                    os.makedirs(d, exist_ok=True)
                    open(os.path.join(d, base + '.png'), 'wb').write(body)
                    return self.send(200, b'ok')
                if name in ('result', 'done'):
                    srv.got[name] = body
                    if name == 'done':
                        srv.done.set()
                    return self.send(200, b'ok')
                self.send(404)

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', port), H)
        self.url = 'http://127.0.0.1:%d/verify.html' % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def post_job(self):
        self.got, self.done = {}, threading.Event()
        with self.cv:
            self.seq += 1
            self.cv.notify_all()

    def close(self):
        with self.cv:
            self.stop = True
            self.cv.notify_all()
        self.httpd.shutdown()

def subject_masks(B, src, cache, detector, tts, confirms, size, vdir):
    """subject mask per track time at verify size: the agent's confirmed boxes where it gave some, else the detector"""
    os.makedirs(os.path.join(vdir, 'mask'), exist_ok=True)
    det = {}
    need = [t for t in tts if not any(c['t0'] - 1e-6 <= t <= c['t1'] + 1e-6 for c in confirms)]
    if need:
        subj = B.Subjects(src, cache, detector)
        det = {t: v[1] for t, v in subj.sample(need).items()}
    out = {}
    for t in tts:
        c = next((c for c in confirms if c['t0'] - 1e-6 <= t <= c['t1'] + 1e-6), None)
        m = B.raster(c['avoid'], size) if c else np.asarray(Image.fromarray((det[t] * 255).astype(np.uint8)).resize(size, Image.NEAREST)) > 127
        p = os.path.join(vdir, 'mask', B.stamp(t) + '.png')
        Image.fromarray((m * 255).astype(np.uint8)).save(p)
        out[t] = 'mask/' + os.path.basename(p)
    return out

class Ctx:
    """where a verify (or fix) run reads and writes"""

    def __init__(self, args, B):
        self.src = src = B.open_source(args)
        self.offset = args.offset if args.offset is not None else getattr(src, 'offset', 0.0)
        self.duration = args.duration or max(1.0, src.duration - self.offset)
        self.out = os.path.abspath(args.out or B.default_out(args.project))
        self.vdir = os.path.join(self.out, 'verify')
        self.cache = os.path.join(self.out, 'cache', B.source_key(src))
        os.makedirs(self.vdir, exist_ok=True)

    def plan(self, args, B):
        return B.jz('plan', os.path.abspath(args.project), self.duration)

def samples_of(info, B, offset):
    """three samples per lyric cut, in time order; `thumb` marks the one that goes on the contact sheet"""
    samples, seen = [], set()
    for c in info['cuts']:
        for f in FRACS:
            t = round(c['start'] + c['dur'] * f, 3)
            if t in seen:
                continue
            seen.add(t)
            samples.append({'t': t, 'tt': round(t + offset, 3), 'line': c['line'], 'thumb': f == 0.6, 'name': 'L%03d_' % c['line'] + B.stamp(t)})
    return samples

class Session:
    """one browser page that renders jobs as they come: verify sends one, the fix loop one per round"""

    def __init__(self, ctx, args, B):
        self.ctx, self.args, self.B = ctx, args, B
        self.srv = Server(ctx.vdir, args.port)
        self.proc = self.profile = None
        self.masks, self.confirms = {}, []
        review = args.review or os.path.join(ctx.out, 'review.json')
        if os.path.exists(review):
            for ln in B.load_json(review).get('lines', []):
                if (ln.get('confirm') or {}).get('avoid'):
                    self.confirms.append({'t0': ln['t0'], 't1': ln['t1'], 'avoid': ln['confirm']['avoid']})
        browser = None if args.browser == 'none' else find_browser(args.browser)
        if browser:
            self.profile = tempfile.mkdtemp(prefix='jzbd_browser_')
            self.proc = subprocess.Popen([browser, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                                          '--user-data-dir=' + self.profile, '--window-size=1400,1000', self.srv.url],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            B.log('rendering in %s (headless) …' % os.path.basename(browser))
        else:
            B.log('open %s in a browser (it renders, measures and posts back here) …' % self.srv.url)

    def render(self, runs):
        """runs: [{id, project (dict), info (its jz plan), lines (set, or None for all), prefix}]. Renders and measures
        each run's samples; returns {id: {rows, errors}}. A run without a prefix replaces the failing-frame images
        and contact tiles of its lines in OUT/verify; prefixed runs leave theirs under the prefix (adopt() moves them)."""
        ctx, args, B, vdir = self.ctx, self.args, self.B, self.ctx.vdir
        K = args.scale
        jobs, tts, size, vw = [], set(), None, None
        for r in runs:
            info, lines, pre = r['info'], r.get('lines'), r.get('prefix', '')
            if not pre:
                clear(vdir, lines)
            samples = [s for s in samples_of(info, B, ctx.offset) if lines is None or s['line'] in lines]
            vw = round(info['W'] * K)
            size = (vw, round(info['H'] * K))
            tts |= {s['tt'] for s in samples}
            jobs.append({'id': r['id'], 'prefix': pre, 'project': r['project'], 'samples': samples})
            B.log('verify%s: %d samples over %d cuts%s' % (' ' + r['id'] if pre else '', len(samples), sum(s['thumb'] for s in samples),
                                                        '' if lines is None else ' (lines %s)' % sorted(lines)))
        if not tts:
            return {r['id']: {'rows': [], 'errors': []} for r in runs}
        tts = sorted(tts)
        frames = ctx.src.frames(tts, os.path.join(vdir, 'bg'), vw)
        need = [t for t in tts if t not in self.masks]
        if need:
            self.masks.update(subject_masks(B, ctx.src, ctx.cache, args.detector, need, self.confirms, size, vdir))
        for j in jobs:
            for s in j['samples']:
                s['bg'] = os.path.relpath(frames[s['tt']], vdir).replace('\\', '/')
                s['mask'] = self.masks[s['tt']]
        B.save_json(os.path.join(vdir, 'job.json'), {'duration': ctx.duration, 'scale': K, 'criteria': CRITERIA, 'runs': jobs})
        self.srv.post_job()
        if not self.srv.done.wait(args.timeout):
            raise SystemExit('verify: no result from the page within %d s (%s)' % (args.timeout, self.srv.url))
        got = json.loads(self.srv.got.get('result') or b'{}')
        out = got.get('runs') or {}
        for r in runs:
            out.setdefault(r['id'], {'rows': [], 'errors': list(got.get('errors', []))})
        return out

    def adopt(self, prefix, lines):
        """a prefixed run's images become the current ones for `lines`"""
        vdir = self.ctx.vdir
        clear(vdir, lines)
        for sub in ('fails', 'tiles'):
            d = os.path.join(vdir, sub)
            for f in os.listdir(d) if os.path.isdir(d) else []:
                if f.startswith(prefix):
                    if line_of(f[len(prefix):]) in lines:
                        os.replace(os.path.join(d, f), os.path.join(d, f[len(prefix):]))

    def drop(self):
        """removes every prefixed (variant) image"""
        for sub in ('fails', 'tiles'):
            d = os.path.join(self.ctx.vdir, sub)
            for f in os.listdir(d) if os.path.isdir(d) else []:
                if not f.startswith('L'):
                    os.remove(os.path.join(d, f))

    def close(self):
        self.srv.close()
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)

def line_of(name):
    return int(name[1:4]) if name[:1] == 'L' and name[1:4].isdigit() else None

def clear(vdir, lines):
    """removes the failing-frame images and contact tiles of `lines` (None: all of them)"""
    for sub in ('fails', 'tiles'):
        d = os.path.join(vdir, sub)
        if lines is None:
            shutil.rmtree(d, ignore_errors=True)
        elif os.path.isdir(d):
            for f in os.listdir(d):
                if line_of(f) in lines:
                    os.remove(os.path.join(d, f))

def check_offset(ctx, project, B):
    if not project.get('backdrop'):
        B.log('warning: the project has no backdrop block — verifying the plain layout')
    off_bd = (project.get('backdrop') or {}).get('offset', ctx.offset)
    if abs(off_bd - ctx.offset) > 1e-6:
        B.log('warning: backdrop.offset is %s but the background is checked with offset %s' % (off_bd, ctx.offset))

def contact_sheet(ctx, B, info):
    """contact.png from the tiles of the current plan's cuts (each from the last run that rendered its line)"""
    names = [s['name'] for s in samples_of(info, B, ctx.offset) if s['thumb']]
    paths = [os.path.join(ctx.vdir, 'tiles', n + '.png') for n in names]
    tiles = [Image.open(p).convert('RGB') for p in paths if os.path.exists(p)]
    if not tiles:
        return
    cols, gap = (4 if info['W'] >= info['H'] else 6), 6
    tw, th = tiles[0].size
    rows = -(-len(tiles) // cols)
    sheet = Image.new('RGB', (cols * (tw + gap), rows * (th + gap)), (17, 17, 17))
    for k, t in enumerate(tiles):
        sheet.paste(t, ((k % cols) * (tw + gap), (k // cols) * (th + gap)))
    sheet.save(os.path.join(ctx.vdir, 'contact.png'))

def write_report(ctx, args, B, info, result, extra_md=''):
    report = summarize(result, info)
    contact_sheet(ctx, B, info)
    B.save_json(os.path.join(ctx.vdir, 'report.json'), report)
    open(os.path.join(ctx.vdir, 'report.md'), 'w', encoding='utf-8', newline='\n').write(markdown(report, args, ctx.src) + extra_md)
    B.log('report: %s, contact sheet: %s' % (os.path.join(ctx.vdir, 'report.md'), os.path.join(ctx.vdir, 'contact.png')))
    return report

def brief(report, vdir):
    return {'pass': report['pass'], 'lines': len(report['lines']), 'failing': [l['line'] for l in report['lines'] if not l['pass']],
            'errors': report['errors'], 'out': vdir}

def run(args, B):
    ctx = Ctx(args, B)
    info = ctx.plan(args, B)
    project = B.load_json(args.project)
    check_offset(ctx, project, B)
    ses = Session(ctx, args, B)
    try:
        res = ses.render([{'id': 'base', 'project': project, 'info': info}])['base']
    finally:
        ses.close()
    report = write_report(ctx, args, B, info, res)
    print(json.dumps(brief(report, ctx.vdir), ensure_ascii=False))

FIX = {
    'outside': 'drawn outside the area: by a full-frame layout with clip off, or a transition (marked ⇐name) next to '
               'a line without an area — re-roll the line\'s seed, or give the neighbouring line an area',
    'subject': 're-roll the seed, then move / enlarge the area away from the subject (overrides[line].zone.rect)',
    'contrast': 'legibility "plate" (or "outline") for the line, or an area over calmer picture',
    'glyph': 'enlarge the area (text scales with it), or a shorter cut split',
}

def summarize(result, info):
    rows = result.get('rows', [])
    texts = {l['line']: l['text'] for l in info['lines']}
    lines = []
    for li in sorted({r['line'] for r in rows}):
        rs = [r for r in rows if r['line'] == li and r['method'] not in ('empty', 'faint')]
        held = lambda k: k in ('contrast', 'bgContrast', 'glyph')     # judged on held text only (not entering / exiting)
        vals = lambda k: [r[k] for r in rs if r.get(k) is not None and not (held(k) and r.get('moving'))]
        fails = sorted({f for r in rs for f in r['fails']})
        lines.append({
            'line': li, 'text': texts.get(li, ''), 'frames': len(rs), 'pass': not fails, 'fails': fails,
            'outside': max(vals('outside'), default=None), 'onSubject': round(max(vals('onSubject'), default=0), 4),
            'contrast': min(vals('contrast'), default=None), 'bgContrast': min(vals('bgContrast'), default=None),
            'glyph': min(vals('glyph'), default=None),
            'failingFrames': [{'t': r['t'], 'cuts': r['cuts'], 'fails': r['fails'], 'image': r.get('image')} for r in rs if r['fails']],
            'fix': [FIX[f] for f in fails],
        })
    ink = sum(r['ink'] for r in rows)
    on = sum(r['onSubject'] * r['ink'] for r in rows)
    return {'pass': bool(lines) and all(l['pass'] for l in lines) and not result.get('errors'), 'criteria': CRITERIA,
            'frames': len(rows), 'inkOnSubject': round(on / ink, 4) if ink else 0, 'errors': result.get('errors', []),
            'lines': lines, 'rows': rows}

def markdown(rep, args, src):
    pct = lambda v: '-' if v is None else '%.1f %%' % (v * 100)
    num = lambda v: '-' if v is None else str(v)
    L = ['# Backdrop verification', '',
         '`%s` over `%s` — %s, %d frames, %.1f %% of all lyric ink on the subject.' % (os.path.basename(args.project), src.name,
             'PASS' if rep['pass'] else 'FAIL', rep['frames'], rep['inkOnSubject'] * 100), '',
         'Criteria (docs/BACKDROP.md): no ink outside the area, < 2 % on the subject, glyphs ≥ 4.5:1 against what is around '
         'them, main glyphs ≥ 3 % of the frame height. Contact sheet: `contact.png` (red tint: subject, dashed green: area).', '',
         '| line | text | frames | outside px | on subject | contrast (bare bg) | glyph h | result |',
         '|---|---|---|---|---|---|---|---|']
    for l in rep['lines']:
        L.append('| %d | %s | %d | %s | %s | %s (%s) | %s | %s |' % (l['line'], l['text'].replace('|', '/'), l['frames'], num(l['outside']),
                 pct(l['onSubject']), num(l['contrast']), num(l['bgContrast']), pct(l['glyph']), 'ok' if l['pass'] else '**' + ', '.join(l['fails']) + '**'))
    bad = [l for l in rep['lines'] if not l['pass']]
    if bad:
        L += ['', '## To fix', '']
        for l in bad:
            L.append('* line %d: %s' % (l['line'], '; '.join(l['fix'])))
            for f in l['failingFrames'][:6]:
                L.append('  * %.2f s %s — %s%s' % (f['t'], f['cuts'], ', '.join(f['fails']), ' — `%s`' % f['image'] if f.get('image') else ''))
    if rep['errors']:
        L += ['', '## Errors', ''] + ['* ' + e for e in rep['errors']]
    return '\n'.join(L) + '\n'
