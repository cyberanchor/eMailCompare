#!/usr/bin/env python3
"""Collect A/MX records, registration dates/registrant info and IP geo/ASN for each domain.

usage: collect_dns.py DOMAINS_TSV PSL_FILE OUT_DIR

DOMAINS_TSV lines are "domain<TAB>struck". One JSON file per domain is written to OUT_DIR
(existing files are skipped, so the script can be re-run), then OUT_DIR/ipinfo.json with
ip-api.com data for every IP seen.
"""
import concurrent.futures as cf
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from urllib.parse import urlparse

DOMAINS_TSV, PSL_FILE, OUT = sys.argv[1:4]
os.makedirs(OUT, exist_ok=True)
UA = "Mozilla/5.0 (compatible; eMailCompare-research/1.0)"

# ---------------------------------------------------------------- public suffix list (ICANN part)
RULES, EXC, WILD = set(), set(), set()
in_icann = False
for line in open(PSL_FILE, encoding="utf-8"):
    line = line.strip()
    if "===BEGIN ICANN DOMAINS===" in line:
        in_icann = True
    elif "===END ICANN DOMAINS===" in line:
        break
    if not in_icann or not line or line.startswith("//"):
        continue
    rule = line.split()[0].lower()
    if rule.startswith("!"):
        EXC.add(rule[1:])
    elif rule.startswith("*."):
        WILD.add(rule[2:])
    else:
        RULES.add(rule)


def registrable(name):
    labels = name.lower().strip(".").split(".")
    n, ps = len(labels), 1
    for i in range(n):
        suffix = ".".join(labels[i:])
        if suffix in EXC:
            ps = n - i - 1
            break
        if suffix in RULES or (i + 1 < n and ".".join(labels[i + 1:]) in WILD):
            ps = max(ps, n - i)
    return ".".join(labels[-(ps + 1):]) if n > ps else name


# ---------------------------------------------------------------- DNS
def query(name, rtype):
    """Return (status, [(type, data), ...]) using dig."""
    try:
        res = subprocess.run(["dig", "+noall", "+answer", "+comments", "+time=4", "+tries=2", name, rtype],
                             capture_output=True, text=True, timeout=40)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", []
    m = re.search(r"status: (\w+)", res.stdout)
    status = m.group(1) if m else ("NOANSWER" if res.returncode == 0 else "ERROR")
    answers = []
    for line in res.stdout.splitlines():
        if not line.strip() or line.startswith(";"):
            continue
        parts = line.split(None, 4)
        if len(parts) == 5:
            answers.append((parts[3], parts[4].strip()))
    return status, answers


def parse_mx(answers):
    out = []
    for typ, data in answers:
        if typ == "MX":
            pref, _, host = data.partition(" ")
            if pref.isdigit():
                out.append((int(pref), host.strip().rstrip(".").lower()))
    return sorted(set(out))


def a_records(name):
    _, ans = query(name, "A")
    return [d for t, d in ans if t == "A"]


# ---------------------------------------------------------------- RDAP / whois
def http_json(url, tries=3):
    err = None
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rdap+json, application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace")), None
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                err = f"HTTP {e.code}"
                time.sleep(8 * (attempt + 1))
                continue
            return e.code, None, f"HTTP {e.code}"
        except Exception as e:  # network errors, bad JSON
            err = f"{type(e).__name__}: {e}"[:200]
            time.sleep(3)
    return None, None, err


