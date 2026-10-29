#!/usr/bin/env python3
"""Build web2/index.html: a static, dependency-free version of the comparison table.

The layout follows https://dismail.de/serverlist.html: one plain HTML table with coloured cells and
footnotes, no JavaScript and no external files. Data comes from web/data.json; each provider's status
(tested by hand, collected automatically, pending, struck out) comes from the README tables.

usage: python3 tools/build_web2.py
"""
import datetime
import html
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_CLASS = {"green": "good", "red": "failed", "yellow": "neutral", "orange": "warning", "purple": "purple",
             "blue": "blue", "grey": "grey"}
COLUMNS = [  # (data.json key, header HTML, cell kind)
    ("flag", 'Flag<sup><a href="#note-4">[4]</a></sup>', "flag"),
    ("Domain", "Domain", "name"),
    ("2nd domain", "2nd domain", "mid"),
    ("A Record", "A record", ""),
    ("MX Record", "MX record", ""),
    ("Domain crated", "Created", ""),
    ("Domain expired", "Expires", ""),
    ("Company owner", "Company owner", "mid"),
    ("Intelligence Alliance", 'Intelligence alliance<sup><a href="#note-1">[1]</a></sup>', ""),
    ("Jurisdiction (based on server location)", 'Jurisdiction<sup><a href="#note-2">[2]</a></sup>', ""),
    ("Site description", "Site description", "long"),
    ("ToS", "ToS", "long"),
    ("Privacy policy", "Privacy policy", "long"),
    ("Email(receive a verification link)", 'Email verification<sup><a href="#note-3">[3]</a></sup>', ""),
    ("Phone(receive s sms code)", 'Phone / SMS<sup><a href="#note-3">[3]</a></sup>', ""),
    ("Price", "Price", "mid"),
    ("Personal Info(first name, last name, adress, etc..)", 'Personal info<sup><a href="#note-3">[3]</a></sup>', ""),
    ("Note", "Note", "long"),
]
GROUPS = [("checked", "✅ Registered and tested by hand"),
          ("auto", "🤖 Collected automatically, registration not tested"),
          ("pending", "⏳ Pending, partial data")]
STATUS_TITLE = {"✅": "registered and tested by hand", "❌": "struck out", "🤖": "collected automatically",
                "⏳": "pending"}
URL = re.compile(r"https?://[^\s<>\"']+")
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'%3E"
           "%3Cpath d='M2 2h12v12H2z' fill='none' stroke='black' stroke-width='1'/%3E%3Cpath d='M4 4h8v8H4z' "
           "fill='gray'/%3E%3C/svg%3E")

CSS = """
body{margin:0;padding:0 1%;background:#fafafa;color:rgba(0,0,0,.87);font:10pt/1.35 system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
h1{font-size:15pt;margin:.8em 0 .3em}
header p,footer p{margin:.35em 0;max-width:120em}
a{color:#4351b5;text-decoration:none}
a:hover{text-decoration:underline}
table{border-collapse:collapse;margin:.6em 0 1.5em;min-width:100%}
th,td{border:1px solid #ddd;padding:3px 5px;text-align:left;vertical-align:top}
thead th{position:sticky;top:0;z-index:2;background:#ddd;white-space:nowrap}
tbody{scroll-margin-top:3em}
tbody tr{background:#eee}
tbody tr:hover td{background:#e0e0e0}
td{white-space:nowrap}
td.long{white-space:normal;min-width:16em;max-width:28em}
td.mid{white-space:normal;min-width:8em;max-width:16em}
td.name{background:#ddd;font-weight:600}
td.status,td.flag{text-align:center}
tr.group th{background:#d0d0d0;font-size:11pt;padding:6px 5px}
td.good,.legend .good{background:#43a047}
td.failed,.legend .failed{background:#e53935}
td.warning,.legend .warning{background:#ed8721}
td.neutral,.legend .neutral{background:#f4c542}
td.purple,.legend .purple{background:#b39ddb}
td.blue,.legend .blue{background:#64b5f6}
td.grey,.legend .grey{background:#bdbdbd;color:#555}
td.good,td.failed,td.warning,td.neutral,td.purple,td.blue{font-weight:600}
tr:hover td.good{background:#2e7d32}
tr:hover td.failed{background:#c62828}
tr:hover td.warning{background:#d77d26}
tr:hover td.neutral{background:#e0b030}
tr:hover td.purple{background:#9575cd}
tr:hover td.grey{background:#a8a8a8}
.legend span{padding:0 5px;border:1px solid #ddd}
hr{border:0;border-top:1px solid #ddd;margin:1.5em 0 1em}
.notes{padding-left:1.4em}
.notes li{margin:.25em 0}
""".strip()


def readme_status(readme):
    """domain -> (group, mark), read from the README checked and pending tables."""
    out, section = {}, None
    for line in readme.splitlines():
        if line.startswith("## ✅ Checked"):
            section = "checked"
        elif line.startswith("## ❓ Pending"):
            section = "pending"
        elif line.startswith("<!--") or (line.startswith("## ") and section == "pending"):
            section = None
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not section or len(cells) < 3 or cells[0] not in ("✅", "❌", "🤖"):
            continue
        domain = re.sub("~~", "", cells[1].replace("̶", "")).strip().lower()
        struck = "̶" in cells[1] or "~~" in cells[1]
        if section == "checked":
            out[domain] = ("auto" if cells[0] == "🤖" else "checked", cells[0])
        else:
            out[domain] = ("pending", "❌" if struck else "⏳")
    return out


