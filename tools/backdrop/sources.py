"""Background pictures for the backdrop tool: frames of a video (ffmpeg), or a HyperFrames-style pan over a still
illustration (rendered here with an affine transform — no video needed).

Pan description (JSON, mirrors a GSAP tween on the illustration's wrapper in a HyperFrames composition):
  {
    "image": "city.png",               # path relative to this file
    "frame": [1920, 1080],             # composition size in px
    "box": [0, 0, 3840, 2160],         # wrapper's left, top, width, height before the transform (default: cover the frame)
    "origin": "50% 50%",               # transformOrigin of the wrapper (GSAP default)
    "offset": 0,                       # composition time = song time + offset (becomes backdrop.offset)
    "keys": [                          # poses at composition seconds; a key's ease shapes the move that arrives at it
      {"t": 0,  "x": 0, "y": 0, "scale": 1},
      {"t": 30, "x": -1200, "y": -400, "scale": 1.2, "ease": "sine.inOut"}
    ]
  }
x / y are px, xPercent / yPercent percent of the box (like GSAP); missing values carry over from the previous key.
"""
import json, math, os, subprocess
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw)

def probe(video):
    """(width, height, duration) of a video"""
    r = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height:format=duration',
             '-of', 'json', video])
    j = json.loads(r.stdout)
    s = j['streams'][0]
    return s['width'], s['height'], float(j['format'].get('duration') or 0)

def stamp(t):
    """file-name key for a time (ms)"""
    return 't%07d' % round(t * 1000)

class VideoSource:
    kind = 'video'

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.w, self.h, self.duration = probe(self.path)
        self.name = os.path.basename(path)

    def clamp(self, t):
        return min(max(t, 0.0), max(0.0, self.duration - 0.06))

    def frames(self, times, out_dir, width):
        """extract one frame per time (track seconds) as JPEG `width` px wide; returns {t: path}. Cached by file name."""
        os.makedirs(out_dir, exist_ok=True)
        res = {}
        todo = []
        for t in times:
            p = os.path.join(out_dir, '%s_w%d.jpg' % (stamp(t), width))
            res[t] = p
            if not os.path.exists(p):
                todo.append((t, p))

        def grab(job):
            t, p = job
            run(['ffmpeg', '-v', 'error', '-y', '-ss', '%.3f' % self.clamp(t), '-i', self.path, '-frames:v', '1',
                 '-vf', 'scale=%d:-2' % width, '-q:v', '3', p])
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(grab, todo))
        return res

def _ease(name):
    """GSAP ease names → f(p) on 0..1"""
    name = (name or 'power1.out').strip()
    if name in ('none', 'linear', 'power0', 'power0.none'):
        return lambda p: p
    base, _, kind = name.partition('.')
    kind = kind or 'out'
    powers = {'power1': 2, 'quad': 2, 'power2': 3, 'cubic': 3, 'power3': 4, 'quart': 4, 'power4': 5, 'quint': 5, 'strong': 5}
    if base in powers:
        n = powers[base]
        f_in = lambda p: p ** n
    elif base == 'sine':
        f_in = lambda p: 1 - math.cos(p * math.pi / 2)
    elif base == 'expo':
        f_in = lambda p: 0 if p == 0 else 2 ** (10 * (p - 1))
    elif base == 'circ':
        f_in = lambda p: 1 - math.sqrt(max(0.0, 1 - p * p))
    else:
        raise ValueError('unsupported ease: %s (use none, power0-4, quad, cubic, quart, quint, sine, expo, circ)' % name)
    if kind == 'in':
        return f_in
    if kind == 'out':
        return lambda p: 1 - f_in(1 - p)
    if kind == 'inOut':
        return lambda p: f_in(2 * p) / 2 if p < 0.5 else 1 - f_in(2 - 2 * p) / 2
    raise ValueError('unsupported ease: ' + name)

def _origin(o, bw, bh):
    """GSAP transformOrigin → px inside the box"""
    if isinstance(o, (list, tuple)):
        return float(o[0]) * bw, float(o[1]) * bh
    words = {'left': '0%', 'top': '0%', 'center': '50%', 'right': '100%', 'bottom': '100%'}
    raw = str(o or '50% 50%').split()
    if len(raw) == 1:
        raw = ['center', raw[0]] if raw[0] in ('top', 'bottom') else [raw[0], 'center']
    if raw[0] in ('top', 'bottom') or raw[1] in ('left', 'right'):            # "top left" order
        raw = raw[::-1]
    parts = [words.get(p, p) for p in raw[:2]]
    def px(v, size):
        return float(v[:-1]) / 100 * size if v.endswith('%') else float(v.replace('px', ''))
    return px(parts[0], bw), px(parts[1], bh)

