# -*- coding: utf-8 -*-
"""Watermark badge removal by exact sprite subtraction, size-adaptive.

The image backend stamps a fixed white semi-transparent badge
("AI生成 / WORKBUDDY>") into the bottom-right corner of every generated
image. The badge sprite was extracted from pure-black reference generations
(on black, a badge pixel's value equals alpha * 255, since the badge is
neutral white).

Geometric law (validated on pristine generations at 1024x1024, 1792x768,
1024x1536 and 1280x1280):

    s             = min(W, H) / 1024          # scale follows the SHORT side
    badge size    = (round(111*s), round(51*s))
    corner margin = floor(10*s)               # right and bottom edges
    position      = (W - bw - margin, H - bh - margin)

The law predicts the badge to within ONE pixel: at 1280x1280 (s=1.25) the
true badge is 140x64 at (1128,1204) while the law predicts 139x64 at
(1129,1204). A one-pixel misalignment removes only ~75% of every stroke
and leaves a clearly visible residue, so non-calibrated sizes run a local
geometry search before subtracting (see below).

Removal inverts the alpha compositing exactly:
    stamped = (1 - a) * original + a * 255
    =>  original = (stamped - a * 255) / (1 - a)

Only pixels covered by the sprite are touched; everything else is preserved
bit-for-bit.

Why layered verification is required
------------------------------------
The re-compositing residual is NOT a cleanliness proof: the inverse
operation reproduces the input exactly wherever no clamping occurs, so a
wrong alpha at a bright pixel still yields residual ~ 0 while leaving a
visible ghost (an under-estimated alpha) or carving a dark badge-shaped
dent (an over-estimated one). The server also re-rasterizes the badge at
each output size, so a resampled sprite is never alpha-exact.

Worse, a MISALIGNED subtraction decorrelates the residue from the sprite:
the leftover pattern is the difference of two shifted badges, so its ghost
NCC collapses (~0.1) even though the badge is plainly visible. The NCC
alone therefore cannot clear a removal; the residue must be hunted for
explicitly.

Pipeline per image (all local, no network, no credits):

  1. geometry     - calibrated sprite when the short side matches a
     calibrated scale (768, 1024); otherwise the nearest calibrated sprite
     bilinearly resampled to the law-predicted size.
  2. search       - for resampled (non-exact) sprites only: a staged local
     search on the INPUT - offsets +/-3, then size +/-1, then offsets
     again - scored by badge-NCC. The input signal is clean (no subtraction
     artifacts), so the true geometry wins decisively (0.97 vs 0.93 at
     1280x1280). Absorbs the server's rounding differences. Calibrated
     sizes skip the search: their sprites are exact.
  3. presence gate - badge evidence at the final position: excess-NCC
     (>= 0.35 on every pristine sample, <= 0.12 on badge-free corners)
     plus a stroke-excess floor. No subtraction without this gate.
     Evidence is measured against a LOCAL BACKGROUND MODEL, not a
     constant: the badge sits on real content whose brightness varies
     across the window (gradients, shading). A constant background leaves
     that trend in the excess map, where it drowns the badge signal (the
     resampled sprites of small scales have blurred, low-alpha strokes)
     and fakes a residual after a perfect removal. A least-squares plane
     fitted on the clean ring around the searchable corner removes the
     trend; a degenerate fit falls back to the ring median.
  4. subtract + ghost metrics.
  5. ghost correction (only when a strong ghost survives, NCC > 0.30) -
     the remaining bright pattern measures the alpha error; a damped,
     smoothed per-pixel correction is iterated (denominator = 255 - output
     value, i.e. the recovered content, NOT the ring background, which
     underestimates the correction on bright content) and the best
     iteration is kept.
  6. verify       - the OUTPUT is searched over the same neighborhood for
     any badge-shaped pattern (best-match pos-NCC and stroke excess).
     This is the real cleanliness proof: it catches misaligned or
     partial removals whose residue decorrelates from the sprite at the
     subtracted position.
  7. verdict      - three independent gates must all pass (see the
     "Verdict data" table below): the best-match pos-NCC, the strongest
     sprite-GRADIENT correlation among badge-like candidates (a one-pixel
     geometry error leaves the sprite's edge pattern as residue), and the
     ghost NCC (a strongly negative value is a badge-shaped dark dent
     from over-subtraction). strict or visual => clean. A detected badge
     whose removal cannot be verified is delivered UNMODIFIED with
     clean = false and tier "unverified" - a missed badge is never passed
     off as a cleaned image. No badge at all => delivered unmodified,
     clean = true, tier "none".
  8. dent check   - diagnostic only (see below).

Verdict data (real generations, synthetic gradients and constructed
controls, plane background):

    gate            perfect removals      failure modes
    pos-NCC         0.01 - 0.32           untouched 0.93-1.00
                                           half-alpha 0.94
                                           1px shift 0.34-0.51
    grad-NCC        <= 0.20               1px shift 0.42-0.79
                                           size error 0.42-0.73
    ghost NCC       -0.02 .. +0.25        over-subtraction -0.67

The stroke excess is deliberately NOT a verdict gate: the true content
under the badge is unknowable, and bright content there (a lantern, snow)
fakes a large excess after a perfect removal (+19 measured). It is still
reported for diagnostics.

The dent check compares the recovered corner's high-frequency energy with
a surrounding ring. It is diagnostic only: a leftover badge lifts the
corner along the strokes (positive ghost NCC) while a carved or misaligned
subtraction dents it (negative ghost NCC) - both are captured by the ghost
NCC and the output-side verify, which measure exactly the property the
user cares about. The dent ratio itself conflates real image texture with
damage (a badge landing on fur or fabric raises it without any watermark
left behind), so it must not gate the verdict.
"""

import argparse
import json
import os
import sys

from png_codec import read_png, write_png

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
REGISTRY_JSON = os.path.join(ASSETS_DIR, "badge_sprites.json")

