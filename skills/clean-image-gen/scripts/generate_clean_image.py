# -*- coding: utf-8 -*-
"""Generate an image through WorkBuddy's own image backend and guarantee the
output carries no visible watermark.

Two backends share the same local removal core:

- path "local" (default) — the miora workbuddy-proxy image API (the same
  backend the built-in ImageGen tool uses: model hy-image-3.5-wb, provider
  copilot-image). The server stamps a fixed badge; it is removed locally by
  exact sprite subtraction (see badge_removal.py) - local math, no extra
  credits. Any size is supported.
- path "native" — the native Hunyuan AIART API (hy-aiart provider) with the
  official LogoAdd=0 parameter: the server does not add a watermark at all.
  Constraints: each side in [512, 2048] and width*height <= 1024*1024. The
  local removal core still runs as a verification/safety net (expected
  result: tier "none", nothing to remove).

Pipeline (single command, pure stdlib):
  1. submit the prompt to the selected backend
  2. download the raw generated PNG
  3. remove any server-side badge by exact sprite subtraction (local, free)
  4. verify by re-compositing: residual < 2/255 proves self-consistency, plus
     three output-side gates prove the badge is gone
  5. print a JSON report with the raw and clean file paths

Usage:
  python generate_clean_image.py --prompt "..." --token <clientTempToken> \
      [--size 1024x1024] [--path local|native] [--quality standard] \
      [--seed 12345] [--output-dir DIR] [--name slug] [--keep-raw]

Exit code 0 on success (including "no badge detected"), 1 on failure.
The JSON report on stdout always contains "clean": true/false - only treat
the image as guaranteed watermark-free when clean == true.
"""

import argparse
import datetime
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from badge_removal import remove_watermark  # noqa: E402

MIORA_BASE = "https://public.miora.qq.com/api/ai/workbuddy-proxy"
SUBMIT_PATH = "/image/generate/submit"
QUERY_PATH = "/image/query-task"
POLL_INTERVAL = 5
POLL_TIMEOUT = 300

# Native Hunyuan AIART API (via the WorkBuddy cloud proxy). The official
# LogoAdd parameter disables the AI watermark server-side: 1 = add (default),
# 0 = do not add.
AIART_ENDPOINT = "https://copilot.tencent.com/agenttool/v1/tcproxy"
AIART_PROVIDER = "hy-aiart"
AIART_SERVICE = "aiart"
AIART_VERSION = "2022-12-29"
AIART_SUBMIT_ACTION = "SubmitTextToImageJob"
AIART_QUERY_ACTION = "QueryTextToImageJob"
AIART_REGION = "ap-guangzhou"
AIART_SIGNING_KEY = "codebuddy"
AIART_SIDE_MIN, AIART_SIDE_MAX = 512, 2048
AIART_AREA_MAX = 1024 * 1024


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


# ---------------------------------------------------------------------------
# Native AIART path (LogoAdd=0): the server does not add a watermark at all
# ---------------------------------------------------------------------------

def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key, msg):
    return hmac.new(key, msg, hashlib.sha256).digest()


def _tc3_headers(secret_id, secret_key, service, action, version, region,
                 host, payload, timestamp):
    """Build TC3-HMAC-SHA256 signed headers (pure stdlib)."""
    date = datetime.datetime.fromtimestamp(
        timestamp, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    content_type = "application/json; charset=utf-8"
    signed_headers = "content-type;host;x-tc-action"
    canonical_headers = (
        "content-type:%s\nhost:%s\nx-tc-action:%s\n"
        % (content_type, host, action.lower())
    )
    canonical_request = (
        "POST\n/\n\n%s\n%s\n%s"
        % (canonical_headers, signed_headers,
           _sha256_hex(payload.encode("utf-8")))
    )
    credential_scope = "%s/%s/tc3_request" % (date, service)
    string_to_sign = (
        "TC3-HMAC-SHA256\n%d\n%s\n%s"
        % (timestamp, credential_scope,
           _sha256_hex(canonical_request.encode("utf-8")))
    )
    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"),
                               date.encode("utf-8"))
    secret_service = _hmac_sha256(secret_date, service.encode("utf-8"))
    secret_signing = _hmac_sha256(secret_service, b"tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    authorization = (
        "TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
        % (secret_id, credential_scope, signed_headers, signature)
    )
    return {
        "Authorization": authorization,
        "Content-Type": content_type,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": version,
        "X-TC-Region": region,
        "X-TC-Timestamp": str(timestamp),
    }


