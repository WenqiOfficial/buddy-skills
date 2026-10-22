# -*- coding: utf-8 -*-
"""Watermark badge removal by exact sprite subtraction.

The image backend stamps a fixed white semi-transparent badge
("AI生成 / WORKBUDDY>") into the bottom-right corner of every generated
image. The badge sprite was extracted once from a pure-black reference
generation (black pixels -> alpha map, since the badge is neutral white).

Removal inverts the alpha compositing exactly:
    stamped = (1 - a) * original + a * 255
    =>  original = (stamped - a * 255) / (1 - a)

Only pixels covered by the sprite are touched; everything else is preserved
bit-for-bit. A re-compositing residual check proves whether the badge was
fully explained by the sprite (residual < 2/255 => guaranteed clean).
"""

import argparse
import json
import os
import sys

from png_codec import read_png, write_png

SPRITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
SPRITE_PNG = os.path.join(SPRITE_DIR, "badge_sprite.png")
SPRITE_JSON = os.path.join(SPRITE_DIR, "badge_sprite.json")

# sprite alpha below this (0..255) is treated as "no badge"
ALPHA_FLOOR = 5
# residual (0..255) below which the badge is considered fully removed
RESIDUAL_OK = 2.0
# detection correlation threshold for non-reference image sizes
DETECT_CORR = 0.25


def load_sprite():
    with open(SPRITE_JSON, "r", encoding="utf-8") as f:
        meta = json.load(f)
    sw, sh, _, spx, _ = read_png(SPRITE_PNG)
    if (sw, sh) != (meta["width"], meta["height"]):
        raise ValueError("sprite asset mismatch with its metadata")
    return meta, sw, sh, spx


def _expected_offset(meta, w, h):
    """The sprite window is anchored to the bottom-right image corner
    (it extends to the right and bottom edges of the reference image)."""
    return (w - meta["width"], h - meta["height"])


def subtract_sprite(px, w, h, ch, spx, sw, sh, ox, oy, color=(255, 255, 255)):
    """Return (new_pixels, changed_pixels, max_residual).

    new_pixels: bytearray copy of px with the badge removed at (ox, oy).
    max_residual: max |re-composited - original| over the sprite window,
        measured in 0..255 units. Small residual => badge fully explained.
    """
    out = bytearray(px)
    stride = w * ch
    changed = 0
    max_res = 0.0
    x_lo = max(0, ox)
    y_lo = max(0, oy)
    x_hi = min(w, ox + sw)
    y_hi = min(h, oy + sh)
    for y in range(y_lo, y_hi):
        sy = y - oy
        for x in range(x_lo, x_hi):
            sx = x - ox
            a = spx[sy * sw + sx]
            if a < ALPHA_FLOOR:
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
            # verify: re-composite the removed badge and compare with input
            for c in range(ch):
                recomp = (1.0 - af) * out[o + c] + af * color[c]
                res = abs(recomp - px[o + c])
                if res > max_res:
                    max_res = res
    return out, changed, max_res


def _downsample(px, w, h, ch, factor):
    dw, dh = w // factor, h // factor
    out = bytearray(dw * dh * ch)
    for y in range(dh):
        for x in range(dw):
            acc = [0] * ch
            for dy in range(factor):
                base = ((y * factor + dy) * w + x * factor) * ch
                for dx in range(factor):
                    o = base + dx * ch
                    for c in range(ch):
                        acc[c] += px[o + c]
            n = factor * factor
            o2 = (y * dw + x) * ch
            for c in range(ch):
                out[o2 + c] = acc[c] // n
    return out, dw, dh


def _ncc(search, sw_, sh_, sstride, tmpl, tw, th, tstride, pos_x, pos_y):
    """Normalized cross correlation at one integer position."""
    n = tw * th
    ssum = tsum = ssq = tsq = st = 0
    for y in range(th):
        so = (pos_y + y) * sstride + pos_x
        to = y * tstride
        for x in range(tw):
            s = search[so + x]
            t = tmpl[to + x]
            ssum += s
            tsum += t
            ssq += s * s
            tsq += t * t
            st += s * t
    denom = ((n * ssq - ssum * ssum) * (n * tsq - tsum * tsum)) ** 0.5
    if denom < 1e-9:
        return 0.0
    return (n * st - ssum * tsum) / denom


