# -*- coding: utf-8 -*-
"""Selftest: verify the size-adaptive badge removal without network or credits.

Stamps the real badge sprite onto synthetic gradient images at several
sizes (using the geometric law and the calibrated/resampled sprites,
exactly the server-side compositing), then runs the standalone CLI and
checks that the recovered images match the originals.

Also covers: a badge-free image (must be delivered unmodified and clean),
and non-PNG input rejection.

Usage: python selftest.py
Exit code 0 = all checks passed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from png_codec import read_png, write_png  # noqa: E402
import badge_removal as br  # noqa: E402

CLI = os.path.join(ROOT, "scripts", "badge_removal.py")

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" - " + detail) if detail else ""))
    if not cond:
        failures.append(name)


def make_gradient(w, h):
    px = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 3
            px[o] = min(255, (x * 255) // max(1, w))
            px[o + 1] = min(255, (y * 255) // max(1, h))
            px[o + 2] = min(255, ((x + y) * 255) // max(1, 2 * w))
    return px


def stamp(px, w, h, alpha, aw, ah, ox, oy):
    stamped = bytearray(px)
    n = 0
    for y in range(ah):
        for x in range(aw):
            a = alpha[y * aw + x]
            if a < 5:
                continue
            af = a / 255.0
            o = ((oy + y) * w + (ox + x)) * 3
            for c in range(3):
                stamped[o + c] = int(round((1 - af) * px[o + c] + af * 255))
            n += 1
    return stamped, n


def run_case(w, h):
    reg = br.load_registry()
    law = reg["law"]
    s, bw, bh, ox, oy = br.predict_geometry(w, h, law)
    alpha, aw, ah, exact = br.base_sprite_for(w, h, reg)
    px = make_gradient(w, h)
    stamped, n_stamped = stamp(px, w, h, alpha, aw, ah, ox, oy)

    tmp = tempfile.mkdtemp(prefix="clean-image-gen-selftest-")
    try:
        raw = os.path.join(tmp, "stamped.png")
        write_png(raw, w, h, 3, bytes(stamped))
        r = subprocess.run([sys.executable, CLI, "--input", raw],
                           capture_output=True, text=True)
        ok_run = r.returncode == 0
        check("%dx%d: cli exit code 0" % (w, h), ok_run, r.stderr.strip()[:200])
        if not ok_run:
            return
        report = json.loads(r.stdout)
        check("%dx%d: report ok" % (w, h), report.get("ok") is True)
        check("%dx%d: badge_detected" % (w, h),
              report.get("badge_detected") is True)
        check("%dx%d: clean == true" % (w, h), report.get("clean") is True,
              "tier=%s ghost=%s residual=%s" % (report.get("tier"),
                                                report.get("ghost"),
                                                report.get("max_residual")))
        check("%dx%d: strict tier" % (w, h), report.get("tier") == "strict",
              "tier=%s" % report.get("tier"))
        check("%dx%d: max_residual <= 2" % (w, h),
              (report.get("max_residual") or 99) <= 2.0,
              "max_residual=%s" % report.get("max_residual"))

        clean = os.path.join(tmp, "stamped_clean.png")
        if os.path.exists(clean):
            _, _, _, px2, _ = read_png(clean)
            diff = max(abs(px2[i] - px[i]) for i in range(len(px)))
            check("%dx%d: recovered ~= original (rounding only)" % (w, h),
                  diff <= 1, "max abs diff = %d" % diff)
            outside = sum(
                1 for i in range(len(px))
                if not (ox <= (i // 3) % w < ox + aw
                        and oy <= (i // 3) // w < oy + ah)
                and px2[i] != px[i])
            check("%dx%d: zero changed pixels outside sprite window" % (w, h),
                  outside == 0, "changed=%d" % outside)
        print("    (stamped %d badge px, sprite %s, offset (%d,%d), s=%.4f)"
              % (n_stamped, "exact" if exact else "resampled", ox, oy, s))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_no_badge_case():
    """A clean gradient must be delivered unmodified and reported clean."""
    w, h = 1024, 1024
    px = make_gradient(w, h)
    tmp = tempfile.mkdtemp(prefix="clean-image-gen-selftest-")
    try:
        raw = os.path.join(tmp, "plain.png")
        write_png(raw, w, h, 3, bytes(px))
        r = subprocess.run([sys.executable, CLI, "--input", raw],
                           capture_output=True, text=True)
        report = json.loads(r.stdout) if r.returncode == 0 else {}
        check("no-badge: clean == true", report.get("clean") is True,
              "method=%s" % report.get("method"))
        check("no-badge: method is no-badge-detected",
              report.get("method") == "no-badge-detected",
              "method=%s" % report.get("method"))
        clean = os.path.join(tmp, "plain_clean.png")
        if os.path.exists(clean):
            _, _, _, px2, _ = read_png(clean)
            check("no-badge: output identical to input", px2 == px)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    reg = br.load_registry()
    print("law: %s" % json.dumps(reg["law"]))
    print("calibrated bases: %s" % ", ".join(
        "min=%d (%dx%d)" % (b["min"], b["w"], b["h"])
        for b in reg["bases"]))
    print()

    # calibrated scales + resampled scales + portrait orientations
    for (w, h) in [(1024, 1024), (768, 768), (1792, 768), (768, 1024),
                   (1024, 1536), (1536, 1024), (1280, 1280), (2048, 2048),
                   (512, 512)]:
        run_case(w, h)
    run_no_badge_case()

    # non-PNG input rejection
    tmp = tempfile.mkdtemp(prefix="clean-image-gen-selftest-")
    try:
        bad = os.path.join(tmp, "bad.txt")
        with open(bad, "w") as f:
            f.write("not a png")
        r2 = subprocess.run([sys.executable, CLI, "--input", bad],
                            capture_output=True, text=True)
        check("non-PNG input rejected",
              r2.returncode == 1 and
              json.loads(r2.stdout).get("error") == "NOT_PNG_OR_UNSUPPORTED")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print("SELFTEST FAILED: %s" % ", ".join(failures))
        sys.exit(1)
    print("SELFTEST PASSED")


if __name__ == "__main__":
    main()
