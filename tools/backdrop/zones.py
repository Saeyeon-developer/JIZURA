"""Free areas for one lyric line: candidate rectangles scored on what the background does during the line.

Inputs are stacks over the line's sample frames at analysis size: the subject mask (any sample), luminance and
colour. Every candidate on a grid (0.03 margin, as the planner's own candidates) is scored with integral images:
  rejected   when more than 0.5 % of it is on the subject (dilated by MARGIN)
  size       short side vs 45 % of the frame's short side, plus √area (small areas give small text)
  clearance  how far it keeps from the subject (0.03 / 0.06 / 0.10 of the frame)
  clutter    luminance stddev and edge density inside it (the picture fights the text)
  continuity overlap with the previous line's area; the exact same area gets a bonus (text stays put)
The planner keeps the previous line's area whenever it is anywhere in `free`, so it is only listed when chosen.
"""
import numpy as np
from detect import box_blur, luminance

MARGIN = 0.02
W_SIZE, W_CLEAR, W_CLUTTER, W_CONT, W_STAY = 1.0, 0.25, 0.6, 0.15, 0.08
BUSY_FLAG = 0.8                     # a chosen area this cluttered is worth a look (legibility 'auto' already reacts from 0.45)

def integral(a):
    c = np.zeros((a.shape[0] + 1, a.shape[1] + 1), np.float64)
    c[1:, 1:] = a.cumsum(0).cumsum(1)
    return c

def rect_sum(I, x0, y0, x1, y1):
    """sums over [y0:y1, x0:x1] for arrays of pixel bounds"""
    return I[y1, x1] - I[y0, x1] - I[y1, x0] + I[y0, x0]

def dilate(mask, frac):
    h, w = mask.shape
    r = max(1, round(frac * max(h, w)))
    return box_blur(mask.astype(np.float32), r) > 1e-3

def candidates():
    xs = np.round(np.arange(0.03, 0.97, 0.02), 4)
    sizes_w = np.round(np.arange(0.28, 0.95, 0.06), 4)
    sizes_h = np.round(np.arange(0.18, 0.95, 0.06), 4)
    out = [(x, y, cw, ch) for cw in sizes_w for ch in sizes_h for x in xs for y in xs
           if x + cw <= 0.97 + 1e-9 and y + ch <= 0.97 + 1e-9]
    return np.array(out, np.float64)

CANDS = candidates()

