"""Subject masks for the backdrop tool.

Two local detectors, combined per frame:
  person    — HyperFrames `remove-background` (u2net_human_seg, runs on the CPU, weights cached after the first run).
              Reliable on people with a body; weak on stylised close-ups; blind to things that are not people.
  saliency  — numpy only: how far each pixel's colour is from the colours at the frame's border, with a centre prior.
              A guess for any subject; it misses subjects that touch the border in the border's colours.
A frame is "sure" when the person mask is solid; otherwise the saliency guess is used (alone, or added to a weak
person mask) and the frame is flagged so an agent can confirm or correct the line (see review.json).
"""
import os, shutil, subprocess, tempfile
import numpy as np
from PIL import Image

PERSON_CMD = os.environ.get('JIZURA_HYPERFRAMES', '')   # e.g. "npx --no-install hyperframes"

def _hf_cmd():
    if PERSON_CMD:
        return PERSON_CMD.split()
    npx = shutil.which('npx') or shutil.which('npx.cmd')
    if not npx:
        return None
    return [npx, '--yes', 'hyperframes']

def _run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True)

def person_masks(frames, cache_dir, log=print):
    """person masks (8-bit grey PNG, same size as the frame) for frame image paths: {frame: mask path}, or {} when the
    detector is not available. All missing frames go through the model in one run (as a short video)."""
    os.makedirs(cache_dir, exist_ok=True)
    out, todo = {}, []
    for f in frames:
        m = os.path.join(cache_dir, os.path.splitext(os.path.basename(f))[0] + '_person.png')
        out[f] = m
        if not os.path.exists(m):
            todo.append((f, m))
    if not todo:
        return out
    cmd = _hf_cmd()
    if not cmd:
        log('person detector unavailable (npx not found): saliency only')
        return {}
    tmp = tempfile.mkdtemp(prefix='jzbd_')
    try:
        if len(todo) == 1 and not todo[0][0].lower().endswith('.mp4'):
            # one picture (a pan's illustration): straight through, RGBA cut-out → alpha
            src = os.path.join(tmp, 'in.png')
            Image.open(todo[0][0]).convert('RGB').save(src)
            _run(cmd + ['remove-background', src, '-o', os.path.join(tmp, 'cut.png')])
            Image.open(os.path.join(tmp, 'cut.png')).getchannel('A').save(todo[0][1])
            return out
        size = Image.open(todo[0][0]).size
        for i, (f, _) in enumerate(todo):
            im = Image.open(f).convert('RGB')
            if im.size != size:
                im = im.resize(size)
            im.save(os.path.join(tmp, '%05d.png' % i))
        seq, cut = os.path.join(tmp, 'seq.mp4'), os.path.join(tmp, 'seq.mov')
        _run(['ffmpeg', '-v', 'error', '-y', '-framerate', '10', '-i', os.path.join(tmp, '%05d.png'),
              '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', '-c:v', 'libx264', '-crf', '10', '-g', '1', '-pix_fmt', 'yuv420p', seq])
        log('person detector: %d frames …' % len(todo))
        _run(cmd + ['remove-background', seq, '-o', cut])
        _run(['ffmpeg', '-v', 'error', '-y', '-i', cut, '-vf', 'alphaextract,format=gray', '-start_number', '0',
              os.path.join(tmp, 'a_%05d.png')])
        for i, (f, m) in enumerate(todo):
            a = os.path.join(tmp, 'a_%05d.png' % i)
            if not os.path.exists(a):
                raise RuntimeError('person detector returned fewer frames than it was given')
            Image.open(a).convert('L').resize(Image.open(f).size).save(m)
        return out
    except (subprocess.CalledProcessError, OSError, RuntimeError) as e:
        err = getattr(e, 'stderr', b'') or b''
        log('person detector failed (%s): saliency only\n%s' % (e, err.decode('utf-8', 'replace')[-600:]))
        return {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# ---------- numpy helpers ----------

def load_rgb(path, size):
    return np.asarray(Image.open(path).convert('RGB').resize(size, Image.BILINEAR), dtype=np.float32) / 255

def load_grey(path, size):
    return np.asarray(Image.open(path).convert('L').resize(size, Image.BILINEAR), dtype=np.float32) / 255

def box_blur(a, r):
    """mean over a (2r+1)² window (edges clamped)"""
    if r < 1:
        return a
    p = np.pad(a, ((r + 1, r), (r + 1, r)), mode='edge')
    c = p.cumsum(0).cumsum(1)
    n = 2 * r + 1
    return (c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]) / (n * n)

def luminance(rgb):
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114

