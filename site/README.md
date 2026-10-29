# eMailCompare site (Hugo + PaperMod)

The comparison as a static site, built by [Hugo](https://gohugo.io/) with the
[PaperMod](https://github.com/adityatelange/hugo-PaperMod) theme from `emails.csv` and the README status
marks (✅ / 🤖 / pending / struck out). One design, "terminal", in a dark and a light version; the button in
the header switches between them and remembers the choice.

Live: <https://cyberanchor.github.io/eMailCompare/site/public/> (`site/` redirects there).

## What is on the site

- Home: headline, summary cards, one block with the overview, comparison criteria and sources, then the
  table of all providers: search, status and country filters, sortable columns (alliance has its own
  column), links to terms / privacy policy / source code, and export of the shown domains as `.txt`.
  Filters are kept in the link (`#state=checked&sort=alliance&dir=asc`).
- One page per provider: favicon, status, jurisdiction and alliance, a screenshot of its site, registration
  requirements, price, links, description, notes, terms, privacy policy, MX/A records, other domains.
- Footer on every page: the legend of the status marks and of the cell colours.

## Layout

```
site/
├── build.sh               fetches PaperMod at a pinned commit and builds the site into public/
├── index.html             redirect to public/
├── config/_default/       settings (hugo.toml, params.toml)
├── content/               _index.md (home text, criteria, sources); providers/_content.gotmpl makes one page per CSV row
├── layouts/               home page, provider page, header and footer, the emc/ partials (data, table, icons, …)
├── assets/emc/            emc.css (palette for light and dark, components), emc.js (table)
├── assets/favicons/       one icon per provider, <domain-with-dashes>.png
├── assets/screenshots/    one screenshot per provider, <domain-with-dashes>.webp
├── static/                site favicon
├── themes/                PaperMod, fetched by build.sh (not in git)
└── public/                the built site (committed, so GitHub Pages can show it)
```

## Build

```sh
./site/build.sh            # build into site/public/
./site/build.sh --serve    # live preview at http://localhost:1313/
```

Needs Hugo extended 0.146 or newer and git; no Node.js. `HUGO=/path/to/hugo ./site/build.sh` uses another
binary. Rebuild after editing `emails.csv`, the README tables or a screenshot, and commit `site/public/`.

## Data columns used besides the table

`emails.csv` has three link columns at the end: `ToS link`, `Privacy policy link` and `Source code`
(GitHub, GitLab, Codeberg …). They are shown in the table and on the provider pages; empty cells show
"not found".

## Favicons and screenshots

Favicons were downloaded from the providers' sites (declared icons, then `/favicon.ico`), with Google's
and DuckDuckGo's icon services and the Wayback Machine as fallbacks for sites without one. Hugo re-encodes
them as small WebP files, so visitors never contact the providers or any third party. Providers without an
icon get a letter on a colour derived from the domain.

Screenshots are taken with headless Chromium (1280×800, WebP) of each live provider's home page. Sites that
answered with a bot check, an error, a parking or maintenance page have none; the provider page then shows
the facts without a picture. To add or replace one by hand, save it as
`assets/screenshots/<domain-with-dashes>.webp` (`.png` and `.jpg` are converted) and rebuild.
