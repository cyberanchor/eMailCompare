#!/usr/bin/env python3
"""Merge collected data into data/providers/<domain>.yaml (EMC_DATA_DIR overrides the folder).

usage:
  merge.py add WORK_DIR                  add the draft records of collect.py (WORK_DIR/records/*.yaml) as pending
                                         providers at the end of the list; existing domains are skipped
  merge.py browser OUT_DIR [DOMAIN...]   fill empty terms / privacy / source-code links and descriptions from
                                         collect_browser.py (OUT_DIR/<domain>.json)
  merge.py refresh DIR [DOMAIN...]       merge a re-check: DIR/dns (collect_dns.py), DIR/deep (run_deep_refresh.py),
                                         DIR/browser (collect_browser.py); rules in cmd_refresh(), hand decisions
                                         in collector/decisions/
  merge.py set DECISIONS.json            hand-checked changes: {"set": {"<domain>": {"<field>": value,
                                         "links.tos": url, ...}}}; a value of null removes the field

Only providers that have collected data are touched. Every run writes a report next to the input
(review-<command>.md). Records are written with emcdata.save, so the files keep one format.
--dry-run writes the report but no record. The hand decisions folder is collector/decisions/ unless
EMC_DECISIONS_DIR names another one; its files are optional.
"""
import argparse
import datetime
import glob
import os
import re
import ssl
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import common  # noqa: E402
import emcdata  # noqa: E402

TODAY = datetime.date.today().isoformat()
UA = common.USER_AGENT
SOCKET = {"SSL": "SSL/TLS", "STARTTLS": "STARTTLS", "plain": "plain"}
SRV_KIND = {"_imaps._tcp": ("IMAP", "SSL/TLS"), "_imap._tcp": ("IMAP", "STARTTLS"),
            "_submissions._tcp": ("SMTP", "SSL/TLS"), "_submission._tcp": ("SMTP", "STARTTLS"),
            "_pop3s._tcp": ("POP3", "SSL/TLS"), "_jmap._tcp": ("JMAP", "")}
LINK_KEYS = ("tos", "privacy", "code")
DECISIONS = os.environ.get("EMC_DECISIONS_DIR") or os.path.join(HERE, "decisions")  # hand decisions kept between runs
DRY_RUN = False


def load(path):
    return common.read_json(path)


def read_words(name):
    """Whitespace-separated entries of a decisions text file; an empty set when the file is missing."""
    path = os.path.join(DECISIONS, name)
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return set(f.read().split())


def clean(t, limit=420):
    t = re.sub(r"\s+", " ", t or "").strip()
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return t


def flag(cc):
    cc = (cc or "").upper()
    return chr(0x1F1E6 + ord(cc[0]) - 65) + chr(0x1F1E6 + ord(cc[1]) - 65) if len(cc) == 2 and cc.isalpha() else ""


def ip_record(info, ip, host):
    """{host, flag, country, asn, org} from ip-api.com data (collect_dns.py ipinfo.json)."""
    i = info.get(ip, {})
    if i.get("status") != "success":
        return {"host": host, "flag": "", "country": "", "asn": "", "org": ""}
    asn, _, org = (i.get("as") or "").partition(" ")
    country = "Netherlands" if i.get("country") == "The Netherlands" else i.get("country") or ""
    return {"host": host, "flag": flag(i.get("countryCode")), "country": country, "asn": asn,
            "org": org or i.get("org") or ""}


