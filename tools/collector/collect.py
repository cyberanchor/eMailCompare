#!/usr/bin/env python3
"""Collect public data about e-mail providers and write draft rows in the emails.csv format.

usage: python3 tools/collector/collect.py [--work DIR] DOMAIN [DOMAIN ...]

Four steps, each caching its results in DIR (delete a domain's files there to collect it again):
  1. collect_dns.py  - A/MX records, RDAP/whois registration dates, ip-api.com country and ASN
  2. collect_web.py  - homepage plus terms, privacy, imprint, pricing, signup and e-mail pages; follows and
                       records every redirect, retries bot checks, falls back to the Wayback Machine
  3. collect_deep.py - SRV/SPF/DMARC/MTA-STS/DANE, mail client autoconfig, IMAP and TLS certificates,
                       crt.sh, Wayback first/last capture, check-host.net for unreachable sites
  4. build_csv.py    - rows for emails.csv (DIR/rows.csv) and a readable DIR/review.md;
                       hand-checked fixes come from overrides.json next to this script
Downloaded pages are only parsed as text. Review the rows before adding them to emails.csv.
"""
import argparse
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PSL_URL = "https://publicsuffix.org/list/public_suffix_list.dat"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("domains", nargs="+", help="provider domains, e.g. example.com")
    parser.add_argument("--work", default=os.path.join(os.path.expanduser("~"), ".cache", "emailcompare-collector"),
                        help="cache directory (default: %(default)s)")
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)

    sys.path.insert(0, os.path.dirname(HERE))
    import emcdata  # providers marked dead in data/providers get a note
    struck = {r["domain"].lower() for r in emcdata.load_all() if r.get("dead")}
    domains_tsv = os.path.join(args.work, "domains.tsv")
    with open(domains_tsv, "w", encoding="utf-8") as f:
        for domain in args.domains:
            domain = domain.strip().lower()
            f.write(f"{domain}\t{int(domain in struck)}\n")
    psl = os.path.join(args.work, "public_suffix_list.dat")
    if not os.path.exists(psl):
        urllib.request.urlretrieve(PSL_URL, psl)

    def run(script, *params):
        subprocess.run([sys.executable, "-I", os.path.join(HERE, script), *params], check=True)

    dns_dir, web_dir, deep_dir = (os.path.join(args.work, d) for d in ("dns", "web", "deep"))
    rows, review = os.path.join(args.work, "rows.csv"), os.path.join(args.work, "review.md")
    run("collect_dns.py", domains_tsv, psl, dns_dir)
    run("collect_web.py", domains_tsv, web_dir)
    run("collect_deep.py", domains_tsv, dns_dir, web_dir, deep_dir)
    run("build_csv.py", domains_tsv, dns_dir, web_dir, "-", rows, review, deep_dir)
    print(f"\nrows:   {rows}\nreview: {review}")


if __name__ == "__main__":
    main()
