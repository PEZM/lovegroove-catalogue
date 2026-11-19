# Lovegroove catalogue

Lovegroove's shared vinyl catalogue, built each month from Discogs' data dumps
(CC0, https://data.discogs.com/). Only vinyl releases are kept, with the fields
the app shows. No images, user collections or marketplace data.

User data (collections, wishlists, sync) does not live here.

## How it fits together

```
Discogs monthly dump (11 GB, 19.6M releases)
   │  read as it downloads, vinyl kept (8.1M in Nov 2026)
   ▼
build_catalogue.py ──► catalogue.sqlite (4 GB, temporary)
   ▼
pack_catalogue.py  ──► catalogue.pack   (2.5 GB, temporary)
   ▼
r2_upload.py       ──► R2 bucket lovegroove-catalogue / vinyl/current.pack
   ▼
worker/  (Cloudflare Worker) answers lookups, one block read each
```

| File | What it does |
| --- | --- |
| `build_catalogue.py` | Streams the releases dump and writes a SQLite database of vinyl pressings. |
| `pack_catalogue.py` | Turns that into one packed file (format v3) in which every lookup is a single block read. |
| `r2_upload.py` | Uploads a file to R2 with Python's standard library only (64 MB parts, retries, aborts cleanly on failure). `--check` just tests the key. |
| `mac/` | The monthly build on a Mac: script, scheduler install, Keychain setup. See [mac/README.md](mac/README.md). |
| `worker/` | Cloudflare Worker: `/release/:id`, `/master/:id`, `/barcode/:digits`, `/search?q=`. Live at **https://catalogue.lovegroove.app** (`wrangler.production.jsonc`); the test copy (`wrangler.jsonc`) stays until old TestFlight builds expire. Deploy both after a code change. |
| `.github/workflows/build-catalogue.yml` | Same build on GitHub, manual backup only (see below). |

## Where the monthly build runs

**On a Mac** (`mac/`), at 10:00 on the 3rd of each month, with retries up to the 7th.
See [mac/README.md](mac/README.md) for setup, logs and troubleshooting.

**Not on GitHub.** Discogs' download site sits behind Cloudflare's bot protection, which
answers GitHub's servers with `403 Forbidden` (November 2026; one run got through, later
ones were all refused). The same request from a home connection works. The workflow is kept
for manual runs in case that changes, but has no schedule, and its R2 secrets were deleted on
19 November 2026: to use it again, make a new R2 token and add `R2_ACCOUNT_ID`,
`R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` to the repo's Actions secrets.

## Safety checks

- The R2 key is checked before the 40-minute download starts.
- A build with fewer than 7 million vinyl pressings is not uploaded, so a broken dump can't
  replace a good catalogue.
- A failed upload is aborted; `vinyl/current.pack` only changes when a complete file is in place.
- The Worker reads by etag, so it picks up a new file by itself and never mixes two versions.

## Costs

Everything stays inside Cloudflare's free tiers: one 2.5 GB file in R2 (10 GB free), one
upload a month (about 50 Class A operations), lookups mostly served from Cloudflare's edge
cache. The Worker limits each visitor to 120 lookups a minute, the bucket aborts unfinished
uploads after 7 days, and a $1 budget alert is set in Cloudflare.

## Running it

