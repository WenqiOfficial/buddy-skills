# -*- coding: utf-8 -*-
"""Selftest: verify the badge-removal math without network or credits.

Stamps the real badge sprite onto a synthetic gradient image (exactly the
server-side compositing), then runs the standalone CLI and checks that the
recovered image matches the original.

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

CLI = os.path.join(ROOT, "scripts", "badge_removal.py")
ASSETS = os.path.join(ROOT, "assets")

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" - " + detail) if detail else ""))
    if not cond:
        failures.append(name)


def main():
    meta = json.load(open(os.path.join(ASSETS, "badge_sprite.json"), encoding="utf-8"))
    sw, sh, _, spx, _ = read_png(os.path.join(ASSETS, "badge_sprite.png"))
    ox, oy = meta["offset_x"], meta["offset_y"]
    print("sprite: %dx%d ref=%s offset=(%d,%d)" % (sw, sh, meta["ref_size"], ox, oy))

    W = H = 1024
    px = bytearray(W * H * 3)
    for y in range(H):
        for x in range(W):
            o = (y * W + x) * 3
            px[o] = (x * 255) // W
            px[o + 1] = (y * 255) // H
            px[o + 2] = ((x + y) * 255) // (2 * W)

    stamped = bytearray(px)
    n_stamped = 0
    for sy in range(sh):
        for sx in range(sw):
            a = spx[sy * sw + sx]
            if a < 5:
                continue
            af = a / 255.0
            o = ((oy + sy) * W + (ox + sx)) * 3
            for c in range(3):
                stamped[o + c] = int(round((1 - af) * px[o + c] + af * 255))
            n_stamped += 1
    print("stamped badge pixels: %d" % n_stamped)

    tmp = tempfile.mkdtemp(prefix="clean-image-gen-selftest-")
    try:
        raw = os.path.join(tmp, "stamped.png")
        write_png(raw, W, H, 3, bytes(stamped))

        r = subprocess.run([sys.executable, CLI, "--input", raw],
                           capture_output=True, text=True)
        check("cli exit code 0", r.returncode == 0, r.stderr.strip())
        try:
            report = json.loads(r.stdout)
        except ValueError:
            check("cli prints JSON report", False, r.stdout[:200])
            return
        check("report ok", report.get("ok") is True)
        check("badge_detected", report.get("badge_detected") is True)
        check("clean == true", report.get("clean") is True,
              "max_residual=%s (threshold 2)" % report.get("max_residual"))

        clean = os.path.join(tmp, "stamped_clean.png")
        check("default output naming", os.path.exists(clean))
        if os.path.exists(clean):
            _, _, _, px2, _ = read_png(clean)
            diff = max(abs(px2[i] - px[i]) for i in range(len(px)))
            check("recovered image ~= original (rounding only)", diff <= 1,
                  "max abs diff = %d" % diff)
            outside = sum(
                1 for i in range(len(px))
                if not (ox <= (i // 3) % W < ox + sw and oy <= (i // 3) // W < oy + sh)
                and px2[i] != px[i])
            check("zero changed pixels outside sprite window", outside == 0,
                  "changed=%d" % outside)

        bad = os.path.join(tmp, "bad.txt")
        with open(bad, "w") as f:
            f.write("not a png")
        r2 = subprocess.run([sys.executable, CLI, "--input", bad],
                            capture_output=True, text=True)
        check("non-PNG input rejected", r2.returncode == 1 and
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