# sprite alpha below this (0..255) is treated as "no badge"
ALPHA_FLOOR = 5
# re-compositing residual (0..255); mathematical consistency check
RESIDUAL_OK = 2.0
# presence gate on the input image (NCC separates cleanly: pristine badges
# score 0.6-1.0, badge-free corners <= 0.12; the excess floor only guards
# degenerate flat images where the NCC denominator vanishes)
PRESENCE_NCC = 0.35
PRESENCE_EXCESS = 3.0
# ghost correction trigger on the output image
GHOST_CORRECT_NCC = 0.30
# output-side verification thresholds (best match over the neighborhood).
# Three INDEPENDENT gates, because no single statistic separates every
# failure mode from a perfect removal (measured on real generations,
# synthetic gradients and constructed controls - see module docstring):
#   pos-NCC  - badge-shaped BRIGHTNESS aligned with the sprite. Catches
#              untouched / partially removed / under-alpha badges.
#   grad-NCC - the residue's correlation with the sprite's spatial
#              GRADIENT. A one-pixel geometry error leaves bright
#              crescents next to carved dents - i.e. the sprite's edge
#              pattern - which decorrelates from the sprite itself.
#   carved   - a strongly negative ghost NCC: a badge-shaped dark dent
#              from over-subtraction, invisible to the bright-only gates.
# The stroke EXCESS is deliberately NOT a gate: the true content under
# the badge is unknowable, and bright content there (a lantern, snow)
# fakes a large excess after a perfect removal.
VERIFY_NCC_STRICT = 0.35
VERIFY_NCC_VISUAL = 0.45
VERIFY_GRAD = 0.30
GRAD_MIN_POS = 0.15
# dent check
DENT_RATIO = 2.0
DENT_FLOOR = 1.0
# ghost correction
CORRECT_DAMP = 0.85
CORRECT_ITERS = 4
# geometry search (input side) and verification search (output side)
SEARCH_OFFSET = 3
SEARCH_SIZE = 1

_registry_cache = None
_scale_cache = {}


def load_registry():
    """Load the sprite registry: law, calibrated bases, reference window."""
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache
    with open(REGISTRY_JSON, "r", encoding="utf-8") as f:
        reg = json.load(f)
    bases = []
    for b in reg["bases"]:
        sw, sh, _, spx, _ = read_png(os.path.join(ASSETS_DIR, b["file"]))
        if "badge" in b:
            bx, by, bw, bh = b["badge"]
            alpha = bytearray(bw * bh)
            for y in range(bh):
                for x in range(bw):
                    alpha[y * bw + x] = spx[(by + y) * sw + (bx + x)]
            bases.append({"min": b["min"], "alpha": bytes(alpha),
                          "w": bw, "h": bh, "window": None})
        else:
            bw, bh = b["size"]
            if (sw, sh) != (bw, bh):
                raise ValueError("sprite asset mismatch: %s" % b["file"])
            bases.append({"min": b["min"], "alpha": bytes(spx),
                          "w": bw, "h": bh, "window": None})
    # reference window (legacy fast path for the calibrated square size)
    ref = None
    for b in reg["bases"]:
        if "window" in b and b.get("min") == reg["law"]["base"]:
            sw, sh, _, spx, _ = read_png(os.path.join(ASSETS_DIR, b["file"]))
            ref = {"w": sw, "h": sh, "spx": spx,
                   "ox": b["window"][0], "oy": b["window"][1]}
    _registry_cache = {"law": reg["law"], "bases": bases, "ref": ref}
    return _registry_cache


def predict_geometry(w, h, law):
    """Apply the geometric law. Returns (s, bw, bh, ox, oy)."""
    base = law.get("base", 1024)
    s = min(w, h) / float(base)
    bw = int(round(law["badge_w"] * s))
    bh = int(round(law["badge_h"] * s))
    bw = max(1, min(bw, w))
    bh = max(1, min(bh, h))
    m = int(law.get("margin", 10) * s)
    ox = max(0, w - bw - m)
    oy = max(0, h - bh - m)
    return s, bw, bh, ox, oy


