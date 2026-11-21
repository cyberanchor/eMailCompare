#!/usr/bin/env python3
"""Checks for data/providers/*.yaml and everything built from it. Exit code 1 on any error.

usage: python3 tools/test_data.py [--csv OLD_EMAILS_CSV --readme OLD_README] [--no-site]

  1. schema       every file: known keys only, types, formats (dates, flags, URLs, records), unique domain/order
  2. format       every file is written exactly the way tools/emcdata.dumps() writes it (stable diffs)
  3. csv          with --csv/--readme: every record gives back the old CSV row cell for cell, and the status,
                  strikethrough and order of the old README tables
  4. hugo         site/public/providers.json (how Hugo read the files) equals how Python reads them, types included
  5. exports      site/public/providers.csv equals the records
  6. pages        one page per record in site/public/providers/, nothing more
  7. readme       the Checked / Pending tables of README.md match status, strikethrough and order
  warnings        alliance that does not follow the jurisdiction, owner flag different from the row flag
"""
import argparse
import csv
import datetime
import glob
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emcdata as E  # noqa: E402

ROOT = E.ROOT
PUBLIC = os.path.join(ROOT, "site", "public")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FLAG = re.compile(r"^[\U0001F1E6-\U0001F1FF]{2}$")
URL = re.compile(r"^https?://\S+$")
ONION = re.compile(r"^https?://[a-z2-7]{56}\.onion/$")
FIVE, NINE, FOURTEEN = {"US", "GB", "CA", "AU", "NZ"}, {"DK", "FR", "NL", "NO"}, {"DE", "BE", "IT", "ES", "SE"}

errors, warnings = [], []


def err(msg):
    errors.append(msg)


def check_schema(recs):
    seen_d, seen_o = {}, {}
    for r in recs:
        f = r.pop("_file")
        d = r.get("domain")
        where = f
        for k in r:
            if k not in E.KEYS:
                err(f"{where}: unknown key {k!r}")
        if not isinstance(d, str) or d != d.strip() or d.lower() + ".yaml" != f:
            err(f"{where}: domain {d!r} does not match the file name")
        if not isinstance(r.get("order"), int) or isinstance(r.get("order"), bool) or r["order"] < 1:
            err(f"{where}: order must be a positive integer")
        if r.get("status") not in E.STATUSES:
            err(f"{where}: status {r.get('status')!r}")
        if "dead" in r and r["dead"] is not True:
            err(f"{where}: dead is only written as true")
        for k in ("domain", "flag", "created", "expires", "owner", "description", "tos_summary", "privacy_summary",
                  "email", "phone", "personal", "note", "onion", "online_since", "last_checked"):
            if k in r and (not isinstance(r[k], str) or not r[k] or r[k] != r[k].strip()):
                err(f"{where}: {k} must be a non-empty trimmed string, is {r[k]!r}")
        for k in ("domains", "jurisdiction", "price", "social"):
            if k in r and (not isinstance(r[k], list) or not r[k]
                           or not all(isinstance(x, str) and x and x == x.strip() and "\n" not in x for x in r[k])):
                err(f"{where}: {k} must be a list of one-line strings")
        if any(isinstance(p, str) and ";" in p for p in r.get("price", []) or []):
            err(f"{where}: a price part contains ';'")
        if "flag" in r and not (isinstance(r["flag"], str) and FLAG.match(r["flag"])):
            err(f"{where}: flag {r['flag']!r}")
        for k in ("created", "expires", "online_since", "last_checked"):
            if k in r and not (isinstance(r[k], str) and DATE.match(r[k]) and valid_date(r[k])):
                err(f"{where}: {k} {r[k]!r} is not a date YYYY-MM-DD")
        if "alliance" in r and r["alliance"] not in E.ALLIANCE:
            err(f"{where}: alliance {r['alliance']!r}")
        for k in ("a", "mx"):
            for rec in r.get(k, []):
                if not isinstance(rec, dict) or set(rec) != set(E.RECORD) or not all(isinstance(v, str) for v in rec.values()):
                    err(f"{where}: {k} record {rec!r} needs exactly {E.RECORD} as strings")
                    continue
                if any("|" in rec[x] or "\n" in rec[x] for x in ("host", "flag", "country", "asn")) or "\n" in rec["org"]:
                    err(f"{where}: {k} record {rec!r} has '|' or a line break")
                if not rec["host"] or (rec["flag"] and not FLAG.match(rec["flag"])) or (rec["asn"] and not re.match(r"^AS\d+$", rec["asn"])):
                    err(f"{where}: {k} record {rec!r}")
        links = r.get("links", {})
        if "links" in r and (not isinstance(links, dict) or not links or set(links) - set(E.LINKS)):
            err(f"{where}: links {links!r}")
        for k, v in (links.items() if isinstance(links, dict) else []):
            if not isinstance(v, str) or not URL.match(v):
                err(f"{where}: links.{k} {v!r} is not a URL")
        for u in r.get("social", []) if isinstance(r.get("social"), list) else []:
            if not (isinstance(u, str) and (URL.match(u) or u.startswith("xmpp:"))):
                err(f"{where}: social {u!r}")
        if "onion" in r and not (isinstance(r["onion"], str) and ONION.match(r["onion"])):
            warnings.append(f"{where}: onion {r['onion']!r} is not a v3 address (the site hides it)")
        sec = r.get("security", {})
        if "security" in r and (not isinstance(sec, dict) or not sec or set(sec) - {k for k, _ in E.SECURITY}
                                or not all(isinstance(v, str) and v and ";" not in v for v in sec.values())):
            err(f"{where}: security {sec!r}")
        for s in r.get("servers", []):
            if not isinstance(s, dict) or set(s) - {"kind", "host", "note"} or not s.get("kind") or not s.get("host") \
                    or not all(isinstance(v, str) and v and "|" not in v and "\n" not in v for v in s.values()):
                err(f"{where}: server {s!r}")
        for k in ("email", "phone", "personal"):
            if k in r and isinstance(r[k], str) and r[k].lower() not in ("yes", "no", "optional", "required"):
                warnings.append(f"{where}: {k} {r[k]!r} has no colour on the site")
        for s in strings(r):
            if "\r" in s:
                err(f"{where}: carriage return in {s[:40]!r}")
        slug = str(d).lower().replace(".", "-")
        if slug in seen_d:
            err(f"{where}: page address providers/{slug}/ also used by {seen_d[slug]}")
        seen_d[slug] = f
        if r.get("order") in seen_o:
            err(f"{where}: order {r.get('order')} also in {seen_o[r.get('order')]}")
        seen_o[r.get("order")] = f
        r["_file"] = f


