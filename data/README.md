# Provider data

One YAML file per provider: [`providers/<domain>.yaml`](providers/). The site and the README tables
are built from these files.

Downloads built from the same files: [providers.csv](https://cyberanchor.github.io/eMailCompare/site/public/providers.csv)
(the column layout of the former `emails.csv`) and
[providers.json](https://cyberanchor.github.io/eMailCompare/site/public/providers.json).

## Fields

Only `order`, `domain` and `status` are required; empty fields are left out.

| Field | Example | Meaning |
|---|---|---|
| `order` | `1` | Position in the lists (site table, README tables). Unique number |
| `domain` | `protonmail.ch` | Main domain; the file is named after it |
| `status` | `checked` | `checked` — account registered and tested by hand · `auto` — collected automatically · `pending` — partial data, not reviewed |
| `dead` | `true` | The service is gone (struck out). Left out otherwise |
| `flag` | `🇨🇭` | Country flag of the provider |
| `domains` | `[proton.me, …]` | Other domains of the same mailbox service |
| `a`, `mx` | `{host: 185.70.42.21, flag: 🇨🇭, country: Switzerland, asn: AS62371, org: Proton AG}` | Web server (A) and mail server (MX) records: host or IP, its country and network |
| `created`, `expires` | `'2013-08-29'` | Domain registration and expiry date |
| `owner` | `🇨🇭 Proton AG` | Operating company, with the flag of its country. Private persons: `Individual operator` |
| `alliance` | `5`, `9`, `14` | Five / Nine / Fourteen Eyes membership of the jurisdiction |
| `jurisdiction` | `[Switzerland]` | Country of the operating company (several lines with flags when it is split) |
| `description` | | Text from the provider's site |
| `tos_summary`, `privacy_summary` | | Short summary of the terms of service and the privacy policy |
| `email`, `phone`, `personal` | `'yes'`, `'no'`, `optional` | Whether signup asks for an e-mail to verify, a phone number / SMS code, personal data. Filled after a manual test |
| `price` | `[free, Unlimited $9.99/month]` | Plans |
| `note` | | Notes: how the data was collected, findings |
| `links` | `{tos: …, privacy: …, code: …, imprint: …}` | Terms, privacy policy, source code, imprint |
| `social` | `[https://…]` | Official social accounts |
| `onion` | `http://….onion/` | Tor address |
| `security` | `{spf: -all, dmarc: reject, mta_sts: enforce, tls_rpt: 'yes', dnssec: 'yes', dane: 'yes'}` | Mail DNS checks (SPF, DMARC, MTA-STS, TLS-RPT, DNSSEC, DANE) |
| `servers` | `{kind: IMAP, host: imap.example.org:993, note: SSL/TLS}` | Mail client settings |
| `online_since` | `'2014-04-22'` | First capture in the Wayback Machine |
| `last_checked` | `'2026-10-30'` | Date of the last automatic check |

Dates and the words `yes` / `no` are quoted so that YAML keeps them as text.

## Adding or changing a provider

Edit or add the file, keep the format of the other files, and open a pull request. A new provider gets the next
free `order` and `status: pending`. The site build stops with a message when a file is broken (missing `domain`,
`order` or `status`, a file name that does not match the domain, a repeated `order`).