def _aiart_call(action, body, token, timeout=120):
    """Call the native Hunyuan AIART API through the cloud proxy."""
    host = urllib.parse.urlparse(AIART_ENDPOINT).hostname
    payload = json.dumps(body, ensure_ascii=False)
    headers = _tc3_headers(
        secret_id="%s.%s" % (AIART_PROVIDER, token),
        secret_key=AIART_SIGNING_KEY,
        service=AIART_SERVICE,
        action=action,
        version=AIART_VERSION,
        region=AIART_REGION,
        host=host,
        payload=payload,
        timestamp=int(time.time()),
    )
    req = urllib.request.Request(
        AIART_ENDPOINT, data=payload.encode("utf-8"), headers=headers,
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
            msg = (detail.get("Response", {}).get("Error", {}).get("Message")
                   or detail.get("message") or detail.get("error"))
        except Exception:
            msg = None
        _fail("AIART_HTTP_%d" % e.code,
              "Native API submit failed with HTTP %d: %s"
              % (e.code, msg or "no detail"))
    except urllib.error.URLError as e:
        _fail("CONNECTION_ERROR", "Cannot reach the image service: %s" % e.reason)
    inner = result.get("Response", result)
    if isinstance(inner, dict) and inner.get("Error"):
        _fail("AIART_ERROR", str(inner["Error"].get("Message")
                                 or inner["Error"].get("Code")
                                 or "native API error"))
    return inner


def _native_generate(token, prompt, size, seed, raw_path):
    """Submit + poll + download via the native AIART API with LogoAdd=0."""
    m = re.match(r"^(\d+)x(\d+)$", size.strip())
    if not m:
        _fail("BAD_SIZE", "Size must be WIDTHxHEIGHT, e.g. 1024x1024.")
    w, h = int(m.group(1)), int(m.group(2))
    if not (AIART_SIDE_MIN <= w <= AIART_SIDE_MAX
            and AIART_SIDE_MIN <= h <= AIART_SIDE_MAX
            and w * h <= AIART_AREA_MAX):
        _fail("NATIVE_SIZE_UNSUPPORTED",
              "The native path (LogoAdd=0) only supports each side in "
              "[%d, %d] and width*height <= %d; %dx%d is outside that range. "
              "Use --path local (default), which supports any size."
              % (AIART_SIDE_MIN, AIART_SIDE_MAX, AIART_AREA_MAX, w, h))
    body = {"Prompt": prompt, "LogoAdd": 0, "Resolution": "%d:%d" % (w, h)}
    if seed is not None:
        body["Seed"] = seed
    resp = _aiart_call(AIART_SUBMIT_ACTION, body, token)
    job_id = resp.get("JobId")
    if not job_id:
        _fail("NO_JOB_ID", "The service did not return a job ID.")
    deadline = time.time() + POLL_TIMEOUT
    while True:
        if time.time() > deadline:
            _fail("POLL_TIMEOUT",
                  "Generation did not finish within %ds." % POLL_TIMEOUT)
        res = _aiart_call(AIART_QUERY_ACTION, {"JobId": job_id}, token)
        status = res.get("Status", "")
        raw_code = res.get("JobStatusCode")
        code = int(raw_code) if raw_code is not None else None
        if status == "DONE" or code == 5:
            break
        if status == "FAIL" or code == 4:
            _fail("TASK_FAILED", str(res.get("ErrorMessage")
                                     or res.get("JobErrorMsg")
                                     or "generation failed"))
        time.sleep(POLL_INTERVAL)
    image_url = res.get("ResultImage") or res.get("ResultImageUrl")
    if isinstance(image_url, list):
        image_url = image_url[0] if image_url else None
    if not image_url:
        _fail("NO_URL", "The service did not return an image URL.")
    return _download(image_url, raw_path)


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
    ap.add_argument("--path", choices=["local", "native"], default="local",
                    help="generation backend: 'local' (default) = miora "
                         "workbuddy-proxy + local badge removal, any size; "
                         "'native' = Hunyuan AIART API with LogoAdd=0 "
                         "(server adds no watermark; each side 512-2048, "
                         "width*height <= 1024x1024)")
    ap.add_argument("--quality", default="")
    ap.add_argument("--seed", type=int, default=None,
                    help="optional random seed (native path only)")
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
    gen_info = {"path": args.path}
    if args.path == "native":
        # The server adds no watermark (LogoAdd=0); the local removal core
        # still runs below as verification and safety net.
        nbytes = _native_generate(args.token, args.prompt, args.size,
                                  args.seed, raw_path)
        gen_info.update({"backend": "hy-aiart/aiart", "logo_add": 0})
    else:
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
        gen_info.update({"model": data.get("model"),
                         "provider": data.get("provider"), "mode": mode})

    # phase 3: remove the watermark badge (local, no credits)
    report = remove_watermark(raw_path, clean_path)

    # phase 4: report
    result = {
        "ok": True,
        "prompt": args.prompt,
        "raw_path": raw_path,
        "clean_path": clean_path,
        "raw_bytes": nbytes,
        "watermark_removal": report,
        "clean": report.get("clean", False),
    }
    result.update(gen_info)
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