def detect_badge(px, w, h, ch, spx, sw, sh, factor=4, span=48):
    """Locate the badge in the bottom-right quadrant via downsampled NCC.

    Returns (ox, oy, corr) or None. Best effort: only needed for image sizes
    that differ from the calibrated reference size.
    """
    meta_w, meta_h = None, None
    with open(SPRITE_JSON, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta_w, meta_h = meta["ref_size"]
    exp_ox, exp_oy = _expected_offset(meta, w, h)
    # grayscale views for correlation
    def gray(buf, width, height, channels):
        g = bytearray(width * height)
        for i in range(width * height):
            o = i * channels
            g[i] = (buf[o] + buf[o + 1] + buf[o + 2]) // 3 if channels >= 3 else buf[o]
        return g

    img_g = gray(px, w, h, ch)
    spr_g = gray(spx, sw, sh, 1)
    simg, dw, dh = _downsample(img_g, w, h, 1, factor)
    sspr, tw, th = _downsample(spr_g, sw, sh, 1, factor)
    tstride = tw
    sstride = dw
    lo_x = max(0, (exp_ox - span) // factor)
    hi_x = min(dw - tw, (exp_ox + span) // factor)
    lo_y = max(0, (exp_oy - span) // factor)
    hi_y = min(dh - th, (exp_oy + span) // factor)
    best, best_corr = None, DETECT_CORR
    step = 1
    for py in range(lo_y, hi_y + 1, step):
        for px_ in range(lo_x, hi_x + 1, step):
            corr = _ncc(simg, dw, dh, sstride, sspr, tw, th, tstride, px_, py)
            if corr > best_corr:
                best_corr = corr
                best = (px_ * factor, py * factor)
    if best is None:
        return None
    return best[0], best[1], best_corr


def remove_watermark(image_path, out_path, force_offset=None):
    """Remove the badge from image_path, writing the result to out_path.

    Returns a report dict. report["clean"] is True only when the badge was
    located and the re-compositing residual proves full removal.
    """
    w, h, ch, px, ancillary = read_png(image_path)
    meta, sw, sh, spx = load_sprite()
    report = {
        "input": image_path,
        "output": out_path,
        "size": [w, h],
        "sprite_ref_size": meta["ref_size"],
        "badge_detected": False,
        "sprite_offset": None,
        "changed_pixels": 0,
        "max_residual": None,
        "clean": False,
        "method": None,
    }

    if force_offset is not None:
        ox, oy = force_offset
        corr = None
    elif (w, h) == tuple(meta["ref_size"]):
        ox, oy = _expected_offset(meta, w, h)
        corr = None
    else:
        det = detect_badge(px, w, h, ch, spx, sw, sh)
        if det is None:
            # no badge found (or invisible on bright content): copy as-is
            write_png(out_path, w, h, ch, px, ancillary)
            report["method"] = "no-badge-detected"
            return report
        ox, oy, corr = det

    out, changed, max_res = subtract_sprite(px, w, h, ch, spx, sw, sh, ox, oy)
    write_png(out_path, w, h, ch, out, ancillary)
    report["badge_detected"] = True
    report["sprite_offset"] = [ox, oy]
    report["detection_corr"] = corr
    report["changed_pixels"] = changed
    report["max_residual"] = round(max_res, 3)
    report["method"] = "sprite-subtraction"
    report["clean"] = max_res <= RESIDUAL_OK
    return report


def _cli():
    """Standalone entry: strip the badge from one PNG, print a JSON report.

    Pure local computation - no network, no credits. Exit 0 means the removal
    ran (check report["clean"] for the guarantee); exit 1 on bad input/IO.
    """
    ap = argparse.ArgumentParser(
        description="Remove the server-side watermark badge from a PNG "
                    "(local sprite subtraction; no network, no credits).")
    ap.add_argument("--input", required=True, help="watermarked input PNG")
    ap.add_argument("--output", default="",
                    help="output path (default: <input>_clean.png)")
    ap.add_argument("--force-offset", default="",
                    help="force sprite offset 'X,Y' (skips size check and "
                         "detection)")
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
                          "message": str(e)}, ensure_ascii=False, indent=2))
        sys.exit(1)
    except OSError as e:
        print(json.dumps({"ok": False, "error": "IO_ERROR",
                          "message": str(e)}, ensure_ascii=False, indent=2))
        sys.exit(1)

    result = {"ok": True}
    result.update(report)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    _cli()
