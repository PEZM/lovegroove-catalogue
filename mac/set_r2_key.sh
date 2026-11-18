#!/bin/bash
# Store the R2 key in this Mac's login Keychain for the monthly build. Run it yourself;
# each value is asked for in a Mac pop-up window (paste with Cmd+V) and saved only in Keychain.
set -euo pipefail
ask() {
  local hidden="" value
  [ "$3" = secret ] && hidden="with hidden answer"
  value="$(osascript -e "text returned of (display dialog \"Paste the $1, then click OK.\" default answer \"\" $hidden with title \"Lovegroove R2 key\")")" \
    || { echo "Cancelled; nothing more saved."; exit 1; }
  value="$(printf %s "$value" | tr -d '[:space:]')"
  [ -n "$value" ] || { echo "$1 was empty; run this again."; exit 1; }
  security add-generic-password -U -s lovegroove-r2 -a "$2" -T /usr/bin/security -w "$value"
  echo "$1 saved."
}
ask "R2 Account ID (from the Account Details panel)" account-id plain
ask "R2 Access Key ID" access-key-id secret
ask "R2 Secret Access Key" secret-access-key secret
echo "Saved in Keychain (lovegroove-r2)."
