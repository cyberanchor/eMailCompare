#!/usr/bin/env python3
"""Fetch each service's homepage plus its terms / privacy / imprint / pricing / signup / domains pages.

usage: collect_web.py DOMAINS_TSV OUT_DIR

Per domain it writes OUT_DIR/<domain>/meta.json, <page>.txt (visible text) and <page>.html (raw,
capped). Domains that already have meta.json are skipped. Downloaded content is only parsed as text.
"""
import concurrent.futures as cf
import gzip
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser

DOMAINS_TSV, OUT = sys.argv[1:3]
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
MAX_BYTES = 2_000_000
TIMEOUT = 20
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
FALLBACK = {"tos": ["/terms", "/tos", "/agb"], "privacy": ["/privacy", "/privacy-policy", "/datenschutz"],
            "imprint": ["/impressum"]}
CHECK = {"tos": re.compile(r"terms|conditions|agb|bedingungen|condizioni|regulamin|rules|acceptable use", re.I),
         "privacy": re.compile(r"privacy|datenschutz|personal data|gdpr|privacidad|riservatezza", re.I),
         "imprint": re.compile(r"impressum|imprint|angaben gem|verantwortlich", re.I)}
# big platforms whose terms/privacy pages are never the mail provider's own
THIRD_PARTY = re.compile(r"(^|\.)(google\.[a-z.]+|facebook\.com|fb\.com|twitter\.com|x\.com|instagram\.com|"
                         r"linkedin\.com|youtube\.com|apple\.com|microsoft\.com|tiktok\.com|pinterest\.[a-z.]+|"
                         r"wikipedia\.org|cloudflare\.com|mozilla\.org|creativecommons\.org|w3\.org|gnu\.org|t\.me|"
                         r"whatsapp\.com)$", re.I)
SAME_SITE_ONLY = {"pricing", "signup", "domains", "service"}
SKIP_EXT = re.compile(r"\.(pdf|png|jpe?g|gif|svg|webp|zip|gz|exe|apk|dmg|msi|asc|sig|xml|rss|ico|mp4|mp3)(\?|#|$)", re.I)
AT_DOMAIN = re.compile(r"(?<![A-Za-z0-9._%+-])@((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})\b", re.I)
OPT_DOMAIN = re.compile(r"^@?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})$", re.I)
ONION = re.compile(r"\b[a-z2-7]{56}\.onion\b", re.I)

CTX_VERIFY = ssl.create_default_context()
CTX_NOVERIFY = ssl._create_unverified_context()


class Redirect308(urllib.request.HTTPRedirectHandler):
    """Python 3.9 only follows 301/302/303/307; treat 308 like 307 (we only send GETs)."""

    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_302(req, fp, 307, msg, headers)


OPENERS = {ctx: urllib.request.build_opener(Redirect308(), urllib.request.HTTPSHandler(context=ctx))
           for ctx in (CTX_VERIFY, CTX_NOVERIFY)}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.meta, self.links, self.options, self.parts = "", {}, [], [], []
        self._skip, self._title, self._a, self._opt = 0, False, None, None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key in ("description", "og:description", "og:title", "og:site_name", "twitter:description") \
                    and a.get("content"):
                self.meta.setdefault(key, a["content"].strip())
        elif tag == "a" and a.get("href"):
            self._a = [a["href"].strip(), ""]
        elif tag == "option":
            self._opt = [a.get("value", ""), ""]
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


def fetch(url):
    err = None
    for ctx in (CTX_VERIFY, CTX_NOVERIFY):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate"})
        try:
            resp = OPENERS[ctx].open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            resp = e
        except urllib.error.URLError as e:
            err = f"{type(e.reason).__name__}: {e.reason}"[:200]
            if isinstance(e.reason, ssl.SSLError) and ctx is CTX_VERIFY:
                continue
            return {"url": url, "error": err}
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:200]
            if isinstance(e, ssl.SSLError) and ctx is CTX_VERIFY:
                continue
            return {"url": url, "error": err}
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
            return {"url": url, "error": f"read {type(e).__name__}: {e}"[:200]}
        finally:
            try:
                resp.close()
            except Exception:
                pass
        return {"url": url, "final_url": resp.geturl() or url, "status": getattr(resp, "code", None),
                "ctype": resp.headers.get("Content-Type", ""), "html": decode(body, resp.headers),
                "tls_unverified": ctx is CTX_NOVERIFY}
    return {"url": url, "error": err}


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
        if re.search(r"/(?:category|categories|tag|tags|news|article|articles|blog|post|posts)/", u.path, re.I):
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


