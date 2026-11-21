#!/usr/bin/env python3
"""Fetch each service's homepage plus its terms / privacy / imprint / pricing / signup / domains pages.

usage: collect_web.py DOMAINS_TSV OUT_DIR

Per domain it writes OUT_DIR/<domain>/meta.json, <page>.txt (visible text) and <page>.html (raw,
capped). Domains that already have meta.json are skipped. Downloaded content is only parsed as text.

How hard it tries:
  - every redirect hop is followed by hand and recorded (HTTP 3xx, <meta refresh>, short JS redirects)
  - https/http with and without www; bot checks are respected (the site is not retried, see below)
  - missing terms/privacy pages are looked up in sitemap.xml/robots.txt and at common paths
  - webmail is looked for at mail./webmail./email. and /mail, /webmail
  - pages listed for the domain in sources.json (checked by hand) are used first; several URLs for one
    category are joined into one text
  - when the site is unreachable, blocked, parked or under maintenance, the newest usable Wayback
    Machine copy is used instead (marked in meta.json as "archive")
"""
import concurrent.futures as cf
import gzip
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser

DOMAINS_TSV, OUT = sys.argv[1:3]
# hand-picked page URLs per domain and category (a string or a list), used before anything found automatically
SOURCES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.json")
SOURCES = {k: v for k, v in json.load(open(SOURCES_PATH, encoding="utf-8")).items() if not k.startswith("_")} \
    if os.path.exists(SOURCES_PATH) else {}
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
# archive.org is asked with an honest, self-describing User-Agent
PLAIN_UA = "eMailCompare-research/1.0 (+https://github.com/cyberanchor/eMailCompare)"
MAX_BYTES = 2_000_000
TIMEOUT = 25
MAX_HOPS = 10
SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
BLOCK_TAGS = {"br", "p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
              "footer", "header", "nav", "ul", "ol", "table", "form", "label", "option", "dt", "dd", "blockquote"}

CAT_WORDS = {
    "tos": ["terms", "tos", "conditions", "agb", "nutzungsbedingungen", "condizioni", "termini", "cgu", "cgv",
            "terminos", "términos", "condiciones", "termos", "voorwaarden", "regulamin", "podminky", "podmínky",
            "acceptable-use", "acceptable use", "aup", "rules", "regeln", "regolamento", "feltetel", "feltétel",
            "vilkar", "vilkår", "villkor", "skilmal", "skilmál", "όροι", "οροι", "uvjeti", "user agreement",
            "service agreement", "policy", "policies", "legal", "условия", "правила", "соглашение", "kullanım",
            "koşullar", "תנאי שימוש", "käyttöehdot", "betingelser"],
    "privacy": ["privacy", "datenschutz", "privacidad", "privacidade", "riservatezza", "confidentialit", "donnees",
                "données", "privacybeleid", "privacyverklaring", "prywatno", "ochrana", "ochrona", "gdpr", "dsgvo",
                "rgpd", "adatvedelem", "adatvédelem", "persondata", "integritet", "persónuvernd", "απόρρητο",
                "privatnost", "informativa", "privadesa", "privacitat", "פרטיות", "конфиденциальн", "gizlilik",
                "privatliv", "personvern", "tietosuoja"],
    "imprint": ["impressum", "imprint", "legal notice", "legal-notice", "mentions", "aviso legal", "aviso-legal",
                "about", "ueber", "über", "uber-uns", "chi siamo", "chi-siamo", "qui sommes", "quienes", "quiénes",
                "who we are", "company", "contact", "kontakt", "contatti"],
    "pricing": ["pricing", "prices", "price", "plans", "plan", "premium", "preise", "tarif", "prezzi", "abbonament",
                "upgrade", "subscription", "subscribe", "precios", "preços", "precos", "cennik", "ceny", "priser",
                "árak", "donate", "donation", "spende", "spenden", "membership", "pro"],
    "signup": ["signup", "sign-up", "sign_up", "sign up", "register", "registr", "join", "create-account",
               "create account", "new account", "get started", "iscriviti", "inscription", "inscri", "crear cuenta",
               "crear-cuenta", "criar conta", "criar-conta", "konto erstellen", "konto-erstellen", "account/new",
               "request an account", "request account", "apply", "załóż", "zaloz", "rejestracja"],
    "domains": ["domains", "domain list", "our domains", "available domains", "aliases"],
    # the provider's own e-mail product page on multi-service sites (e.g. egali.top/email)
    "service": ["email", "e-mail", "mailbox", "webmail", "mail"],
}
WEAK = {"policy", "policies", "legal", "about", "company", "contact", "kontakt", "contatti", "plan", "pro", "apply",
        "join", "mentions"}
