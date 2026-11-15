#!/bin/bash
# Pack a finished build for sharing: one tar per raw part (pages are already gzipped), the database
# compressed with zstd, state, logs and SHA256SUMS. Optionally copy it with rclone.
#
#   scripts/release.sh OUT_DIR NAME [RCLONE_DEST]
#   scripts/release.sh colombia impactu-dev-2026-11-08 gdrive:impactu-releases
#
# For a Google Drive folder shared with you, set RCLONE_DRIVE_ROOT_FOLDER_ID to the id in its URL
# (drive.google.com/drive/folders/<id>) and pass gdrive: as RCLONE_DEST.
set -euo pipefail
OUT="$1"; NAME="$2"; DEST="${3:-}"
STAGE="releases/$NAME"
mkdir -p "$STAGE"
for d in "$OUT"/*/; do
  p=$(basename "$d"); [ "$p" = tmp_build ] && continue
  [ -f "$STAGE/$p.tar" ] || { echo "tar $p"; tar -cf "$STAGE/$p.tar.tmp" -C "$OUT" "$p" && mv "$STAGE/$p.tar.tmp" "$STAGE/$p.tar"; }
done
cp "$OUT/state.json" "$STAGE/"
DBF="$OUT/$(basename "$OUT").duckdb"
[ -f "$STAGE/$(basename "$DBF").zst" ] || zstd -q -T0 -6 "$DBF" -o "$STAGE/$(basename "$DBF").zst"
(cd "$STAGE" && sha256sum -- *.tar *.zst *.json > SHA256SUMS)
ls -la "$STAGE"
if [ -n "$DEST" ]; then
  rclone copy "$STAGE" "$DEST/$NAME" --transfers 4 --drive-chunk-size 64M --stats 60s --stats-one-line -v
  rclone check "$STAGE" "$DEST/$NAME" --size-only
fi