def otsu(v):
    h, edges = np.histogram(v, bins=64, range=(0, 1))
    w = h.cumsum()
    m = (h * edges[:-1]).cumsum()
    tot, mt = w[-1], m[-1]
    w0, w1 = w, tot - w
    with np.errstate(divide='ignore', invalid='ignore'):
        between = (mt * w0 / tot - m) ** 2 / (w0 * w1)
    between[~np.isfinite(between)] = 0
    return edges[int(between.argmax()) + 1]

def components(mask, min_frac=0.01, keep=4):
    """bounding boxes [x, y, w, h] (0..1) of the largest 4-connected parts of a bool mask, largest first"""
    h, w = mask.shape
    lab = np.zeros(mask.shape, np.int32)
    boxes, n = [], 0
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys, xs):
        if lab[y0, x0]:
            continue
        n += 1
        stack = [(y0, x0)]
        lab[y0, x0] = n
        cnt, bx0, by0, bx1, by1 = 0, x0, y0, x0, y0
        while stack:
            y, x = stack.pop()
            cnt += 1
            bx0, bx1, by0, by1 = min(bx0, x), max(bx1, x), min(by0, y), max(by1, y)
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not lab[yy, xx]:
                    lab[yy, xx] = n
                    stack.append((yy, xx))
        if cnt >= min_frac * h * w:
            boxes.append((cnt, [bx0 / w, by0 / h, (bx1 + 1 - bx0) / w, (by1 + 1 - by0) / h], n))
    boxes.sort(key=lambda b: -b[0])
    kept = boxes[:keep]
    keep_mask = np.isin(lab, [b[2] for b in kept]) if kept else np.zeros_like(mask)
    return [b[1] for b in kept], keep_mask

def dilate_bool(m, r):
    return box_blur(m.astype(np.float32), r) > 1e-3

def saliency(rgb):
    """0..1 map: colour distance from the border's palette × a centre prior"""
    h, w, _ = rgb.shape
    L = luminance(rgb)
    feat = np.stack([L, (rgb[..., 0] - rgb[..., 1]) * 0.8, ((rgb[..., 0] + rgb[..., 1]) / 2 - rgb[..., 2]) * 0.8], -1)
    r = max(1, w // 160)
    feat = np.stack([box_blur(feat[..., k], r) for k in range(3)], -1)
    bw, bh = max(2, int(w * 0.06)), max(2, int(h * 0.06))
    border = np.concatenate([feat[:bh].reshape(-1, 3), feat[-bh:].reshape(-1, 3), feat[:, :bw].reshape(-1, 3), feat[:, -bw:].reshape(-1, 3)])
    rng = np.random.default_rng(0)
    pts = border[rng.choice(len(border), min(len(border), 2000), replace=False)]
    cen = pts[rng.choice(len(pts), 6, replace=False)]
    for _ in range(8):                                        # k-means of the border colours
        lab = ((pts[:, None] - cen[None]) ** 2).sum(-1).argmin(1)
        cen = np.array([pts[lab == k].mean(0) if (lab == k).any() else cen[k] for k in range(len(cen))])
    flat = feat.reshape(-1, 3)
    d = np.sqrt(np.min(((flat[:, None] - cen[None]) ** 2).sum(-1), 1)).reshape(h, w)
    d = np.clip(d / (np.percentile(d, 99) + 1e-4), 0, 1)
    yy, xx = np.mgrid[0:h, 0:w]
    g = np.exp(-(((xx / w - 0.5) / 0.32) ** 2 + ((yy / h - 0.5) / 0.38) ** 2) / 2)
    return box_blur(d * (0.35 + 0.65 * g), r)

def subject(rgb, person=None):
    """(mask bool, source, reasons) for one frame at analysis size. person: 0..1 map or None."""
    h, w, _ = rgb.shape
    small = lambda m: components(m[::2, ::2], 0.01)[1].repeat(2, 0).repeat(2, 1)[:h, :w]
    if person is not None:
        strong, weak = person > 0.5, person > 0.15
        solid = float(person[weak].mean()) if weak.any() else 0.0
        if strong.mean() >= 0.01 and solid >= 0.55:
            # sure of at least one figure; fainter parts (a second, stylised face) are kept but noted
            faint = small((person > 0.2) & ~dilate_bool(strong, 2))
            notes = ['part of the figures is a faint person mask'] if faint.mean() >= 0.03 else []
            return small(person > 0.2), 'person', notes
    sal = saliency(rgb)
    thr = max(otsu(sal), 0.3)
    smask = small(sal > thr)
    if person is not None and (person > 0.15).mean() >= 0.02:
        return smask | small(person > 0.2), 'person?+saliency', ['weak person mask (stylised or partial figure?)']
    if smask.mean() < 0.005:
        return smask, 'none', ['nothing stands out (no subject, or one the detectors miss)']
    return smask, 'saliency', ['no person found: subject is a saliency guess']
