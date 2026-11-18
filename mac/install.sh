#!/bin/bash
# Install (or update) the monthly catalogue build on this Mac. Run again after changing
# the scripts. The scripts are copied out of Documents because macOS stops scheduled
# jobs reading that folder.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/Application Support/LovegrooveCatalogue"
LABEL=app.lovegroove.catalogue
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$DEST" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/LovegrooveCatalogue"
cp "$SRC/build_catalogue.py" "$SRC/pack_catalogue.py" "$SRC/r2_upload.py" "$SRC/mac/monthly_build.sh" "$DEST/"
chmod +x "$DEST/monthly_build.sh"

DAYS=""
for d in 3 4 5 6 7; do
  DAYS="$DAYS<dict><key>Day</key><integer>$d</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>"
done
cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <!-- caffeinate keeps the Mac awake while the build runs (about 45 minutes). -->
  <key>ProgramArguments</key><array>
    <string>/usr/bin/caffeinate</string><string>-is</string>
    <string>/bin/bash</string><string>$DEST/monthly_build.sh</string>
  </array>
  <key>StartCalendarInterval</key><array>$DAYS</array>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/LovegrooveCatalogue/launchd.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/LovegrooveCatalogue/launchd.log</string>
</dict></plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed. Runs at 10:00 on the 3rd-7th of each month (once a month's build is uploaded, later days skip)."