def linkify(match):
    url, trail = match.group(0), ""
    while url and url[-1] in ".,;:)":
        url, trail = url[:-1], url[-1] + trail
    return f'<a href="{url}" rel="noopener noreferrer">{url}</a>{trail}'


def text_html(value):
    return URL.sub(linkify, html.escape(value or "")).replace("\n", "<br>")


def row_html(entry, mark):
    domain = html.escape(entry["Domain"]["value"] or "")
    link = f'<a href="https://{domain}" rel="noopener noreferrer">{domain}</a>'
    cells = [f'<td class="status" title="{STATUS_TITLE[mark]}">{mark}</td>']
    for key, _, kind in COLUMNS:
        cell = entry[key]
        content = (f"<s>{link}</s>" if mark == "❌" else link) if key == "Domain" else text_html(cell["value"])
        classes = " ".join(c for c in (kind, CSS_CLASS.get(cell["color"])) if c)
        cells.append(f'<td class="{classes}">{content}</td>' if classes else f"<td>{content}</td>")
    return "<tr>" + "".join(cells) + "</tr>"


def main():
    data = json.load(open(os.path.join(ROOT, "web", "data.json"), encoding="utf-8"))
    status = readme_status(open(os.path.join(ROOT, "README.md"), encoding="utf-8").read())
    groups = {gid: [] for gid, _ in GROUPS}
    for entry in data:
        group, mark = status.get((entry["Domain"]["value"] or "").lower(), ("pending", "⏳"))
        groups[group].append(row_html(entry, mark))
    today = datetime.date.today().isoformat()
    width = len(COLUMNS) + 1
    bodies = "\n".join(
        f'<tbody id="{gid}">\n<tr class="group"><th colspan="{width}" scope="colgroup">{title} ({len(groups[gid])})</th></tr>\n'
        + "\n".join(groups[gid]) + "\n</tbody>" for gid, title in GROUPS)
    head_cells = '<th scope="col">Status</th>' + "".join(f'<th scope="col">{h}</th>' for _, h, _ in COLUMNS)
    jump = " · ".join(f'<a href="#{gid}">{title.split(" ", 1)[1].split(",")[0]} ({len(groups[gid])})</a>'
                      for gid, title in GROUPS)
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Email Service Comparison | eMailCompare</title>
<meta name="description" content="Static comparison of {len(data)} email providers: domains, servers, jurisdiction, intelligence alliances, terms, privacy policies and prices.">
<link rel="icon" type="image/svg+xml" href="{FAVICON}">
<style>
{CSS}
</style>
</head>
<body>
<header>
<h1>Yet Another Email Service Comparison</h1>
<p>{len(data)} email providers compared by domain, servers, jurisdiction, intelligence alliance, terms, privacy policy and price.
Generated from <a href="../emails.csv">emails.csv</a> on {today} · <a href="../web/">interactive version</a> · <a href="https://github.com/cyberanchor/eMailCompare">source on GitHub</a></p>
<p class="legend">Status: ✅ registered and tested by hand · 🤖 collected automatically, registration not tested · ⏳ pending, partial data · ❌ struck out.
Cells: <span class="good">favourable</span> <span class="neutral">caution</span> <span class="failed">unfavourable</span> <span class="purple">Fourteen Eyes</span> <span class="grey">struck out</span></p>
<p>Jump to: {jump}</p>
</header>
<table aria-label="Email provider comparison">
<thead><tr>{head_cells}</tr></thead>
{bodies}
</table>
<footer>
<hr>
<p><strong>To be clear: this list does not recommend any provider.</strong> All data comes from public sources (DNS, RDAP/WHOIS, GeoIP and the providers' own websites) and may be outdated or wrong. ToS and privacy summaries of collected rows are keyword-based; follow the linked page for the full text. Corrections are welcome in <a href="https://github.com/cyberanchor/eMailCompare/issues">Issues</a>.</p>
<ul class="notes">
<li id="note-1">[1] Five Eyes: United States, United Kingdom, Canada, Australia, New Zealand. Nine Eyes adds Denmark, France, the Netherlands and Norway. Fourteen Eyes adds Germany, Belgium, Italy, Spain and Sweden. Assigned from the country of the mail servers.</li>
<li id="note-2">[2] Country of the MX (mail) servers by GeoIP. For a domain without MX records, the country of its web server.</li>
<li id="note-3">[3] Filled only after an account was registered by hand (✅ rows); empty for 🤖 and ⏳ rows.</li>
<li id="note-4">[4] Country of the operator where it is known. For collected rows it comes from the domain registrant (RDAP), the company's legal form, a country-code domain or the imprint; otherwise it is the mail servers' country.</li>
</ul>
</footer>
</body>
</html>
"""
    os.makedirs(os.path.join(ROOT, "web2"), exist_ok=True)
    with open(os.path.join(ROOT, "web2", "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(page)
    print("web2/index.html:", {gid: len(rows) for gid, rows in groups.items()}, f"{len(page.encode()) // 1024} KB")


if __name__ == "__main__":
    main()
