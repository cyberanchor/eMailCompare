#!/usr/bin/env python3
"""Download a favicon for every provider in data/providers/ into site/assets/favicons/<slug>.<ext>.

Order of preference for each domain:
  1. icons declared on the homepage (<link rel="icon" / "apple-touch-icon">), largest first, SVG rasterised
  2. /favicon.ico — an embedded PNG is used as is, a BMP image is converted to PNG here
  3. Google's and DuckDuckGo's favicon services, then the newest archived /favicon.ico on the Wayback
     Machine (for sites that are gone); all of these are used only here, at collection time
Hugo resizes and re-encodes the files, so the site never serves a downloaded file byte for byte and never
asks a third party for an icon at view time. Bot checks are respected: a blocked site is not retried.

Stdlib only, plus rsvg-convert for SVG icons when it is installed.

usage: python3 tools/fetch_favicons.py [-h] [--force] [--all] [DOMAIN ...]

Without DOMAIN it does nothing unless --all is given: then every provider without a favicon is fetched.
"""
import argparse
import concurrent.futures as cf
import html.parser
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time

import urllib.error
import urllib.parse
import urllib.request
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "collector"))
import common  # noqa: E402
import emcdata  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "site", "assets", "favicons")
REPORT = os.path.join(ROOT, "tools", "favicons.json")
UA = "eMailCompare-favicons/1.0 (+https://github.com/cyberanchor/eMailCompare)"
MAX_BYTES = 1_500_000
EXTS = (".png", ".jpg", ".gif", ".webp")
WAYBACK = threading.Lock()  # archive.org rate-limits; one request at a time


