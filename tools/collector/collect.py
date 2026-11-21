#!/usr/bin/env python3
"""Collect public data about e-mail providers and write draft records for data/providers/.

usage: python3 tools/collector/collect.py [--work DIR] [--no-crtsh] [--no-checkhost] DOMAIN [DOMAIN ...]

Four steps, each caching its results in DIR (delete a domain's files there to collect it again):
  1. collect_dns.py  - A/MX records, RDAP/whois registration dates, ip-api.com country and ASN
  2. collect_web.py  - homepage plus terms, privacy, imprint, pricing, signup and e-mail pages; follows and
                       records every redirect, obeys robots.txt, falls back to the Wayback Machine
  3. collect_deep.py - SRV/SPF/DMARC/MTA-STS/DANE, mail client autoconfig, IMAP and TLS certificates,
                       crt.sh, Wayback first/last capture, check-host.net for unreachable sites
  4. build_csv.py    - draft records (DIR/records/), rows in the old emails.csv layout (DIR/rows.csv) and a
                       readable DIR/review.md; hand-checked fixes come from overrides.json next to this script
Then: merge.py add DIR. Downloaded pages are only parsed as text. Review the drafts before adding them.

Each step runs in its own isolated Python process (python -I), so nothing in the work folder can be imported.
Exit code: 0 when every step succeeded, 1 when a step reported failed domains, 2 on bad arguments.
"""
import argparse
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import common  # noqa: E402

PSL_URL = "https://publicsuffix.org/list/public_suffix_list.dat"
PSL_MAX_AGE = 30 * 86400  # refresh the cached public suffix list after 30 days


def default_work():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "emailcompare-collector")


def fetch_psl(path):
    import time
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < PSL_MAX_AGE:
        return
    req = urllib.request.Request(PSL_URL, headers={"User-Agent": common.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode("utf-8")
    except Exception as e:  # noqa: BLE001
        if os.path.exists(path):  # an old copy is better than none
            print(f"warning: public suffix list not refreshed ({e}); using the cached copy", file=sys.stderr)
            return
        raise SystemExit(f"collect.py: cannot download the public suffix list: {e}")
    if "===BEGIN ICANN DOMAINS===" not in text:
        raise SystemExit("collect.py: the downloaded public suffix list looks wrong")
    common.write_text(path, text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("domains", nargs="+", help="provider domains, e.g. example.com")
    ap.add_argument("--work", default=default_work(), help="cache directory (default: %(default)s)")
    ap.add_argument("--no-crtsh", action="store_true", help="skip certificate transparency (crt.sh)")
    ap.add_argument("--no-checkhost", action="store_true", help="skip check-host.net tests from abroad")
    args = ap.parse_args(argv)
    try:
        domains = list(dict.fromkeys(common.normalize_domain(d) for d in args.domains))
    except ValueError as e:
        ap.error(str(e))
    os.makedirs(args.work, exist_ok=True)

    import emcdata  # providers marked dead in data/providers get a note
    struck = {r["domain"].lower() for r in emcdata.load_all() if r.get("dead")}
    domains_tsv = os.path.join(args.work, "domains.tsv")
    common.write_text(domains_tsv, "".join(f"{d}\t{int(d in struck)}\n" for d in domains))
    psl = os.path.join(args.work, "public_suffix_list.dat")
    fetch_psl(psl)

    worst = 0

    def run(script, *params):
        nonlocal worst
        rc = subprocess.run([sys.executable, "-I", os.path.join(HERE, script), *params], cwd=args.work).returncode
        if rc:
            print(f"warning: {script} exited with code {rc}", file=sys.stderr, flush=True)
            worst = max(worst, 1)
        return rc

    dns_dir, web_dir, deep_dir = (os.path.join(args.work, d) for d in ("dns", "web", "deep"))
    rows, review = os.path.join(args.work, "rows.csv"), os.path.join(args.work, "review.md")
    run("collect_dns.py", domains_tsv, psl, dns_dir)
    run("collect_web.py", domains_tsv, web_dir)
    deep_flags = (["--no-crtsh"] if args.no_crtsh else []) + (["--no-checkhost"] if args.no_checkhost else [])
    run("collect_deep.py", *deep_flags, domains_tsv, dns_dir, web_dir, deep_dir)
    if run("build_csv.py", "--deep", deep_dir, domains_tsv, dns_dir, web_dir, rows, review):
        return 1
    print(f"\nrows:   {rows}\nreview: {review}\nnext:   python3 {os.path.join(HERE, 'merge.py')} add {args.work}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
