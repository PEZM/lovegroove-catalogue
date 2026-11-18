#!/bin/bash
# Monthly vinyl catalogue build on this Mac: read this month's Discogs dump, keep vinyl,
# pack it and upload it to R2 as vinyl/current.pack. launchd starts it at 10:00 on the
# 3rd to the 7th; once a month's build is uploaded, the later days do nothing.
#
#   monthly_build.sh            this month's dump (skipped if already done)
#   monthly_build.sh --test     a 300,000-release sample, uploaded to test/mac.pack
#   monthly_build.sh 20261101   a given dump, even if already done
set -euo pipefail

HOME_DIR="$HOME/Library/Application Support/LovegrooveCatalogue"
WORK="$HOME_DIR/work"
LOGS="$HOME/Library/Logs/LovegrooveCatalogue"
BUCKET=lovegroove-catalogue
KEY=vinyl/current.pack
LIMIT=0
FORCE=0
DUMP="$(date -u +%Y%m01)"
case "${1:-}" in
  --test) KEY=test/mac.pack; LIMIT=300000; FORCE=1 ;;
  [0-9]*) DUMP="$1"; FORCE=1 ;;
esac

mkdir -p "$WORK" "$LOGS"
LOG="$LOGS/$DUMP$([ "$LIMIT" = 0 ] || echo -test).log"
DONE="$HOME_DIR/done-$DUMP"

notify() { osascript -e "display notification \"$1\" with title \"Lovegroove catalogue\"" >/dev/null 2>&1 || true; }

if [ "$FORCE" = 0 ] && [ -e "$DONE" ]; then exit 0; fi
exec >>"$LOG" 2>&1
echo "=== $(date) dump $DUMP -> $KEY"
trap 'echo "FAILED at line $LINENO"; notify "Monthly build failed. Log: $LOG"' ERR

secret() { security find-generic-password -s lovegroove-r2 -a "$1" -w; }
export R2_ACCOUNT_ID="$(secret account-id)" R2_ACCESS_KEY_ID="$(secret access-key-id)" R2_SECRET_ACCESS_KEY="$(secret secret-access-key)"
cd "$HOME_DIR"

python3 r2_upload.py --bucket "$BUCKET" --check

FREE_GB=$(df -g "$WORK" | awk 'NR==2 {print $4}')
if [ "$FREE_GB" -lt 15 ]; then echo "only ${FREE_GB} GB free; need 15"; false; fi

rm -f "$WORK"/catalogue.*
# Discogs' server can't resume a download, so if it drops, start again (up to 3 tries).
for TRY in 1 2 3; do
  if python3 build_catalogue.py --dump "$DUMP" --limit "$LIMIT" --out "$WORK/catalogue.sqlite"; then break; fi
  if [ "$TRY" = 3 ]; then echo "reading the dump failed 3 times"; false; fi
  echo "reading the dump failed (try $TRY); retrying in 5 minutes"
  sleep 300
done

# Don't replace a good catalogue with a broken one (November 2026: 8.1 million).
KEPT=$(python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute(\"SELECT value FROM meta WHERE key='vinyl_kept'\").fetchone()[0])" "$WORK/catalogue.sqlite")
echo "vinyl pressings kept: $KEPT"
if [ "$LIMIT" = 0 ] && [ "$KEPT" -lt 7000000 ]; then echo "expected over 7 million; not uploading"; false; fi

TMPDIR="$WORK" python3 pack_catalogue.py --db "$WORK/catalogue.sqlite" --out "$WORK/catalogue.pack"
rm -f "$WORK/catalogue.sqlite"

python3 r2_upload.py --bucket "$BUCKET" --key "$KEY" --file "$WORK/catalogue.pack"
rm -f "$WORK/catalogue.pack"

if [ "$LIMIT" = 0 ]; then touch "$DONE"; fi
echo "=== done $(date)"
notify "Catalogue $DUMP uploaded ($KEPT vinyl pressings)."