FALLBACK = {"tos": ["/terms", "/tos", "/terms-of-service", "/terms.html", "/legal", "/agb", "/rules", "/aup"],
            "privacy": ["/privacy", "/privacy-policy", "/privacy.html", "/legal/privacy", "/datenschutz"],
            "imprint": ["/impressum", "/imprint", "/about", "/contact"],
            "pricing": ["/pricing", "/plans", "/prices", "/price"],
            "signup": ["/signup", "/register", "/join"]}
CHECK = {"tos": re.compile(r"terms|conditions|agb|bedingungen|condizioni|regulamin|rules|acceptable use", re.I),
         "privacy": re.compile(r"privacy|datenschutz|personal data|gdpr|privacidad|riservatezza", re.I),
         "imprint": re.compile(r"impressum|imprint|angaben gem|verantwortlich|about us|contact|kontakt", re.I),
         "pricing": re.compile(r"pric|plans?\b|tarif|preis|€|\$|per month|/month|/year", re.I),
         "signup": re.compile(r"sign ?up|register|registr|create (?:an |your )?account|username|password", re.I)}
# big platforms whose terms/privacy pages are never the mail provider's own
THIRD_PARTY = re.compile(r"(^|\.)(google\.[a-z.]+|facebook\.com|fb\.com|twitter\.com|x\.com|instagram\.com|"
                         r"linkedin\.com|youtube\.com|apple\.com|microsoft\.com|tiktok\.com|pinterest\.[a-z.]+|"
                         r"wikipedia\.org|cloudflare\.com|mozilla\.org|creativecommons\.org|w3\.org|gnu\.org|t\.me|"
                         r"whatsapp\.com|archive\.org)$", re.I)
SAME_SITE_ONLY = {"pricing", "signup", "domains", "service"}
SKIP_EXT = re.compile(r"\.(pdf|png|jpe?g|gif|svg|webp|zip|gz|exe|apk|dmg|msi|asc|sig|xml|rss|ico|mp4|mp3)(\?|#|$)", re.I)
AT_DOMAIN = re.compile(r"(?<![A-Za-z0-9._%+-])@((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})\b", re.I)
OPT_DOMAIN = re.compile(r"^@?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})$", re.I)
ONION = re.compile(r"\b[a-z2-7]{56}\.onion\b", re.I)
CHALLENGE = re.compile(r"just a moment|attention required|ddos-guard|checking your browser|access denied|"
                       r"please enable js|enable javascript and cookies|captcha|are you a robot|403 forbidden|"
                       r"making sure you.?re not a bot|verifying you are human|bot verification|one more step", re.I)
MAINTENANCE = re.compile(r"maintenance|outage|status update|coming soon|coming q\d|under construction|wartung|"
                         r"under development|come back soon", re.I)
PARKED = re.compile(r"domain (?:is |may be )?for sale|buy this domain|this domain (?:is|has been) (?:parked|registered)|"
                    r"parked (?:free|domain)|domain parking|sedo|dan\.com|afternic|hugedomains|"
                    r"account (?:has been )?suspended|default web page|welcome to nginx|apache2? (?:ubuntu |debian )?"
                    r"default page|it works!|index of /|test page for|not configured on this server|"
                    r"no (?:web ?)?site (?:is )?configured|site not found", re.I)
MAIL_WORDS = re.compile(r"e-?mail|\bmail|correo|posta|courriel|почт|e-?post|επιστολ|דואר|webmail|imap|smtp", re.I)
WEBMAIL_SOFTWARE = [("Roundcube", r"roundcube|rcmloginuser"), ("SOGo", r"\bsogo\b"), ("SnappyMail", r"snappymail"),
                    ("RainLoop", r"rainloop"), ("Horde", r"\bhorde\b|imp_login"), ("Zimbra", r"zimbra"),
                    ("SquirrelMail", r"squirrelmail"), ("AfterLogic", r"afterlogic"), ("Mailcow", r"mailcow"),
                    ("Open-Xchange", r"open-xchange|ox app suite|appsuite"), ("Cypht", r"cypht"),
                    ("Stalwart", r"stalwart"), ("Kolab", r"kolab"), ("Mailpile", r"mailpile"),
                    ("Nextcloud", r"nextcloud"), ("Tutanota", r"tutanota"), ("iRedMail", r"iredmail"),
                    ("Mailu", r"\bmailu\b"), ("Modoboa", r"modoboa"), ("Zoho", r"zoho"), ("Gmail", r"gmail")]
