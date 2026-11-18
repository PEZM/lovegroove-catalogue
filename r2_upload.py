#!/usr/bin/env python3
"""Upload a file to Cloudflare R2 (S3 API) with nothing but Python's standard library.

    R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
      python3 r2_upload.py --bucket lovegroove-catalogue --key vinyl/current.pack --file catalogue.pack
    python3 r2_upload.py --bucket lovegroove-catalogue --check     # just test the key works

Large files go up in 64 MB parts, 4 at a time, each retried up to 5 times. If the
upload fails it is aborted, so no half-finished parts are left in the bucket.
"""
import argparse
import datetime
import hashlib
import hmac
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PART = 64 * 1024 * 1024
THREADS = 4


class R2:
    def __init__(self, account, key_id, secret, bucket):
        self.host = f"{account}.r2.cloudflarestorage.com"
        self.key_id, self.secret, self.bucket = key_id, secret, bucket

    def _sign(self, method, path, query, headers):
        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        headers = {**headers, "host": self.host, "x-amz-date": amz_date,
                   "x-amz-content-sha256": "UNSIGNED-PAYLOAD"}
        names = sorted(h.lower() for h in headers)
        lower = {k.lower(): str(v).strip() for k, v in headers.items()}
        canonical_query = "&".join(
            f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(str(v), safe='-_.~')}"
            for k, v in sorted(query.items()))
        canonical = "\n".join([
            method, urllib.parse.quote(path, safe="/-_.~"), canonical_query,
            "".join(f"{n}:{lower[n]}\n" for n in names), ";".join(names), "UNSIGNED-PAYLOAD"])
        scope = f"{day}/auto/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        k = ("AWS4" + self.secret).encode()
        for part in (day, "auto", "s3", "aws4_request"):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        headers["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={self.key_id}/{scope}, "
                                    f"SignedHeaders={';'.join(names)}, Signature={signature}")
        return headers, canonical_query

    def request(self, method, key="", query=None, body=None, headers=None):
        path = f"/{self.bucket}/{key}" if key else f"/{self.bucket}"
        headers, qs = self._sign(method, path, query or {}, headers or {})
        url = f"https://{self.host}{urllib.parse.quote(path, safe='/-_.~')}" + (f"?{qs}" if qs else "")
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"R2 {method} {key or '(bucket)'}: HTTP {e.code} {e.read()[:300]!r}") from None


def upload(r2, key, path):
    size = os.path.getsize(path)
    parts = (size + PART - 1) // PART
    print(f"{size / 1e9:.2f} GB in {parts} parts", flush=True)
    _, _, body = r2.request("POST", key, {"uploads": ""}, headers={"content-type": "application/octet-stream"})
    upload_id = re.search(rb"<UploadId>(.+?)</UploadId>", body).group(1).decode()
    started = time.time()

    def put(n):
        with open(path, "rb") as f:
            f.seek((n - 1) * PART)
            data = f.read(PART)
        for attempt in range(5):
            try:
                _, headers, _ = r2.request("PUT", key, {"partNumber": n, "uploadId": upload_id}, body=data,
                                           headers={"content-length": str(len(data))})
                print(f"part {n}/{parts} done, {time.time() - started:.0f} s", flush=True)
                return n, {k.lower(): v for k, v in headers.items()}["etag"]
            except Exception as e:
                if attempt == 4:
                    raise
                print(f"part {n} failed ({e}); retrying", flush=True)
                time.sleep(10 * (attempt + 1))

    try:
        with ThreadPoolExecutor(THREADS) as pool:
            etags = sorted(pool.map(put, range(1, parts + 1)))
        xml = "<CompleteMultipartUpload>" + "".join(
            f"<Part><PartNumber>{n}</PartNumber><ETag>{e}</ETag></Part>" for n, e in etags) + "</CompleteMultipartUpload>"
        _, _, body = r2.request("POST", key, {"uploadId": upload_id}, body=xml.encode(),
                                headers={"content-type": "application/xml"})
        if b"<Error>" in body:
            raise RuntimeError(f"R2 refused to finish the upload: {body[:300]!r}")
    except BaseException:
        try:
            r2.request("DELETE", key, {"uploadId": upload_id})
            print("upload aborted; nothing was replaced", flush=True)
        except Exception:
            pass
        raise
    print(f"uploaded {key} in {time.time() - started:.0f} s", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--key")
    ap.add_argument("--file")
    ap.add_argument("--check", action="store_true", help="only check the key can list the bucket")
    args = ap.parse_args()
    env = {n: "".join(os.environ.get(n, "").split()) for n in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")}
    missing = [n for n, v in env.items() if not v]
    if missing:
        sys.exit(f"missing {', '.join(missing)}")
    r2 = R2(env["R2_ACCOUNT_ID"], env["R2_ACCESS_KEY_ID"], env["R2_SECRET_ACCESS_KEY"], args.bucket)
    if args.check:
        r2.request("GET", query={"list-type": "2", "max-keys": "1"})
        print("R2 key OK")
        return
    if not (args.key and args.file):
        sys.exit("--key and --file are needed to upload")
    upload(r2, args.key, args.file)


if __name__ == "__main__":
    main()
