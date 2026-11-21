#!/usr/bin/env python3
"""Deeper checks of a provider's mail infrastructure, beyond the A/MX records and the website.

usage: collect_deep.py [-h] [--no-crtsh] [--no-checkhost] DOMAINS_TSV DNS_DIR WEB_DIR OUT_DIR

Per domain it writes OUT_DIR/<domain>.json (existing files are skipped) with:
  - mail DNS: SRV records (RFC 6186/8314), SPF, DMARC, MTA-STS, TLS-RPT, DANE (TLSA on the MX), DNSSEC (DS), NS
  - well-known hostnames (mail., webmail., imap., smtp., autoconfig. ...) that resolve
  - mail client autoconfig: the provider's own config-v1.1.xml and Thunderbird's ISPDB (lists served domains)
  - IMAP on port 993: does it answer, which server software, which names its TLS certificate covers
  - the website's TLS certificate (validity, issuer, names it covers)
  - certificate transparency (crt.sh): hostnames ever issued a certificate under the domain
  - Wayback Machine: first and last archived capture of the homepage
  - check-host.net: when the site is unreachable from here, whether it is reachable from other countries
Ports 25/465 are not probed: many networks block them outbound. WEB_DIR may be a folder that does not exist
(then the check-host.net website test is skipped). Exit code 1 when a domain failed.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

DNS_DIR = WEB_DIR = OUT = ""  # set by main()
USE_CRTSH = USE_CHECKHOST = True
UA = common.USER_AGENT
SUBDOMAINS = ("www", "mail", "webmail", "email", "imap", "smtp", "pop", "mx", "mx1", "autoconfig", "autodiscover",
              "mta-sts", "register", "signup", "account", "accounts", "my", "admin", "status")
SRV = ("_imaps._tcp", "_imap._tcp", "_submissions._tcp", "_submission._tcp", "_pop3s._tcp", "_jmap._tcp",
       "_autodiscover._tcp")
IMAP_SOFTWARE = [("Dovecot", r"dovecot"), ("Cyrus", r"cyrus"), ("Courier", r"courier"), ("Zimbra", r"zimbra"),
                 ("Stalwart", r"stalwart"), ("Microsoft Exchange", r"exchange|microsoft"), ("Gmail", r"gimap"),
                 ("hMailServer", r"hmailserver"), ("Axigen", r"axigen"), ("Kerio", r"kerio"), ("MDaemon", r"mdaemon"),
                 ("IceWarp", r"icewarp|merak"), ("CommuniGate", r"communigate"), ("Maddy", r"maddy"),
                 ("Dovecot (Proton Bridge)", r"proton"), ("Archiveopteryx", r"archiveopteryx"),
                 ("Mail-in-a-Box", r"mail-in-a-box"), ("Open-Xchange", r"open-xchange|ox imap"),
                 ("Yandex", r"yandex"), ("Zoho", r"zoho"), ("Fastmail", r"fastmail|cyrus")]
SEMS = {"crt": threading.Semaphore(2), "wayback": threading.Semaphore(1), "checkhost": threading.Semaphore(1)}


def dig(name, rtype, extra=()):
    try:
        out = subprocess.run(["dig", "+short", "+time=4", "+tries=2", *extra, name, rtype], capture_output=True,
                             text=True, timeout=40).stdout
    except subprocess.TimeoutExpired:
        return []
    return [l.strip() for l in out.splitlines() if l.strip() and not l.startswith(";")]


def txt(name):
    return ["".join(re.findall(r'"([^"]*)"', l)) or l for l in dig(name, "TXT")]


def http_get(url, timeout=40, headers=None, tries=2):
    err = None
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
                return r.status, r.read(3_000_000).decode("utf-8", "replace"), None
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt + 1 < tries:
                time.sleep(10 * (attempt + 1))
                continue
            return e.code, "", f"HTTP {e.code}"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:160]
            time.sleep(2)
    return None, "", err


# ---------------------------------------------------------------- TLS
def decode_cert(der):
    """Subject CN, issuer O/CN, expiry and DNS names of a DER certificate (no verification)."""
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(ssl.DER_cert_to_PEM_cert(der))
        path = f.name
    try:
        c = ssl._ssl._test_decode_cert(path)
    except Exception:
        return {}
    finally:
        os.unlink(path)

    def field(seq, key):
        return next((v for rdn in seq for k, v in rdn if k == key), "")

    return {"cn": field(c.get("subject", ()), "commonName"),
            "issuer": field(c.get("issuer", ()), "organizationName") or field(c.get("issuer", ()), "commonName"),
            "not_after": c.get("notAfter", ""),
            "names": sorted({v.lower() for k, v in c.get("subjectAltName", ()) if k == "DNS"})[:100]}


def tls_probe(host, port, read_banner=False, timeout=12):
    out = {"host": host, "port": port}
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:120]
        return out
    try:
        s = ssl._create_unverified_context().wrap_socket(raw, server_hostname=host)
        s.settimeout(timeout)
        der = s.getpeercert(binary_form=True)
        if der:
            out["cert"] = decode_cert(der)
        if read_banner:
            out["banner"] = s.recv(400).decode("utf-8", "replace").strip()[:300]
        s.close()
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:120]
        return out
    try:  # second handshake, verified, only to learn whether the certificate is trusted for this name
        with socket.create_connection((host, port), timeout=timeout) as raw2:
            with ssl.create_default_context().wrap_socket(raw2, server_hostname=host):
                out["valid"] = True
    except ssl.SSLCertVerificationError as e:
        out["valid"], out["invalid_reason"] = False, (e.verify_message or str(e))[:120]
    except Exception:
        pass
    return out


def imap_software(banner):
    return next((name for name, rx in IMAP_SOFTWARE if re.search(rx, banner or "", re.I)), "")


# ---------------------------------------------------------------- autoconfig
def parse_autoconfig(xml):
    if "<clientConfig" not in xml:
        return None
    servers = []
    for kind, typ, body in re.findall(r"<(incomingServer|outgoingServer)\s+type=\"(\w+)\"\s*>(.*?)</\1>", xml, re.S):
        def tag(t):
            m = re.search(rf"<{t}>\s*([^<]+?)\s*</{t}>", body)
            return m.group(1) if m else ""
        servers.append({"dir": "in" if kind.startswith("in") else "out", "type": typ, "host": tag("hostname"),
                        "port": tag("port"), "socket": tag("socketType")})
    return {"display_name": (re.findall(r"<displayName>\s*([^<]+?)\s*</displayName>", xml) or [""])[0],
            "domains": sorted({d.strip().lower() for d in re.findall(r"<domain>\s*([^<]+?)\s*</domain>", xml)})[:200],
            "servers": servers[:8]}


def autoconfig(domain):
    for url, src in ((f"https://autoconfig.{domain}/mail/config-v1.1.xml?emailaddress=info%40{domain}", "provider"),
                     (f"https://{domain}/.well-known/autoconfig/mail/config-v1.1.xml", "provider (.well-known)"),
                     (f"https://autoconfig.thunderbird.net/v1.1/{domain}", "Thunderbird ISPDB")):
        if src.startswith("provider") and not dig(urllib.parse.urlparse(url).hostname, "A"):
            continue
        status, body, _ = http_get(url, timeout=25, tries=1)
        if status == 200:
            cfg = parse_autoconfig(body)
            if cfg:
                cfg.update(source=src, url=url.split("?")[0])
                return cfg
    return None


# ---------------------------------------------------------------- outside sources
def crtsh(base):
    with SEMS["crt"]:
        status, body, err = http_get(f"https://crt.sh/?q=%25.{base}&output=json&deduplicate=Y", timeout=45, tries=2)
    if status != 200:
        return {"error": err or f"HTTP {status}"}
    try:
        rows = json.loads(body)
    except ValueError:
        return {"error": "bad JSON"}
    names = set()
    for row in rows:
        for n in (row.get("name_value") or "").lower().split("\n"):
            n = n.strip().lstrip("*.")
            if n.endswith(base) and "@" not in n:
                names.add(n)
    years = sorted(row.get("not_before", "")[:4] for row in rows if row.get("not_before"))
    return {"names": sorted(names)[:300], "certs": len(rows), "first": years[0] if years else "",
            "last": years[-1] if years else ""}


def wayback_range(domain):
    """First capture and last successful capture of the homepage (exact URL, so rows are sorted by time)."""
    out = {}
    for key, limit, filt in (("first", "1", []), ("last", "-1", [("filter", "statuscode:200")])):
        q = urllib.parse.urlencode([("url", domain + "/"), ("output", "json"),
                                    ("fl", "timestamp,original,statuscode"), ("limit", limit)] + filt)
        with SEMS["wayback"]:  # archive.org blocks clients that query too fast
            status, body, err = http_get("https://web.archive.org/cdx/search/cdx?" + q, timeout=60, tries=3)
            if status is None and "refused" in (err or ""):
                time.sleep(30)
                status, body, err = http_get("http://web.archive.org/cdx/search/cdx?" + q, timeout=60, tries=2)
            time.sleep(3)
        try:
            rows = json.loads(body)[1:] if status == 200 and body.strip() else []
        except ValueError:
            rows = []
        if rows:
            ts = rows[-1][0]
            out[key] = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
            out[key + "_url"] = rows[-1][1]
        elif err:
            out[key + "_error"] = err
    return out


def checkhost(kind, target, nodes=5):
    """Ask check-host.net to fetch target from several countries. kind: 'http' or 'tcp'."""
    with SEMS["checkhost"]:
        status, body, err = http_get(f"https://check-host.net/check-{kind}?host={urllib.parse.quote(target, safe=':/')}"
                                     f"&max_nodes={nodes}", timeout=30, headers={"Accept": "application/json"})
        time.sleep(3)
    try:
        req = json.loads(body)
        rid, node_info = req["request_id"], req["nodes"]
    except (ValueError, KeyError, TypeError):
        return {"error": err or "bad response"}
    result = {}
    for _ in range(6):
        time.sleep(6)
        _, body, _ = http_get(f"https://check-host.net/check-result/{rid}", timeout=30,
                              headers={"Accept": "application/json"})
        try:
            result = json.loads(body)
        except ValueError:
            continue
        if result and all(v is not None for v in result.values()):
            break
    checks = []
    for node, val in (result or {}).items():
        country = (node_info.get(node) or ["", "?"])[1]
        if not val:
            continue
        first = val[0] if isinstance(val, list) else val
        if kind == "http" and isinstance(first, list):
            checks.append({"country": country, "ok": bool(first[0]), "msg": str(first[2])[:60],
                           "code": first[3]})
        elif kind == "tcp" and isinstance(first, dict):
            checks.append({"country": country, "ok": "error" not in first,
                           "msg": str(first.get("error") or first.get("address", ""))[:60]})
    return {"target": target, "report": f"https://check-host.net/check-report/{rid}", "checks": checks,
            "ok": sum(c["ok"] for c in checks), "total": len(checks)}


# ---------------------------------------------------------------- per domain
def collect(domain):
    path = os.path.join(OUT, domain + ".json")
    if os.path.exists(path):
        return domain, "cached"
    dns_path = os.path.join(DNS_DIR, domain + ".json")
    if not os.path.exists(dns_path):
        raise FileNotFoundError(f"{dns_path} missing: run collect_dns.py first")
    dns = common.read_json(dns_path)
    web = common.read_json(os.path.join(WEB_DIR, domain, "meta.json"))
    base = dns["base"]
    alive = dns.get("status") == "NOERROR"
    rec = {"domain": domain}
    if alive:
        rec["ns"] = sorted(h.rstrip(".").lower() for h in dig(base, "NS"))
        rec["dnssec"] = bool(dig(base, "DS"))
        rec["spf"] = next((t for t in txt(domain) if t.lower().startswith("v=spf1")), "")
        rec["dmarc"] = next((t for t in txt("_dmarc." + domain) if t.lower().startswith("v=dmarc1")), "")
        rec["mta_sts"] = next((t for t in txt("_mta-sts." + domain) if t.lower().startswith("v=stsv1")), "")
        rec["tls_rpt"] = next((t for t in txt("_smtp._tls." + domain) if t.lower().startswith("v=tlsrptv1")), "")
        mx_hosts = [h for _, h in dns.get("mx", [])]
        rec["dane"] = bool(mx_hosts and dig(f"_25._tcp.{mx_hosts[0]}", "TLSA"))
        rec["srv"] = {}
        for name in SRV:
            recs = [r for r in dig(f"{name}.{domain}", "SRV") if not r.endswith(" .")]
            if recs:
                rec["srv"][name] = recs[:4]
        rec["subdomains"] = {}
        for sub in SUBDOMAINS:
            ans = dig(f"{sub}.{domain}", "A")
            ips = [a for a in ans if re.fullmatch(r"[\d.]+", a)]
            if ips:
                rec["subdomains"][sub] = {"a": ips[:3], "cname": [a.rstrip(".") for a in ans if a not in ips][:2]}
        rec["autoconfig"] = autoconfig(domain)
        # IMAP: SRV target, autoconfig host, then the usual names and the MX hosts
        hosts = []
        for r in rec["srv"].get("_imaps._tcp", []):
            parts = r.split()
            if len(parts) == 4:
                hosts.append(parts[3].rstrip("."))
        for s in (rec["autoconfig"] or {}).get("servers", []):
            if s["type"] == "imap" and s["host"] and "%" not in s["host"]:
                hosts.append(s["host"])
        hosts += [f"{s}.{domain}" for s in ("imap", "mail") if s in rec["subdomains"]] + mx_hosts[:2]
        rec["imap"] = []
        for host in list(dict.fromkeys(h.lower() for h in hosts))[:5]:
            p = tls_probe(host, 993, read_banner=True)
            p["software"] = imap_software(p.get("banner"))
            rec["imap"].append(p)
            if p.get("banner", "").startswith("* OK"):
                break
        if dns.get("a"):
            rec["web_tls"] = tls_probe(domain, 443)
    rec["crtsh"] = crtsh(base) if USE_CRTSH else None
    rec["wayback"] = wayback_range(domain)
    live_state = web.get("live_state")
    imap_ok = any(p.get("banner", "").startswith("* OK") for p in rec.get("imap", []))
    if USE_CHECKHOST:
        if alive and dns.get("a") and live_state in ("unreachable", "http-error", "blocked"):
            rec["checkhost_http"] = checkhost("http", f"https://{domain}/")
        if alive and dns.get("mx") and not imap_ok:
            target = next((p["host"] for p in rec.get("imap", []) if p.get("host")), dns["mx"][0][1])
            rec["checkhost_imap"] = checkhost("tcp", f"{target}:993")
    common.write_json(path, rec)
    return domain, "ok" + (" imap" if imap_ok else "") + (" autoconfig" if rec.get("autoconfig") else "")


def main(argv=None):
    global DNS_DIR, WEB_DIR, OUT, USE_CRTSH, USE_CHECKHOST
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("domains_tsv", help='file of "domain<TAB>0|1" lines')
    ap.add_argument("dns_dir", help="results of collect_dns.py")
    ap.add_argument("web_dir", help="results of collect_web.py (may be missing)")
    ap.add_argument("out_dir", help="folder for the results (cache)")
    ap.add_argument("--no-crtsh", action="store_true", help="skip certificate transparency (crt.sh is slow)")
    ap.add_argument("--no-checkhost", action="store_true", help="skip the check-host.net tests from abroad")
    args = ap.parse_args(argv)
    DNS_DIR, WEB_DIR, OUT = args.dns_dir, args.web_dir, args.out_dir
    USE_CRTSH, USE_CHECKHOST = not args.no_crtsh, not args.no_checkhost
    domains = [d for d, _ in common.read_domains_tsv(args.domains_tsv)]
    os.makedirs(OUT, exist_ok=True)
    done, failed = 0, 0
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(collect, d): d for d in domains}
        for fut in cf.as_completed(futs):
            done += 1
            try:
                d, st = fut.result()
            except Exception as e:  # noqa: BLE001  report and go on with the other domains
                d, st = futs[fut], f"FAILED {type(e).__name__}: {e}"
                failed += 1
            if st != "cached":
                print(f"[{done}/{len(domains)}] {d}: {st}", flush=True)
    print(f"done ({failed} failed)" if failed else "done", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