JS_REDIRECT = re.compile(r"(?:window|document|top|self)?\.?location(?:\.href)?\s*=\s*[\"']([^\"'#][^\"']*)[\"']|"
                         r"location\.(?:replace|assign)\(\s*[\"']([^\"']+)[\"']\s*\)", re.I)

CTX_VERIFY = ssl.create_default_context()
CTX_NOVERIFY = ssl._create_unverified_context()
# archive.org refuses connections for a while from clients that send too many requests: space them out
WAYBACK_LOCK, WAYBACK_GAP, WAYBACK_NEXT = threading.Lock(), 2.5, [0.0]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return 3xx responses as they are, so every hop can be recorded and followed by hand."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENERS = {ctx: urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ctx))
           for ctx in (CTX_VERIFY, CTX_NOVERIFY)}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.meta, self.links, self.options, self.parts = "", {}, [], [], []
        self.refresh, self.password = "", False
        self._skip, self._title, self._a, self._opt = 0, False, None, None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key in ("description", "og:description", "og:title", "og:site_name", "twitter:description",
                       "generator") and a.get("content"):
                self.meta.setdefault(key, a["content"].strip())
            if a.get("http-equiv", "").lower() == "refresh" and a.get("content") and not self._skip:  # not <noscript>
                self.refresh = a["content"]
        elif tag == "a" and a.get("href"):
            self._a = [a["href"].strip(), ""]
        elif tag == "option":
            self._opt = [a.get("value", ""), ""]
        elif tag == "input" and a.get("type", "").lower() == "password":
            self.password = True
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._title = False
        elif tag == "a" and self._a is not None:
            self.links.append((self._a[0], " ".join(self._a[1].split())))
            self._a = None
        elif tag == "option" and self._opt is not None:
            self.options.append((self._opt[0].strip(), " ".join(self._opt[1].split())))
            self._opt = None
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._title:
            self.title += data
            return
        if self._a is not None:
            self._a[1] += data
        if self._opt is not None:
            self._opt[1] += data
        self.parts.append(data)

    def text(self):
        out, blank = [], False
        for line in "".join(self.parts).split("\n"):
            line = " ".join(line.split())
            if line:
                out.append(line)
                blank = False
            elif not blank:
                out.append("")
                blank = True
        return "\n".join(out).strip()


def parse(html):
    p = Page()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    return p


