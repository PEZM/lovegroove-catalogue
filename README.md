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
| `worker/` | Cloudflare Worker: `/release/:id`, `/master/:id`, `/barcode/:digits`, `/search?q=`. |
| `.github/workflows/build-catalogue.yml` | Same build on GitHub, manual backup only (see below). |

## Where the monthly build runs

**On a Mac** (`mac/`), at 10:00 on the 3rd of each month, with retries up to the 7th.
See [mac/README.md](mac/README.md) for setup, logs and troubleshooting.

**Not on GitHub.** Discogs' download site sits behind Cloudflare's bot protection, which
answers GitHub's servers with `403 Forbidden` (November 2026; one run got through, later
ones were all refused). The same request from a home connection works. The workflow is kept
for manual runs in case that changes, but has no schedule.

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

## Run locally on a sample

    python3 build_catalogue.py --dump 20261101 --limit 200000 --out sample.sqlite
    python3 pack_catalogue.py --db sample.sqlite --out sample.pack
