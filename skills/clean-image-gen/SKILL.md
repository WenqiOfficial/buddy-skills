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
- Because the sprite is fixed, removal is **exact math**, not generative
  inpainting: invert the alpha compositing
  `stamped = (1-a)*original + a*255` to recover `original`. Only the ~3k badge
  pixels change; everything else is untouched, so the output is still the same
  generated image. Inpainting-based erase (buddy-image-processing) re-encodes
  the whole picture and visibly alters it — never use it for this purpose.
- Removal is **pure local computation**: no network, no API call, no credits.
  The only credit cost in this skill is the image generation itself
  (roughly 5-10 per image).
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
   (verbatim) plus size/quality; prefer `1024x1024` (the badge sprite is
   calibrated for it). The platform's credit confirmation and consumption
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
  `clean == true` in the report guarantees the badge is gone.

5. **Verify the result (mandatory)**:
   1. Check `clean == true` in the JSON report. This flag is true only when the
      re-compositing residual (`max_residual <= 2/255`) proves the badge was
      fully explained and removed.
   2. Open the clean file with the Read tool and visually confirm the
      bottom-right corner is clean and the image looks untouched.
   3. Only then deliver the image with `present_files`.

6. **Handle failures**:
   - `NOT_PNG_OR_UNSUPPORTED`: the generated file is not an 8-bit PNG. Report
     it; do not silently re-encode. Ask the user before trying Path 2 (which
     downloads the backend's raw file and may still be a PNG).
   - `clean == false` or `badge_detected == false`: the badge sprite did not
     match (e.g. an unusual output size). Regenerate at `1024x1024`, or report
     that automatic removal failed and offer the `buddy-image-processing` erase
     fallback (warning: erase re-encodes the whole image and alters it).

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

- `--size` — `WIDTHxHEIGHT`, default `1024x1024`. The badge sprite is
  calibrated for 1024x1024; other sizes are handled by automatic badge
  detection (best effort, always verified — see step 4).
- `--quality` — optional passthrough (e.g. `standard`, `hd`); omit for default.
- `--name` — filename slug; defaults to a sanitized prompt prefix.
- `--keep-raw` — keep the raw watermarked image alongside the clean one.
- `--output-dir` — defaults to `<cwd>/generated-images`.

The script prints a JSON report on stdout with `clean_path`,
`watermark_removal` details and an overall `clean` flag. Exit code 0 means the
pipeline ran; it does NOT by itself guarantee cleanliness.

4. **Verify the result (mandatory)** — same as Path 1 step 5, using the JSON
   report's `clean` flag.

5. **Handle failures**:
   - `AUTH_EXPIRED`: refresh the token via `connect_cloud_service` and rerun.
   - `NO_RESULT` / `TASK_FAILED` / `POLL_TIMEOUT`: report the failure; do not
     resubmit in a tight loop.
   - `clean == false`: do not deliver the image as clean. Regenerate at
     `--size 1024x1024`, or report the failure.

## Resources

### scripts/

- `badge_removal.py` — local badge removal: sprite subtraction, badge detection
  for non-reference sizes (downsampled NCC around the corner anchor), residual
  verification. Also runnable standalone (Path 1 step 4): `--input` (required),
  `--output` (default `<input>_clean.png`), `--force-offset X,Y`. Pure local
  computation: no network, no credits.
- `generate_clean_image.py` — Path 2 pipeline: generate via the miora
  workbuddy-proxy API, download, remove the badge, verify, report JSON.
- `png_codec.py` — pure-stdlib PNG reader/writer that preserves ancillary
  chunks (keeps the TC260 AIGC eXIf metadata intact).

### assets/

- `badge_sprite.png` — grayscale alpha map of the badge sprite (extracted once
  from a pure-black reference generation; on black, badge pixel value equals
  `alpha * 255`).
- `badge_sprite.json` — sprite metadata: window offset, size, reference image
  size, sprite color (255,255,255).

## Recalibrating the sprite

If the backend ever changes the badge (new text, position, size or color),
subtraction residuals will rise and `clean` will flip to false. To recalibrate:

1. Generate a pure-black reference image (prompt: a completely black image,
   nothing else) at the target size through this same pipeline.
2. Locate the badge: pixels above near-zero brightness in the bottom-right
   quadrant give the alpha map directly (`alpha = value / 255`); verify the
   badge pixels are color-neutral (R=G=B, white sprite).
3. Replace `assets/badge_sprite.png` / `badge_sprite.json` with the new map,
   offset, and reference size.
4. Validate on at least two real generations: `max_residual <= 2` and zero
   changed pixels outside the sprite window.
