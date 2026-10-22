# -*- coding: utf-8 -*-
"""Generate an image through WorkBuddy's own image backend and guarantee the
output carries no visible watermark.

Pipeline (single command, pure stdlib):
  1. submit the prompt to the miora workbuddy-proxy image API
     (the same backend the built-in ImageGen tool uses: model
     hy-image-3.5-wb, provider copilot-image)
  2. download the raw generated PNG
  3. remove the server-side "AI生成 / WORKBUDDY>" badge by exact sprite
     subtraction (see badge_removal.py) - local math, no extra credits
  4. verify by re-compositing: residual < 2/255 proves the badge is gone
  5. print a JSON report with the raw and clean file paths

Usage:
  python generate_clean_image.py --prompt "..." --token <clientTempToken> \
      [--size 1024x1024] [--quality standard] [--output-dir DIR] \
      [--name slug] [--keep-raw]

Exit code 0 on success (including "no badge detected"), 1 on failure.
The JSON report on stdout always contains "clean": true/false - only treat
the image as guaranteed watermark-free when clean == true.
"""

import argparse
import base64
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from badge_removal import remove_watermark  # noqa: E402

MIORA_BASE = "https://public.miora.qq.com/api/ai/workbuddy-proxy"
SUBMIT_PATH = "/image/generate/submit"
QUERY_PATH = "/image/query-task"
POLL_INTERVAL = 5
POLL_TIMEOUT = 300


def _post_json(url, token, body, timeout=120):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _find_result_files(obj):
    """Recursively locate a resultFiles list in an arbitrary response."""
    if isinstance(obj, dict):
        if isinstance(obj.get("resultFiles"), list) and obj["resultFiles"]:
            return obj["resultFiles"]
        for v in obj.values():
            found = _find_result_files(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_result_files(v)
            if found:
                return found
    return None


def _submit(token, prompt, size, quality):
    body = {"prompt": prompt, "size": size}
    if quality:
        body["quality"] = quality
    try:
        res = _post_json(MIORA_BASE + SUBMIT_PATH, token, body)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            _fail("AUTH_EXPIRED",
                  "The clientTempToken is invalid or expired. Call "
                  "connect_cloud_service again for a fresh token.")
        _fail("SUBMIT_HTTP_%d" % e.code, "Generation submit failed with HTTP %d." % e.code)
    except urllib.error.URLError as e:
        _fail("CONNECTION_ERROR", "Cannot reach the image service: %s" % e.reason)
    if res.get("failed"):
        _fail("SUBMIT_FAILED", str(res.get("msg") or res.get("message") or "submit failed"))
    data = res.get("data") or {}
    return data


def _poll(token, task_id):
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            res = _post_json(MIORA_BASE + QUERY_PATH, token,
                             {"upstreamTaskIds": [task_id]})
        except urllib.error.HTTPError as e:
            if e.code == 401:
                _fail("AUTH_EXPIRED",
                      "The clientTempToken expired while polling. Call "
                      "connect_cloud_service again; the task is not resubmitted.")
            _fail("QUERY_HTTP_%d" % e.code, "Task query failed with HTTP %d." % e.code)
        except urllib.error.URLError as e:
            _fail("CONNECTION_ERROR", "Cannot reach the image service: %s" % e.reason)
        files = _find_result_files(res)
        if files:
            return files
        failed = res.get("failed") or (res.get("data") or {}).get("failed")
        if failed:
            _fail("TASK_FAILED", "The generation task failed upstream.")
        time.sleep(POLL_INTERVAL)
    _fail("POLL_TIMEOUT", "Generation did not finish within %ds." % POLL_TIMEOUT)


def _download(url, path):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=180) as resp:
        blob = resp.read()
    if len(blob) < 100:
        _fail("DOWNLOAD_EMPTY", "Downloaded image payload is suspiciously small.")
    with open(path, "wb") as f:
        f.write(blob)
    return len(blob)


def _slugify(text, limit=24):
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", text.strip())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:limit] or "image"


def _fail(code, message):
    print(json.dumps({"ok": False, "error": code, "message": message},
                     ensure_ascii=False, indent=2))
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Generate a watermark-free image")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--token", required=True,
                    help="clientTempToken from connect_cloud_service")
    ap.add_argument("--size", default="1024x1024")
    ap.add_argument("--quality", default="")
    ap.add_argument("--output-dir", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--keep-raw", action="store_true",
                    help="keep the raw (watermarked) image next to the clean one")
    args = ap.parse_args()

    out_dir = args.output_dir or os.path.join(os.getcwd(), "generated-images")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%H-%M-%S")
    slug = _slugify(args.name or args.prompt)
    raw_path = os.path.join(out_dir, "raw_%s_%s.png" % (slug, stamp))
    clean_path = os.path.join(out_dir, "clean_%s_%s.png" % (slug, stamp))

    # phase 1+2: generate and download
    data = _submit(args.token, args.prompt, args.size, args.quality)
    files = data.get("resultFiles")
    mode = data.get("mode", "sync")
    if not files and data.get("upstreamTaskId"):
        files = _poll(args.token, data["upstreamTaskId"])
    if not files:
        _fail("NO_RESULT", "The generation response contained no result files.")
    signed = files[0].get("signedUrl")
    if not signed:
        _fail("NO_URL", "The result file has no signedUrl.")
    nbytes = _download(signed, raw_path)

    # phase 3: remove the watermark badge (local, no credits)
    report = remove_watermark(raw_path, clean_path)

    # phase 4: report
    result = {
        "ok": True,
        "prompt": args.prompt,
        "model": data.get("model"),
        "provider": data.get("provider"),
        "mode": mode,
        "raw_path": raw_path,
        "clean_path": clean_path,
        "raw_bytes": nbytes,
        "watermark_removal": report,
        "clean": report.get("clean", False),
    }
    if not args.keep_raw:
        try:
            os.remove(raw_path)
            result["raw_path"] = None
        except OSError:
            pass
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
