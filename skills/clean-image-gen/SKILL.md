---
name: clean-image-gen
display_name: 无水印AI生图（双路径）
display_name_en: Watermark-Free AI Image (Dual-Path)
version: 2.1.0
agent_created: true
description: 无水印 AI 生图（双路径）。为用户生成不带水印的图片：路径一（默认）用与内置 ImageGen 相同的 WorkBuddy 生图后端（模型 hy-image-3.5-wb）生成，再以精确 sprite 减法本地去除右下角「AI生成 / WORKBUDDY」徽章——除徽章像素外逐字节不变，支持任意尺寸（1024 母版 + lanczos 缩放复刻服务端渲染管线，非校准尺寸 alpha 误差约 1.7/255）；路径二（--path native）调用原生混元 AIART 接口的官方 LogoAdd=0 参数，服务端直接不加水印（限单边 512–2048、面积 ≤ 1024×1024）。生成前会询问用户使用哪条路径；生图本身与直接调用 ImageGen 完全一致（提示词正常优化）。触发词：无水印、无水印生图、生成图片、生成一张图、AI画图、生图、生成不带水印的图、干净的图、没有水印。不适用：给用户已有的图片去水印（用 buddy-image-processing erase）、视频生成（VideoGen）、3D 模型（buddy-multimodal-generation）。
description_zh: 无水印 AI 生图（双路径）。为用户生成不带水印的图片：路径一（默认）用与内置 ImageGen 相同的 WorkBuddy 生图后端（模型 hy-image-3.5-wb）生成，再以精确 sprite 减法本地去除右下角「AI生成 / WORKBUDDY」徽章——除徽章像素外逐字节不变，支持任意尺寸（1024 母版 + lanczos 缩放复刻服务端渲染管线，非校准尺寸 alpha 误差约 1.7/255）；路径二（--path native）调用原生混元 AIART 接口的官方 LogoAdd=0 参数，服务端直接不加水印（限单边 512–2048、面积 ≤ 1024×1024）。生成前会询问用户使用哪条路径；生图本身与直接调用 ImageGen 完全一致（提示词正常优化）。触发词：无水印、无水印生图、生成图片、生成一张图、AI画图、生图、生成不带水印的图、干净的图、没有水印。不适用：给用户已有的图片去水印（用 buddy-image-processing erase）、视频生成（VideoGen）、3D 模型（buddy-multimodal-generation）。
description_en: Watermark-free AI image generation (dual-path). Path 1 (default) — generate with the same WorkBuddy backend as the built-in ImageGen tool (model hy-image-3.5-wb), then remove the server-side "AI生成 / WORKBUDDY" badge locally by exact sprite subtraction; every pixel outside the badge is preserved bit-for-bit and any size is supported. Path 2 (--path native) — call the native Hunyuan AIART API with the official LogoAdd=0 parameter so the server adds no watermark at all (each side 512-2048, area <= 1024x1024). Always ask the user which path to use before generating. Triggers — watermark-free, no watermark, generate an image, AI drawing, clean image. Not for removing watermarks from user-supplied existing images (use buddy-image-processing erase), video (VideoGen), or 3D models (buddy-multimodal-generation).
---

# 无水印 AI 生图（双路径）

## Overview

Generate an image with WorkBuddy's standard image backend and deliver it with the
visible "AI生成 / WORKBUDDY>" badge removed, while keeping the image itself
pixel-identical everywhere else. An optional second backend generates with no
watermark at all.

Key facts established by investigation of the backend:

- The badge is added **server-side** by the image backend (provider
  `copilot-image`, model `hy-image-3.5-wb`) on essentially every generation.
  It cannot be disabled through API parameters (`logo_add`, `footnote`, prompt
  instructions such as "no watermark" were all tested and have no effect).
- **Two generation backends, one cleanliness guarantee**: the default miora
  workbuddy-proxy backend (badge stamped server-side, removed locally) and an
  optional **native Hunyuan AIART backend** (`--path native`) where the
  official `LogoAdd=0` parameter makes the server add **no watermark at all**
  (verified 2026-10-23 at 1024x1024, 1280x720 and 768x1024 — corner visually
  clean, sprite detector `tier: "none"`). Both cost the same credits (one
  generation); the native path is limited to each side in [512, 2048] and
  area <= 1024x1024, and its output still runs through the same local
  verification as a safety net. The two backends are **different model
  generations** (hy-image-3.5-wb vs HunyuanImage 3.0), so image style may
  differ slightly.
- The badge is a **fixed sprite**: neutral-white semi-transparent text anchored
  to the bottom-right corner, identical across prompts and generation paths.
- Its geometry follows a **law of the short side**: `s = min(W,H)/1024`, badge
  `(round(111*s), round(51*s))`, margin `floor(10*s)`, anchored to the
  bottom-right corner. The law predicts the badge to within ONE pixel, so
  non-calibrated sizes run a local geometry search (offsets ±3, size ±1,
  offsets again) on the input before subtracting. **Every resolution is
  supported** — 512x512 through 3000x2000, square/portrait/landscape alike
  (validated by `selftest.py` and a 21-size sweep).
