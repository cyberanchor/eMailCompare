# Tools

Scripts that collect provider data and keep the data files in sync. Python 3.9+ standard library only;
the collector also needs `dig`.

| Script | What it does |
|---|---|
| `collector/collect.py DOMAIN...` | Collects public data (DNS, RDAP/whois, ip-api.com, the provider's website) and writes draft rows in the `emails.csv` format plus a readable `review.md` |
| `build_json.py` | Rebuilds `web/data.json` from `emails.csv`: cell colours follow the table's rules, providers struck out in the README are grey |
| `build_web2.py` | Rebuilds the static page `web2/index.html` from `web/data.json` and the README tables |

## Adding a provider

1. `python3 tools/collector/collect.py example.com` and read `~/.cache/emailcompare-collector/review.md`.
2. Put corrections for anything the collector got wrong in `tools/collector/overrides.json` and run step 1 again
   (results are cached, only the rows are rebuilt).
3. Append the row from `rows.csv` to `emails.csv` and add the provider to the README table
   (🤖 for collected data, ✅ after registering an account by hand).
4. `python3 tools/build_json.py && python3 tools/build_web2.py`

Collected rows leave Email / Phone / Personal Info empty: those columns are filled only after an account
was registered by hand.