Everything lives in one Cloudflare account (Haptic Matter's, signed in with Apple) and on
Pablo's Mac.

| Piece | Where | What it is |
| --- | --- | --- |
| The data | R2 bucket `lovegroove-catalogue`, file `vinyl/current.pack` | The only file in the bucket. Private: no public or r2.dev address |
| Live address | Worker `lovegroove-catalogue` at **https://catalogue.lovegroove.app** (Custom Domain) | What the iPhone app uses. Config `worker/wrangler.production.jsonc` |
| Test address | Worker `lovegroove-catalogue-test` at `lovegroove-catalogue-test.hapticmatter-site.workers.dev` | Older TestFlight builds only. Delete when Pablo says they've expired. Config `worker/wrangler.jsonc` |
| Monthly build | Pablo's Mac, launchd job `app.lovegroove.catalogue` | 10:00 on the 3rd–7th; see [mac/README.md](mac/README.md) |
| Write key | R2 API token (18 Nov 2026), stored only in the Mac's Keychain (`lovegroove-r2`) | The only key that can change the bucket |

Day to day there's nothing to do. Each month you get a Mac notification saying the new
catalogue was uploaded (or that the build failed, with the log's name).

**Quick checks** (Terminal):

```bash
# The live address answers, and which month's dump it serves ("catalogue")
curl -s https://catalogue.lovegroove.app/release/249504 | head -c 300; echo

# The Mac's R2 key still works
cd ~/Library/Application\ Support/LovegrooveCatalogue && s(){ security find-generic-password -s lovegroove-r2 -a "$1" -w; }; R2_ACCOUNT_ID="$(s account-id)" R2_ACCESS_KEY_ID="$(s access-key-id)" R2_SECRET_ACCESS_KEY="$(s secret-access-key)" python3 -I r2_upload.py --bucket lovegroove-catalogue --check

# The monthly build is scheduled
launchctl print gui/$(id -u)/app.lovegroove.catalogue | grep state

# The last build's log
tail -30 ~/Library/Logs/LovegrooveCatalogue/$(date -u +%Y%m01).log
```

**Changing the Worker's code:** edit `worker/src/`, then from `worker/` deploy both copies:
`npx wrangler deploy -c wrangler.production.jsonc` and `npx wrangler deploy`.

**In the Cloudflare dashboard** (https://dash.cloudflare.com):
- **Compute → Workers & Pages**: the two Workers, their domains, requests and errors.
- **Storage & databases → R2 Object Storage**: the bucket, usage this month, and
  **Manage API Tokens** (under Account Details).
- **Manage account → Notifications**: the $1 budget alert.
- **Profile → Authentication**: two-factor sign-in.

## Security

**What anyone can do:** look up records through the Worker, as the app does. That's by
design: the data is Discogs' public-domain (CC0) dump, which anyone can download anyway. Nothing
private is stored here: no accounts, collections, plays, names or emails, and the Worker keeps
no logs of lookups. Cloudflare sees the address of each phone that asks, as for any website.

**What is protected, and how:**

| Risk | Protection |
| --- | --- |
| Changing or deleting the data | The bucket is private (no public address). Writes need the R2 token in the Mac's Keychain, or the Cloudflare login. GitHub's copies of the key were deleted, and the old token too (19 Nov 2026) |
| Changing the Worker | Needs the Cloudflare login (or the Mac's Wrangler sign-in). The Worker itself only answers `GET` (anything else gets 405), accepts only number ids, 8–14 digit barcodes and up to 6 search words, and never runs a query anyone could tamper with |
| Downloading the whole file | Not possible through the Worker (the file's path gives 404); it reads only small blocks |
| Floods and costs | 120 lookups a minute per visitor (loosely enforced: about 950 got through in a test before the first 429). Most lookups come from Cloudflare's cache, not R2. The budget alert emails once billable usage passes $1; it warns but doesn't stop charges. Unfinished uploads are aborted after 7 days |
| A broken monthly build | Under 7 million vinyl pressings is never uploaded; a failed upload is aborted and the live file stays as it was |
| The catalogue being down | The iPhone app falls back to MusicBrainz and Apple, with no error shown |
| Someone in the Cloudflare account | Two-factor sign-in (Profile → Authentication), or Apple Account two-factor if signing in with Apple |

Rules: never paste the R2 keys into chats, files or screenshots; make a new token rather than
copying the Mac's one; keep two-factor on.

## If something goes wrong

Pick by what's happening. Turning things off is safe: the iPhone app falls back to MusicBrainz
and Apple, and everything can be brought back from this repo.

| What's happening | Do |
| --- | --- |
| Unexpected bill, budget alert email, or a flood of requests | **Step 1** |
| Wrong or broken data in the app | **Step 2**, then fix the data (step 4) |
| R2 key may have leaked | **Step 2** and **step 3** |
| Cloudflare login may have leaked | Change the password / sign out other sessions, check two-factor, then **step 3** and check the Workers and bucket |

**1. Stop all reads (takes the catalogue offline).** Nothing reads R2 except the Workers.
- Dashboard: **Compute → Workers & Pages** → `lovegroove-catalogue` → **Settings → Domains &
  Routes** → remove `catalogue.lovegroove.app`. Then `lovegroove-catalogue-test` → same place →
  disable **workers.dev**. Undo: add the domain back, turn workers.dev on.
- Or Terminal (all settings are in the repo, so this is easy to undo):

  ```bash
  cd "/Users/pablo/Documents/Haptic Matter/Apps/Lovegroove/Code/Catalogue/worker"
  npx wrangler delete --name lovegroove-catalogue
  npx wrangler delete --name lovegroove-catalogue-test
  ```

  Bring back: `npx wrangler deploy -c wrangler.production.jsonc` (this also brings back
  `catalogue.lovegroove.app`) and, while TestFlight builds still need it, `npx wrangler deploy`.

**2. Stop the monthly upload.**

```bash
launchctl bootout gui/$(id -u)/app.lovegroove.catalogue
```

Turn back on: `mac/install.sh`.

**3. Revoke the write key.** Dashboard → **R2 Object Storage → Manage API Tokens** → delete
the token. Nothing can then write to the bucket. Afterwards make a new token and save it with
`mac/set_r2_key.sh` (see [mac/README.md](mac/README.md)).

**4. Fix the data.** Rebuild on the Mac (about 45 minutes) and upload over the bad file:

```bash
~/Library/Application\ Support/LovegrooveCatalogue/monthly_build.sh 20261101
```

(use the 1st of the month you want). Last resort: delete `vinyl/current.pack` in **R2 Object
Storage → lovegroove-catalogue**; the app then falls back to MusicBrainz until the next build.
Keeping the file costs nothing (2.5 GB of the 10 GB free).

After any of these, check with the **Quick checks** above, and note what happened and the date
here.

## Run locally on a sample

    python3 build_catalogue.py --dump 20261101 --limit 200000 --out sample.sqlite
    python3 pack_catalogue.py --db sample.sqlite --out sample.pack
