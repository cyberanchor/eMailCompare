# eMailCompare site (Hugo + PaperMod)

The comparison as a static site, built by [Hugo](https://gohugo.io/) with the
[PaperMod](https://github.com/adityatelange/hugo-PaperMod) theme from `emails.csv` and the README status
marks (✅ / 🤖 / pending / struck out). It comes in ten design variants; pick one and the others can go.

All designs side by side: <https://cyberanchor.github.io/eMailCompare/site/>

| # | Variant | Catalogue | Hero | Preview |
|---|---------|-----------|------|---------|
| 1 | Paper | table | center | [open](https://cyberanchor.github.io/eMailCompare/site/public/paper/) |
| 2 | Midnight | table | split | [open](https://cyberanchor.github.io/eMailCompare/site/public/midnight/) |
| 3 | Swiss | table | left | [open](https://cyberanchor.github.io/eMailCompare/site/public/swiss/) |
| 4 | Terminal | table | terminal | [open](https://cyberanchor.github.io/eMailCompare/site/public/terminal/) |
| 5 | Nord | cards | center | [open](https://cyberanchor.github.io/eMailCompare/site/public/nord/) |
| 6 | Editorial | table | masthead | [open](https://cyberanchor.github.io/eMailCompare/site/public/editorial/) |
| 7 | Brutalist | cards | left | [open](https://cyberanchor.github.io/eMailCompare/site/public/brutalist/) |
| 8 | Aurora | cards | center | [open](https://cyberanchor.github.io/eMailCompare/site/public/aurora/) |
| 9 | Ledger | table | split | [open](https://cyberanchor.github.io/eMailCompare/site/public/ledger/) |
| 10 | Solar | table | left | [open](https://cyberanchor.github.io/eMailCompare/site/public/solar/) |

## What is on the site

- Home: headline, summary cards, the catalogue of all providers (search, status, alliance, country, free plan,
  sorting; the filters are kept in the link) and the legend.
- One page per provider: favicon, status, jurisdiction and alliance, a frame for a screenshot, registration
  requirements, price, description, notes, terms, privacy policy, MX/A records and other domains.
- Methodology: `content/about.md` (Markdown).

## Layout

```
site/
├── build.sh               fetches PaperMod at a pinned commit and builds every variant
├── index.html             gallery of the variants
├── config/_default/       shared settings (hugo.toml, params.toml, menus.toml)
├── config/<variant>/      one folder per design: output folder, catalogue layout, hero style, light/dark
├── content/               Markdown pages; providers/_content.gotmpl makes one page per CSV row
├── layouts/               home page, provider pages and the emc/ partials (data, catalogue, icons, …)
├── assets/emc/            emc.css (components), theme.css (PaperMod mapping), variants/<variant>.css, emc.js
├── assets/favicons/       one icon per provider, <domain-with-dashes>.png
├── assets/screenshots/    optional screenshots, <domain-with-dashes>.png
├── static/                site favicon
├── themes/                PaperMod, fetched by build.sh (not in git)
└── public/<variant>/      the built sites (committed, so GitHub Pages can show them)
```

## Build

```sh
./site/build.sh                  # all variants
./site/build.sh midnight swiss   # some variants
./site/build.sh --serve nord     # live preview at http://localhost:1313/
```

Needs Hugo extended 0.146 or newer and git; no Node.js. `HUGO=/path/to/hugo ./site/build.sh` uses another
binary. Rebuild after editing `emails.csv` or the README tables and commit `site/public/`.

## Favicons and screenshots

Favicons were downloaded from the providers' sites (declared icons, then `/favicon.ico`), with Google's
and DuckDuckGo's icon services and the Wayback Machine as fallbacks for sites without one. Hugo re-encodes
them as small WebP files, so visitors never contact the providers or any third party. Providers without an
icon get a letter on a colour derived from the domain.

To show a screenshot on a provider page, save it as `assets/screenshots/<domain-with-dashes>.png` (for
example `protonmail-ch.png`; `.jpg` and `.webp` work too) and rebuild. Until then the frame shows a
placeholder.

## Writing pages

Pages are Markdown with front matter in `content/`. The catalogue can be placed in any page with the
`emc-table` shortcode; `emc-stats` and `emc-legend` add the summary cards and the legend.