def decode(body, headers):
    m = re.search(r"charset=([\w-]+)", headers.get("Content-Type", "") or "", re.I)
    enc = m.group(1) if m else None
    if not enc:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
        enc = m.group(1).decode() if m else "utf-8"
    try:
        return body.decode(enc, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def fetch_once(url, ua, timeout):
    """One request, no redirect following. Falls back to an unverified TLS context on certificate errors."""
    err = None
    for ctx in (CTX_VERIFY, CTX_NOVERIFY):
        req = urllib.request.Request(url, headers={
            "User-Agent": ua, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate"})
        try:
            resp = OPENERS[ctx].open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            resp = e
        except urllib.error.URLError as e:
            err = f"{type(e.reason).__name__}: {e.reason}"[:200]
            if isinstance(e.reason, (ssl.SSLError, ssl.CertificateError)) and ctx is CTX_VERIFY:
                continue
            return {"error": err}
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:200]
            if isinstance(e, (ssl.SSLError, ssl.CertificateError)) and ctx is CTX_VERIFY:
                continue
            return {"error": err}
        status = getattr(resp, "code", None)
        loc = resp.headers.get("Location") if resp.headers else None
        if status and 300 <= status < 400 and loc:
            try:
                resp.close()
            except Exception:
                pass
            return {"status": status, "location": urllib.parse.urljoin(url, loc.strip()),
                    "tls_unverified": ctx is CTX_NOVERIFY}
        try:
            body = resp.read(MAX_BYTES)
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            if "gzip" in enc:
                body = gzip.decompress(body)
            elif "deflate" in enc:
                try:
                    body = zlib.decompress(body)
                except zlib.error:
                    body = zlib.decompress(body, -zlib.MAX_WBITS)
        except Exception as e:
            return {"status": status, "error": f"read {type(e).__name__}: {e}"[:200]}
        finally:
            try:
                resp.close()
            except Exception:
                pass
        return {"status": status, "ctype": resp.headers.get("Content-Type", ""), "html": decode(body, resp.headers),
                "tls_unverified": ctx is CTX_NOVERIFY}
    return {"error": err}


def client_redirect(html, base):
    """Target of a <meta refresh> or a short JavaScript redirect page, if any."""
    p = parse(html)
    m = re.match(r"\s*(\d+)?\s*[;,]?\s*url\s*=\s*['\"]?([^'\"]+)", p.refresh or "", re.I)
    if m and int(m.group(1) or 0) <= 10:
        return urllib.parse.urljoin(base, m.group(2).strip()), "meta-refresh"
    if len(p.text()) < 400:
        m = JS_REDIRECT.search(html)
        if m:
            target = (m.group(1) or m.group(2) or "").strip()
            if target and not target.startswith(("javascript:", "data:")):
                return urllib.parse.urljoin(base, target), "javascript"
    return None, None


def fetch(url, ua=UA, timeout=TIMEOUT):
    """GET with every redirect hop recorded in "chain": HTTP 3xx, <meta refresh> and short JS redirects."""
    chain, cur, unverified, seen = [], url, False, {url}
    r = {}
    for _ in range(MAX_HOPS):
        r = fetch_once(cur, ua, timeout)
        unverified = unverified or r.get("tls_unverified", False)
        hop = {"url": cur, "status": r.get("status")}
        if r.get("error"):
            hop["error"] = r["error"]
        chain.append(hop)
        nxt, kind = r.get("location"), "http"
        if not nxt and r.get("html"):
            nxt, kind = client_redirect(r["html"], cur)
        if not nxt or urllib.parse.urlparse(nxt).scheme not in ("http", "https"):
            break
        if nxt in seen:
            hop["to"], hop["loop"] = nxt, True
            break
        hop["to"], hop["kind"] = nxt, kind
        seen.add(nxt)
        cur = nxt
    out = dict(r)
    out.pop("location", None)
    out.update(url=url, final_url=cur, chain=chain, tls_unverified=unverified)
    return out


def site_key(host):
    labels = (host or "").lower().split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in ("co", "com", "org", "net", "ac", "gov", "edu"):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def score(href, text):
    hay = (urllib.parse.unquote(href) + " " + text).lower()
    tokens = set(re.split(r"[^\w]+", hay))
    out = {}
    for cat, words in CAT_WORDS.items():
        s = 0
        for w in words:
            hit = (w in tokens) if len(w) <= 4 else (w in hay)
            if hit:
                s += 1 if w in WEAK else 3
        if s:
            out[cat] = s
    return out


def pick_links(links, base_url):
    host = urllib.parse.urlparse(base_url).hostname
    best = {}
    for href, text in links:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")) or SKIP_EXT.search(href):
            continue
        url = urllib.parse.urljoin(base_url, href).split("#")[0]
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or (url.rstrip("/") == base_url.rstrip("/")):
            continue
        if u.hostname and THIRD_PARTY.search(u.hostname):
            continue
        sc = score(u.path + "?" + u.query, text)
        if not sc:
            continue
        top = max(sc.values())
        same = site_key(u.hostname) == site_key(host)
        # long slugs and news/blog sections are articles, not policy pages
        penalty = 3 if len(u.path) > 60 else 0
        if re.search(r"/(?:category|categories|tag|tags|news|article|articles|blog|post|posts|forums?|threads?)/|"
                     r"viewtopic|showthread", u.path, re.I):
            penalty += 8
        for cat, s in sc.items():
            if s < top or (cat in SAME_SITE_ONLY and not same):
                continue
            rank = (s + (2 if same else 0) - penalty, -len(url))
            if rank[0] <= 0:
                continue
            if cat not in best or rank > best[cat][0]:
                best[cat] = (rank, url, text[:80])
    return {cat: {"url": v[1], "text": v[2]} for cat, v in best.items()}


def good(r):
    return bool((r.get("html") or "").strip()) and bool(r.get("status")) and r["status"] < 400


def resolves(host):
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return True
    except Exception:
        return False


def page_state(r):
    """'ok', or why the page is not the provider's real website."""
    if not r.get("html", "").strip():
        return "unreachable"
    p = parse(r["html"])
    title, txt = " ".join(p.title.split()), p.text()
    head = title + "\n" + txt[:1500]
    if CHALLENGE.search(title) or (r.get("status") in (400, 403, 429, 503) and CHALLENGE.search(head)):
        return "blocked"
    if not r.get("status") or r["status"] >= 400:
        return "http-error"
    if PARKED.search(head):
        return "parked"
    if MAINTENANCE.search(title) or (len(txt) < 600 and MAINTENANCE.search(txt)):
        return "maintenance"
    return "ok"


# ---------------------------------------------------------------- Wayback Machine
def wayback_get(url):
    """Rate-limited archive.org request; on a refused connection wait and retry, then over plain http."""
    r = {}
    for attempt in range(4):
        with WAYBACK_LOCK:
            time.sleep(max(0.0, WAYBACK_NEXT[0] - time.time()))
            WAYBACK_NEXT[0] = time.time() + WAYBACK_GAP
        r = fetch(url, ua=PLAIN_UA, timeout=60)
        if "refused" not in (r.get("error") or "") and r.get("status") != 429:
            return r
        time.sleep(15 * (attempt + 1))
        if attempt >= 1 and url.startswith("https://"):
            url = "http://" + url[len("https://"):]
    return r


def wayback_snapshots(domain):
    """Newest-first (timestamp, original) homepage captures with HTTP 200, for the domain and www."""
    out = []
    for host in (domain, "www." + domain):
        q = urllib.parse.urlencode([("url", host + "/"), ("output", "json"), ("filter", "statuscode:200"),
                                    ("filter", "mimetype:text/html"), ("fl", "timestamp,original"),
                                    ("collapse", "timestamp:8"), ("limit", "-12")])
        r = wayback_get("https://web.archive.org/cdx/search/cdx?" + q)
        try:
            rows = json.loads(r.get("html") or "[]")[1:]
        except ValueError:
            rows = []
        out += [(ts, orig) for ts, orig in rows]
    return sorted(set(out), reverse=True)


def wayback_url(ts, url, raw=True):
    return f"https://web.archive.org/web/{ts}{'id_' if raw else ''}/{url}"


def binary(html):
    """True when a body could not be decoded (e.g. zstd or brotli, which Python's standard library lacks)."""
    head = (html or "")[:3000]
    return bool(head) and (head.count("\ufffd") > len(head) * 0.03 or "\x00" in head)


ARCHIVE_PREFIX = re.compile(r"(?:https?:)?(?://web\.archive\.org)?/web/\d{14}(?:[a-z]{2}_)?/(?=https?:)")


def wayback_fetch(ts, url):
    r = wayback_get(wayback_url(ts, url))
    if good(r) and binary(r.get("html")):
        # the raw capture keeps the original compression; the replay view ("if_") is decoded by archive.org
        r2 = wayback_get(f"https://web.archive.org/web/{ts}if_/{url}")
        if good(r2) and not binary(r2.get("html")):
            r2["html"] = ARCHIVE_PREFIX.sub("", r2["html"])
            r2["final_url"] = re.sub(r"(/web/\d{14})if_/", r"\1id_/", r2.get("final_url") or "")
            r = r2
    m = re.match(r"https?://web\.archive\.org/web/(\d+)id_/(.+)$", r.get("final_url") or "")
    if m:  # point readers at the normal (non-raw) archive view of the capture that was actually served
        r["archive_ts"], r["original_url"] = m.group(1), m.group(2)
        r["view_url"] = wayback_url(m.group(1), m.group(2), raw=False)
    return r


# ---------------------------------------------------------------- per domain
def save(d_out, name, r):
    html = r.get("html") or ""
    with open(os.path.join(d_out, name + ".html"), "w", encoding="utf-8") as f:
        f.write(html[:1_500_000])
    p = parse(html)
    with open(os.path.join(d_out, name + ".txt"), "w", encoding="utf-8") as f:
        f.write(p.text())
    return p


def write_page(d_out, name, html, txt):
    with open(os.path.join(d_out, name + ".html"), "w", encoding="utf-8") as f:
        f.write(html[:1_500_000])
    with open(os.path.join(d_out, name + ".txt"), "w", encoding="utf-8") as f:
        f.write(txt)


def chain_summary(r):
    return [{k: h.get(k) for k in ("url", "status", "error", "to", "kind", "loop") if h.get(k) is not None}
            for h in r.get("chain", [])]


def find_home(domain, meta):
    """Try the usual homepage URLs. Returns (response, ua, state)."""
    best = None
    for url in (f"https://{domain}/", f"https://www.{domain}/", f"http://{domain}/", f"http://www.{domain}/"):
        if url.startswith(("https://www.", "http://www.")) and not resolves("www." + domain):
            continue
        r, ua = fetch(url), UA
        state = page_state(r)
        meta["tried"].append({"url": url, "status": r.get("status"), "error": r.get("error"),
                              "final_url": r.get("final_url"), "tls_unverified": r.get("tls_unverified"),
                              "state": state, "ua": "plain" if ua == PLAIN_UA else "browser",
                              "chain": chain_summary(r)})
        rank = {"ok": 5, "maintenance": 4, "parked": 3, "blocked": 2, "http-error": 1}.get(state, 0)
        if rank and (best is None or rank > best[3]):
            best = (r, ua, state, rank)
        if state == "ok":
            break
        if r.get("error") and "timed out" in r["error"] and url.startswith("http://"):
            break  # https and http both time out: the host is down or drops us, more variants won't help
    if best:
        return best[0], best[1], best[2]
    return None, UA, "unreachable"


def sitemap_links(root, get):
    """(url, "") pairs from robots.txt Sitemap: entries and /sitemap.xml, one level of sitemap indexes."""
    maps = []
    robots = get(root + "/robots.txt")
    if good(robots):
        maps += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots["html"])[:3]
    maps = maps or [root + "/sitemap.xml"]
    locs, seen = [], set()
    for sm in maps[:3]:
        if sm in seen:
            continue
        seen.add(sm)
        r = get(sm)
        if not good(r):
            continue
        found = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r["html"], re.I)[:3000]
        if re.search(r"<sitemapindex", r["html"], re.I):
            for child in found[:4]:
                c = get(child)
                if good(c):
                    locs += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", c["html"], re.I)[:3000]
        else:
            locs += found
    return [(u, "") for u in locs if not SKIP_EXT.search(u)]


