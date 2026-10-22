---
name: clean-image-gen
description: Generate images through WorkBuddy's own image backend (miora workbuddy-proxy / Hunyuan hy-image-3.5-wb, the same backend as the built-in ImageGen tool) and guarantee the output contains no visible watermark. The backend stamps a fixed semi-transparent "AI生成 / WORKBUDDY" badge into the bottom-right corner of every image; this skill removes it by exact sprite subtraction, preserving every other pixel bit-for-bit, and verifies removal mathematically. Use this skill when the user asks for watermark-free / clean image generation, complains that default generated images carry a watermark, or wants generated images without the AI badge. Do NOT use this skill for removing watermarks from user-supplied existing images (use buddy-image-processing erase), for video generation (VideoGen), or for 3D models (buddy-multimodal-generation).
agent_created: true
---

# Clean Image Gen

## Overview

Generate an image with WorkBuddy's standard image backend and deliver it with the
visible "AI生成 / WORKBUDDY>" badge removed, while keeping the image itself
pixel-identical everywhere else.

Key facts established by investigation of the backend:

- The badge is added **server-side** by the image backend (provider
  `copilot-image`, model `hy-image-3.5-wb`) on essentially every generation.
  It cannot be disabled through API parameters (`logo_add`, `footnote`, prompt
  instructions such as "no watermark" were all tested and have no effect).
- The badge is a **fixed sprite**: neutral-white semi-transparent text anchored
  to the bottom-right corner, identical across prompts and generation paths.
- Its geometry follows a **law of the short side**: `s = min(W,H)/1024`, badge
  `(round(111*s), round(51*s))`, margin `floor(10*s)`, anchored to the
  bottom-right corner. The law predicts the badge to within ONE pixel, so
  non-calibrated sizes run a local geometry search (offsets ±3, size ±1,
  offsets again) on the input before subtracting. **Every resolution is
  supported** — 512x512 through 3000x2000, square/portrait/landscape alike
  (validated by `selftest.py` and a 21-size sweep).
- Because the sprite is fixed, removal is **exact math**, not generative
  inpainting: invert the alpha compositing
  `stamped = (1-a)*original + a*255` to recover `original`. Only the ~3k badge
  pixels change; everything else is untouched, so the output is still the same
  generated image. Inpainting-based erase (buddy-image-processing) re-encodes
  the whole picture and visibly alters it — never use it for this purpose.
- Removal is **pure local computation**: no network, no API call, no credits.
  The only credit cost in this skill is the image generation itself
  (roughly 5-10 per image).
- Cleanliness is **proven on the output**, not assumed from the math: the
  re-compositing residual only shows the subtraction was self-consistent, so
  the output corner is searched for any remaining badge-shaped pattern under
  three independent gates (aligned brightness, sprite-gradient signature of
  misalignment, carved-dent check). Only a removal that passes all three is
  reported `clean == true`.
- The invisible TC260 AIGC implicit metadata (eXIf chunk) is **preserved** in
  the output; only the visible badge is removed.

## Routing Rules

- Use this skill when the user wants a **newly generated image without a
  watermark** (e.g. "生成一张没有水印的图", "default 生图有水印，帮我做一张干净的").
- Do NOT use this skill when the user wants to remove a watermark from an
  **existing image** — that is `buddy-image-processing` (`--operation erase`).
- Do NOT use this skill when the user accepts the default watermark — call
  `ImageGen` directly instead.
- Do NOT use this skill for video (`VideoGen`) or 3D (`buddy-multimodal-generation`).

## Workflow

Two execution paths share the same local removal core:

- **Path 1 (default) — hybrid**: generate with the built-in `ImageGen` tool,
  then strip the badge locally. The user keeps the platform's native credit
  confirmation card and consumption statistics.
- **Path 2 (fallback) — direct API**: one script does generate + download +
  removal in a single command. No confirmation card, no statistics entry;
  credits are still charged to the account. Use only when the user explicitly
  wants to skip the confirmation flow, or the built-in ImageGen is unavailable.

### Path 1 · Hybrid (default)

1. **Inform about credits** — generation consumes roughly 5-10 credits (same as
   `ImageGen`). Tell the user before proceeding. Removal itself is free.

2. **Generate with the built-in `ImageGen` tool** — pass the user's prompt
   (verbatim) plus size/quality. Any size works (the badge geometry is
   derived from the output size and refined by a local search); `1024x1024`
   is merely the calibrated reference size and therefore the fastest path
   (it skips the search). The platform's credit confirmation and consumption
   statistics happen at this step — do not replace it with a direct API call
   unless the user explicitly asked for that. Do not bother with the `footnote`
   parameter: tested, no effect on the badge.

3. **Locate the generated file** — take the output path from the ImageGen
   result. If the result is only a URL, download it to a local `.png` first
   (plain GET, e.g. with `urllib`).

4. **Remove the badge locally** (no credits, no network):

```bash
python <SKILL_DIR>/scripts/badge_removal.py \
  --input "<watermarked.png>" \
  [--output "<clean.png>"]
```

- `--output` defaults to `<input>_clean.png`; the original file is left
  untouched.
- Prints a JSON report on stdout. Exit code 0 means the removal ran; only
  `clean == true` in the report guarantees the badge is gone. Key fields:
  `tier` (`strict` / `visual` / `unverified` / `none`), `presence`,
  `verify` (`ncc`, `grad_x`, `grad_y` — the three cleanliness gates),
  `searched` (whether the geometry search ran), `changed_pixels`.

