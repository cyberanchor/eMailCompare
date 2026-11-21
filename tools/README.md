# Tools

Scripts that collect provider data and keep the data files in sync. Python 3.9+ with PyYAML
(`dnf install python3-pyyaml`); the collector also needs `dig`. The data is `data/providers/<domain>.yaml`, one file
per provider (fields: `data/README.md`); every script reads and writes it through `emcdata.py`, so the files keep
one format. `EMC_DATA_DIR=/other/folder` points the scripts at a copy of the data.

| Script | What it does |
|---|---|
| `emcdata.py` | Shared module: load / save the records in the canonical YAML format (quotes anything a YAML 1.2 parser would not read as text), and record ↔ the 28 cells of the old `emails.csv` layout |
| `test_data.py [--csv OLD.csv --readme OLD.md] [--no-site]` | Checks every file (schema, formats, canonical format), how Hugo read them (`site/public/providers.json`), the export (`providers.csv`), one page per provider and the README tables. Run it after every change and after `site/build.sh` |
| `readme_tables.py` | Writes the Checked / Pending tables of `README.md` from the records (status, strikethrough, flag, order) and aligns every table |
| `collector/collect.py DOMAIN...` | Collects public data (DNS, RDAP/whois, ip-api.com, the provider's website, mail server checks, Wayback Machine): draft records in `WORK/records/`, rows in the old CSV layout and a readable `review.md` |
| `collector/collect_browser.py [OUT] [DOMAIN...]` | Opens every live provider's site in headless Chromium (EPEL `chromium-headless`): screenshot for the site (`site/assets/screenshots/<domain-with-dashes>.webp`), terms / privacy / source-code links from the rendered page, meta description. Also social accounts (with a flag whether the name matches the domain), onion address (`Onion-Location` or links), imprint link, operator guess from imprint / copyright lines. Existing screenshots are kept unless `EMC_RETAKE=1`. One JSON per domain in `collector/out-browser/` |
| `collector/run_deep_refresh.py DOMAINS_TSV DNS_DIR OUT_DIR` | `collect_deep.py` for a refresh of all providers, without crt.sh and check-host |
| `collector/merge.py add WORK` | Adds the draft records of `collect.py` as pending providers at the end of the list |
| `collector/merge.py browser OUT` | Fills empty terms / privacy / source-code links and descriptions from `collect_browser.py` |
| `collector/merge.py refresh DIR` | Merges a full re-check (`DIR/dns`, `DIR/deep`, `DIR/browser`): domain expiry, changed A/MX, empty links, description, social accounts, onion, imprint, owner; rewrites mail security, client settings, online since, last checked. Only providers with collected data are touched. Hand decisions are kept in `collector/decisions/`: `owner_accept.json` (operator for rows without one), `social_accept.txt` / `social_reject.txt` (accounts whose name does not match the domain), `skip.json` (fields removed by hand that the crawler keeps finding) |
| `collector/merge.py set DECISIONS.json` | Hand-checked changes: `{"set": {"<domain>": {"owner": "…", "links.tos": "…"}}}`, `null` removes a field |
| `collector/manual/<domain>/` | Pages the user saved by hand for sites behind bot checks (home, tos, privacy, imprint, screenshot). Read these instead of retrying the site |
| `fetch_favicons.py` | Downloads the providers' favicons into `site/assets/favicons/` |
| `collector/decisions/manual-check.md` | Open questions from the 2026-10-30 research dump (closures, disputed jurisdictions, services not added) for a manual check |
| `_archive/` | Scripts and run outputs replaced by the move to YAML (2026-10-31): the CSV merge scripts, build_json.py, align_readme.py, the converter |

## What the collector checks

| Step | Script | Checks |
|---|---|---|
| 1 | `collect_dns.py` | A and MX records, registration and expiry dates (RDAP, whois as fallback), registrant, country and ASN of every IP (ip-api.com) |
| 2 | `collect_web.py` | Homepage over https/http, with and without www. Every redirect hop is followed by hand and recorded (HTTP 3xx, `<meta refresh>`, short JavaScript redirects). Bot checks are respected: a blocked site is not retried. Terms, privacy, imprint, pricing, signup, domain list and e-mail product pages come from links, `sitemap.xml`/`robots.txt` and common paths. Webmail is looked for at `mail.`/`webmail.`/`email.` and `/mail`, `/webmail`. When the site is unreachable, blocked, parked or under maintenance, the newest usable Wayback Machine copy is crawled instead |
| 3 | `collect_deep.py` | SRV records, SPF, DMARC, MTA-STS, TLS-RPT, DANE, DNSSEC, NS; well-known hostnames; mail client autoconfig (the provider's own and Thunderbird's ISPDB); IMAP on port 993 (greeting, server software, TLS certificate names); the website's TLS certificate; crt.sh hostnames; first and last Wayback Machine capture; check-host.net from other countries when the site or the IMAP server does not answer from here |
| 4 | `build_csv.py` | Draft records (`records/<domain>.yaml`), the same data as rows in the old CSV layout, and `review.md` with every redirect chain and deep check. Second domains are kept only when their MX points at the same mail servers |

Ports 25 and 465 are not probed because many networks block them outbound.

## Adding a provider

1. `python3 tools/collector/collect.py example.com` and read `~/.cache/emailcompare-collector/review.md`.
2. Put corrections for anything the collector got wrong in `tools/collector/overrides.json` (keyed by domain and
   the old CSV column name; "Note" is appended, other columns replaced) and run step 1 again (results are cached,
   only the drafts are rebuilt).
3. `python3 tools/collector/merge.py add ~/.cache/emailcompare-collector` — the provider is added as pending.
   Optionally `collect_browser.py OUT example.com` and `merge.py browser OUT` for links, screenshot and description.
4. `python3 tools/readme_tables.py`, `site/build.sh`, `python3 tools/test_data.py`, then commit `data/`,
   `README.md` and `site/public/`.

Collected records leave email / phone / personal empty: those fields are filled only after an account was
registered by hand (then set `status: checked`).