def scale_badge(alpha, bw0, bh0, bw, bh):
    """Bilinear resample of a badge alpha map (cached per target size)."""
    if (bw, bh) == (bw0, bh0):
        return alpha
    key = (bw0, bh0, bw, bh)
    hit = _scale_cache.get(key)
    if hit is not None:
        return hit
    out = bytearray(bw * bh)
    for y in range(bh):
        fy = (y + 0.5) * bh0 / bh - 0.5
        for x in range(bw):
            fx = (x + 0.5) * bw0 / bw - 0.5
            x0 = int(fx // 1)
            y0 = int(fy // 1)
            x1 = min(bw0 - 1, x0 + 1)
            y1 = min(bh0 - 1, y0 + 1)
            x0 = max(0, x0)
            y0 = max(0, y0)
            tx = fx - x0
            ty = fy - y0
            v00 = alpha[y0 * bw0 + x0]
            v01 = alpha[y0 * bw0 + x1]
            v10 = alpha[y1 * bw0 + x0]
            v11 = alpha[y1 * bw0 + x1]
            v = (v00 * (1 - tx) * (1 - ty) + v01 * tx * (1 - ty) +
                 v10 * (1 - tx) * ty + v11 * tx * ty)
            out[y * bw + x] = int(round(v))
    _scale_cache[key] = out
    return out


def base_sprite_for(w, h, registry):
    """Calibrated sprite for the short side, resampled to the predicted size.

    Returns (alpha, aw, ah, exact) where exact is True when a calibrated
    sprite matches the predicted size directly.
    """
    law = registry["law"]
    _, bw, bh, _, _ = predict_geometry(w, h, law)
    mn = min(w, h)
    best = None
    for b in registry["bases"]:
        if b["min"] == mn and (b["w"], b["h"]) == (bw, bh):
            return b["alpha"], b["w"], b["h"], True
        d = abs(b["min"] - mn)
        if best is None or d < best[0]:
            best = (d, b)
    b = best[1]
    if (b["w"], b["h"]) == (bw, bh):
        return b["alpha"], b["w"], b["h"], True
    return scale_badge(b["alpha"], b["w"], b["h"], bw, bh), bw, bh, False


def source_base_for(w, h, registry):
    """The calibrated base a resampled sprite for (w, h) derives from."""
    mn = min(w, h)
    best = None
    for b in registry["bases"]:
        d = abs(b["min"] - mn)
        if best is None or d < best[0]:
            best = (d, b)
    return best[1]


def subtract_sprite(px, w, h, ch, alpha, aw, ah, ox, oy, color=(255, 255, 255)):
    """Return (new_pixels, changed_pixels, max_residual, mean_residual)."""
    out = bytearray(px)
    stride = w * ch
    changed = 0
    max_res = 0.0
    res_sum = 0.0
    res_cnt = 0
    x_lo = max(0, ox)
    y_lo = max(0, oy)
    x_hi = min(w, ox + aw)
    y_hi = min(h, oy + ah)
    for y in range(y_lo, y_hi):
        sy = y - oy
        for x in range(x_lo, x_hi):
            sx = x - ox
            a = alpha[sy * aw + sx]
            if a < ALPHA_FLOOR or a > 254:
                continue
            af = a / 255.0
            o = y * stride + x * ch
            for c in range(ch):
                v = px[o + c]
                rec = (v - a * color[c] / 255.0) / (1.0 - af)
                rc = int(round(rec))
                if rc < 0:
                    rc = 0
                elif rc > 255:
                    rc = 255
                if rc != v:
                    changed += 1
                out[o + c] = rc
            for c in range(ch):
                recomp = (1.0 - af) * out[o + c] + af * color[c]
                res = abs(recomp - px[o + c])
                res_sum += res
                res_cnt += 1
                if res > max_res:
                    max_res = res
    mean_res = res_sum / res_cnt if res_cnt else 0.0
    return out, changed, max_res, mean_res


def _gray(buf, w, h, ch):
    g = bytearray(w * h)
    for i in range(w * h):
        o = i * ch
        g[i] = (buf[o] + buf[o + 1] + buf[o + 2]) // 3 if ch >= 3 else buf[o]
    return g


def _ncc(a, b, n):
    sa = sb = aa = bb = ab = 0.0
    for i in range(n):
        x = a[i]
        y = b[i]
        sa += x
        sb += y
        aa += x * x
        bb += y * y
        ab += x * y
    denom = ((n * aa - sa * sa) * (n * bb - sb * sb)) ** 0.5
    if denom < 1e-9:
        return 0.0
    return (n * ab - sa * sb) / denom


def _ring_bg(gray, w, h, ox, oy, aw, ah, ring=14):
    rx0, ry0 = max(0, ox - ring), max(0, oy - ring)
    rx1, ry1 = min(w, ox + aw + ring), min(h, oy + ah + ring)
    vals = []
    for y in range(ry0, ry1):
        for x in range(rx0, rx1):
            if ox <= x < ox + aw and oy <= y < oy + ah:
                continue
            vals.append(gray[y * w + x])
    vals.sort()
    return vals[len(vals) // 2] if vals else 0


def fit_plane(gray, w, h, x0, y0, x1, y1, excl=None):
    """Least-squares plane z = a + b*(x-cx) + c*(y-cy) over a rect.

    The rect is the local neighbourhood of the badge window; excl skips
    the window itself (and, when shared across a search, every window the
    search may examine) so the badge never biases its own background
    model. Coordinates are centred for conditioning. Returns
    (a, b, c, cx, cy), or None when the system is degenerate (flat or
    single-row ring) - callers then fall back to the ring median.
    """
    ex0 = ey0 = ex1 = ey1 = -1
    if excl is not None:
        ex0, ey0, ex1, ey1 = excl
    cx = (x0 + x1 - 1) / 2.0
    cy = (y0 + y1 - 1) / 2.0
    n = su = sv = suu = svv = suv = sz = suz = svz = 0.0
    for y in range(y0, y1):
        v = y - cy
        base = y * w
        for x in range(x0, x1):
            if ex0 <= x < ex1 and ey0 <= y < ey1:
                continue
            u = x - cx
            z = gray[base + x]
            n += 1.0
            su += u
            sv += v
            suu += u * u
            svv += v * v
            suv += u * v
            sz += z
            suz += u * z
            svz += v * z
    if n < 8:
        return None
    M = [[n, su, sv, sz],
         [su, suu, suv, suz],
         [sv, suv, svv, svz]]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-9:
            return None
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(3):
            if r != col and M[r][col] != 0.0:
                f = M[r][col] / pv
                for c2 in range(col, 4):
                    M[r][c2] -= f * M[col][c2]
    return (M[0][3] / M[0][0], M[1][3] / M[1][1], M[2][3] / M[2][2],
            cx, cy)


def _plane_bg(plane, aw, ah, ox, oy):
    """Per-pixel background array under a plane model (None if no plane)."""
    if plane is None:
        return None
    a, b, c, cx, cy = plane
    bg = [0.0] * (aw * ah)
    for y in range(ah):
        row = a + c * (oy + y - cy)
        base = y * aw
        for x in range(aw):
            bg[base + x] = row + b * (ox + x - cx)
    return bg


def bg_model(gray, w, h, ox, oy, aw, ah, ring=14, plane=None):
    """Per-pixel background for one window: shared plane, own fit, or the
    ring median as a last resort. Always returns a list of aw*ah floats."""
    if plane is not None:
        return _plane_bg(plane, aw, ah, ox, oy)
    p = fit_plane(gray, w, h, max(0, ox - ring), max(0, oy - ring),
                  min(w, ox + aw + ring), min(h, oy + ah + ring),
                  excl=(ox, oy, ox + aw, oy + ah))
    if p is not None:
        return _plane_bg(p, aw, ah, ox, oy)
    m = float(_ring_bg(gray, w, h, ox, oy, aw, ah, ring))
    return [m] * (aw * ah)


def window_metrics(gray, w, h, alpha, aw, ah, ox, oy, ring=14, plane=None):
    """Badge evidence over the window at (ox, oy) of a gray buffer.

    Returns (ncc, stroke_excess): correlation of (v - local_bg) with the
    sprite alpha, and the mean brightness lift on stroke pixels. Works on
    any gray buffer (input or output) - the ring around the window is
    outside any subtraction area, so input and output agree there.

    local_bg is a per-pixel background model rather than a constant: the
    badge sits on real image content whose brightness varies across the
    window (gradients, shading), and a constant background leaves that
    trend in the excess map, where it drowns the badge signal (the
    resampled sprites of small scales have blurred, low-alpha strokes)
    and fakes a residual after a perfect removal. A least-squares plane
    fitted on the clean ring around the window removes the trend (see
    fit_plane / bg_model); a degenerate fit falls back to the ring
    median. Pass a shared plane to reuse one fit across a search.

    The excess map fed to the NCC is clipped at zero: a white badge only
    BRIGHTENS the content, so negative excursions are content noise, not
    badge signal. Clipping halves the noise floor and sharpens the NCC
    (0.929 vs 0.924 at the true 1280x1280 position).
    """
    bg = bg_model(gray, w, h, ox, oy, aw, ah, ring, plane)
    n = aw * ah
    sa = sb = aa = bb = ab = 0.0
    ex_sum = 0.0
    ex_cnt = 0
    for y in range(ah):
        row = (oy + y) * w + ox
        brow = y * aw
        for x in range(aw):
            i = brow + x
            e = gray[row + x] - bg[i]
            ei = int(e + 0.5) if e >= 0 else 0
            if ei > 255:
                ei = 255
            a = alpha[i]
            sa += ei
            sb += a
            aa += ei * ei
            bb += a * a
            ab += ei * a
            if a >= 77:
                ex_sum += e
                ex_cnt += 1
    denom = ((n * aa - sa * sa) * (n * bb - sb * sb)) ** 0.5
    ncc = (n * ab - sa * sb) / denom if denom > 1e-9 else 0.0
    return ncc, ex_sum / max(ex_cnt, 1)


def presence_score(px, w, h, ch, alpha, aw, ah, ox, oy, ring=14):
    """Badge-presence evidence on the INPUT at the predicted position.

    Returns (ncc, stroke_excess): correlation of (v - local_bg) with the
    sprite alpha, and the mean brightness lift on stroke pixels.
    """
    gray = _gray(px, w, h, ch)
    return window_metrics(gray, w, h, alpha, aw, ah, ox, oy, ring)


def _ghost_from_window(wgray, alpha, aw, ah, bg):
    """Ghost metrics from a window gray buffer and a background model.

    bg is either a scalar or a per-pixel sequence (see bg_model).
    Returns (mean_excess, p95_excess, ncc).
    """
    per_pixel = not isinstance(bg, (int, float))
    n = aw * ah
    sa = sb = aa = bb = ab = 0.0
    stroke = []
    for i in range(n):
        e = wgray[i] - (bg[i] if per_pixel else bg)
        a = alpha[i]
        sa += e
        sb += a
        aa += e * e
        bb += a * a
        ab += e * a
        if a >= 77:
            stroke.append(e)
    denom = ((n * aa - sa * sa) * (n * bb - sb * sb)) ** 0.5
    ncc = (n * ab - sa * sb) / denom if denom > 1e-9 else 0.0
    stroke.sort()
    mean_ex = sum(stroke) / len(stroke) if stroke else 0.0
    p95 = stroke[int(len(stroke) * 0.95)] if stroke else 0.0
    return mean_ex, p95, ncc


def _window_gray(buf, w, ch, ox, oy, aw, ah):
    """Gray values of one window of a pixel buffer."""
    wg = bytearray(aw * ah)
    for y in range(ah):
        row = (oy + y) * w * ch
        for x in range(aw):
            o = row + (ox + x) * ch
            wg[y * aw + x] = ((buf[o] + buf[o + 1] + buf[o + 2]) // 3
                              if ch >= 3 else buf[o])
    return wg


def ghost_metrics(out_gray, w, h, alpha, aw, ah, ox, oy, ring=14):
    """Remaining badge-shaped pattern in the OUTPUT.

    Returns (mean_excess, p95_excess, ncc, bg). A correct removal leaves
    mean ~ 0 and ncc ~ 0 (no badge-shaped bright pattern) - but a
    MISALIGNED removal also scores ~0 here while remaining visible, which
    is why remove_watermark() verifies with a neighborhood search.
    """
    bg = bg_model(out_gray, w, h, ox, oy, aw, ah, ring)
    wg = bytearray(aw * ah)
    for y in range(ah):
        for x in range(aw):
            wg[y * aw + x] = out_gray[(oy + y) * w + (ox + x)]
    mean_ex, p95, ncc = _ghost_from_window(wg, alpha, aw, ah, bg)
    return mean_ex, p95, ncc, bg


def _blur3(src, aw, ah):
    out = [0.0] * (aw * ah)
    for y in range(ah):
        for x in range(aw):
            acc = 0.0
            n = 0
            for dy in (-1, 0, 1):
                yy = y + dy
                if yy < 0 or yy >= ah:
                    continue
                for dx in (-1, 0, 1):
                    xx = x + dx
                    if xx < 0 or xx >= aw:
                        continue
                    acc += src[yy * aw + xx]
                    n += 1
            out[y * aw + x] = acc / n
    return out


def _score(mean_ex, p95, ncc):
    return abs(mean_ex) + 3.0 * abs(ncc) + p95 / 20.0


def search_offsets(gray, w, h, alpha, aw, ah, ox, oy, r=SEARCH_OFFSET,
                   plane=None):
    """Best (dx, dy) in [-r, r]^2 by window NCC on a gray buffer.

    Returns (best_ox, best_oy, best_ncc). Pass a shared plane (fitted once
    over the whole searchable corner) to reuse one background fit.
    """
    best_ox, best_oy, best_ncc = ox, oy, None
    for dy in range(-r, r + 1):
        cy = oy + dy
        if cy < 0 or cy + ah > h:
            continue
        for dx in range(-r, r + 1):
            cx = ox + dx
            if cx < 0 or cx + aw > w:
                continue
            ncc, _ = window_metrics(gray, w, h, alpha, aw, ah, cx, cy,
                                    plane=plane)
            if best_ncc is None or ncc > best_ncc:
                best_ox, best_oy, best_ncc = cx, cy, ncc
    return best_ox, best_oy, best_ncc


def search_size(gray, w, h, src, aw, ah, ox, oy, plane=None):
    """Best (bw, bh) in aw+-1 x ah+-1 by window NCC, resampled from src.

    Returns (bw, bh, alpha, ncc).
    """
    best = (aw, ah, None, None)
    for dbw in range(-SEARCH_SIZE, SEARCH_SIZE + 1):
        for dbh in range(-SEARCH_SIZE, SEARCH_SIZE + 1):
            cw, chh = aw + dbw, ah + dbh
            if cw < 4 or chh < 2 or cw > w or chh > h:
                continue
            ca = scale_badge(src["alpha"], src["w"], src["h"], cw, chh)
            ncc, _ = window_metrics(gray, w, h, ca, cw, chh, ox, oy,
                                    plane=plane)
            if best[3] is None or ncc > best[3]:
                best = (cw, chh, ca, ncc)
    return best


def correct_alpha(px, w, h, ch, alpha, aw, ah, ox, oy, bg):
    """Iteratively self-calibrate the alpha map from the residual ghost.

    The remaining bright pattern after subtraction measures the alpha
    error: excess ~ (a_true - a_mine) * (255 - content). The content at
    each pixel is estimated by the current OUTPUT value (not the ring
    background, which underestimates the correction on bright content).
    A damped, 3x3 smoothed correction is applied per pixel and the best
    iteration (by ghost score) is kept. bg is the per-pixel background
    model (see bg_model); it is constant across iterations because the
    subtraction never touches the ring it was fitted on.

    Returns (best_alpha, best_metrics, iters).
    """
    best = None
    cur = bytearray(alpha)
    for it in range(CORRECT_ITERS + 1):
        out, _, _, _ = subtract_sprite(px, w, h, ch, cur, aw, ah, ox, oy)
        wg = _window_gray(out, w, ch, ox, oy, aw, ah)
        mean_ex, p95, ncc = _ghost_from_window(wg, cur, aw, ah, bg)
        sc = _score(mean_ex, p95, ncc)
        if best is None or sc < best[0]:
            best = (sc, bytearray(cur), (mean_ex, p95, ncc), it)
        if abs(mean_ex) < 0.8 and abs(ncc) < 0.12:
            break
        if it == CORRECT_ITERS:
            break
        # per-pixel alpha correction from the excess map
        corr = [0.0] * (aw * ah)
        for i in range(aw * ah):
            e = wg[i] - bg[i]
            denom = max(30.0, 255.0 - wg[i])
            corr[i] = e / denom
        corr = _blur3(corr, aw, ah)
        for i in range(aw * ah):
            na = cur[i] / 255.0 + CORRECT_DAMP * corr[i]
            na = 0.0 if na < 0 else (1.0 if na > 1 else na)
            cur[i] = min(254, int(round(na * 255)))
    return best[1], best[2], best[3]


def _hf_energy(buf, w, h, ch, x0, y0, x1, y1, excl=None):
    """Mean |v - box3(v)| over a rect (high-frequency energy, 0..255)."""
    ex0 = ey0 = ex1 = ey1 = -1
    if excl is not None:
        ex0, ey0, ex1, ey1 = excl
    g = []
    for y in range(y0, y1):
        row = []
        base = y * w * ch
        for x in range(x0, x1):
            if ex0 <= x < ex1 and ey0 <= y < ey1:
                row.append(None)
                continue
            o = base + x * ch
            row.append((buf[o] + buf[o + 1] + buf[o + 2]) // 3 if ch >= 3 else buf[o])
        g.append(row)
    hh = y1 - y0
    ww = x1 - x0
    if hh < 3 or ww < 3:
        return 0.0
    acc = 0.0
    n = 0
    for y in range(1, hh - 1):
        for x in range(1, ww - 1):
            if g[y][x] is None:
                continue
            if (g[y - 1][x - 1] is None or g[y - 1][x] is None or
                    g[y - 1][x + 1] is None or g[y][x - 1] is None or
                    g[y][x + 1] is None or g[y + 1][x - 1] is None or
                    g[y + 1][x] is None or g[y + 1][x + 1] is None):
                continue
            m = (g[y - 1][x - 1] + g[y - 1][x] + g[y - 1][x + 1] +
                 g[y][x - 1] + g[y][x] + g[y][x + 1] +
                 g[y + 1][x - 1] + g[y + 1][x] + g[y + 1][x + 1]) / 9.0
            acc += abs(g[y][x] - m)
            n += 1
    return acc / n if n else 0.0


def dent_check(out_px, w, h, ch, alpha, aw, ah, ox, oy):
    """Recovered-corner texture energy vs a surrounding ring.

    Returns (e_in, e_out, ok). ok is False when the recovered corner
    carries sprite-shaped energy far above its surroundings - the
    signature of a misaligned or phantom subtraction. Diagnostic only.
    """
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(w, ox + aw), min(h, oy + ah)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return 0.0, 0.0, True
    g = []
    for y in range(y0, y1):
        row = []
        base = y * w * ch
        for x in range(x0, x1):
            o = base + x * ch
            row.append((out_px[o] + out_px[o + 1] + out_px[o + 2]) // 3
                       if ch >= 3 else out_px[o])
        g.append(row)
    hh = y1 - y0
    ww = x1 - x0
    acc = 0.0
    n = 0
    for y in range(1, hh - 1):
        sy = y + (y0 - oy)
        for x in range(1, ww - 1):
            sx = x + (x0 - ox)
            if 0 <= sy < ah and 0 <= sx < aw and alpha[sy * aw + sx] < 77:
                continue
            m = (g[y - 1][x - 1] + g[y - 1][x] + g[y - 1][x + 1] +
                 g[y][x - 1] + g[y][x] + g[y][x + 1] +
                 g[y + 1][x - 1] + g[y + 1][x] + g[y + 1][x + 1]) / 9.0
            acc += abs(g[y][x] - m)
            n += 1
    e_in = acc / n if n else 0.0
    rx0, ry0 = max(0, ox - 8), max(0, oy - 8)
    rx1, ry1 = min(w, ox + aw + 8), min(h, oy + ah + 8)
    e_out = _hf_energy(out_px, w, h, ch, rx0, ry0, rx1, ry1,
                       excl=(x0, y0, x1, y1))
    ok = e_in <= DENT_RATIO * e_out + DENT_FLOOR
    return e_in, e_out, ok


def _evaluate(px, w, h, ch, alpha, aw, ah, ox, oy, plane=None, in_gray=None):
    """Subtract + measure. Returns a candidate dict.

    plane is the shared background model (fitted on the input's ring);
    in_gray is the input gray buffer, used only to fit a local model when
    no shared plane is given. The ring is outside the subtraction area,
    so input and output agree there and one model serves both.
    """
    out, changed, max_res, mean_res = subtract_sprite(
        px, w, h, ch, alpha, aw, ah, ox, oy)
    wg = _window_gray(out, w, ch, ox, oy, aw, ah)
    bg = bg_model(in_gray if in_gray is not None else out, w, h, ox, oy,
                  aw, ah, plane=plane)
    g_mean, g_p95, g_ncc = _ghost_from_window(wg, alpha, aw, ah, bg)
    return {"alpha": alpha, "aw": aw, "ah": ah, "ox": ox, "oy": oy,
            "out": out, "changed": changed, "max_res": max_res,
            "mean_res": mean_res, "g_mean": g_mean, "g_p95": g_p95,
            "g_ncc": g_ncc, "bg": bg,
            "score": _score(g_mean, g_p95, g_ncc)}


def _alpha_gradients(alpha, aw, ah):
    """Central-difference gradients of an alpha map (cached per size)."""
    gx = [0.0] * (aw * ah)
    gy = [0.0] * (aw * ah)
    for y in range(ah):
        for x in range(aw):
            i = y * aw + x
            gx[i] = (alpha[y * aw + min(aw - 1, x + 1)]
                     - alpha[y * aw + max(0, x - 1)])
            gy[i] = (alpha[min(ah - 1, y + 1) * aw + x]
                     - alpha[max(0, y - 1) * aw + x])
    return gx, gy


def verify_output(out_gray, w, h, alpha, aw, ah, ox, oy, src,
                  r=SEARCH_OFFSET, plane=None):
    """Search the OUTPUT for any remaining badge-shaped pattern.

    A residue from a misaligned or partial removal decorrelates from the
    sprite at the subtracted position but still matches it somewhere in
    the neighborhood. Returns (ncc, excess, dx, dy, grad_x, grad_y):
    the best pos-NCC match, and the strongest sprite-GRADIENT correlation
    among badge-like candidates (pos-NCC >= GRAD_MIN_POS).

    Why the gradient: a one-pixel geometry error subtracts the sprite one
    pixel off, so every stroke edge leaves a bright crescent on one side
    and a carved dent on the other - the residue IS the sprite's spatial
    derivative. It correlates weakly with the sprite itself (a perfect
    removal scores 0.24-0.32 there from stamp rounding noise alone, a
    1px error 0.34-0.36) but strongly with its gradient (perfect <= 0.20,
    1px error 0.42-0.79).
    """
    sizes = [(aw, ah, alpha)]
    # size variants only for resampled sprites: a calibrated sprite's size
    # is exact, so only the offset needs re-checking there
    if src is not None and (src["w"], src["h"]) != (aw, ah):
        for dbw in range(-SEARCH_SIZE, SEARCH_SIZE + 1):
            for dbh in range(-SEARCH_SIZE, SEARCH_SIZE + 1):
                cw, chh = aw + dbw, ah + dbh
                if cw < 4 or chh < 2 or cw > w or chh > h:
                    continue
                sizes.append((cw, chh,
                              scale_badge(src["alpha"], src["w"], src["h"],
                                          cw, chh)))
    best = None
    best_gx = best_gy = 0.0
    for (cw, chh, ca) in sizes:
        cgx, cgy = _alpha_gradients(ca, cw, chh)
        # constant sums of the gradient maps (offset-independent)
        sgx = sum(cgx)
        sggx = sum(v * v for v in cgx)
        sgy = sum(cgy)
        sggy = sum(v * v for v in cgy)
        for dy in range(-r, r + 1):
            cy = oy + dy
            if cy < 0 or cy + chh > h:
                continue
            for dx in range(-r, r + 1):
                cx = ox + dx
                if cx < 0 or cx + cw > w:
                    continue
                ncc, exc = window_metrics(out_gray, w, h, ca, cw, chh, cx,
                                          cy, plane=plane)
                if best is None or ncc > best[0]:
                    best = (ncc, exc, dx, dy)
                # gradient correlation only where the corner looks
                # badge-like at all - elsewhere the excess map is pure
                # content and its random correlations would fake a floor
                if ncc >= GRAD_MIN_POS:
                    ngx, ngy = _grad_ncc(out_gray, w, h, cx, cy, cw, chh,
                                         cgx, cgy, sgx, sggx, sgy, sggy,
                                         plane)
                    if abs(ngx) > abs(best_gx):
                        best_gx = ngx
                    if abs(ngy) > abs(best_gy):
                        best_gy = ngy
    return (best[0], best[1], best[2], best[3], best_gx, best_gy)


def _grad_ncc(gray, w, h, ox, oy, aw, ah, gx, gy, sgx, sggx, sgy, sggy,
              plane):
    """NCC of the signed window excess vs the sprite's x/y gradients.

    gray is a single-channel buffer; the excess is signed (carved dents
    are signal here, unlike the half-wave presence metric).
    """
    bg = bg_model(gray, w, h, ox, oy, aw, ah, plane=plane)
    n = aw * ah
    se = see = abgx = abgy = 0.0
    for y in range(ah):
        row = (oy + y) * w + ox
        for x in range(aw):
            i = y * aw + x
            e = gray[row + x] - bg[i]
            se += e
            see += e * e
            abgx += e * gx[i]
            abgy += e * gy[i]
    out = []
    for (sb, sbb, ab) in ((sgx, sggx, abgx), (sgy, sggy, abgy)):
        d = ((n * see - se * se) * (n * sbb - sb * sb)) ** 0.5
        out.append((n * ab - se * sb) / d if d > 1e-9 else 0.0)
    return out[0], out[1]

def remove_watermark(image_path, out_path, force_offset=None):
    """Remove the badge from image_path, writing the result to out_path.

    Returns a report dict. report["clean"] is True only when the delivered
    file provably carries no visible watermark (output-side verification).
    """
    w, h, ch, px, ancillary = read_png(image_path)
    registry = load_registry()
    law = registry["law"]
    report = {
        "input": image_path,
        "output": out_path,
        "size": [w, h],
        "law_base": law.get("base", 1024),
        "badge_detected": False,
        "presence": None,
        "sprite_offset": None,
        "changed_pixels": 0,
        "max_residual": None,
        "mean_residual": None,
        "ghost": None,
        "tier": "none",
        "method": None,
        "clean": False,
        "notes": [],
    }

    def finish(out_px, method, badge_detected, tier, clean, note=None):
        write_png(out_path, w, h, ch, out_px, ancillary)
        report["method"] = method
        report["badge_detected"] = badge_detected
        report["tier"] = tier
        report["clean"] = clean
        if note:
            report["notes"].append(note)
        return report

    def copy_through(method, note):
        return finish(px, method, False, "none", True, note)

    def report_candidate(cand, method):
        report["sprite_offset"] = [cand["ox"], cand["oy"]]
        report["changed_pixels"] = cand["changed"]
        report["max_residual"] = round(cand["max_res"], 3)
        report["mean_residual"] = round(cand["mean_res"], 3)
        report["ghost"] = {"mean": round(cand["g_mean"], 3),
                           "p95": round(cand["g_p95"], 3),
                           "ncc": round(cand["g_ncc"], 4)}

    def verdict(cand, method, src):
        report_candidate(cand, method)
        e_in, e_out, ok = dent_check(cand["out"], w, h, ch, cand["alpha"],
                                     cand["aw"], cand["ah"], cand["ox"],
                                     cand["oy"])
        report["dent_check"] = {"hf_in": round(e_in, 3),
                                "hf_out": round(e_out, 3), "ok": ok}
        # ---- output-side verification: hunt for ANY badge-shaped pattern
        # left in the output, over the neighborhood of the subtraction.
        # This is the cleanliness proof; the ghost NCC at the subtracted
        # position alone cannot clear a removal (a 1px misalignment
        # decorrelates the residue and still looks like a badge).
        out_gray = bytearray(_gray(px, w, h, ch))
        wg = _window_gray(cand["out"], w, ch, cand["ox"], cand["oy"],
                          cand["aw"], cand["ah"])
        for y in range(cand["ah"]):
            row = (cand["oy"] + y) * w + cand["ox"]
            for x in range(cand["aw"]):
                out_gray[row + x] = wg[y * cand["aw"] + x]
        v = verify_output(out_gray, w, h, cand["alpha"], cand["aw"],
                          cand["ah"], cand["ox"], cand["oy"], src,
                          plane=plane)
        report["verify"] = {"ncc": round(v[0], 4), "excess": round(v[1], 3),
                            "dx": v[2], "dy": v[3],
                            "grad_x": round(v[4], 4),
                            "grad_y": round(v[5], 4)}
        # NOTE: the dent check is diagnostic only (see module docstring).
        # Three independent gates clear a removal (measured separations
        # in the module docstring):
        #   pos-NCC  - no badge-shaped brightness aligned with the sprite
        #   grad-NCC - no sprite-gradient signature (1px geometry errors)
        #   carved   - ghost NCC >= -0.45 (no badge-shaped dark dent)
        # The stroke excess is NOT a gate: the content under the badge is
        # unknowable and bright content there fakes a large excess.
        carved = cand["g_ncc"] < -VERIFY_NCC_VISUAL
        grad_ok = abs(v[4]) <= VERIFY_GRAD and abs(v[5]) <= VERIFY_GRAD
        if not carved and v[0] <= VERIFY_NCC_STRICT and grad_ok:
            return finish(cand["out"], method, True, "strict", True)
        if not carved and v[0] <= VERIFY_NCC_VISUAL and grad_ok:
            return finish(cand["out"], method, True, "visual", True,
                          "visual tier: verify NCC %.3f, grad (%.3f, %.3f)"
                          % (v[0], v[4], v[5]))
        # a badge WAS found and subtracted, but the output still carries a
        # badge-shaped pattern (or a carved dent): deliver the ORIGINAL
        # unmodified and report clean=false - never pass a watermarked
        # image off as a cleaned one.
        why = ("carved dent (ghost NCC %.3f)" % cand["g_ncc"] if carved else
               ("badge-shaped residue (NCC %.3f)" % v[0]
                if v[0] > VERIFY_NCC_VISUAL else
                "misaligned residue (grad %.3f, %.3f)" % (v[4], v[5])))
        return finish(px, "unverified", True, "unverified", False,
                      "badge pattern found but removal could not be "
                      "verified (%s); delivered unmodified - the image "
                      "may still carry a visible watermark" % why)

    # ---- forced offset escape hatch ----
    if force_offset is not None:
        ox, oy = force_offset
        alpha, aw, ah, _ = base_sprite_for(w, h, registry)
        src = source_base_for(w, h, registry)
        gray = _gray(px, w, h, ch)
        plane = fit_plane(gray, w, h, max(0, ox - 16), max(0, oy - 16),
                          min(w, ox + aw + 16), min(h, oy + ah + 16),
                          excl=(ox, oy, ox + aw, oy + ah))
        best = _evaluate(px, w, h, ch, alpha, aw, ah, ox, oy, plane, gray)
        if abs(best["g_ncc"]) > GHOST_CORRECT_NCC:
            calpha, metrics, iters = correct_alpha(
                px, w, h, ch, best["alpha"], best["aw"], best["ah"],
                best["ox"], best["oy"], best["bg"])
            cand = _evaluate(px, w, h, ch, calpha, best["aw"], best["ah"],
                             best["ox"], best["oy"], plane, gray)
            if cand["score"] < best["score"]:
                best = cand
        return verdict(best, "forced-offset", src)

    # ---- adaptive path (handles every size, including the reference size:
    # ---- the calibrated base sprite is used as-is when the short side
    # ---- matches a calibrated scale) ----
    s, bw, bh, ox, oy = predict_geometry(w, h, law)
    alpha, aw, ah, exact = base_sprite_for(w, h, registry)
    src = source_base_for(w, h, registry)
    report["scale"] = round(s, 4)
    report["badge_size"] = [bw, bh]
    report["sprite_exact"] = exact
    gray = _gray(px, w, h, ch)
    method = "sprite-subtraction" + ("" if exact else "+resampled")

    # ---- shared local background model for the whole searchable corner.
    # The geometry search and the output-side verify examine every window
    # within +/- (SEARCH_OFFSET + SEARCH_SIZE) of the predicted one, so the
    # plane is fitted on the ring AROUND that whole union, excluding the
    # union itself - every candidate window is then surrounded by clean,
    # badge-free pixels. One fit serves presence, search, ghost and verify.
    pad = 16
    ux0 = max(0, ox - SEARCH_OFFSET - SEARCH_SIZE)
    uy0 = max(0, oy - SEARCH_OFFSET - SEARCH_SIZE)
    ux1 = min(w, ox + aw + SEARCH_OFFSET + SEARCH_SIZE)
    uy1 = min(h, oy + ah + SEARCH_OFFSET + SEARCH_SIZE)
    plane = fit_plane(gray, w, h, max(0, ux0 - pad), max(0, uy0 - pad),
                      min(w, ux1 + pad), min(h, uy1 + pad),
                      excl=(ux0, uy0, ux1, uy1))

    # ---- stage 1: geometry search on the INPUT (non-exact sprites only).
    # The law predicts the badge to within a pixel; at s=1.25 the true
    # badge is 140x64 at (1128,1204) while the law says 139x64 at
    # (1129,1204). Subtracting one pixel off leaves a visible residue
    # whose pattern decorrelates from the sprite, so the geometry is
    # refined locally before subtracting. Calibrated sizes skip this:
    # their sprites are exact and validated.
    searched = False
    if not exact:
        p_ncc0, _ = window_metrics(gray, w, h, alpha, aw, ah, ox, oy,
                                   plane=plane)
        nox, noy, n_ncc = search_offsets(gray, w, h, alpha, aw, ah, ox, oy,
                                         plane=plane)
        cw, chh, ca, s_ncc = search_size(gray, w, h, src, aw, ah, nox, noy,
                                         plane=plane)
        nox, noy, n_ncc = search_offsets(gray, w, h, ca, cw, chh, nox, noy,
                                         plane=plane)
        if s_ncc > p_ncc0 + 0.005:
            ox, oy, aw, ah, alpha = nox, noy, cw, chh, ca
            searched = True
            method += "+searched"
    report["searched"] = searched

    # ---- stage 2: presence gate: never subtract without badge evidence
    p_ncc, p_excess = window_metrics(gray, w, h, alpha, aw, ah, ox, oy,
                                     plane=plane)
    report["presence"] = {"excess_ncc": round(p_ncc, 3),
                          "stroke_excess": round(p_excess, 2)}
    if p_ncc < PRESENCE_NCC or p_excess < PRESENCE_EXCESS:
        rx0, ry0 = max(0, ox - 8), max(0, oy - 8)
        rx1, ry1 = min(w, ox + bw + 8), min(h, oy + bh + 8)
        rw = rx1 - rx0
        e_corner = _hf_energy(px, w, h, ch, rx0, ry0, rx1, ry1)
        cx0 = max(0, ox - rw - 40)
        e_ctrl = _hf_energy(px, w, h, ch, cx0, ry0,
                            min(w, cx0 + rw), ry1)
        if e_corner > 1.8 * e_ctrl and e_corner > 6.0:
            return copy_through(
                "unverified",
                "no badge-shaped pattern at the predicted position, but "
                "the corner carries unusual high-frequency content; "
                "delivered unmodified")
        return copy_through(
            "no-badge-detected",
            "no badge-shaped bright pattern in the corner; delivered "
            "unmodified (already free of a visible watermark)")

    report["badge_detected"] = True

    # ---- stage 3: subtract at the final geometry
    best = _evaluate(px, w, h, ch, alpha, aw, ah, ox, oy, plane, gray)

    # ---- stage 4: ghost correction (only when a strong ghost survives)
    if abs(best["g_ncc"]) > GHOST_CORRECT_NCC:
        calpha, metrics, iters = correct_alpha(
            px, w, h, ch, best["alpha"], best["aw"], best["ah"],
            best["ox"], best["oy"], best["bg"])
        cand = _evaluate(px, w, h, ch, calpha, best["aw"], best["ah"],
                         best["ox"], best["oy"], plane, gray)
        if cand["score"] < best["score"]:
            best = cand
            method += "+corrected(%d)" % iters

    # ---- stages 5-7: verify + verdict
    return verdict(best, method, src)


def _cli():
    """Standalone entry: strip the badge from one PNG, print a JSON report.

    Pure local computation - no network, no credits. Exit 0 means the
    removal ran (check report["clean"] for the guarantee); exit 1 on bad
    input/IO.
    """
    ap = argparse.ArgumentParser(
        description="Remove the server-side watermark badge from a PNG "
                    "(local sprite subtraction; no network, no credits).")
    ap.add_argument("--input", required=True, help="watermarked input PNG")
    ap.add_argument("--output", default="",
                    help="output path (default: <input>_clean.png)")
    ap.add_argument("--force-offset", default="",
                    help="force sprite offset 'X,Y' (skips size check, "
                         "presence gate and detection)")
    args = ap.parse_args()

    if not args.output:
        stem, ext = os.path.splitext(args.input)
        args.output = stem + "_clean" + (ext or ".png")

    force = None
    if args.force_offset:
        try:
            fx, fy = args.force_offset.split(",")
            force = (int(fx.strip()), int(fy.strip()))
        except ValueError:
            print(json.dumps({"ok": False, "error": "BAD_OFFSET",
                              "message": "--force-offset must be 'X,Y'"},
                             ensure_ascii=False, indent=2))
            sys.exit(1)

    try:
        report = remove_watermark(args.input, args.output, force_offset=force)
    except ValueError as e:
        print(json.dumps({"ok": False, "error": "NOT_PNG_OR_UNSUPPORTED",
                          "message": str(e)},
                         ensure_ascii=False, indent=2))
        sys.exit(1)
    except OSError as e:
        print(json.dumps({"ok": False, "error": "IO_ERROR",
                          "message": str(e)},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    result = {"ok": True}
    result.update(report)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    _cli()
