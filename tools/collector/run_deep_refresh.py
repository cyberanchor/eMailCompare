#!/usr/bin/env python3
"""collect_deep.py for a data refresh: mail DNS, autoconfig, IMAP, Wayback; crt.sh and check-host left out.

usage: run_deep_refresh.py DOMAINS_TSV DNS_DIR OUT_DIR

The same as: collect_deep.py --no-crtsh --no-checkhost DOMAINS_TSV DNS_DIR OUT_DIR/_noweb OUT_DIR
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_deep  # noqa: E402


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or argv[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr if len(argv) != 1 else sys.stdout)
        return 0 if argv[:1] in (["-h"], ["--help"]) else 2
    tsv, dns_dir, out = argv
    return collect_deep.main(["--no-crtsh", "--no-checkhost", tsv, dns_dir, os.path.join(out, "_noweb"), out])


if __name__ == "__main__":
    sys.exit(main())
