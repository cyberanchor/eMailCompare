#!/usr/bin/env bash
# Build the eMailCompare site (Hugo + PaperMod) in every design variant: site/public/<variant>/.
#
# The theme is not stored in the repository: build.sh fetches PaperMod into site/themes/ at the pinned
# commit below. Only Hugo extended (0.146 or newer) and git are needed; no Node.js.
#
#   ./build.sh                  fetch the theme and build all variants
#   ./build.sh midnight swiss   build only these variants
#   ./build.sh --serve nord     live preview of one variant at http://localhost:1313/
#
# HUGO=/path/to/hugo ./build.sh uses another Hugo binary. Variants are the folders in config/ besides _default.
set -euo pipefail
cd "$(dirname "$0")"
HUGO=${HUGO:-hugo}
THEME_REPO=https://github.com/adityatelange/hugo-PaperMod
THEME_COMMIT=118119693d8b5ac211b939775678310973bcb232

dir=themes/PaperMod
if ! [ -d "$dir/.git" ] || [ "$(git -C "$dir" rev-parse HEAD)" != "$THEME_COMMIT" ]; then
  echo "fetching PaperMod ($THEME_COMMIT)"
  rm -rf "$dir"
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin "$THEME_REPO"
  git -C "$dir" fetch -q --depth 1 origin "$THEME_COMMIT"
  git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
fi

if [ "${1:-}" = "--serve" ]; then
  exec "$HUGO" server --environment "${2:-paper}" --disableFastRender --baseURL http://localhost:1313/
fi

variants=("$@")
if [ ${#variants[@]} -eq 0 ]; then
  for d in config/*/; do
    d=$(basename "$d")
    [ "$d" = "_default" ] || variants+=("$d")
  done
fi

"$HUGO" version
for v in "${variants[@]}"; do
  [ -d "config/$v" ] || { echo "unknown variant: $v" >&2; exit 1; }
  echo "building $v"
  "$HUGO" --environment "$v" --minify --gc --cleanDestinationDir --logLevel warn --quiet
done