def valid_date(s):
    try:
        datetime.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from strings(x)


def load_files():
    """Every file, read on its own: a file that is not a YAML mapping is an error, not a crash."""
    recs = []
    for p in sorted(glob.glob(os.path.join(E.DIR, "*.yaml"))):
        try:
            r = E.loads(open(p, encoding="utf-8").read())
        except Exception as e:  # noqa: BLE001
            err(f"{os.path.basename(p)}: not valid YAML: {str(e).splitlines()[0]}")
            continue
        if not isinstance(r, dict):
            err(f"{os.path.basename(p)}: not a mapping of fields (empty or only comments?)")
            continue
        r["_file"] = os.path.basename(p)
        recs.append(r)
    recs.sort(key=lambda r: r["order"] if isinstance(r.get("order"), int) else 10 ** 9)
    return recs


def lint(recs):
    rank = lambda cc: 3 if cc in FIVE else 2 if cc in NINE else 1 if cc in FOURTEEN else 0  # noqa: E731
    label = {3: 5, 2: 9, 1: 14, 0: None}
    names = {}
    for r in recs:
        for rec in r.get("a", []) + r.get("mx", []):
            if rec["flag"]:
                names[rec["country"]] = rec["flag"]
    names.update({"United States": "🇺🇸", "United Kingdom": "🇬🇧", "Australia": "🇦🇺", "Canada": "🇨🇦"})
    cc = lambda flag: "".join(chr(ord(c) - 0x1F1E6 + 65) for c in flag)  # noqa: E731
    for r in recs:
        ccs = []
        for j in r.get("jurisdiction", []):
            m = re.match(r"^([\U0001F1E6-\U0001F1FF]{2}) (.+)$", j)
            flag = m.group(1) if m else names.get(j)
            if flag:
                ccs.append(cc(flag))
        if ccs and len(ccs) == len(r.get("jurisdiction", [])):
            want = label[max(rank(c) for c in ccs)]
            if want != r.get("alliance"):
                warnings.append(f"{r['_file']}: alliance {r.get('alliance')} but jurisdiction {r['jurisdiction']} "
                                f"means {want}")
        own = re.match(r"^([\U0001F1E6-\U0001F1FF]{2})", r.get("owner", ""))
        if own and r.get("flag") and own.group(1) != r["flag"]:
            warnings.append(f"{r['_file']}: owner {r['owner']!r} vs row flag {r['flag']}")


def check_format(recs):
    for r in recs:
        p = os.path.join(E.DIR, r["_file"])
        text = open(p, encoding="utf-8").read()
        try:
            same = E.dumps({k: v for k, v in r.items() if not k.startswith("_")}) == text
        except Exception as e:  # noqa: BLE001  unknown fields and the like, reported by check_schema too
            err(f"{r['_file']}: cannot be written back: {e}")
            continue
        if not same:
            err(f"{r['_file']}: not in the canonical format (write it with tools/emcdata.save)")


def readme_tables(text):
    out, section = [], ""
    for line in text.split("\n"):
        if line.startswith("## ") and "Checked email services" in line:
            section = "checked"
        elif line.startswith("## ") and "Pending email services" in line:
            section = "pending"
        elif line.startswith("<!--") or (line.startswith("## ") and section == "pending"):
            section = ""
        s = line.strip()
        if section and s.startswith("|"):
            cells = s.strip("|").split("|")
            if len(cells) >= 3 and cells[0].strip() in ("✅", "🤖", "❌"):
                raw = cells[1]
                dead = "̶" in raw or "~~" in raw
                d = raw.replace("̶", "").replace("~~", "").strip().lower()
                mark = cells[0].strip()
                st = "pending" if section == "pending" else ("auto" if mark == "🤖" else "checked")
                out.append((d, st, dead, section, mark))
    return out