def save(d_out, name, r):
    html = r.get("html") or ""
    with open(os.path.join(d_out, name + ".html"), "w", encoding="utf-8") as f:
        f.write(html[:1_500_000])
    p = parse(html)
    with open(os.path.join(d_out, name + ".txt"), "w", encoding="utf-8") as f:
        f.write(p.text())
    return p


def process(domain):
    d_out = os.path.join(OUT, domain)
    meta_path = os.path.join(d_out, "meta.json")
    if os.path.exists(meta_path):
        return domain, "cached"
    os.makedirs(d_out, exist_ok=True)
    meta = {"domain": domain, "tried": [], "pages": {}}
    home, fallback = None, None
    for url in (f"https://{domain}/", f"https://www.{domain}/", f"http://{domain}/"):
        r = fetch(url)
        meta["tried"].append({k: r.get(k) for k in ("url", "status", "error", "final_url", "tls_unverified")})
        if r.get("html", "").strip():
            if r.get("status") and r["status"] < 400:
                home = r
                break
            fallback = fallback or r
    home = home or fallback
    if not home:
        meta["home_ok"] = False
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        return domain, "unreachable"
    meta["home_ok"] = bool(home.get("status") and home["status"] < 400)
    meta["home"] = {k: home.get(k) for k in ("url", "final_url", "status", "ctype", "tls_unverified")}
    p = save(d_out, "home", home)
    meta["title"] = " ".join(p.title.split())
    meta["meta"] = p.meta
    links = pick_links(p.links, home["final_url"])
    meta["links"] = links
    options, texts = list(p.options), [p.text()]
    hrefs = [h for h, _ in p.links]
    fetched = {}

    def get(u):
        if u not in fetched:
            fetched[u] = fetch(u)
        return fetched[u]

    def good(r):
        return bool(r.get("html", "").strip()) and bool(r.get("status")) and r["status"] < 400

    root = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(home["final_url"]))
    for cat in ("tos", "privacy", "imprint", "pricing", "signup", "domains", "service"):
        url = links.get(cat, {}).get("url")
        if url:
            candidates, source = [url], "link"
        elif cat in FALLBACK and meta["home_ok"]:
            candidates, source = [root + path for path in FALLBACK[cat]], "fallback"
        else:
            continue
        chosen, last = None, None
        for cand in candidates:
            r = get(cand)
            if not good(r):
                last = {"url": cand, "status": r.get("status"), "error": r.get("error")}
                continue
            sub = parse(r["html"])
            txt = sub.text()
            if source == "fallback" and not CHECK[cat].search(txt[:5000]):
                last = {"url": cand, "status": r.get("status"), "error": "fallback page not relevant"}
                continue
            chosen = [cand, r, sub, txt]
            break
        if not chosen:
            meta["pages"][cat] = last
            continue
        cand, r, sub, txt = chosen
        if cat in ("tos", "privacy") and len(txt) < 2000:  # short index page: follow one level deeper
            deeper = pick_links(sub.links, r["final_url"]).get(cat)
            if deeper and deeper["url"] not in fetched:
                r2 = get(deeper["url"])
                if good(r2):
                    sub2 = parse(r2["html"])
                    txt2 = sub2.text()
                    if len(txt2) > len(txt):
                        cand, r, sub, txt, source = deeper["url"], r2, sub2, txt2, source + "+hop"
        with open(os.path.join(d_out, cat + ".html"), "w", encoding="utf-8") as f:
            f.write(r["html"][:1_500_000])
        with open(os.path.join(d_out, cat + ".txt"), "w", encoding="utf-8") as f:
            f.write(txt)
        meta["pages"][cat] = {"url": cand, "final_url": r.get("final_url"), "status": r.get("status"),
                              "title": " ".join(sub.title.split())[:200], "chars": len(txt), "from": source}
        if cat in ("signup", "domains", "pricing", "service"):
            options += sub.options
            texts.append(txt)
        hrefs += [h for h, _ in sub.links]
    found = set()
    for value, text in options:
        for cand in (value, text):
            m = OPT_DOMAIN.match((cand or "").strip())
            if m:
                found.add(m.group(1).lower())
    meta["option_domains"] = sorted(found)
    meta["at_domains"] = sorted({m.lower() for t in texts for m in AT_DOMAIN.findall(t)})
    meta["onion"] = sorted({m.lower() for t in texts + hrefs for m in ONION.findall(t)})[:5]
    tmp = meta_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    os.replace(tmp, meta_path)
    return domain, f"ok ({len(meta['pages'])} pages)"


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
