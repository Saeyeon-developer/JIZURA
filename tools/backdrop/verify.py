"""`backdrop.py verify`: the lyric layer over the background, measured against the pass criteria of docs/BACKDROP.md.

Prepares OUT/verify/ (job.json, background frames and subject masks at each sample time), serves it with the engine
(src/*.js joined, like build.py --dev) and verify.html on 127.0.0.1, and lets a browser render and measure:
headless Chrome / Edge by default, or any browser opened on the printed URL (--browser none — e.g. the Claude
desktop browser pane). The page posts its numbers and the contact sheet back; this writes report.json / report.md.
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
                if name.startswith('frame/') and name[6:].replace('_', '').isalnum():
                    os.makedirs(os.path.join(srv.vdir, 'fails'), exist_ok=True)
                    open(os.path.join(srv.vdir, 'fails', name[6:] + '.png'), 'wb').write(body)
                    return self.send(200, b'ok')
                if name in ('result', 'sheet', 'done'):
                    srv.got[name] = body
                    if name == 'done':
                        srv.done.set()
                    return self.send(200, b'ok')
                self.send(404)

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', port), H)
        self.url = 'http://127.0.0.1:%d/verify.html' % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
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

def run(args, B):
    src = B.open_source(args)
    offset = args.offset if args.offset is not None else getattr(src, 'offset', 0.0)
    duration = args.duration or max(1.0, src.duration - offset)
    out = os.path.abspath(args.out or B.default_out(args.project))
    vdir = os.path.join(out, 'verify')
    cache = os.path.join(out, 'cache', B.source_key(src))
    os.makedirs(vdir, exist_ok=True)
    shutil.rmtree(os.path.join(vdir, 'fails'), ignore_errors=True)
    project = B.load_json(args.project)
    if not project.get('backdrop'):
        B.log('warning: the project has no backdrop block — verifying the plain layout')
    off_bd = (project.get('backdrop') or {}).get('offset', offset)
    if abs(off_bd - offset) > 1e-6:
        B.log('warning: backdrop.offset is %s but the background is checked with offset %s' % (off_bd, offset))
    info = B.jz('plan', os.path.abspath(args.project), duration)
    samples, seen = [], set()
    for c in info['cuts']:
        for f in FRACS:
            t = round(c['start'] + c['dur'] * f, 3)
            if t in seen:
                continue
            seen.add(t)
            samples.append({'t': t, 'tt': round(t + offset, 3), 'line': c['line'], 'thumb': f == 0.6, 'name': 'L%03d_' % c['line'] + B.stamp(t)})
    K = args.scale
    vw = round(info['W'] * K)
    size = (vw, round(info['H'] * K))
    tts = sorted({s['tt'] for s in samples})
    B.log('verify: %d samples over %d cuts' % (len(samples), len(info['cuts'])))
    frames = src.frames(tts, os.path.join(vdir, 'bg'), vw)
    review = args.review or os.path.join(out, 'review.json')
    confirms = []
    if os.path.exists(review):
        for ln in B.load_json(review).get('lines', []):
            if (ln.get('confirm') or {}).get('avoid'):
                confirms.append({'t0': ln['t0'], 't1': ln['t1'], 'avoid': ln['confirm']['avoid']})
    masks = subject_masks(B, src, cache, args.detector, tts, confirms, size, vdir)
    for s in samples:
        s['bg'] = os.path.relpath(frames[s['tt']], vdir).replace('\\', '/')
        s['mask'] = masks[s['tt']]
    B.save_json(os.path.join(vdir, 'job.json'), {'project': project, 'duration': duration, 'scale': K, 'criteria': CRITERIA, 'samples': samples})
    srv = Server(vdir, args.port)
    browser = None if args.browser == 'none' else find_browser(args.browser)
    proc, profile = None, None
    try:
        if browser:
            profile = tempfile.mkdtemp(prefix='jzbd_browser_')
            proc = subprocess.Popen([browser, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                                     '--user-data-dir=' + profile, '--window-size=1400,1000', srv.url],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            B.log('rendering in %s (headless) …' % os.path.basename(browser))
        else:
            B.log('open %s in a browser (it renders, measures and posts back here) …' % srv.url)
        if not srv.done.wait(args.timeout):
            raise SystemExit('verify: no result from the page within %d s (%s)' % (args.timeout, srv.url))
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        srv.close()
        if profile:
            shutil.rmtree(profile, ignore_errors=True)
    result = json.loads(srv.got.get('result') or b'{}')
    if srv.got.get('sheet'):
        open(os.path.join(vdir, 'contact.png'), 'wb').write(srv.got['sheet'])
    report = summarize(result, info)
    B.save_json(os.path.join(vdir, 'report.json'), report)
    open(os.path.join(vdir, 'report.md'), 'w', encoding='utf-8', newline='\n').write(markdown(report, args, src))
    B.log('report: %s, contact sheet: %s' % (os.path.join(vdir, 'report.md'), os.path.join(vdir, 'contact.png')))
    print(json.dumps({'pass': report['pass'], 'lines': len(report['lines']), 'failing': [l['line'] for l in report['lines'] if not l['pass']],
                      'errors': report['errors'], 'out': vdir}, ensure_ascii=False))

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
        vals = lambda k: [r[k] for r in rs if r.get(k) is not None]
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
