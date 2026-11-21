#!/usr/bin/env python3
"""collect_deep.py for a data refresh: mail DNS, autoconfig, IMAP, Wayback; crt.sh and check-host left out.

usage: run_deep_refresh.py DOMAINS_TSV DNS_DIR OUT_DIR
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
tsv, dns_dir, out = sys.argv[1:4]
sys.argv = ["collect_deep.py", tsv, dns_dir, os.path.join(out, "_noweb"), out]
sys.path.insert(0, HERE)
import collect_deep as cd  # noqa: E402

cd.crtsh = lambda base: None
cd.checkhost = lambda kind, target, nodes=5: None
cd.main()
