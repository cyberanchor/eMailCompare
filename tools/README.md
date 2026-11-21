# eMailCompare tools

The scripts that collect the provider data of [eMailCompare](../README.md) and keep the data files, the README tables
and the site in step. The data is `data/providers/<domain>.yaml`, one file per provider (fields:
[`data/README.md`](../data/README.md)). Every script reads and writes it through `emcdata.py`, so the files keep one
format.

The collector can also be used on its own: give it any domain and it writes draft records and a readable report
without touching the data.

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Scripts](#scripts)
- [What the collector checks](#what-the-collector-checks)
- [How polite it is](#how-polite-it-is)
- [Configuration](#configuration)
- [Local files that are not published](#local-files-that-are-not-published)
- [Adding a provider](#adding-a-provider)
- [Refreshing all providers](#refreshing-all-providers)
- [Tests](#tests)
- [License](#license)

## Requirements

| What | Needed for | Package |
|---|---|---|
| Python 3.9 or newer | everything | |
| PyYAML | everything | `pip install -r tools/requirements.txt`, `dnf install python3-pyyaml`, `apt install python3-yaml` |
| `dig` | `collect_dns.py`, `collect_deep.py`, `build_csv.py` | `bind-utils` (Fedora, RHEL), `dnsutils` (Debian, Ubuntu) |
| Chromium or Google Chrome | `collect_browser.py` (screenshots, rendered links) | `chromium-headless` (EPEL), `chromium`, `google-chrome-stable` |
| `rsvg-convert` (optional) | SVG favicons in `fetch_favicons.py` | `librsvg2-tools`, `librsvg2-bin` |
| Hugo extended 0.156+ | building the site (`site/build.sh`) | |

Nothing else: the scripts use the Python standard library for HTTP, TLS, HTML parsing and the Chrome DevTools
protocol.

## Quick start

```sh
git clone https://github.com/cyberanchor/eMailCompare && cd eMailCompare
python3 -m pip install -r tools/requirements.txt

python3 tools/collector/collect.py example.com          # collect; read the review it prints
python3 tools/collector/collect_browser.py --shots /tmp/shots /tmp/browser example.com
```

`collect.py` caches everything in `~/.cache/emailcompare-collector` (`--work DIR` for another folder) and ends with
`review.md` (every value, every redirect chain and every check) and `records/<domain>.yaml` (a draft in the data
format). Every script has `--help`.

## Scripts

| Script | What it does |
|---|---|
| `collector/collect.py DOMAIN...` | Runs the four collector steps below for the given domains and writes `WORK/records/`, `WORK/rows.csv` (old 28-column CSV layout) and `WORK/review.md`. `--no-crtsh`, `--no-checkhost` skip the slow outside services |
| `collector/collect_browser.py [OUT] [DOMAIN...]` | Opens each live provider's site in headless Chrome/Chromium: screenshot (`site/assets/screenshots/<domain-with-dashes>.webp`, 1280x800, cache off; `--shots DIR`, `--retake`), terms / privacy / source-code links from the rendered page, description, social accounts (flagged when the name does not match the domain), onion address (`Onion-Location` or links), imprint link, operator guess from imprint and copyright lines. One JSON per domain in `OUT` (default `collector/out-browser/`) |
| `collector/merge.py add WORK` | Adds the drafts of `collect.py` as pending providers at the end of the list; providers already in the data are skipped |
| `collector/merge.py browser OUT [DOMAIN...]` | Fills empty terms / privacy / source-code links and descriptions from `collect_browser.py` |
| `collector/merge.py refresh DIR [DOMAIN...]` | Merges a full re-check (`DIR/dns`, `DIR/deep`, `DIR/browser`): domain expiry, changed A/MX records, empty links, description, social accounts, onion, imprint, owner; rewrites mail security, client settings, online since and last checked. Only providers with collected data are touched |
| `collector/merge.py set CHANGES.json` | Hand-checked changes: `{"set": {"<domain>": {"owner": "…", "links.tos": "…"}}}`; `null` removes a field |
| `collector/merge.py --dry-run …` | Any of the above, writing the report (`review-<command>.md`) but no record |
| `collector/run_deep_refresh.py TSV DNS_DIR OUT` | `collect_deep.py` for a refresh of all providers, without crt.sh and check-host.net |
| `fetch_favicons.py DOMAIN... \| --all` | Downloads favicons into `site/assets/favicons/`: icons declared on the homepage, `/favicon.ico`, then Google's and DuckDuckGo's favicon services and the Wayback Machine. Hugo re-encodes them, so the site never asks a third party for an icon |
| `readme_tables.py [README]` | Writes the Checked / Pending tables of `README.md` from the records and aligns every table |
| `test_data.py [--no-site]` | Checks every record (schema, formats, canonical YAML), how Hugo read them (`site/public/providers.json`), the CSV export, one page per provider and the README tables. Exit code 1 on any error |
| `emcdata.py` | Shared module: load and save records in the canonical YAML format (quotes what a YAML 1.2 reader such as Hugo would not read as text; refuses unknown fields; writes atomically) |
| `collector/common.py` | Shared module: User-Agent, domain validation, atomic files, robots.txt |

Collector exit codes: 0 when everything worked, 1 when a domain or a step failed (the others are still done),
2 on bad arguments.

## What the collector checks

| Step | Script | Checks |
|---|---|---|
| 1 | `collect_dns.py` | A and MX records, registration and expiry dates (RDAP, port-43 whois as fallback), registrant when public, country and ASN of every IP (ip-api.com) |
| 2 | `collect_web.py` | Homepage over https/http, with and without www. Every redirect hop is followed by hand and recorded (HTTP 3xx, `<meta refresh>`, short JavaScript redirects). Terms, privacy, imprint, pricing, signup, domain list and e-mail product pages come from links, `sitemap.xml` and common paths; JavaScript-only policy pages are read from the site's own bundle. Webmail is looked for at `mail.`/`webmail.`/`email.` and `/mail`, `/webmail`. When the site is unreachable, blocked, parked, under maintenance or closed by robots.txt, the newest usable Wayback Machine copy is crawled instead |
| 3 | `collect_deep.py` | SRV records, SPF, DMARC, MTA-STS, TLS-RPT, DANE, DNSSEC, NS; well-known hostnames; mail client autoconfig (the provider's own and Thunderbird's ISPDB); IMAP on port 993 (greeting, server software, TLS certificate names); the website's TLS certificate; crt.sh hostnames; first and last Wayback Machine capture; check-host.net from other countries when the site or the IMAP server does not answer from here |
| 4 | `build_csv.py` | Draft records, the same data as rows in the old CSV layout, and `review.md`. Summaries of the terms and the privacy policy, prices, operator, jurisdiction and a note are drafted with regular expressions in several languages: read and correct them before publishing. Second domains are kept only when their MX points at the same mail servers |

Ports 25 and 465 are not probed because many networks block them outbound. Fields that need a real account
(`email`, `phone`, `personal`) are left empty: they are filled only after an account was registered by hand (then
`status: checked`).

Downloaded pages are treated as data: they are parsed as text and matched with regular expressions, never run.
Every collector step runs in its own isolated Python process (`python -I`), so nothing in the work folder can be
imported.

## How polite it is

- Every request sends `eMailCompare-research/1.0 (+https://github.com/cyberanchor/eMailCompare)`, which says who
  is asking. The headless browser keeps its own User-Agent.
- `robots.txt` of the provider's site is obeyed for every page (product token `eMailCompare-research`, read as
  RFC 9309 says: no file allows everything, a server error disallows everything).
- A site that answers with a bot check is recorded as blocked and is not retried in any other way.
- About a dozen pages per provider. archive.org, crt.sh and check-host.net are asked one or two requests at a
  time, with pauses; ip-api.com's rate-limit headers are followed.

## Configuration

| Variable | Meaning |
|---|---|
| `EMC_USER_AGENT` | Another User-Agent for every request (name your fork and a contact URL) |
| `EMC_DATA_DIR` | Another folder of records instead of `data/providers/` (for tests or a copy) |
| `EMC_DECISIONS_DIR` | Another folder of hand decisions instead of `collector/decisions/` |
| `CHROMIUM` | The browser binary for `collect_browser.py` (also `--chrome PATH`) |
| `EMC_RETAKE=1` | `collect_browser.py` replaces existing screenshots (also `--retake`) |
| `XDG_CACHE_HOME` | Where the default work folder `emailcompare-collector/` goes |

## Local files that are not published

The maintainer keeps hand decisions next to the scripts. They are listed in `tools/.gitignore` and are optional:
without them the scripts work the same, with nothing decided by hand.

| File | Format | Used by |
|---|---|---|
| `collector/sources.json` | `{"<domain>": {"tos": url, "privacy": [url, …], "imprint": …, "pricing": …, "signup": …, "domains": …, "service": …}}`; several URLs are joined into one text; keys starting with `_` are comments | `collect_web.py` (`--sources FILE`): these pages are used before anything found automatically |
| `collector/overrides.json` | `{"<domain>": {"<old CSV column>": "value"}}`; `Note` is appended, other columns are replaced | `build_csv.py` (`--overrides FILE`) |
| `collector/decisions/owner_accept.json` | `{"<domain>": "🇩🇪 Example GmbH"}` | `merge.py refresh`: operator for records without one |
| `collector/decisions/social_accept.txt`, `social_reject.txt` | one URL per line | `merge.py refresh`: accounts whose name does not match the domain |
| `collector/decisions/skip.json` | `{"<domain>": ["links.privacy", "description"]}` | `merge.py refresh`: fields removed by hand that the crawler keeps finding |
| `collector/manual/<domain>/` | pages saved by hand for sites behind bot checks | read by hand instead of retrying the site |

## Adding a provider

1. `python3 tools/collector/collect.py example.com` and read `review.md` in the work folder.
2. Put corrections for anything the collector got wrong in `collector/overrides.json` and run step 1 again (results
   are cached, only the drafts are rebuilt).
3. `python3 tools/collector/merge.py add ~/.cache/emailcompare-collector`: the provider is added as pending.
4. `python3 tools/collector/collect_browser.py OUT example.com`, then `merge.py browser OUT` for links,
   screenshot and description; `merge.py refresh WORK example.com` for mail security, client settings and dates
   (copy `OUT/example.com.json` to `WORK/browser/` first, or leave it out).
5. `python3 tools/fetch_favicons.py example.com`.
6. Write the hand-checked fields (summaries, owner, jurisdiction, prices, note) into a JSON file and apply it with
   `merge.py set` (or edit the YAML and check it with `test_data.py`).
7. `python3 tools/readme_tables.py`, `HUGO=/path/to/hugo site/build.sh`, `python3 tools/test_data.py`, then
   commit `data/`, `README.md`, `site/assets/` and `site/public/`.

## Refreshing all providers

```sh
W=~/.cache/emc-refresh
mkdir -p $W && python3 - > $W/domains.tsv <<'PY'
import sys; sys.path.insert(0, "tools"); import emcdata
for r in emcdata.load_all(): print(f"{r['domain']}\t{int(bool(r.get('dead')))}")
PY
curl -sS -o $W/psl.dat https://publicsuffix.org/list/public_suffix_list.dat
python3 -I tools/collector/collect_dns.py $W/domains.tsv $W/psl.dat $W/dns
python3 tools/collector/run_deep_refresh.py $W/domains.tsv $W/dns $W/deep
python3 tools/collector/collect_browser.py $W/browser
python3 tools/collector/merge.py --dry-run refresh $W     # read $W/review-refresh.md, then without --dry-run
```

The browser restarts every 25 sites and the collectors cache per domain, so an interrupted run continues where it
stopped. On a small machine run the steps one after another, not in parallel.

## Tests

```sh
python3 -m unittest discover -s tools/tests -t tools/tests     # offline unit tests, about 1 s
python3 tools/test_data.py                                     # the data, the site and the README agree
```

The [tools workflow](../.github/workflows/tools.yml) runs both on every push and pull request that touches
`tools/`, `data/`, `site/public/` or `README.md`, on Python 3.9, 3.12 and 3.13.

## License

The code in `tools/` is under the [MIT license](./LICENSE).