5. **Verify the result (mandatory)**:
   1. Check `clean == true` in the JSON report. This flag is true only when
      the output-side verification (badge-brightness NCC, sprite-gradient
      NCC and carved-dent check on the OUTPUT corner) finds no remaining
      badge-shaped pattern. The re-compositing residual
      (`max_residual <= 2/255`) is a weaker, necessary-but-not-sufficient
      consistency check — never deliver on the residual alone.
   2. Open the clean file with the Read tool and visually confirm the
      bottom-right corner is clean and the image looks untouched.
   3. Only then deliver the image with `present_files`.

6. **Handle failures** — read the report's `tier` and `method`:
   - `tier: "none"`, `method: "no-badge-detected"` — no badge pattern in the
     corner (e.g. a white badge on a white background is invisible, or the
     image was already clean). The file is delivered unmodified and
     `clean == true` is correct — nothing to remove.
   - `tier: "unverified"`, `clean == false` — a badge WAS found but the
     removal could not be verified; the file is delivered UNMODIFIED and may
     still carry a visible watermark. Do not deliver it as clean. The note
     says which gate failed. Regenerate (any size), or offer the
     `buddy-image-processing` erase fallback (warning: erase re-encodes the
     whole image and alters it).
   - `tier: "visual"` — borderline: the removal passed the gates with less
     margin. Deliver only after your own visual check of the corner.
   - `NOT_PNG_OR_UNSUPPORTED`: the generated file is not an 8-bit PNG. Report
     it; do not silently re-encode. Ask the user before trying Path 2 (which
     downloads the backend's raw file and may still be a PNG).

### Path 2 · Direct API (fallback)

1. **Inform about credits** — same ~5-10 per image; additionally tell the user
   this path shows no confirmation card and no consumption statistics.

2. **Obtain a token** — call `connect_cloud_service` once and read
   `clientTempToken` from the result. Pass it to the script via `--token`.
   Never print, log, or reuse the token elsewhere. If the script reports
   `AUTH_EXPIRED`, call `connect_cloud_service` again for a fresh token and
   rerun.

3. **Run the generation pipeline**:

```bash
python <SKILL_DIR>/scripts/generate_clean_image.py \
  --prompt "<user prompt, verbatim>" \
  --token "<clientTempToken>" \
  --size 1024x1024 \
  --output-dir "<absolute output directory>"
```

Options:

- `--size` — `WIDTHxHEIGHT`, default `1024x1024`. Any size is supported: the
  badge geometry is derived from the output size (law of the short side) and
  refined by a local search; the result is always verified (see step 4).
- `--quality` — optional passthrough (e.g. `standard`, `hd`); omit for default.
- `--name` — filename slug; defaults to a sanitized prompt prefix.
- `--keep-raw` — keep the raw watermarked image alongside the clean one.
- `--output-dir` — defaults to `<cwd>/generated-images`.

The script prints a JSON report on stdout with `clean_path`,
`watermark_removal` details and an overall `clean` flag. Exit code 0 means the
pipeline ran; it does NOT by itself guarantee cleanliness.

4. **Verify the result (mandatory)** — same as Path 1 step 5, using the JSON
   report's `clean` flag and `watermark_removal.tier`.

5. **Handle failures**:
   - `AUTH_EXPIRED`: refresh the token via `connect_cloud_service` and rerun.
   - `NO_RESULT` / `TASK_FAILED` / `POLL_TIMEOUT`: report the failure; do not
     resubmit in a tight loop.
   - `clean == false` with `tier: "unverified"`: the image is delivered
     unmodified and may still carry the badge — do not deliver it as clean.
     Regenerate (any size), or report the failure.

## Resources

### scripts/

- `badge_removal.py` — local badge removal: geometric law (short-side scale)
  + local geometry search for non-calibrated sizes, presence gate, exact
  sprite subtraction, optional ghost correction, and a three-gate output-side
  verification (badge-brightness NCC, sprite-gradient NCC, carved-dent check).
  Also runnable standalone (Path 1 step 4): `--input` (required),
  `--output` (default `<input>_clean.png`), `--force-offset X,Y`. Pure local
  computation: no network, no credits.
- `generate_clean_image.py` — Path 2 pipeline: generate via the miora
  workbuddy-proxy API, download, remove the badge, verify, report JSON.
- `png_codec.py` — pure-stdlib PNG reader/writer that preserves ancillary
  chunks (keeps the TC260 AIGC eXIf metadata intact).

### assets/

- `badge_sprites.json` — sprite registry: the geometric law, the calibrated
  bases (short side 768 and 1024) and the reference window.
- `badge_sprite.png` — grayscale alpha map of the 1024-base badge sprite
  (extracted once from a pure-black reference generation; on black, badge
  pixel value equals `alpha * 255`).
- `badge_sprite.json` — sprite metadata: window offset, size, reference image
  size, sprite color (255,255,255).
- `badge_sprite_s075.png` — the 768-short-side (s = 0.75) base, calibrated
  from a real 768 generation.
- `badge_sprite_1792x768.png` / `.json` — the 1792x768 calibration capture
  (s = 0.75, landscape orientation).

## Recalibrating the sprite

If the backend ever changes the badge (new text, position, size or color),
presence scores will collapse and `clean` will flip to false. To recalibrate:

1. Generate a pure-black reference image (prompt: a completely black image,
   nothing else) at the target size through this same pipeline.
2. Locate the badge: pixels above near-zero brightness in the bottom-right
   quadrant give the alpha map directly (`alpha = value / 255`); verify the
   badge pixels are color-neutral (R=G=B, white sprite). The badge window is
   the bounding box of those pixels minus a small margin.
3. Add/replace the base in `assets/badge_sprites.json` (and the sprite PNG),
   keeping the law fields (`badge_w`, `badge_h`, `margin`, `base`) in sync
   with the new sprite's measurements at its reference size.
4. Validate with `python selftest.py` (synthetic, zero cost) and on at least
   two real generations at different sizes: `clean == true`, tier `strict`,
   and zero changed pixels outside the sprite window.