def get(url, limit=MAX_BYTES, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.headers.get("Content-Type", ""), r.read(limit + 1)[:limit]
    except urllib.error.HTTPError as e:
        return e.code, url, "", b""
    except Exception:
        return 0, url, "", b""


class IconLinks(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower().split()
        if not a.get("href") or "mask-icon" in rel:
            return
        if "icon" in rel or "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
            sizes = [int(n) for n in re.findall(r"(\d+)x\d+", a.get("sizes", ""))]
            size = max(sizes) if sizes else (180 if "apple-touch-icon" in " ".join(rel) else 0)
            if a.get("type", "").endswith("svg") or a["href"].lower().split("?")[0].endswith(".svg"):
                size = 512
            self.icons.append((size, a["href"]))


def kind(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"\x00\x00\x01\x00":
        return "ico"
    head = data[:600].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head) or b"<svg" in head[:300]:
        return "svg"
    return None


def png_size(data):
    return struct.unpack(">II", data[16:24]) if data.startswith(b"\x89PNG") else (0, 0)


def write_png(width, height, rgba):
    rows = b"".join(b"\x00" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def dib_to_png(dib):
    """Convert the BMP (DIB) image of an .ico entry to PNG: 32, 24, 8, 4 and 1 bits per pixel."""
    hsize, width, height2, _, bpp, comp = struct.unpack("<IiiHHI", dib[:20])
    height = abs(height2) // 2
    if comp not in (0, 3) or width <= 0 or height <= 0 or width > 512:
        return None
    pos = hsize
    palette = []
    if bpp <= 8:
        n = struct.unpack("<I", dib[32:36])[0] or (1 << bpp)
        palette = [dib[pos + i * 4:pos + i * 4 + 4] for i in range(n)]
        pos += n * 4
    stride = ((width * bpp + 31) // 32) * 4
    mask_stride = ((width + 31) // 32) * 4
    xor, mask = dib[pos:pos + stride * height], dib[pos + stride * height:]
    out = bytearray(width * height * 4)
    has_alpha = bpp == 32 and any(xor[i + 3] for i in range(0, len(xor), 4))
    for y in range(height):
        row = xor[(height - 1 - y) * stride:]
        mrow = mask[(height - 1 - y) * mask_stride:]
        for x in range(width):
            if bpp == 32:
                b, g, r, a = row[x * 4:x * 4 + 4]
            elif bpp == 24:
                b, g, r = row[x * 3:x * 3 + 3]
                a = 255
            else:
                bit = x * bpp
                idx = (row[bit // 8] >> (8 - bpp - bit % 8)) & ((1 << bpp) - 1)
                b, g, r = palette[idx][:3] if idx < len(palette) else (0, 0, 0)
                a = 255
            if not has_alpha and mrow and (mrow[x // 8] >> (7 - x % 8)) & 1:
                a = 0
            out[(y * width + x) * 4:(y * width + x) * 4 + 4] = bytes((r, g, b, a))
    return write_png(width, height, bytes(out))


def from_ico(data):
    count = struct.unpack("<H", data[4:6])[0]
    entries = []
    for i in range(min(count, 32)):
        w, h, _, _, _, bpp, size, offset = struct.unpack("<BBBBHHII", data[6 + i * 16:22 + i * 16])
        entries.append(((w or 256) * (h or 256), bpp, data[offset:offset + size]))
    for _, _, blob in sorted(entries, key=lambda e: (e[0], e[1]), reverse=True):
        if blob.startswith(b"\x89PNG"):
            return blob
        try:
            png = dib_to_png(blob)
        except Exception:
            png = None
        if png:
            return png
    return None


def rasterise_svg(data, tmp):
    if not shutil.which("rsvg-convert"):
        return None
    src, dst = os.path.join(tmp, "icon.svg"), os.path.join(tmp, "icon.png")
    with open(src, "wb") as f:
        f.write(data)
    try:
        subprocess.run(["rsvg-convert", "-w", "128", "-h", "128", "-o", dst, src], check=True, timeout=20,
                       capture_output=True)
        return open(dst, "rb").read()
    except Exception:
        return None


def usable(data, tmp):
    """(extension, bytes) of an image Hugo can process, or None (also for broken or truncated files)."""
    try:
        return _usable(data, tmp)
    except (struct.error, IndexError, ValueError, zlib.error):
        return None


def _usable(data, tmp):
    k = kind(data)
    if k == "ico":
        data, k = from_ico(data), "png"
    elif k == "svg":
        data, k = rasterise_svg(data, tmp), "png"
    if not data or not k:
        return None
    if k == "png" and min(png_size(data)) < 16:
        return None
    return ("." + k, data)


def fetch(domain):
    with tempfile.TemporaryDirectory() as tmp:
        tried = []
        status, final, _, page = get(f"https://{domain}/")
        if status == 0:
            status, final, _, page = get(f"http://{domain}/")
        candidates = []
        if status == 200 and page:
            parser = IconLinks()
            try:
                parser.feed(page.decode("utf-8", "replace"))
            except Exception:
                pass
            candidates = [urllib.parse.urljoin(final, href) for _, href in sorted(parser.icons, reverse=True)]
        origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(final if status == 200 else f"https://{domain}/"))
        candidates += [origin + "/favicon.ico", origin + "/apple-touch-icon.png"]
        blocked = status in (403, 429, 503)
        for url in dict.fromkeys(candidates):
            if blocked and url.startswith(origin):
                break  # the site answered with a bot check; do not keep asking it
            s, _, _, data = get(url)
            tried.append(url)
            if s == 200 and data:
                got = usable(data, tmp)
                if got:
                    return domain, got, url
        for name, url in (("google-s2", f"https://www.google.com/s2/favicons?domain={urllib.parse.quote(domain)}&sz=128"),
                          ("duckduckgo", f"https://icons.duckduckgo.com/ip3/{urllib.parse.quote(domain)}.ico")):
            s, _, _, data = get(url)
            if s == 200 and data:
                got = usable(data, tmp)
                if got:
                    return domain, got, name
        # Last resort for sites that are gone: the newest archived /favicon.ico (raw bytes via id_).
        with WAYBACK:
            time.sleep(2)
            s, final, _, data = get(f"https://web.archive.org/web/{time.strftime('%Y')}id_/https://{domain}/favicon.ico",
                                  timeout=30)
        if s == 200 and data:
            got = usable(data, tmp)
            if got:
                return domain, got, "wayback " + final.split("/web/")[-1][:14]
        return domain, None, "; ".join(tried) or f"homepage {status}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("domains", nargs="*", metavar="DOMAIN", help="providers to fetch (need not be in the data)")
    ap.add_argument("--all", action="store_true", help="every provider in data/providers/")
    ap.add_argument("--force", action="store_true", help="fetch again even when a favicon exists")
    args = ap.parse_args(argv)
    if not args.domains and not args.all:
        ap.error("name the DOMAINs to fetch, or pass --all")
    try:
        only = [common.normalize_domain(d) for d in args.domains]
    except ValueError as e:
        ap.error(str(e))
    force = args.force
    domains = list(dict.fromkeys(only)) or [r["domain"].lower() for r in emcdata.load_all()]
    os.makedirs(OUT, exist_ok=True)
    have = {f.rsplit(".", 1)[0] for f in os.listdir(OUT)}
    todo = [d for d in domains if force or d.replace(".", "-") not in have]
    report = common.read_json(REPORT)
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        for i, (domain, got, source) in enumerate(ex.map(fetch, todo), 1):
            slug = domain.replace(".", "-")
            for ext in EXTS:
                if os.path.exists(os.path.join(OUT, slug + ext)):
                    os.remove(os.path.join(OUT, slug + ext))
            if got:
                common.write_bytes(os.path.join(OUT, slug + got[0]), got[1])
            report[domain] = {"source": source, "file": slug + got[0] if got else None}
            print(f"[{i}/{len(todo)}] {domain}: {source if got else 'none'}", flush=True)
    common.write_json(REPORT, report, sort_keys=True)
    found = sum(1 for d in domains if report.get(d, {}).get("file"))
    print(f"{found}/{len(domains)} providers have a favicon; report in {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