def find_webmail(domain, root, get):
    """First webmail login page among the usual places, with its software when recognisable."""
    cands = [f"https://{sub}.{domain}/" for sub in ("mail", "webmail", "email") if resolves(f"{sub}.{domain}")]
    cands += [root + p for p in ("/webmail/", "/mail/", "/roundcube/", "/sogo/")]
    for url in cands:
        r = get(url)
        if not good(r):
            continue
        p = parse(r["html"])
        hay = p.title + " " + p.meta.get("generator", "") + " " + r["html"][:20000]
        soft = next((name for name, rx in WEBMAIL_SOFTWARE if re.search(rx, hay, re.I)), "")
        if p.password and (soft or MAIL_WORDS.search(p.title + " " + p.text()[:2000])):
            return {"url": r.get("final_url") or url, "software": soft, "title": " ".join(p.title.split())[:120]}
    return None


SPA_ROOT = re.compile(r"<div\s+id=[\"'](?:root|app|__next|svelte)[\"']\s*>\s*</div>", re.I)
SPA_HEADING = {"tos": r"terms of (?:service|use)|terms (?:and|&) conditions|acceptable use policy|user agreement",
               "privacy": r"privacy (?:policy|notice|statement)|datenschutzerkl[äa]rung",
               "imprint": r"contact us|impressum|imprint|legal notice|about us"}