def whois_query(server, q, timeout=25):
    with socket.create_connection((server, 43), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((q + "\r\n").encode())
        chunks = []
        while True:
            try:
                b = s.recv(65536)
            except socket.timeout:
                break
            if not b:
                break
            chunks.append(b)
    return b"".join(chunks).decode("utf-8", "replace")


MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def norm_date(s):
    s = (s or "").strip()
    m = re.search(r"\b((?:19|20)\d\d)[-./](\d{1,2})[-./](\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.search(r"\b(\d{1,2})[-./](\d{1,2})[-./]((?:19|20)\d\d)\b", s)
        if m:
            d, mo, y = map(int, m.groups())
        else:
            m = re.search(r"\b(\d{1,2})[- ]([A-Za-z]{3})[a-z]*\.?[- ,]+((?:19|20)\d\d)\b", s)
            if m and m.group(2).lower() in MONTHS:
                d, mo, y = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
            else:
                m = re.search(r"\b([A-Za-z]{3})[a-z]*\.? +(\d{1,2}),? +((?:19|20)\d\d)\b", s)
                if m and m.group(1).lower() in MONTHS:
                    d, mo, y = int(m.group(2)), MONTHS[m.group(1).lower()], int(m.group(3))
                else:
                    m = re.search(r"\b((?:19|20)\d\d)(\d\d)(\d\d)\b", s)
                    if not m:
                        return None
                    y, mo, d = map(int, m.groups())
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


CREATED_RE = re.compile(
    r"^\s*(?:creation date|created(?: on| at| date)?|registered(?: on| date)?|registration (?:date|time)|"
    r"domain registration date|record created|first registration date|activated|domain record activated|"
    r"domain created|regdate|fecha de creaci[oó]n|data de cria[cç][aã]o|registrert|registrerad)\s*[:.]+\s*(.+)$",
    re.I | re.M)
EXPIRES_RE = re.compile(
    r"^\s*(?:registry expiry date|registrar registration expiration date|expiration date|expiry date|"
    r"expires(?: on| at)?|expire date|expire|expiration time|paid-till|valid until|validity|renewal date|"
    r"domain expiration date|expiry|fecha de (?:expiraci[oó]n|vencimiento)|data de expira[cç][aã]o)\s*[:.]+\s*(.+)$",
    re.I | re.M)
NOT_FOUND_RE = re.compile(r"no match|not found|no entries found|no data found|status:\s*(?:free|available)|"
                          r"is free|not registered|available for registration|no object found|object does not exist", re.I)
PRIVACY_RE = re.compile(r"privacy|redact|withheld|proxy|protect|guard|gdpr|not disclosed|non-public|whoisguard|"
                        r"domains by proxy|contact privacy|identity|anonym|statutory|mask|hidden|data protected|"
                        r"private|personal data|not available|n/a", re.I)


def parse_whois(txt):
    out = {}
    m = CREATED_RE.search(txt)
    if m:
        out["created"] = norm_date(m.group(1))
    m = EXPIRES_RE.search(txt)
    if m:
        out["expires"] = norm_date(m.group(1))
    m = re.search(r"^\s*registrant organi[sz]ation\s*:\s*(.+)$", txt, re.I | re.M)
    if m and not PRIVACY_RE.search(m.group(1)):
        out["registrant_org"] = m.group(1).strip()
    m = re.search(r"^\s*registrant country(?: code)?\s*:\s*([A-Za-z]{2})\s*$", txt, re.I | re.M)
    if m:
        out["registrant_cc"] = m.group(1).upper()
    return out


def vcard(entity):
    out = {}
    vc = entity.get("vcardArray")
    if not isinstance(vc, list) or len(vc) < 2:
        return out
    for item in vc[1]:
        if not isinstance(item, list) or len(item) < 4:
            continue
        name, params, value = item[0], item[1] if isinstance(item[1], dict) else {}, item[3]
        if name in ("fn", "org"):
            text = " ".join(v for v in value if isinstance(v, str)) if isinstance(value, list) else str(value)
            if text.strip():
                out.setdefault(name, text.strip())
        elif name == "adr":
            cc = params.get("cc")
            if isinstance(cc, str) and len(cc) == 2:
                out["cc"] = cc.upper()
            elif isinstance(value, list) and len(value) >= 7 and isinstance(value[6], str) and len(value[6].strip()) == 2:
                out["cc"] = value[6].strip().upper()
    return out


def walk(entities):
    for e in entities or []:
        if isinstance(e, dict):
            yield e
            yield from walk(e.get("entities"))


def parse_rdap(data):
    out = {}
    for ev in data.get("events", []):
        act = (ev.get("eventAction") or "").lower()
        if act == "registration":
            out["created"] = norm_date(ev.get("eventDate"))
        elif act == "expiration":
            out["expires"] = norm_date(ev.get("eventDate"))
    for e in walk(data.get("entities")):
        roles = [r.lower() for r in e.get("roles", [])]
        info = vcard(e)
        if "registrar" in roles and info.get("fn"):
            out.setdefault("registrar", info["fn"])
        if "registrant" in roles:
            org = info.get("org") or info.get("fn")
            if org and not PRIVACY_RE.search(org):
                out.setdefault("registrant_org", org)
                if info.get("cc"):
                    out.setdefault("registrant_cc", info["cc"])
            elif info.get("cc") and not org:
                out.setdefault("registrant_cc", info["cc"])
    out["rdap_status_values"] = data.get("status", [])
    return out


# bootstrap tables, filled before the worker threads start
RDAP, WHOIS = {}, {}
HOST_SEM = defaultdict(lambda: threading.Semaphore(2))
SEM_LOCK = threading.Lock()


def host_sem(host):
    with SEM_LOCK:
        return HOST_SEM[host]


def registration(base):
    tld = base.rsplit(".", 1)[-1]
    out = {}
    url = RDAP.get(tld)
    if url:
        with host_sem(urlparse(url).netloc):
            code, data, err = http_json(url.rstrip("/") + "/domain/" + base)
        out["rdap_http"] = code
        if err:
            out["rdap_error"] = err
        if data:
            out.update({k: v for k, v in parse_rdap(data).items() if v})
    if not out.get("created") and WHOIS.get(tld):
        server = WHOIS[tld]
        try:
            with host_sem(server):
                txt = whois_query(server, base)
            out["whois_server"] = server
            for k, v in parse_whois(txt).items():
                if v and not out.get(k):
                    out[k] = v
            if NOT_FOUND_RE.search(txt[:2000]):
                out["whois_not_found"] = True
            out["whois_head"] = txt[:600]
        except Exception as e:
            out["whois_error"] = f"{type(e).__name__}: {e}"[:200]
    return out


def collect(domain, struck):
    path = os.path.join(OUT, domain + ".json")
    if os.path.exists(path):
        return domain, "cached"
    base = registrable(domain)
    rec = {"domain": domain, "base": base, "struck": struck}
    status, ans = query(domain, "A")
    rec["status"] = status
    rec["a"] = [d for t, d in ans if t == "A"][:4]
    rec["cname"] = [d.rstrip(".") for t, d in ans if t == "CNAME"]
    mx_status, mx_ans = query(domain, "MX")
    mx, mx_src = parse_mx(mx_ans), domain
    if not mx and domain != base:
        alt = parse_mx(query(base, "MX")[1])
        if alt:
            mx, mx_src = alt, base
    rec["mx_status"] = mx_status
    rec["mx_source"] = mx_src
    rec["mx_null"] = any(h in ("", ".") for _, h in mx)
    rec["mx"] = [[p, h] for p, h in mx if h not in ("", ".")][:6]
    rec["mx_ips"] = {h: a_records(h)[:2] for _, h in rec["mx"][:4]}
    rec["reg"] = registration(base)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return domain, "ok"


def ip_info():
    """ip-api.com batch lookup for every IP seen; cached in ipinfo.json."""
    path = os.path.join(OUT, "ipinfo.json")
    cache = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    ips = set()
    for fn in os.listdir(OUT):
        if fn.endswith(".json") and fn != "ipinfo.json":
            rec = json.load(open(os.path.join(OUT, fn), encoding="utf-8"))
            ips.update(rec.get("a", []))
            for v in rec.get("mx_ips", {}).values():
                ips.update(v)
    todo = sorted(ip for ip in ips if ip not in cache or cache[ip].get("status") != "success")
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        req = urllib.request.Request(
            "http://ip-api.com/batch?fields=status,message,query,country,countryCode,isp,org,as",
            data=json.dumps(chunk).encode(), headers={"User-Agent": UA, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            for row in json.loads(r.read().decode()):
                cache[row["query"]] = row
            remaining, ttl = r.headers.get("X-Rl"), r.headers.get("X-Ttl")
        if remaining is not None and int(remaining) < 2:
            time.sleep(int(ttl or 60) + 1)
        else:
            time.sleep(4.5)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    return len(ips), len(todo)


def main():
    domains = []
    for line in open(DOMAINS_TSV, encoding="utf-8"):
        d, _, s = line.rstrip("\n").partition("\t")
        if d:
            domains.append((d, s == "1"))
    # RDAP bootstrap + whois servers for TLDs without RDAP
    boot = json.load(urllib.request.urlopen(
        urllib.request.Request("https://data.iana.org/rdap/dns.json", headers={"User-Agent": UA}), timeout=60))
    for tlds, urls in boot["services"]:
        url = next((u for u in urls if u.startswith("https://")), urls[0])
        for t in tlds:
            RDAP[t.lower()] = url
    # SWITCH serves RDAP for .ch/.li but is missing from the IANA bootstrap; its port-43 whois blocks us
    RDAP.setdefault("ch", "https://rdap.nic.ch/")
    RDAP.setdefault("li", "https://rdap.nic.ch/")
    for t in sorted({registrable(d).rsplit(".", 1)[-1] for d, _ in domains}):
        try:
            m = re.search(r"^whois:\s*(\S+)", whois_query("whois.iana.org", t), re.M)
            WHOIS[t] = m.group(1) if m else None
        except Exception:
            WHOIS[t] = None
    print("TLDs without RDAP:", sorted(t for t in WHOIS if t not in RDAP), flush=True)
    done = 0
    with cf.ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(collect, d, s) for d, s in domains]
        for fut in cf.as_completed(futs):
            try:
                d, st = fut.result()
            except Exception as e:
                d, st = "?", f"FAILED {type(e).__name__}: {e}"
            done += 1
            if st != "cached":
                print(f"[{done}/{len(domains)}] {d}: {st}", flush=True)
    n_ips, n_new = ip_info()
    print(f"done: {len(domains)} domains, {n_ips} IPs ({n_new} looked up)", flush=True)


if __name__ == "__main__":
    main()