class PanSource:
    kind = 'pan'

    def __init__(self, path):
        self.path = os.path.abspath(path)
        with open(self.path, encoding='utf-8-sig') as f:
            d = json.load(f)
        self.image_path = os.path.join(os.path.dirname(self.path), d['image'])
        self.name = os.path.basename(d['image'])
        self.image = Image.open(self.image_path).convert('RGB')
        iw, ih = self.image.size
        self.w, self.h = [int(v) for v in d.get('frame', [1920, 1080])]
        if d.get('box'):
            self.box = [float(v) for v in d['box']]
        else:                                    # object-fit: cover, centred
            s = max(self.w / iw, self.h / ih)
            self.box = [(self.w - iw * s) / 2, (self.h - ih * s) / 2, iw * s, ih * s]
        L, T, BW, BH = self.box
        ox, oy = _origin(d.get('origin'), BW, BH)
        self.origin = (L + ox, T + oy)
        self.offset = float(d.get('offset', 0) or 0)
        keys, cur = [], {'x': 0.0, 'y': 0.0, 'xPercent': 0.0, 'yPercent': 0.0, 'scale': 1.0}
        for k in sorted(d['keys'], key=lambda k: float(k['t'])):
            cur = dict(cur, **{p: float(k[p]) for p in cur if p in k})
            keys.append((float(k['t']), dict(cur), _ease(k.get('ease'))))
        if not keys:
            raise ValueError('pan needs at least one key')
        self.keys = keys
        self.duration = keys[-1][0]

    def clamp(self, t):
        return t

    def pose(self, t):
        """(tx, ty, scale) at composition time t"""
        ks = self.keys
        if t <= ks[0][0]:
            p = ks[0][1]
        elif t >= ks[-1][0]:
            p = ks[-1][1]
        else:
            i = next(i for i in range(1, len(ks)) if t <= ks[i][0])
            (t0, a, _), (t1, b, ease) = ks[i - 1], ks[i]
            u = ease((t - t0) / (t1 - t0)) if t1 > t0 else 1
            p = {k: a[k] + (b[k] - a[k]) * u for k in a}
        return p['x'] + p['xPercent'] / 100 * self.box[2], p['y'] + p['yPercent'] / 100 * self.box[3], p['scale']

    def render(self, img, t, size):
        """`img` (the illustration, or its mask: anything with the illustration's aspect) as it shows in the frame at
        time t, `size` = (w, h) of the output"""
        tx, ty, s = self.pose(t)
        L, T, BW, BH = self.box
        Ox, Oy = self.origin
        k = size[0] / self.w
        sx, sy = img.size[0] / BW, img.size[1] / BH
        # output px → composition px (÷k) → undo translate and scale about the origin → box px → image px
        a = sx / (k * s)
        c = sx * ((-Ox - tx) / s + Ox - L)
        e = sy / (k * s)
        f = sy * ((-Oy - ty) / s + Oy - T)
        return img.transform(size, Image.AFFINE, (a, 0, c, 0, e, f), resample=Image.BILINEAR)

    def box_at(self, rect, t):
        """a rectangle on the illustration (0..1 of the image) → 0..1 of the frame at time t"""
        tx, ty, s = self.pose(t)
        L, T, BW, BH = self.box
        Ox, Oy = self.origin
        def to(u, v):
            X, Y = L + u * BW, T + v * BH
            return (Ox + tx + s * (X - Ox)) / self.w, (Oy + ty + s * (Y - Oy)) / self.h
        x0, y0 = to(rect[0], rect[1])
        x1, y1 = to(rect[0] + rect[2], rect[1] + rect[3])
        return [x0, y0, x1 - x0, y1 - y0]

    def frames(self, times, out_dir, width):
        os.makedirs(out_dir, exist_ok=True)
        size = (width, round(width * self.h / self.w / 2) * 2)
        res = {}
        for t in times:
            p = os.path.join(out_dir, '%s_w%d.jpg' % (stamp(t), width))
            if not os.path.exists(p):
                self.render(self.image, t, size).save(p, quality=90)
            res[t] = p
        return res