- The sprite follows a **master-at-1024 pipeline**: the server rasterizes
  the master badge once at the 1024 base and resamples it to every output
  size with a lanczos-class filter. Measured by cross-resampling three
  real calibrations (short sides 1024 / 768 / 2048): master→s=0.75 matches
  the real badge to meanAE 1.69/255, while the s=2.0 badge resampled back
  to 1024 matches only 5.87 — i.e. the 2048 badge is a **resampled copy of
  the master, not a sharper master**. The removal therefore performs the
  same single operation: one lanczos resample of the 1024 master for any
  non-calibrated size (the old bilinear approximation was 2.6x worse;
  chaining resamples — "highest-resolution master" — is measurably worse
  still). Calibrated bases exist for short sides 768, 1024 and 2048; those
  sizes skip the search entirely. On the real 1280x1280 badge this cut the
  verification residue by ~37% and the misalignment signature to zero.
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

- Use this skill whenever the user wants a **newly generated image** and no
  visible watermark matters — e.g. "生成一张没有水印的图", "帮我画一只猫",
  "生成图片", "AI画图", "default 生图有水印，帮我做一张干净的". For plain
  generation requests without any watermark mention, this skill is still the
  right choice: it delivers the same image minus the badge.
- Do NOT use this skill when the user wants to remove a watermark from an
  **existing image** — that is `buddy-image-processing` (`--operation erase`).
- Do NOT use this skill when the user **explicitly wants the default
  watermarked image** — call `ImageGen` directly instead (rare).
- Do NOT use this skill for video (`VideoGen`) or 3D (`buddy-multimodal-generation`).

## Workflow

**Scope of this skill (inviolable)**: compared with calling `ImageGen`
directly, this skill changes exactly two things — (1) it asks which path
to use before generating (Step 0), and (2) it removes and verifies the
badge after generation. The generation step itself — prompt optimization
and expansion, `revise`, size/quality/style — must remain a plain
`ImageGen` call with no behavioral change.

### Step 0 · Ask the user which path (mandatory before generating)

**Before any generation**, if the user has not already stated a preference,
ASK with the `AskUserQuestion` tool — offer exactly these two options:

- **本地去水印（默认推荐）** — 用与内置 ImageGen 相同的后端（模型
  `hy-image-3.5-wb`，最新一代）生成，再本地精确减除右下角水印徽章：除徽章
  约 3k 像素外逐字节不变；支持任意尺寸（512×512 到 3000×2000）；保留平台
  积分确认卡片与消耗统计。
- **原生无水印** — 走原生混元 AIART 接口并传 `LogoAdd=0`，服务端直接不加水印
  （模型为 HunyuanImage 3.0，上一代，画面风格可能略有差异）；限单边
  512–2048、面积 ≤ 1024×1024；无积分确认卡片。

告诉用户：**两条路径积分成本相同**（各 1 次生成，约 5–10 积分）。

Route by the answer:

- 选「本地去水印」→ **Path 1**（默认）。
- 选「原生无水印」→ **Path 2** 并加 `--path native`。
- 用户明确要求跳过确认卡片/消耗统计，或内置 ImageGen 不可用 → **Path 2**
  （`--path local` 或 `--path native`）。

