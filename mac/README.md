# Monthly build on a Mac

The catalogue is rebuilt on a Mac because Discogs refuses downloads from GitHub's servers
(see the main README). Everything here uses tools that come with macOS: the system
`python3`, `launchd`, `caffeinate`, Keychain and `osascript`. No Homebrew or AWS tools.

## What happens each month

launchd starts `monthly_build.sh` at **10:00 on the 3rd to the 7th** of each month. If the
Mac is asleep or off at 10:00, the run happens when it wakes. Once a month's build has been
uploaded, the later days do nothing, so a failed run is retried the next day.

Each run:

1. Reads the R2 key from Keychain and checks it works.
2. Checks there are at least 15 GB free.
3. Downloads this month's Discogs dump (`discogs_YYYYMM01_releases.xml.gz`) and keeps
   vinyl, retrying the download up to 3 times.
4. Stops without uploading if fewer than 7 million vinyl pressings were kept.
5. Packs the catalogue and uploads it to `vinyl/current.pack`. If the upload fails it is
   aborted and the live file is left as it was.
6. Deletes its temporary files, records the month as done, and shows a Mac notification.

A full run takes about 45 minutes (download and read about 30, pack about 10, upload about 1).
`caffeinate` keeps the Mac awake while it runs.

## Files

| Where | What |
| --- | --- |
| `mac/monthly_build.sh` | The monthly job. |
| `mac/install.sh` | Copies the scripts and sets up the schedule. Run again after changing any script. |
| `mac/set_r2_key.sh` | Saves the R2 key in Keychain, asking in Mac pop-up windows. |
| `~/Library/Application Support/LovegrooveCatalogue/` | The installed copy of the scripts, `work/` (temporary files) and `done-YYYYMM01` markers. |
| `~/Library/LaunchAgents/app.lovegroove.catalogue.plist` | The schedule. |
| `~/Library/Logs/LovegrooveCatalogue/` | One log per month (`20261201.log`), test logs (`-test.log`) and `launchd.log`. |

The scripts are copied out of `Documents` because macOS doesn't let scheduled jobs read
that folder.

## Setting up on a Mac

1. Create an R2 API token in Cloudflare: **R2 Object Storage → Manage API tokens → Create
   Account API token**, permission **Object Read & Write**, bucket **lovegroove-catalogue**
   only. Cloudflare shows the Access Key ID and Secret Access Key once.
2. Save the key in Keychain (service `lovegroove-r2`):

       mac/set_r2_key.sh

   It asks for the **Account ID** (R2 overview, Account Details panel), the **Access Key ID**
   and the **Secret Access Key** in pop-up windows. Running it again replaces the values.
3. Install the schedule:

       mac/install.sh

4. Test it with a 300,000-release sample, uploaded to `test/mac.pack` (a few minutes):

       ~/Library/Application\ Support/LovegrooveCatalogue/monthly_build.sh --test

   Then delete `test/mac.pack` from the bucket.

## Everyday use

| To | Run |
| --- | --- |
| See how the last build went | `tail -30 ~/Library/Logs/LovegrooveCatalogue/$(date -u +%Y%m01).log` |
| Build a month again (even if done) | `~/Library/Application\ Support/LovegrooveCatalogue/monthly_build.sh 20261201` |
| Start the scheduled job now | `launchctl kickstart gui/$(id -u)/app.lovegroove.catalogue` |
| Check the schedule is loaded | `launchctl print gui/$(id -u)/app.lovegroove.catalogue` |
| Replace the R2 key | `mac/set_r2_key.sh` |
| Stop the monthly build | `launchctl bootout gui/$(id -u)/app.lovegroove.catalogue` then delete the plist |

## If a build fails

The notification names the log. Look at the last lines:

- **`R2 ... HTTP 403`** in the key check: the R2 token was deleted or lacks write access. Make
  a new one and run `mac/set_r2_key.sh`.
- **`security: SecKeychainSearchCopyNext`**: the key isn't in Keychain. Run `mac/set_r2_key.sh`.
- **`HTTP Error 403: Forbidden`** from `build_catalogue.py`: Discogs refused the download.
  Try again later, or from another network.
- **`HTTP Error 404`**: this month's dump isn't published yet. The next day's run will retry.
- **`expected over 7 million; not uploading`**: the dump looks incomplete. The live catalogue
  is untouched; check https://data.discogs.com/ and run again with the month's date.
- **`only N GB free; need 15`**: free some disk space; the next day's run will retry.

After the 7th nothing runs until next month, so a month that failed every day needs a manual
run (see Everyday use).