JS_TEXT_NOISE = re.compile(r"^(?:[a-z!:/\[\].-]+(?:-[\w/.\[\]%]+)+\s*)+$|^(?:h[1-6]|p|ul|ol|li|a|b|i|div|span|strong|em|"
                           r"section|main|br|button|_blank|noopener noreferrer)$")


def spa_text(html, base, get, cat):
    """Text of a JavaScript-rendered page: the section after its heading in the site's own JS bundle."""
    if cat not in SPA_HEADING or not SPA_ROOT.search(html):
        return ""
    host = urllib.parse.urlparse(base).hostname
    for src in re.findall(r"<script[^>]+src=[\"']([^\"']+\.js)[\"']", html, re.I)[:3]:
        url = urllib.parse.urljoin(base, src)
        if site_key(urllib.parse.urlparse(url).hostname) != site_key(host):
            continue
        js = get(url).get("html") or ""
        best, best_score = "", 0
        # the heading also appears as a link label or a form hint: keep the occurrence followed by most prose
        for m in list(re.finditer(r"[\"'`](?:" + SPA_HEADING[cat] + r")[\"'`]", js, re.I))[:30]:
            seg = js[m.start():m.start() + 20000]
            end = re.search(r"\bfunction\s+\w+\s*\(|\bexport\s", seg)
            seg = re.sub(r"className:\s*\"[^\"]*\"", "", seg[:end.start()] if end else seg)
            quote = seg[0]
            parts = [t for t in re.findall(re.escape(quote) + r"([^" + re.escape(quote) + r"\n]*)" + re.escape(quote), seg)
                     if re.search(r"[A-Za-z]{2}", t) and not JS_TEXT_NOISE.match(t.strip())
                     and not re.search(r"[{}=]|\bjsx|children:", t)]
            text = "\n".join(" ".join(t.split()) for t in parts if t.strip())
            score = sum(len(l.split()) for l in text.splitlines() if len(l.split()) >= 6)
            if score > best_score:
                best, best_score = text, score
        if best_score >= 40:
            return best
    return ""