def mta_sts_mode(domain):
    req = urllib.request.Request(f"https://mta-sts.{domain}/.well-known/mta-sts.txt", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
            m = re.search(r"^mode:\s*(\w+)", r.read(20000).decode("utf-8", "replace"), re.M)
            return m.group(1).lower() if m else "broken"
    except Exception:  # noqa: BLE001  no valid policy file
        return "broken"


def security(domain, dns, deep):
    if not dns.get("mx") or not deep or "spf" not in deep:
        return {}
    spf = deep.get("spf", "")
    if spf:
        m = re.search(r"(?:^|\s)([-~?+]?)all\b", spf)
        v = (m.group(1) or "+") + "all" if m else ("redirect" if "redirect=" in spf else "no all")
    else:
        v = "missing"
    m = re.search(r"\bp=(\w+)", deep.get("dmarc", ""))
    return {"spf": v, "dmarc": m.group(1).lower() if m else "missing",
            "mta_sts": mta_sts_mode(domain) if deep.get("mta_sts") else "missing",
            "tls_rpt": "yes" if deep.get("tls_rpt") else "missing",
            "dnssec": "yes" if deep.get("dnssec") else "no", "dane": "yes" if deep.get("dane") else "no"}


def servers(deep):
    out = []
    for s in (deep.get("autoconfig") or {}).get("servers", []):
        host = s.get("host", "")
        if host and "%" not in host:
            out.append((s["type"].upper(), f"{host}:{s.get('port')}", SOCKET.get(s.get("socket"), s.get("socket", ""))))
    if not out:
        for name, recs in (deep.get("srv") or {}).items():
            kind, sock = SRV_KIND.get(name, ("", ""))
            for r in recs[:1]:
                parts = r.split()
                if kind and len(parts) == 4:
                    out.append((kind, f"{parts[3].rstrip('.')}:{parts[2]}", sock))
    probes = [p for p in deep.get("imap", []) if (p.get("banner") or "").startswith("* OK")]
    if not any(k == "IMAP" for k, _, _ in out) and probes:
        out.append(("IMAP", f"{probes[0]['host']}:993", "SSL/TLS"))
    soft = next((p["software"] for p in probes if p.get("software")), "")
    if soft:
        out.append(("IMAP software", soft, ""))
    return [dict((k, v) for k, v in zip(("kind", "host", "note"), s) if v) for s in dict.fromkeys(out[:7])]


class Store:
    """The provider files, a change log and the report."""

    def __init__(self):
        self.recs = {r["domain"].lower(): r for r in emcdata.load_all()}
        self.changed, self.report = set(), {}

    def note(self, section, line):
        self.report.setdefault(section, []).append(line)

    def put(self, domain, key, value, section, note=None):
        r = self.recs[domain]
        if key.startswith("links."):
            links = r.setdefault("links", {})
            old = links.get(key[6:])
            links[key[6:]] = value
        else:
            old = r.get(key)
            r[key] = value
        if old != value:
            self.changed.add(domain)
            self.note(section, f"{domain}: {note or f'{key} → {value}'}")

    def save(self, report_path, title):
        for d in sorted(self.changed):
            r = self.recs[d]
            if not r.get("links"):
                r.pop("links", None)
            emcdata.dumps({k: v for k, v in r.items() if not k.startswith("_")})  # unknown fields fail here
        if not DRY_RUN:
            for d in sorted(self.changed):
                emcdata.save(self.recs[d])
        lines = [f"# {title}", "", f"Run: {TODAY}" + (" (dry run, nothing written)" if DRY_RUN else ""),
                 f"Files changed: {len(self.changed)}", ""]
        for section, items in self.report.items():
            lines += [f"## {section} ({len(items)})", ""] + [f"- {x}" for x in items] + [""]
        common.write_text(report_path, "\n".join(lines) + "\n")
        print(f"{len(self.changed)} files {'would change' if DRY_RUN else 'changed'}; report: {report_path}")

    def selected(self, only):
        return [d for d in self.recs if not only or d in only]


def cmd_add(work):
    st = Store()
    order = max((r["order"] for r in st.recs.values()), default=0)
    paths = sorted(glob.glob(os.path.join(work, "records", "*.yaml")))
    if not paths:
        raise SystemExit(f"merge.py: no draft records in {os.path.join(work, 'records')}; run collect.py first")
    for path in paths:
        with open(path, encoding="utf-8") as f:
            rec = emcdata.loads(f.read())
        d = rec["domain"].lower()
        if d in st.recs:
            st.note("Skipped (already in the data)", d)
            continue
        order += 1
        rec.update(order=order, status="pending")
        rec.pop("dead", None)
        st.recs[d] = rec
        st.changed.add(d)
        st.note("Added", f"{d} (order {order})")
    st.save(os.path.join(work, "review-add.md"), "collect.py drafts → data/providers")


def cmd_browser(out_dir, only):
    st = Store()
    for d in st.selected(only):
        br = load(os.path.join(out_dir, d + ".json"))
        if not br:
            continue
        links = st.recs[d].get("links") or {}
        for key in LINK_KEYS:
            if not links.get(key) and br.get(key):
                st.put(d, "links." + key, br[key], "Links filled")
        if not st.recs[d].get("description") and br.get("state") == "ok":
            desc = clean(br.get("desc") or next(iter(br.get("paras") or []), "") or br.get("h1", ""))
            if desc:
                st.put(d, "description", desc, "Descriptions filled")
        for u in br.get("code_other", []):
            st.note("Code links found but not used", f"{d}: {u}")
    st.save(os.path.join(out_dir, "review-browser.md"), "collect_browser.py → data/providers")


def cmd_refresh(base, only):
    """Rules: expiry replaced when RDAP differs; creation date filled when empty; A replaced when none of the old
    addresses is still in DNS; MX replaced when the hosts changed; links, description, imprint, social accounts,
    onion and owner (owner_accept.json) filled when empty; security, servers, online_since and last_checked
    rewritten. Hand decisions live in collector/decisions/: owner_accept.json, social_accept.txt (accounts whose
    name does not match the domain but are the provider's), social_reject.txt, skip.json (fields removed by hand
    that the collectors keep finding)."""
    st = Store()
    info = load(os.path.join(base, "dns", "ipinfo.json"))
    accept, reject = read_words("social_accept.txt"), read_words("social_reject.txt")
    skip = load(os.path.join(DECISIONS, "skip.json"))           # {"domain": ["links.privacy", "description"]}
    owners = load(os.path.join(DECISIONS, "owner_accept.json"))  # {domain: "🇩🇪 Example GmbH"}
    names = {flag(v.get("countryCode")): ("Netherlands" if v.get("country") == "The Netherlands" else v.get("country"))
             for v in info.values() if v.get("status") == "success"}
    for d in st.selected(only):
        dns = load(os.path.join(base, "dns", d + ".json"))
        if not dns:
            continue
        deep = load(os.path.join(base, "deep", d + ".json"))
        br = load(os.path.join(base, "browser", d + ".json"))
        r = st.recs[d]
        reg = dns.get("reg", {})
        if reg.get("expires") and reg["expires"] != r.get("expires"):
            st.put(d, "expires", reg["expires"], "Domain expiry updated", f"{r.get('expires') or '—'} → {reg['expires']}")
        if reg.get("created"):
            if not r.get("created"):
                st.put(d, "created", reg["created"], "Domain created")
            elif reg["created"] != r["created"]:
                st.note("Domain created", f"{d}: file {r['created']} vs RDAP {reg['created']} (not changed)")
        if dns.get("status") == "NOERROR":
            new_a = dns.get("a", [])[:2]
            old_a = {x["host"].lower() for x in r.get("a", [])}
            if new_a and not (old_a & set(new_a)):
                st.put(d, "a", [ip_record(info, ip, ip) for ip in new_a], "A records replaced",
                       f"{' '.join(sorted(old_a)) or '—'} → {' '.join(new_a)}")
            hosts = list(dict.fromkeys(h for _, h in dns.get("mx", [])))[:4]
            old_mx = [x["host"].lower() for x in r.get("mx", [])]
            if hosts and set(hosts) != set(old_mx) and not (old_mx and old_mx[0].startswith("null mx")):
                recs = []
                for h in hosts:
                    ips = dns.get("mx_ips", {}).get(h, [])
                    recs.append(ip_record(info, ips[0], h) if ips else
                                {"host": h, "flag": "", "country": "", "asn": "", "org": "(no A record)"})
                st.put(d, "mx", recs, "MX records replaced", f"{' '.join(old_mx) or '—'} → {' '.join(hosts)}")
        links = r.get("links") or {}
        for key in LINK_KEYS:
            if not links.get(key) and br.get(key) and "links." + key not in skip.get(d, []):
                st.put(d, "links." + key, br[key], "Links filled")
        if not r.get("description") and br.get("state") == "ok" and "description" not in skip.get(d, []):
            desc = clean(br.get("desc") or next(iter(br.get("paras") or []), "") or br.get("h1", ""))
            if desc:
                st.put(d, "description", desc, "Descriptions filled")
        guess = (br.get("owner_guess") or "").strip()
        if not r.get("owner") and owners.get(d):
            st.put(d, "owner", owners[d], "Owner filled from owner_accept.json")
        elif guess:
            if not r.get("owner"):
                st.note("Operator guesses (approve in owner_accept.json)",
                        f"{d}: '{guess}' (imprint country {br.get('imprint_cc') or '?'})")
            elif guess.lower() not in r["owner"].lower():
                st.note("Operator on the site differs", f"{d}: file '{r['owner']}' · site '{guess}'")
        own = re.match(r"^\s*([\U0001F1E6-\U0001F1FF]{2})", r.get("owner", ""))
        jur = r.get("jurisdiction") or []
        if own and len(jur) == 1 and names.get(own.group(1), jur[0]) != jur[0]:
            st.note("Owner's country differs from the jurisdiction",
                    f"{d}: owner {r['owner']} · jurisdiction {jur[0]} · flag {r.get('flag', '')}")
        if r.get("dead") and br.get("state") == "ok":
            st.note("Struck out but answering", f"{d}: {br.get('title', '')[:60]}")
        social, seen = [], set()
        for s in br.get("social", []):
            key = (s["net"], s["handle"].lower())
            if s["url"] in reject or key in seen:
                continue
            seen.add(key)
            if s.get("match") or s["url"] in accept:
                social.append(s["url"])
            else:
                st.note("Social links to check (name does not match the domain)", f"{d}: {s['net']} {s['url']}")
        if not r.get("social") and social:
            st.put(d, "social", list(dict.fromkeys(social)), "Social links filled")
        onion = br.get("onion", "")
        if not r.get("onion") and re.fullmatch(r"https?://[a-z2-7]{56}\.onion/", onion):
            st.put(d, "onion", onion, "Onion filled")
        if not links.get("imprint") and br.get("imprint"):
            st.put(d, "links.imprint", br["imprint"], "Imprint filled")
        for key, value in (("security", security(d, dns, deep)), ("servers", servers(deep)),
                           ("online_since", (deep.get("wayback") or {}).get("first", "")), ("last_checked", TODAY)):
            if value != r.get(key, {} if key == "security" else [] if key == "servers" else ""):
                if value:
                    st.put(d, key, value, "Rewritten every run", f"{key} updated")
                elif key in r:
                    del r[key]
                    st.changed.add(d)
                    st.note("Rewritten every run", f"{d}: {key} removed (no data)")
    st.save(os.path.join(base, "review-refresh.md"), "Data refresh → data/providers")


def cmd_set(path):
    st = Store()
    dec = load(path)
    if not isinstance(dec, dict) or not isinstance(dec.get("set"), dict):
        raise SystemExit(f'{path}: expected {{"set": {{"<domain>": {{"<field>": value}}}}}}')
    for domain, fields in dec["set"].items():
        d = domain.lower()
        if d not in st.recs:
            st.note("Unknown domains (skipped)", d)
            continue
        for key, value in fields.items():
            if value is None:
                target = st.recs[d].get("links", {}) if key.startswith("links.") else st.recs[d]
                if target.pop(key[6:] if key.startswith("links.") else key, None) is not None:
                    st.changed.add(d)
                    st.note("Removed", f"{d}: {key}")
            else:
                st.put(d, key, value, "Set")
    st.save(os.path.splitext(path)[0] + "-review.md", f"{os.path.basename(path)} → data/providers")


def main(argv=None):
    global DRY_RUN
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="write the report only, change no record")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND", required=True)
    sub.add_parser("add", help="add collect.py drafts as pending providers").add_argument("work_dir")
    for name, what in (("browser", "collect_browser.py results"), ("refresh", "a re-check folder (dns/deep/browser)")):
        p = sub.add_parser(name, help=f"merge {what}")
        p.add_argument("dir")
        p.add_argument("domains", nargs="*", metavar="DOMAIN", help="only these providers")
    sub.add_parser("set", help="apply hand-checked changes from a JSON file").add_argument("decisions_json")
    args = ap.parse_args(argv)
    DRY_RUN = args.dry_run
    only = set()
    try:
        only = {common.normalize_domain(d) for d in getattr(args, "domains", [])}
    except ValueError as e:
        ap.error(str(e))
    if args.cmd == "add":
        cmd_add(args.work_dir)
    elif args.cmd == "browser":
        cmd_browser(args.dir, only)
    elif args.cmd == "refresh":
        cmd_refresh(args.dir, only)
    else:
        cmd_set(args.decisions_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
