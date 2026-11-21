#!/usr/bin/env python3
"""Write the "Checked email services" and "Pending email services" tables of README.md from data/providers/,
then align the | of every Markdown table in the file.

  checked table: status checked -> ✅, auto -> 🤖; a struck-out (dead) provider -> ❌
  pending table: status pending -> ❌
  domain: ~~domain~~ when dead; flag: the record's flag, ❓ when it has none
  order: the records' "order"

Widths are measured as a monospace editor shows them: emoji and wide characters take two columns, combining
marks and variation selectors none.

usage: python3 tools/readme_tables.py [-h] [README]
"""
import argparse
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emcdata  # noqa: E402

ZERO_WIDTH = {"‍", "︎", "️"}


def width(text):
    return sum(0 if unicodedata.combining(ch) or ch in ZERO_WIDTH
               else 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def align(rows):
    cells = [[c.strip() for c in row.strip().strip("|").split("|")] for row in rows]
    ncol = max(len(r) for r in cells)
    cells = [r + [""] * (ncol - len(r)) for r in cells]
    sep = [i for i, r in enumerate(cells) if all(re.fullmatch(r":?-+:?", c) for c in r if c)]
    widths = [max([width(r[j]) for i, r in enumerate(cells) if i not in sep] + [3]) for j in range(ncol)]
    out = []
    for i, r in enumerate(cells):
        if i in sep:
            parts = [((":" if c.startswith(":") else "-") + "-" * widths[j] + (":" if c.endswith(":") and len(c) > 1 else "-"))
                     if c else "-" * (widths[j] + 2) for j, c in enumerate(r)]
            out.append("|" + "|".join(parts) + "|")
        else:
            out.append("| " + " | ".join(c + " " * (widths[j] - width(c)) for j, c in enumerate(r)) + " |")
    return out


def rows_for(recs, section):
    out = []
    for r in recs:
        if (r["status"] == "pending") != (section == "pending"):
            continue
        dead = r.get("dead", False)
        mark = "❌" if section == "pending" or dead else {"checked": "✅", "auto": "🤖"}[r["status"]]
        domain = f"~~{r['domain']}~~" if dead else r["domain"]
        out.append(f"| {mark} | {domain} | {r.get('flag') or '❓'} |")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("readme", nargs="?", default=os.path.join(emcdata.ROOT, "README.md"))
    path = ap.parse_args(argv).readme
    recs = emcdata.load_all()
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    out, i, done = [], 0, set()
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        section = ("checked" if "Checked email services" in line else "pending" if "Pending email services" in line
                   else None) if line.startswith("## ") else None
        if not section:
            continue
        while i < len(lines) and not lines[i].strip().startswith("|"):  # text between heading and table
            out.append(lines[i])
            i += 1
        out += lines[i:i + 2]  # header row and separator
        i += 2
        while i < len(lines) and lines[i].strip().startswith("|"):  # old rows
            i += 1
        out += rows_for(recs, section)
        done.add(section)
    if done != {"checked", "pending"}:
        raise SystemExit(f"{path}: tables not found: {sorted({'checked', 'pending'} - done)}")
    final, block = [], []
    for line in out + [""]:
        if line.strip().startswith("|"):
            block.append(line)
            continue
        if block:
            final += align(block)
            block = []
        final.append(line)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(final[:-1]))
    print(f"{path}: {sum(r['status'] != 'pending' for r in recs)} checked, "
          f"{sum(r['status'] == 'pending' for r in recs)} pending rows")


if __name__ == "__main__":
    main()