def clutter_maps(lum):
    """per-pixel local stddev of luminance and edge flags (one frame)"""
    h, w = lum.shape
    r = max(1, w // 64)
    m = box_blur(lum, r)
    sd = np.sqrt(np.maximum(box_blur(lum * lum, r) - m * m, 0))
    gx = np.zeros_like(lum); gy = np.zeros_like(lum)
    gx[:, 1:-1] = lum[:, 2:] - lum[:, :-2]
    gy[1:-1] = lum[2:] - lum[:-2]
    edge = (np.hypot(gx, gy) > 0.12).astype(np.float32)
    return sd, edge

def iou(a, b):
    ax1, ay1, bx1, by1 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw, ih = max(0, min(ax1, bx1) - max(a[0], b[0])), max(0, min(ay1, by1) - max(a[1], b[1]))
    i = iw * ih
    return i / (a[2] * a[3] + b[2] * b[3] - i + 1e-9)

def tone_of(rect, rgbs):
    """tone of a given area (an agent's confirmed `free`): median colour and clutter, all samples together"""
    h, w, _ = rgbs[0].shape
    x0, y0 = int(rect[0] * w), int(rect[1] * h)
    x1, y1 = max(x0 + 1, int((rect[0] + rect[2]) * w)), max(y0 + 1, int((rect[1] + rect[3]) * h))
    pix = np.concatenate([rgb[y0:y1, x0:x1].reshape(-1, 3) for rgb in rgbs])
    maps = [clutter_maps(luminance(rgb)) for rgb in rgbs]
    sd = np.mean([s[y0:y1, x0:x1].mean() for s, _ in maps])
    ed = np.mean([e[y0:y1, x0:x1].mean() for _, e in maps])
    med = np.median(pix, 0)
    return {'color': '#%02X%02X%02X' % tuple(int(round(v * 255)) for v in med),
            'busy': round(float(np.clip(0.5 * sd / 0.12 + 0.5 * ed / 0.18, 0, 1)), 3)}

def choose(subj, rgbs, frame_wh, prev=None, n_free=4):
    """subj: bool mask (union over samples); rgbs: list of h×w×3 frames. Returns dict with free (best first), busy,
    tone, score details, and problems (list of strings) when the result needs a look."""
    h, w = subj.shape
    W, H = frame_wh
    C = CANDS
    px = lambda v, n: np.clip(np.round(v * n).astype(int), 0, n)
    x0, y0, x1, y1 = px(C[:, 0], w), px(C[:, 1], h), px(C[:, 0] + C[:, 2], w), px(C[:, 1] + C[:, 3], h)
    area_px = np.maximum((x1 - x0) * (y1 - y0), 1)
    hit = rect_sum(integral(dilate(subj, MARGIN)), x0, y0, x1, y1) / area_px
    clear = np.zeros(len(C))
    for d in (0.03, 0.06, 0.10):
        clear += rect_sum(integral(dilate(subj, MARGIN + d)), x0, y0, x1, y1) == 0
    clear /= 3
    sd = np.zeros(len(C)); ed = np.zeros(len(C))
    for rgb in rgbs:
        s, e = clutter_maps(luminance(rgb))
        sd += rect_sum(integral(s), x0, y0, x1, y1) / area_px
        ed += rect_sum(integral(e), x0, y0, x1, y1) / area_px
    sd /= len(rgbs); ed /= len(rgbs)
    clutter = np.clip(0.5 * sd / 0.12 + 0.5 * ed / 0.18, 0, 1)
    short = np.minimum(C[:, 2] * W, C[:, 3] * H) / min(W, H)
    aspect = (C[:, 2] * W) / (C[:, 3] * H)
    size = 0.6 * np.minimum(1, short / 0.45) + 0.4 * np.sqrt(C[:, 2] * C[:, 3])
    size -= 0.3 * ((aspect > 4) | (aspect < 0.25))
    score = W_SIZE * size + W_CLEAR * clear - W_CLUTTER * clutter
    if prev is not None:
        pv = np.array(prev)
        ix = np.maximum(0, np.minimum(C[:, 0] + C[:, 2], pv[0] + pv[2]) - np.maximum(C[:, 0], pv[0]))
        iy = np.maximum(0, np.minimum(C[:, 1] + C[:, 3], pv[1] + pv[3]) - np.maximum(C[:, 1], pv[1]))
        inter = ix * iy
        score += W_CONT * inter / (C[:, 2] * C[:, 3] + pv[2] * pv[3] - inter)
    ok = hit <= 0.005
    problems = []
    if not ok.any():
        problems.append('no area clear of the subject: least-covered areas used (%.0f %% on the subject)' % (hit.min() * 100))
        ok = hit <= hit.min() + 0.05
        score = score - 3 * hit
    # the previous line's exact area, re-scored (it may not be on this grid's best spots but staying put reads calmer)
    stay = None
    if prev is not None:
        P = np.array([prev])
        j = int(np.argmin(np.abs(C - P).sum(1)))
        if np.abs(C[j] - P[0]).sum() < 1e-6 and ok[j]:
            stay = j
            score[j] += W_STAY
    order = np.argsort(-np.where(ok, score, -1e9))
    free = []
    for j in order:
        if not ok[j] or len(free) >= n_free:
            break
        r = C[j]
        if stay is not None and j == stay and free:
            continue                       # listed only when it is the choice (the planner would always take it)
        if all(iou(r, C[k]) < 0.3 for k in free):
            free.append(int(j))
    best = free[0]
    rects = [[round(float(v), 4) for v in C[j]] for j in free]
    if short[best] < 0.3:
        problems.append('small free area (short side %.0f %% of the frame): text will be small' % (short[best] * 100))
    if clutter[best] >= BUSY_FLAG:
        problems.append('busy picture in the free area (%.2f): check the legibility treatment' % clutter[best])
    return {
        'free': rects, 'tone': tone_of(rects[0], rgbs),
        'detail': {'score': round(float(score[best]), 3), 'size': round(float(size[best]), 3), 'clearance': round(float(clear[best]), 3),
                   'clutter': round(float(clutter[best]), 3), 'onSubject': round(float(hit[best]), 4), 'kept': stay is not None and best == stay},
        'problems': problems,
    }
