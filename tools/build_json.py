#!/usr/bin/env python3
"""Regenerate web/data.json from emails.csv.

Each CSV cell becomes {"value": <cell stripped, or null when empty>, "color": ...}. Colours follow the
rules the table has always used (see color_for). Every cell of a provider that is struck out in README.md
is grey.

usage: python3 tools/build_json.py
"""
import csv
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKS = ("✅", "❌", "🤖")


def color_for(key, value):
    v = (value or "").strip().lower()
    if key == "Domain crated":  # old domains are a good sign, very new ones a warning
        if not value:
            return "none"
        year = int(value[:4])
        return "green" if year <= 2010 else "red" if year >= 2021 else "none"
    if key == "Intelligence Alliance":
        for prefix, color in (("five eyes", "red"), ("nine eyes", "yellow"), ("fourteen eyes", "purple")):
            if v.startswith(prefix):
                return color
        return "none"
    if key == "Email(receive a verification link)":
        return {"yes": "yellow", "no": "red"}.get(v, "none")
    if key == "Phone(receive s sms code)":
        return {"optional": "green", "no": "red", "yes": "yellow"}.get(v, "none")
    if key == "Personal Info(first name, last name, adress, etc..)":
        return {"yes": "red", "no": "green", "optional": "yellow"}.get(v, "none")
    return "none"


def struck_domains(readme):
    """Domains written with strikethrough (combining U+0336 or ~~markdown~~) in the README tables."""
    out = set()
    for line in readme.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0] in MARKS and ("̶" in cells[1] or "~~" in cells[1]):
            out.add(re.sub("~~", "", cells[1].replace("̶", "")).strip().lower())
    return out


def main():
    rows = list(csv.reader(open(os.path.join(ROOT, "emails.csv"), encoding="utf-8", newline="")))
    keys = [h.replace("\n", "") for h in rows[0]]
    struck = struck_domains(open(os.path.join(ROOT, "README.md"), encoding="utf-8").read())
    data = []
    for row in rows[1:]:
        grey = row[1].strip().lower() in struck
        entry = {}
        for key, cell in zip(keys, row):
            value = cell.strip() or None
            entry[key] = {"value": value, "color": "grey" if grey else color_for(key, value)}
        data.append(entry)
    with open(os.path.join(ROOT, "web", "data.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, indent=4, ensure_ascii=False) + "\n")
    print(f"web/data.json: {len(data)} providers, {sum(e['Domain']['value'] in struck for e in data)} struck out (grey)")


if __name__ == "__main__":
    main()