def crawl(domain, d_out, meta, home, fetch_page, live):
    """Homepage links -> terms / privacy / imprint / pricing / signup / domains / service pages."""
    p = save(d_out, "home", home)
    meta["title"] = " ".join(p.title.split())
    meta["meta"] = p.meta
    base_url = home.get("original_url") or home["final_url"]
    links = pick_links(p.links, base_url)
    meta["links"] = links
    options, texts = list(p.options), [p.text()]
    hrefs = [h for h, _ in p.links]
    fetched = {}

    def get(u):
        if u not in fetched:
            fetched[u] = fetch_page(u)
        return fetched[u]

    root = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(base_url))
    smap = None
    manual = SOURCES.get(domain, {})
    for cat in ("tos", "privacy", "imprint", "pricing", "signup", "domains", "service"):
        urls = manual.get(cat) or []
        got = [(u, get(u)) for u in ([urls] if isinstance(urls, str) else urls)]
        got = [(u, r) for u, r in got if good(r)]
        if got:
            subs = [parse(r["html"]) for _, r in got]
            parts = []
            for sub, (_, r) in zip(subs, got):
                t = sub.text()
                if len(t) < 200:
                    t = max(t, spa_text(r["html"], r.get("original_url") or r["final_url"], get, cat), key=len)
                parts.append(t)
            txt = "\n\n".join(parts)
            write_page(d_out, cat, "\n".join(r["html"][:500_000] for _, r in got), txt)
            r = got[0][1]
            meta["pages"][cat] = {"url": got[0][0], "final_url": r.get("view_url") or r.get("final_url"),
                                  "status": r.get("status"), "title": " ".join(subs[0].title.split())[:200],
                                  "chars": len(txt), "from": "sources.json" + ("+archive" if r.get("archive_ts") else ""),
                                  "also": [u for u, _ in got[1:]]}
            for sub in subs:
                if cat in ("signup", "domains", "pricing", "service"):
                    options += sub.options
                hrefs += [h for h, _ in sub.links]
            if cat in ("signup", "domains", "pricing", "service"):
                texts.append(txt)
            continue
        candidates = []
        if links.get(cat):
            candidates.append((links[cat]["url"], "link"))
        if live and cat in ("tos", "privacy", "imprint") and not candidates:
            if smap is None:
                smap = pick_links(sitemap_links(root, get), base_url)
                meta["sitemap"] = sorted(smap)
            if smap.get(cat):
                candidates.append((smap[cat]["url"], "sitemap"))
        if live and cat in FALLBACK:
            candidates += [(root + path, "fallback") for path in FALLBACK[cat]]
        chosen, last = None, None
        for cand, source in candidates:
            r = get(cand)
            if not good(r):
                last = {"url": cand, "status": r.get("status"), "error": r.get("error")}
                continue
            sub = parse(r["html"])
            txt = sub.text()
            if source != "link" and not CHECK[cat].search(sub.title + "\n" + txt[:5000]):
                last = {"url": cand, "status": r.get("status"), "error": f"{source} page not relevant"}
                continue
            if source == "fallback" and r.get("final_url", "").rstrip("/") == home["final_url"].rstrip("/"):
                last = {"url": cand, "status": r.get("status"), "error": "redirects to the homepage"}
                continue
            chosen = [cand, r, sub, txt, source]
            break
        if not chosen:
            if last:
                meta["pages"][cat] = last
            continue
        cand, r, sub, txt, source = chosen
        if len(txt) < 200:
            js_txt = spa_text(r["html"], r.get("original_url") or r["final_url"], get, cat)
            if len(js_txt) > len(txt):
                txt, source = js_txt, source + "+js"
        if cat in ("tos", "privacy") and len(txt) < 2000:  # short index page: follow one level deeper
            deeper = pick_links(sub.links, r.get("original_url") or r["final_url"]).get(cat)
            if deeper and deeper["url"] not in fetched:
                r2 = get(deeper["url"])
                if good(r2):
                    sub2 = parse(r2["html"])
                    txt2 = sub2.text()
                    if len(txt2) > len(txt):
                        cand, r, sub, txt, source = deeper["url"], r2, sub2, txt2, source + "+hop"
        write_page(d_out, cat, r["html"], txt)
        meta["pages"][cat] = {"url": cand, "final_url": r.get("view_url") or r.get("final_url"),
                              "status": r.get("status"), "title": " ".join(sub.title.split())[:200],
                              "chars": len(txt), "from": source + ("+archive" if r.get("archive_ts") else ""),
                              "chain": chain_summary(r) if len(r.get("chain", [])) > 1 else None}
        if r.get("archive_ts"):
            meta["pages"][cat]["archive_ts"] = r["archive_ts"]
        if cat in ("signup", "domains", "pricing", "service"):
            options += sub.options
            texts.append(txt)
        hrefs += [h for h, _ in sub.links]
    if live:
        meta["webmail"] = find_webmail(domain, root, get)
    found = set()
    for value, text in options:
        for cand in (value, text):
            m = OPT_DOMAIN.match((cand or "").strip())
            if m:
                found.add(m.group(1).lower())
    meta["option_domains"] = sorted(found)
    meta["at_domains"] = sorted({m.lower() for t in texts for m in AT_DOMAIN.findall(t)})
    meta["onion"] = sorted({m.lower() for t in texts + hrefs for m in ONION.findall(t)})[:5]