def check_readme(recs, text, label):
    rows = readme_tables(text)
    by = {r["domain"].lower(): r for r in recs}
    if sorted(d for d, *_ in rows) != sorted(by):
        err(f"{label}: tables list {sorted(set(d for d, *_ in rows) ^ set(by))} differently from the files")
    for d, st, dead, section, mark in rows:
        r = by.get(d)
        if r and (r["status"], r.get("dead", False)) != (st, dead):
            err(f"{label}: {d} is {st}{' dead' if dead else ''} there, {r['status']}{' dead' if r.get('dead') else ''} in the file")
        if r and section == "checked" and mark == "❌" and not dead:
            err(f"{label}: {d} has ❌ in the checked table but is not struck out")
    for section in ("checked", "pending"):
        got = [d for d, st, dead, sec, _ in rows if sec == section]
        want = [r["domain"].lower() for r in recs if (r["status"] == "pending") == (section == "pending")]
        if got != want:
            err(f"{label}: the {section} table is not in the files' order")


def check_csv(recs, path):
    rows = list(csv.reader(open(path, encoding="utf-8", newline="")))
    if rows[0] != E.HEADER:
        err(f"{path}: header differs")
    if len(rows) - 1 != len(recs):
        err(f"{path}: {len(rows) - 1} rows, {len(recs)} records")
    for n, (cells, r) in enumerate(zip(rows[1:], recs), 1):
        want = [E.clean_cell(c) for c in cells]
        got = E.to_row(r)
        if got != want:
            for i, (a, b) in enumerate(zip(got, want)):
                if a != b:
                    err(f"{path} row {n} ({b if i == 1 else r['domain']}) {E.HEADER[i]!r}: {b!r} -> {a!r}")
        if r["order"] != n:
            err(f"{r['_file']}: order {r['order']} but row {n} in {path}")


def strip_meta(recs):
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in recs]


def check_site(recs):
    pj = os.path.join(PUBLIC, "providers.json")
    if not os.path.exists(pj):
        err("site/public/providers.json missing: build the site first (site/build.sh)")
        return
    hugo = json.load(open(pj, encoding="utf-8"))
    py = strip_meta(recs)
    if hugo != py:
        for a, b in zip(hugo, py):
            if a != b:
                for k in set(a) | set(b):
                    if a.get(k) != b.get(k):
                        err(f"Hugo read {b.get('domain')} {k}: {a.get(k)!r} — Python: {b.get(k)!r}")
        if len(hugo) != len(py):
            err(f"providers.json has {len(hugo)} records, the files {len(py)}")
    # types: an int in Python must be an int for Hugo, a string a string
    for a, b in zip(hugo, py):
        for k in b:
            if type(a.get(k)) is not type(b[k]):
                err(f"Hugo type of {b['domain']} {k}: {type(a.get(k)).__name__} vs {type(b[k]).__name__}")
    rows = list(csv.reader(io.StringIO(open(os.path.join(PUBLIC, "providers.csv"), encoding="utf-8", newline="").read())))
    if rows[0] != E.HEADER:
        err("providers.csv header differs")
    if [E.to_row(r) for r in recs] != rows[1:]:
        bad = [r["domain"] for r, row in zip(recs, rows[1:]) if E.to_row(r) != row]
        err(f"providers.csv differs from the records: {bad[:10]} (rows: {len(rows) - 1})")
    pages = {d for d in os.listdir(os.path.join(PUBLIC, "providers")) if os.path.isdir(os.path.join(PUBLIC, "providers", d))}
    slugs = {r["domain"].lower().replace(".", "-") for r in recs}
    if pages != slugs:
        err(f"provider pages differ from the files: {sorted(pages ^ slugs)}")
    home = open(os.path.join(PUBLIC, "index.html"), encoding="utf-8").read()
    if home.count("data-emc-row") != len(recs):
        err(f"home table has {home.count('data-emc-row')} rows, {len(recs)} records")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--readme")
    ap.add_argument("--no-site", action="store_true")
    args = ap.parse_args()
    recs = load_files()
    print(f"records: {len(recs)}")
    check_schema(recs)
    check_format(recs)
    if errors:  # the other checks need well-formed records
        for e in errors:
            print("ERROR:", e)
        print(f"{len(errors)} errors (schema); fix these first")
        sys.exit(1)
    lint(recs)
    if args.csv:
        check_csv(recs, args.csv)
    if args.readme:
        check_readme(recs, open(args.readme, encoding="utf-8").read(), args.readme)
    check_readme(recs, open(os.path.join(ROOT, "README.md"), encoding="utf-8").read(), "README.md")
    if not args.no_site:
        check_site(recs)
    for w in warnings:
        print("warning:", w)
    for e in errors:
        print("ERROR:", e)
    print(f"{len(errors)} errors, {len(warnings)} warnings")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