Skip the question only when the user already specified a path (e.g. "用原生
那个", "走本地去水印"), or when re-generating after a failure where the path
was already chosen.

### Path 1 · Hybrid — 本地去水印 (default)

1. **Confirm credits** — generation consumes roughly 5-10 credits (same as
   `ImageGen`). Removal itself is free. (Already covered by the Step 0
   question; just proceed.)

2. **Generate with the built-in `ImageGen` tool** — exactly as you would
   when calling `ImageGen` directly, with NO changes introduced by this
   skill: optimize and expand the user's prompt into a rich visual
   description (subject, style, lighting, composition, quality), then pass
   it with size/quality. Also pass `revise: true` (the ImageGen prompt-
   revision switch, Hunyuan) so the backend revises the prompt too — the
   same behavior as a plain `ImageGen` call. This skill adds ONLY two
   things to the generation flow: the path question (Step 0) and the
   post-generation badge removal (step 4). Nothing else may differ from a
   direct `ImageGen` call. Any size works (the badge geometry is derived
   from the output size and refined by a local search); `1024x1024` is
   merely the calibrated reference size and therefore the fastest path
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

### Path 2 · Direct API — 直连脚本

Use when the user chose 原生无水印 in Step 0 (`--path native`), or explicitly
wants to skip the confirmation flow, or the built-in ImageGen is unavailable.

1. **Confirm credits** — same ~5-10 per image; on the native path additionally
   tell the user this path shows no confirmation card and no consumption
   statistics.

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
  --path <local|native> \
  --output-dir "<absolute output directory>"
```

Options:

- `--path` — generation backend: `local` (default) or `native`.
  - `local`: miora workbuddy-proxy API + local badge removal. **Any size**
    (badge geometry is derived from the output size and refined by a local
    search; the result is always verified, see step 4).
  - `native`: native Hunyuan AIART API with the official `LogoAdd=0`
    parameter — the server adds no watermark, so no removal is needed
    (expected report: `tier: "none"`, `changed_pixels: 0`). Constraints:
    each side in [512, 2048] and `width*height <= 1024x1024`; outside that
    range the script fails fast with `NATIVE_SIZE_UNSUPPORTED` — rerun with
    `--path local`.
- `--size` — `WIDTHxHEIGHT`, default `1024x1024` (supported range depends on
  `--path`, see above).
- `--quality` — optional passthrough (e.g. `standard`, `hd`); omit for default.
- `--seed` — optional random seed (native path only) for reproducible prompts.
- `--name` — filename slug; defaults to a sanitized prompt prefix.
- `--keep-raw` — keep the raw watermarked image alongside the clean one.
- `--output-dir` — defaults to `<cwd>/generated-images`.

The script prints a JSON report on stdout with `clean_path`,
`watermark_removal` details, the selected `path`/`backend` and an overall
`clean` flag. Exit code 0 means the pipeline ran; it does NOT by itself
guarantee cleanliness.

4. **Verify the result (mandatory)** — same as Path 1 step 5, using the JSON
   report's `clean` flag and `watermark_removal.tier`. On the native path the
   removal report is expected to be `tier: "none"` (the server added no
   badge); if a badge-shaped pattern IS found there, it goes through the
   same exact removal and three-gate verification as the local path. The
   visual corner check (step 5.2) remains mandatory on both paths — it is
   the only guard against a differently-styled watermark the sprite detector
   cannot recognize.

5. **Handle failures**:
   - `AUTH_EXPIRED`: refresh the token via `connect_cloud_service` and rerun.
   - `NO_RESULT` / `TASK_FAILED` / `POLL_TIMEOUT`: report the failure; do not
     resubmit in a tight loop.
   - `NATIVE_SIZE_UNSUPPORTED`: the requested size exceeds the native API
     constraints — rerun with `--path local` (any size) or ask the user for a
     smaller size.
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
- `generate_clean_image.py` — Path 2 pipeline: generate (miora
  workbuddy-proxy + local badge removal, or native Hunyuan AIART API with
  `LogoAdd=0` via `--path native`), download, remove/verify the badge,
  report JSON. Pure stdlib, no third-party dependencies.
- `png_codec.py` — pure-stdlib PNG reader/writer that preserves ancillary
  chunks (keeps the TC260 AIGC eXIf metadata intact).

### assets/

- `badge_sprites.json` — sprite registry: the geometric law, the calibrated
  bases (short sides 768, 1024 and 2048) and the reference window. The
  1024 base is the **master**: every non-calibrated size is derived from
  it by one lanczos resample, reproducing the server's own pipeline.
- `badge_sprite.png` — grayscale alpha map of the 1024-base badge sprite
  (extracted once from a pure-black reference generation; on black, badge
  pixel value equals `alpha * 255`).
- `badge_sprite.json` — sprite metadata: window offset, size, reference image
  size, sprite color (255,255,255).
- `badge_sprite_s075.png` — the 768-short-side (s = 0.75) base, calibrated
  from a real 768 generation.
- `badge_sprite_1792x768.png` / `.json` — the 1792x768 calibration capture
  (s = 0.75, landscape orientation).
- `badge_sprite_s200.png` — the 2048-short-side (s = 2.0) base, calibrated
  from a real 2048x2048 pure-black generation (peak alpha 139 vs the
  master's 142 — resampling lowers the peak).

## Recalibrating the sprite

If the backend ever changes the badge (new text, position, size or color),
presence scores will collapse and `clean` will flip to false. To recalibrate:

1. Generate a pure-black reference image (prompt: a completely black image,
   nothing else) at **1024x1024 — the master size** — through this same
   pipeline (`--keep-raw` keeps the watermarked original). Optionally add a
   2048x2048 capture as a second exact base.
2. Locate the badge: pixels above near-zero brightness in the bottom-right
   quadrant give the alpha map directly (`alpha = value / 255`); verify the
   badge pixels are color-neutral (R=G=B, white sprite). The badge window is
   the bounding box of those pixels minus a small margin.
3. Add/replace the base in `assets/badge_sprites.json` (and the sprite PNG),
   keeping the law fields (`badge_w`, `badge_h`, `margin`, `base`) in sync
   with the new sprite's measurements at its reference size. Keep the 1024
   entry the master: every other size derives from it.
4. Validate with `python selftest.py` (synthetic, zero cost) and on at least
   two real generations at different sizes: `clean == true`, tier `strict`,
   and zero changed pixels outside the sprite window.

Why not calibrate the highest possible resolution as the master: the
server itself renders the master at 1024 and resamples down/up with a
lanczos-class filter, so a higher-resolution capture is just another
resampled copy — deriving target sizes from it chains two resamplings and
matches the real badge *worse* (measured: 2048→1024 meanAE 5.87 vs
1024→768 meanAE 1.69). One master at 1024 plus one same-class resample is
the closest reproduction of the server's own pipeline.

As an alternative to recalibration, the `--path native` backend
(`LogoAdd=0`) does not depend on the badge sprite at all.
