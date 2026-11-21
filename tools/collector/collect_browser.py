#!/usr/bin/env python3
"""Open every live provider's site in headless Chromium: screenshot, terms/privacy/source-code links, description.

usage: collect_browser.py [-h] [--chrome PATH] [--shots DIR] [--retake] [OUT_DIR] [DOMAIN ...]

  OUT_DIR defaults to tools/collector/out-browser. Per domain it writes OUT_DIR/<domain>.json; domains that
  already have one are skipped (delete the file to redo it). Without DOMAIN every live provider in
  data/providers/ is visited; a DOMAIN does not need to be in the data yet. The screenshot goes to
  site/assets/screenshots/<domain-with-dashes>.webp (1280x800 viewport, WebP, browser cache off); existing
  screenshots are kept unless --retake (or EMC_RETAKE=1).

The browser is found in this order: --chrome, $CHROMIUM, then chrome-headless-shell / headless_shell (EPEL
chromium-headless), chromium, chromium-browser, google-chrome-stable, google-chrome on PATH or in the usual
folders. A full browser is started with --headless=new. It is driven over the DevTools protocol through
--remote-debugging-pipe (no network port), so nothing besides Python 3 is needed. The browser keeps its own
User-Agent. A site that answers with a bot check is recorded as "blocked" and not retried; no screenshot is
kept for it. Exit code 1 when a domain failed with an error.

Terms and privacy links are picked from the rendered page's links with the scorer of collect_web.py; when
the home page has none, a few common paths (/terms, /privacy, ...) are tried and kept only if the page
reads like a policy. Source-code links (GitHub, GitLab, Codeberg, sourcehut) count only when the account
name matches the provider's name; other ones are listed under "code_other" for review.
"""
import argparse
import base64
import json
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import build_csv as bc  # noqa: E402
import collect_web as cw  # noqa: E402
import common  # noqa: E402
import emcdata  # noqa: E402

CHROMIUM = ""  # set by main(): the browser binary
HEADLESS_SHELL = False  # True for chrome-headless-shell / headless_shell, which need no --headless flag
BROWSER_NAMES = ("chrome-headless-shell", "headless_shell", "chromium", "chromium-browser", "google-chrome-stable",
                 "google-chrome")