def process(domain):
    d_out = os.path.join(OUT, domain)
    meta_path = os.path.join(d_out, "meta.json")
    if os.path.exists(meta_path):
        return domain, "cached"
    os.makedirs(d_out, exist_ok=True)
    meta = {"domain": domain, "tried": [], "pages": {}}
    home, ua, state = find_home(domain, meta)
    meta["live_state"] = state
    if home:
        meta["live"] = {"url": home["url"], "final_url": home.get("final_url"), "status": home.get("status"),
                        "tls_unverified": home.get("tls_unverified"), "ua": "plain" if ua == PLAIN_UA else "browser",
                        "title": " ".join(parse(home.get("html") or "").title.split())[:200],
                        "chain": chain_summary(home)}
    live = state == "ok"
    if not live:
        # the live site says nothing useful: use the newest Wayback Machine copy that is a real page
        # (a webmail login or a JavaScript app has little text: take it only when nothing longer exists)
        short = None
        for ts, orig in wayback_snapshots(domain)[:4]:
            r = wayback_fetch(ts, orig)
            if not (good(r) and page_state(r) == "ok"):
                continue
            p = parse(r["html"])
            if len(p.text()) > 150:
                short = None
                break
            if short is None and (p.title.strip() or p.text()):
                short = r
        else:
            r = short
        if r is not None and good(r):
            meta["archive"] = {"timestamp": r.get("archive_ts", ""), "url": r.get("view_url"),
                               "original": r.get("original_url", "")}
            home = r
    if not home or not (live or meta.get("archive")):
        meta["home_ok"] = False
        if home and home.get("html"):  # keep the block / maintenance page for the record
            meta["title"] = " ".join(parse(home["html"]).title.split())
            save(d_out, "live", home)
    else:
        meta["home_ok"] = True
        meta["home"] = {k: home.get(k) for k in ("url", "final_url", "status", "ctype", "tls_unverified")}
        if meta.get("archive"):
            arch_ts = meta["archive"]["timestamp"]
            crawl(domain, d_out, meta, home, lambda u: wayback_fetch(arch_ts, u), live=False)
        else:
            crawl(domain, d_out, meta, home, lambda u: fetch(u, ua=ua), live=True)
    tmp = meta_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    os.replace(tmp, meta_path)
    src = f"archive {meta['archive']['timestamp'][:8]}" if meta.get("archive") else state
    return domain, f"{src} ({len([p for p in meta['pages'].values() if p and p.get('chars')])} pages)"


def main():
    domains = [l.split("\t")[0] for l in open(DOMAINS_TSV, encoding="utf-8") if l.strip()]
    os.makedirs(OUT, exist_ok=True)
    done = 0
    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        futs = {pool.submit(process, d): d for d in domains}
        for fut in cf.as_completed(futs):
            done += 1
            try:
                d, st = fut.result()
            except Exception as e:
                d, st = futs[fut], f"FAILED {type(e).__name__}: {e}"
            if st != "cached":
                print(f"[{done}/{len(domains)}] {d}: {st}", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
