# Hugo prototypes

Four prototypes of the eMailCompare site, one per Hugo theme. All of them are built from the same data:
`emails.csv` for the providers and the README tables for each provider's status (✅ / 🤖 / pending / struck out).
They need only Hugo extended (0.146 or newer) and git, no Node.js.

| Folder | Theme | Preview |
|--------|-------|---------|
| `hugo-book/` | [hugo-book](https://github.com/alex-shpak/hugo-book) v0.15.0 | [open](https://cyberanchor.github.io/eMailCompare/site/hugo-book/public/) |
| `hextra/` | [Hextra](https://github.com/imfing/hextra) v0.13.0 | [open](https://cyberanchor.github.io/eMailCompare/site/hextra/public/) |
| `papermod/` | [PaperMod](https://github.com/adityatelange/hugo-PaperMod) (master) | [open](https://cyberanchor.github.io/eMailCompare/site/papermod/public/) |
| `congo/` | [Congo](https://github.com/jpanther/congo) v2.14.0 | [open](https://cyberanchor.github.io/eMailCompare/site/congo/public/) |

All previews side by side: <https://cyberanchor.github.io/eMailCompare/site/>

## Layout

```
site/
├── build.sh            fetches the themes at pinned commits and builds every prototype
├── index.html          the preview gallery
├── _shared/            code used by every prototype
│   ├── content/        about.md (Markdown) and providers/_content.gotmpl (one page per CSV row)
│   ├── layouts/        partials: data, table, provider page, stats, legend; shortcodes for Markdown
│   └── assets/emc/     emc.css and emc.js (search, filters, sorting)
└── <theme>/
    ├── hugo.toml       theme settings and the mounts of _shared/, emails.csv and README.md
    ├── content/        the home page text (Markdown)
    ├── layouts/        home page and provider page in the theme's style
    ├── assets/emc/     theme.css: maps the theme's colours and dark mode onto the shared components
    ├── themes/         the theme itself, fetched by build.sh (not in git)
    └── public/         the built site (committed, so GitHub Pages can show it)
```

The colour rules are the same as for `web/data.json`: domain age, intelligence alliance and signup
requirements. A provider struck out in the README is shown as gone.

## Build

```sh
./site/build.sh                  # all prototypes
./site/build.sh hextra           # one prototype
./site/build.sh --serve hextra   # live preview at http://localhost:1313/
```

Install Hugo extended from <https://github.com/gohugoio/hugo/releases>, or point the script at a binary
with `HUGO=/path/to/hugo ./site/build.sh`. Rebuild after editing `emails.csv` or the README tables and
commit the updated `public/` folders.

## Writing pages

Text pages are Markdown files with front matter. The shared methodology page is `_shared/content/about.md`.
The provider table can be placed in any Markdown page of any prototype with the `emc-table` shortcode;
`emc-stats` and `emc-legend` add the summary cards and the legend.