BROWSER_PATHS = ("/usr/lib64/chromium-browser/headless_shell", "/usr/lib/chromium/headless_shell",
                 "/usr/lib/chromium-browser/headless_shell", "/opt/google/chrome/chrome",
                 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium")
SHOTS = os.path.join(ROOT, "site", "assets", "screenshots")
W, H = 1280, 800
LOAD_TIMEOUT = 30
SETTLE = 3.0
RESTART_EVERY = 25
RETAKE = os.environ.get("EMC_RETAKE") == "1"  # 1 = replace existing screenshots too (also --retake)
CODE_HOSTS = ("github.com", "gitlab.com", "codeberg.org", "git.sr.ht", "sr.ht", "framagit.org", "0xacab.org")
CODE_SKIP_OWNERS = {"login", "join", "about", "features", "pricing", "sponsors", "orgs", "topics", "explore",
                    "marketplace", "settings", "site", "contact", "security", "enterprise", "users", "search",
                    "roundcube", "mailcow", "snappymail", "the-djmaze", "rainloop", "nextcloud", "sogo", "inverse-inc",
                    "stalwartlabs", "zimbra", "mailu", "modoboa", "iredmail", "cypht-org", "jason-munro", "horde",
                    "hugo", "gohugoio", "wordpress", "twbs", "jquery", "fortawesome", "mozilla", "thunderbird",
                    "k9mail", "thundernest", "fairemail", "m66b", "deltachat", "deltachat-desktop", "keepassxc",
                    "simplex-chat", "matrix-org", "element-hq", "vector-im", "signalapp", "torproject", "tpo",
                    "letsencrypt", "certbot", "openpgpjs", "nicolo-ribaudo", "babel", "facebook", "google"}
PLAIN_UA = cw.PLAIN_UA
STRONG = {"tos": re.compile(r"^(terms( of (service|use))?|terms (and|&) conditions|tos|nutzungsbedingungen|agb|"
                            r"conditions g[ée]n[ée]rales( d'utilisation)?|user agreement|service terms|"
                            r"пользовательское соглашение|условия( использования)?)$", re.I),
          "privacy": re.compile(r"^(privacy( policy| notice| statement)?|datenschutz(erkl[äa]rung)?|"
                                r"politique de confidentialit[ée]|pol[íi]tica de privacidad|"
                                r"политика конфиденциальности)$", re.I)}

PAGE_JS = r"""(() => {
  const meta = (k) => { const m = document.querySelector(`meta[name="${k}"],meta[property="${k}"]`); return m ? (m.content || "").trim() : ""; };
  const links = [...document.querySelectorAll("a[href]")].slice(0, 1500).map(a => [a.href, ((a.innerText || a.textContent || a.title || a.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim()).slice(0, 120)]);
  const p = [...document.querySelectorAll("main p, article p, section p, p")].map(e => e.innerText.replace(/\s+/g, " ").trim()).filter(t => t.length > 60);
  return JSON.stringify({url: location.href, title: document.title, desc: meta("description"), og: meta("og:description"),
    tw: meta("twitter:description"), h1: (document.querySelector("h1") || {}).innerText || "",
    paras: p.slice(0, 3), text: (document.body ? document.body.innerText : "").slice(0, 12000),
    tail: (document.body ? document.body.innerText : "").slice(-3000),
    foot: ((document.querySelector("footer") || {}).innerText || "").slice(0, 3000),
    relme: [...document.querySelectorAll('a[rel~="me"],link[rel~="me"]')].map(a => a.href).slice(0, 50), links});
})()"""


def find_browser(explicit=""):
    """(path, is_headless_shell) of the first browser found, or (None, False)."""
    for cand in (explicit, os.environ.get("CHROMIUM", "")):
        if cand:
            path = shutil.which(cand) or (cand if os.access(cand, os.X_OK) else None)
            if not path:
                raise SystemExit(f"collect_browser.py: browser not found or not executable: {cand}")
            return path, "headless" in os.path.basename(path)
    for name in BROWSER_NAMES:
        path = shutil.which(name)
        if path:
            return path, "headless" in name
    for path in BROWSER_PATHS:
        if os.access(path, os.X_OK):
            return path, "headless" in os.path.basename(path)
    return None, False


class Browser:
    """Headless Chromium driven over --remote-debugging-pipe (fd 3 in, fd 4 out, NUL-separated JSON)."""

    def __init__(self):
        self.profile = tempfile.mkdtemp(prefix="emc-chromium-")
        to_r, to_w = os.pipe()
        from_r, from_w = os.pipe()

        def fds():  # in the child: our pipe ends become fd 3 and 4 (dup first, they may already be 3 or 4)
            a, b = os.dup(to_r), os.dup(from_w)
            os.dup2(a, 3)
            os.dup2(b, 4)

        self.proc = subprocess.Popen(
            [CHROMIUM] + ([] if HEADLESS_SHELL else ["--headless=new"]) +
            ["--remote-debugging-pipe", f"--user-data-dir={self.profile}", "--no-sandbox",
             "--disable-gpu", "--disable-dev-shm-usage", "--hide-scrollbars", "--mute-audio", "--no-first-run",
             "--disable-extensions", "--disable-background-networking", "--renderer-process-limit=1",
             "--disk-cache-size=1", f"--window-size={W},{H}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=fds, close_fds=False)
        os.close(to_r)
        os.close(from_w)
        self.w, self.r = to_w, from_r
        self.buf = b""
        self.next_id = 0
        self.events = []
        self.session = None
        target = self.call("Target.createTarget", url="about:blank")["targetId"]
        self.session = self.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        self.call("Page.enable")
        self.call("Network.enable")
        self.call("Network.setCacheDisabled", cacheDisabled=True)
        self.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)

    def _read(self, timeout):
        end = time.time() + timeout
        while b"\0" not in self.buf:
            left = end - time.time()
            if left <= 0 or not select.select([self.r], [], [], left)[0]:
                raise socket.timeout("no answer from chromium")
            chunk = os.read(self.r, 1 << 20)
            if not chunk:
                raise ConnectionError("chromium exited")
            self.buf += chunk
        msg, self.buf = self.buf.split(b"\0", 1)
        return json.loads(msg)

    def call(self, method, timeout=60, **params):
        self.next_id += 1
        mid = self.next_id
        msg = {"id": mid, "method": method, "params": params}
        if self.session:
            msg["sessionId"] = self.session
        os.write(self.w, json.dumps(msg).encode() + b"\0")
        while True:
            msg = self._read(timeout)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def wait_event(self, name, timeout):
        end = time.time() + timeout
        while True:
            for e in self.events:
                if e["method"] == name:
                    return e
            left = end - time.time()
            if left <= 0:
                return None
            try:
                msg = self._read(left)
            except socket.timeout:
                return None
            if "method" in msg:
                self.events.append(msg)

    def open(self, url):
        self.events.clear()
        status = {}
        res = self.call("Page.navigate", url=url)
        if res.get("errorText"):
            return {"error": res["errorText"]}
        loaded = self.wait_event("Page.loadEventFired", LOAD_TIMEOUT) is not None
        for e in self.events:  # main document response status
            if e["method"] == "Network.responseReceived" and e["params"].get("type") == "Document":
                resp = e["params"]["response"]
                status.setdefault("status", resp.get("status"))
                hdr = {k.lower(): v for k, v in (resp.get("headers") or {}).items()}
                if hdr.get("onion-location"):
                    status.setdefault("onion_location", hdr["onion-location"].strip())
        time.sleep(SETTLE)
        info = json.loads(self.call("Runtime.evaluate", expression=PAGE_JS, returnByValue=True)["result"]["value"])
        info.update(status, loaded=loaded)
        return info

    def shot(self):
        res = self.call("Page.captureScreenshot", format="webp", quality=72,
                        clip={"x": 0, "y": 0, "width": W, "height": H, "scale": 1})
        return base64.b64decode(res["data"])

    def close(self):
        for fd in (self.w, self.r):
            try:
                os.close(fd)
            except OSError:
                pass
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


def label(domain):
    return re.sub(r"[^a-z0-9]", "", domain.split(".")[0].lower())


def code_links(links, domain):
    mine, other = [], []
    lab = label(domain)
    for href, _ in links:
        u = urllib.parse.urlparse(href)
        host = (u.hostname or "").lower().removeprefix("www.")
        if host not in CODE_HOSTS:
            continue
        parts = [p for p in u.path.split("/") if p]
        if not parts or parts[0].lower() in CODE_SKIP_OWNERS:
            continue
        owner = re.sub(r"[^a-z0-9]", "", parts[0].lower().lstrip("~"))
        url = f"https://{host}/" + "/".join(parts[:2])
        ok = len(lab) >= 3 and (lab in owner or owner in lab and len(owner) >= 4)
        (mine if ok else other).append(url)
    dedup = lambda xs: list(dict.fromkeys(xs))  # noqa: E731
    mine = dedup(mine)
    # an account link ("github.com/acme") beats a single repository
    mine.sort(key=lambda x: (x.count("/"), len(x)))
    return mine, dedup(other)


def get_text(url):
    if not cw.ROBOTS.allowed(url):
        return None, None
    req = urllib.request.Request(url, headers={"User-Agent": PLAIN_UA, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=cw.CTX_NOVERIFY) as r:
            if r.status != 200 or "html" not in (r.headers.get("Content-Type") or "html"):
                return None, None
            body = r.read(cw.MAX_BYTES)
            final = r.geturl()
        page = cw.Page()
        page.feed(body.decode("utf-8", "replace"))
        return final, page.title + "\n" + " ".join(page.parts)
    except Exception:  # noqa: BLE001  any failure just means "not found here"
        return None, None


def fallback(base, cat):
    for path in cw.FALLBACK[cat]:
        final, text = get_text(urllib.parse.urljoin(base, path))
        if final and text and cw.CHECK[cat].search(text[:3000]) and not cw.CHALLENGE.search(text[:500]) \
                and urllib.parse.urlparse(final).path.strip("/"):
            return final
    return None


# ---------------------------------------------------------------- social accounts, imprint, operator
# (network, host regex or None for any host, path regex). Checked in order; the first match wins.
SOCIAL = [
    ("X", r"(?:www\.|mobile\.)?(?:twitter|x)\.com",
     r"/(?!share\b|intent\b|home\b|search\b|hashtag\b|i/|privacy\b|tos\b|login\b|signup\b|explore\b)@?([A-Za-z0-9_]{1,15})/?"),
    ("Bluesky", r"bsky\.app", r"/profile/([^/]+)/?"),
    ("Telegram", r"(?:www\.)?(?:t\.me|telegram\.me)", r"/(?!share\b|joinchat\b|addstickers\b|\+|s/)([A-Za-z0-9_]{4,})/?"),
    ("Reddit", r"(?:www\.|old\.)?reddit\.com", r"/(?:r|u|user)/([A-Za-z0-9_]+)/?"),
    ("Facebook", r"(?:www\.|m\.|[a-z]{2}-[a-z]{2}\.)?facebook\.com",
     r"/(?!sharer|share\b|dialog\b|plugins\b|tr\b|login\b|privacy\b|policies\b|help\b|groups/?$)([A-Za-z0-9.\-]{3,})/?"),
    ("Instagram", r"(?:www\.)?instagram\.com", r"/(?!p/|explore\b|accounts\b|reel/)([A-Za-z0-9_.]{2,})/?"),
    ("LinkedIn", r"(?:[a-z]{2,3}\.|www\.)?linkedin\.com", r"/(?:company|in|school|showcase)/([^/]+)/?"),
    ("YouTube", r"(?:www\.|m\.)?youtube\.com", r"/(?:(@[^/]+)|channel/([^/]+)|c/([^/]+)|user/([^/]+))/?"),
    ("Threads", r"(?:www\.)?threads\.(?:net|com)", r"/@([^/]+)/?"),
    ("TikTok", r"(?:www\.)?tiktok\.com", r"/@([^/]+)/?"),
    ("Discord", r"(?:www\.)?(?:discord\.gg|discord\.com)", r"/(?:invite/)?([A-Za-z0-9\-]{4,})/?"),
    ("Matrix", r"matrix\.to", r"/?"),
    ("Pixelfed", r"pixelfed\.\w+", r"/([A-Za-z0-9_.]+)/?"),
    ("Mastodon", None, r"/@([A-Za-z0-9_.]+)/?"),
]
NOT_FEDI = re.compile(r"(?:^|\.)(?:medium\.com|youtube\.com|tiktok\.com|threads\.(?:net|com)|pinterest\.\w+|"
                      r"substack\.com|linktr\.ee|github\.com|gitlab\.com|codeberg\.org|npmjs\.com|figma\.com|"
                      r"dribbble\.com|behance\.net|patreon\.com|ko-fi\.com|liberapay\.com|opencollective\.com)$")
IMPRINT_TEXT = re.compile(r"^(impressum|imprint|legal notice|legal information|legal info|legal|mentions l[ée]gales|"
                          r"aviso legal|note legali|colof[oó]n|kolofon|site notice|company information|"
                          r"wettelijke informatie|изготовитель|правовая информация)$", re.I)
ABOUT_TEXT = re.compile(r"^(about|about us|who we are|über uns|ueber uns|chi siamo|qui sommes-nous|"
                        r"à propos|a propos|quiénes somos|sobre nosotros|o nas|о нас|om oss|over ons)$", re.I)
IMPRINT_PATHS = ("/impressum", "/imprint", "/legal-notice", "/legal", "/en/imprint", "/de/impressum",
                 "/mentions-legales")
IMPRINT_CHECK = re.compile(r"impressum|imprint|legal notice|mentions l[ée]gales|registered office|handelsregister|"
                           r"registergericht|company (?:number|registration)|registration number|ust-?id|vat (?:id|no|number)|"
                           r"geschäftsführer|managing director|kvk|chamber of commerce|angaben gem[äa]ß", re.I)
COPYRIGHT_LINE = re.compile(r"(?:©|\(c\)|copyright)[^\n]{2,120}", re.I)


def social_links(links, relme, domain):
    lab = label(domain)
    out = {}
    for href in [h for h, _ in links] + list(relme):
        if href.startswith("xmpp:"):
            out.setdefault(href.split("?")[0], {"net": "XMPP", "url": href.split("?")[0], "handle": href[5:].split("?")[0]})
            continue
        u = urllib.parse.urlparse(href)
        if u.scheme not in ("http", "https") or not u.hostname:
            continue
        host = u.hostname.lower()
        if cw.site_key(host) == cw.site_key(domain):
            continue  # the provider's own site
        for net, hrx, prx in SOCIAL:
            if hrx and not re.fullmatch(hrx, host):
                continue
            if net == "Mastodon" and (NOT_FEDI.search(host) or href not in relme and not re.search(
                    r"mastodon|mstdn|toot|social|fosstodon|chaos\.social|infosec\.exchange|fediverse|"
                    r"masto|troet|todon|mas\.to|hachyderm|norden|ioc\.exchange", host)):
                continue
            if net == "Matrix":
                frag = urllib.parse.unquote(u.fragment.lstrip("/"))
                if not re.fullmatch(r"[#@!][^:\s]+:[\w.\-]+", frag):
                    continue
                url = f"https://matrix.to/#/{frag}"
                out.setdefault(url, {"net": net, "url": url, "handle": frag})
                break
            m = re.fullmatch(prx, u.path or "/")
            if not m:
                continue
            handle = next((g for g in m.groups() if g), "") if m.groups() else ""
            if net == "Mastodon":
                handle = f"@{handle}@{host}"
            url = f"https://{host}{u.path.rstrip('/')}"
            out.setdefault(url.lower(), {"net": net, "url": url, "handle": handle})
            break
    res = list(out.values())
    for r in res:
        h = re.sub(r"[^a-z0-9]", "", r["handle"].lstrip("@#!").split("@")[0].split(":")[0].lower())
        r["match"] = bool(len(lab) >= 3 and h and (lab in h or (h in lab and len(h) >= 4)))
    return res


def page_link(links, rx, domain, host):
    for href, txt in links:
        u = urllib.parse.urlparse(href)
        if rx.match(txt.strip()) and u.scheme in ("http", "https") and \
                cw.site_key(u.hostname or "") in (cw.site_key(host), cw.site_key(domain)):
            return href.split("#")[0]
    return None


def page_text(br, url):
    """Text of a policy / imprint page: plain HTTP first, the browser when that gives almost nothing."""
    final, text = get_text(url)
    if text and len(text) > 600 and not cw.CHALLENGE.search(text[:500]):
        return final, text
    if not cw.ROBOTS.allowed(url):
        return final, text or ""
    try:
        info = br.open(url)
    except Exception:  # noqa: BLE001
        return final, text or ""
    if "error" in info:
        return final, text or ""
    return info.get("url"), (info.get("title", "") + "\n" + info.get("text", ""))


def operator(home_text, texts):
    """Operator name from copyright lines and company names (build_csv.pick_owner), plus the imprint country."""
    owner = bc.pick_owner(home_text, texts.get("imprint", ""), texts.get("tos", ""), texts.get("privacy", ""), [])
    cc = bc.imprint_country(texts.get("imprint", "")) if texts.get("imprint") else ""
    return owner, cc


def process(br, domain, out_dir):
    rec = {"domain": domain, "when": time.strftime("%Y-%m-%d")}
    info = None
    if not cw.ROBOTS.allowed(f"https://{domain}/"):
        rec["state"] = "disallowed"
        rec["error"] = "disallowed by robots.txt"
        return rec
    for url in (f"https://{domain}/", f"http://{domain}/"):
        info = br.open(url)
        if "error" not in info:
            break
    if "error" in info:
        rec["state"] = "unreachable"
        rec["error"] = info["error"]
        return rec
    text = (info.get("title", "") + "\n" + info.get("text", ""))
    rec.update(url=info["url"], status=info.get("status"), title=info.get("title", "").strip())
    if info["url"].startswith("chrome-error://"):
        rec["state"] = "unreachable"
        return rec
    if cw.CHALLENGE.search(text[:600]) and len(info.get("text", "")) < 1500:
        rec["state"] = "blocked"
        return rec
    if cw.MAINTENANCE.search(rec["title"] + " " + info.get("h1", "")) and len(info.get("text", "")) < 3000:
        rec["state"] = "maintenance"
    elif cw.PARKED.search(text[:1500]):
        rec["state"] = "parked"
    elif (info.get("status") or 200) >= 400:
        rec["state"] = f"http {info['status']}"
    elif len(info.get("text", "").strip()) < 20:
        rec["state"] = "blank"
    else:
        rec["state"] = "ok"
    shot_path = os.path.join(SHOTS, domain.replace(".", "-") + ".webp")
    if rec["state"] == "ok" and (RETAKE or not os.path.exists(shot_path)):
        img = br.shot()
        common.write_bytes(shot_path, img)
        rec["screenshot"] = len(img)
    elif os.path.exists(shot_path):
        rec["screenshot"] = "kept"
    picked = cw.pick_links([tuple(x) for x in info.get("links", [])], info["url"])
    host = urllib.parse.urlparse(info["url"]).hostname or domain
    for cat, rx in STRONG.items():  # a link titled exactly "Privacy policy" / "Terms" on the same site wins
        for href, txt in info.get("links", []):
            u = urllib.parse.urlparse(href)
            if rx.match(txt) and u.scheme in ("http", "https") and \
                    cw.site_key(u.hostname or "") in (cw.site_key(host), cw.site_key(domain)):
                picked[cat] = {"url": href.split("#")[0], "text": txt}
                break
    for cat in ("tos", "privacy"):
        if cat in picked:
            rec[cat] = picked[cat]["url"]
        elif rec["state"] == "ok":
            found = fallback(info["url"], cat)
            if found:
                rec[cat] = found
                rec[cat + "_via"] = "path"
    mine, other = code_links(info.get("links", []), domain)
    if mine:
        rec["code"] = mine[0]
        rec["code_all"] = mine
    if other:
        rec["code_other"] = other[:10]
    # social accounts, onion address, imprint and operator
    links = info.get("links", [])
    rec["social"] = social_links(links, info.get("relme", []), domain)
    onions = [h for h, _ in links if re.match(r"https?://[a-z2-7]{56}\.onion(?:/|$)", h)]
    if info.get("onion_location"):
        onions.insert(0, info["onion_location"])
    if onions:
        u = urllib.parse.urlparse(onions[0])
        rec["onion"] = f"{u.scheme}://{u.hostname}/"
        rec["onion_all"] = list(dict.fromkeys(onions))[:5]
        rec["onion_via"] = "header" if info.get("onion_location") else "link"
    if rec["state"] == "ok":
        imprint = page_link(links, IMPRINT_TEXT, domain, host)
        if not imprint:
            for path in IMPRINT_PATHS:
                final, text = get_text(urllib.parse.urljoin(info["url"], path))
                if final and text and IMPRINT_CHECK.search(text[:4000]) and urllib.parse.urlparse(final).path.strip("/"):
                    imprint = final
                    break
        if imprint:
            rec["imprint"] = imprint
        about = page_link(links, ABOUT_TEXT, domain, host)
        if about:
            rec["about"] = about
        texts = {}
        for cat in ("imprint", "tos", "privacy", "about"):
            if rec.get(cat):
                _, texts[cat] = page_text(br, rec[cat])
        if texts.get("about"):
            texts["imprint"] = texts.get("imprint", "") + "\n" + texts["about"][:6000]
        home_text = "\n".join((info.get("text", ""), info.get("foot", ""), info.get("tail", "")))
        rec["owner_guess"], rec["imprint_cc"] = operator(home_text, texts)
        rec["copyright"] = list(dict.fromkeys(m.group(0).strip() for m in COPYRIGHT_LINE.finditer(
            info.get("foot", "") + "\n" + info.get("tail", ""))))[:3]
        rec["text_sizes"] = {k: len(v or "") for k, v in texts.items()}
    desc = info.get("desc") or info.get("og") or info.get("tw") or ""
    rec["desc"] = desc.strip()
    rec["h1"] = (info.get("h1") or "").strip()[:200]
    rec["paras"] = info.get("paras", [])
    return rec


def main(argv=None):
    global CHROMIUM, HEADLESS_SHELL, SHOTS, RETAKE
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("out_dir", nargs="?", default=os.path.join(HERE, "out-browser"))
    ap.add_argument("domains", nargs="*", metavar="DOMAIN")
    ap.add_argument("--chrome", default="", metavar="PATH", help="browser binary")
    ap.add_argument("--shots", default=SHOTS, metavar="DIR", help="screenshot folder (default: %(default)s)")
    ap.add_argument("--retake", action="store_true", help="replace existing screenshots")
    args = ap.parse_args(argv)
    try:
        only = [common.normalize_domain(d) for d in args.domains]
    except ValueError as e:
        ap.error(str(e))
    CHROMIUM, HEADLESS_SHELL = find_browser(args.chrome)
    if not CHROMIUM:
        raise SystemExit("collect_browser.py: no Chromium or Chrome found; install one or pass --chrome PATH")
    SHOTS, RETAKE = args.shots, RETAKE or args.retake
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    recs = emcdata.load_all()  # EMC_DATA_DIR picks another folder of records
    dead = {r["domain"].lower() for r in recs if r.get("dead")}
    domains = only or [r["domain"].lower() for r in recs]
    todo = [d for d in dict.fromkeys(domains) if d not in dead
            and not os.path.exists(os.path.join(out_dir, d + ".json"))]
    print(f"{len(todo)} to visit ({len(dead & set(domains))} struck out skipped); browser {CHROMIUM}", flush=True)
    br, done, failed = None, 0, 0
    for d in todo:
        if br is None or done % RESTART_EVERY == 0:
            if br:
                br.close()
            try:
                br = Browser()
            except (OSError, ConnectionError, RuntimeError, socket.timeout) as e:
                raise SystemExit(f"collect_browser.py: {CHROMIUM} did not start: {type(e).__name__}: {e}")
        t0 = time.time()
        try:
            rec = process(br, d, out_dir)
        except Exception as e:  # noqa: BLE001  restart the browser and move on
            rec = {"domain": d, "state": "error", "error": f"{type(e).__name__}: {e}"}
            failed += 1
            try:
                br.close()
            except Exception:  # noqa: BLE001
                pass
            br = None
        done += 1
        common.write_json(os.path.join(out_dir, d + ".json"), rec)
        print(f"[{done}/{len(todo)}] {d}: {rec.get('state')} {time.time() - t0:.0f}s "
              f"tos={'y' if rec.get('tos') else '-'} priv={'y' if rec.get('privacy') else '-'} "
              f"code={rec.get('code', '-')}", flush=True)
    if br:
        br.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
