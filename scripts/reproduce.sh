#!/bin/bash
# Download every ImpactU product and build the DuckDB database, resumably.
#
#   scripts/reproduce.sh [OUT_DIR]          # default ./colombia
#
# 1. crawl_all.py   -> OUT_DIR/{le100,le100_noyear,gt100,gt100_noyear}/ + state.json  (about 5-6 days)
# 2. fetch_authors  -> OUT_DIR/gt100_authors/, gt100_noyear_authors/  (full lists of >100-author works, ~6,000 requests)
# 3. build.py       -> OUT_DIR/<name of OUT_DIR>.duckdb, one dataset per part, e.g. colombia_le100 (about 1 hour)
# 4. check          -> distinct works vs. the API total in state.json
#
# Interrupt it at any time and run it again: every step skips what is already on disk.
# Settings (environment): PY (python with duckdb and requests), IMPACTU_API_BASE, PAUSE (seconds between requests).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-colombia}"
PY="${PY:-python3}"
PAUSE="${PAUSE:-3}"
NAME="$(basename "$OUT")"
DB="$OUT/$NAME.duckdb"
stamp() { echo "[$(date '+%F %T')] $*"; }

stamp "1/4 crawl $OUT"
$PY -u "$HERE/crawl_all.py" "$OUT" --pause "$PAUSE"

stamp "2/4 full author lists"
for part in gt100 gt100_noyear; do
  [ -d "$OUT/$part" ] && $PY -u "$HERE/fetch_authors.py" "$OUT/$part" --pause "$PAUSE"
done

stamp "3/4 build $DB"
export TMPDIR="$PWD/$OUT/tmp_build"   # the build writes several GB of temporary files
mkdir -p "$TMPDIR"
for part in le100 le100_noyear gt100 gt100_noyear; do
  [ -d "$OUT/$part" ] && $PY -u "$HERE/build.py" "$OUT/$part" --dataset "${NAME}_$part" --db "$DB"
done
rm -rf "$TMPDIR"

stamp "4/4 check"
$PY "$HERE/check.py" "$OUT"
stamp "done: $DB"
