# Lovegroove catalogue

Lovegroove's shared vinyl catalogue, built each month from Discogs' data dumps
(CC0, https://data.discogs.com/). Only vinyl releases are kept, with the fields
the app shows. No images, user collections or marketplace data.

- `build_catalogue.py` reads the releases dump as it downloads and writes a SQLite database.
- `pack_catalogue.py` turns that into one packed file in which every lookup is a single block read.
- `.github/workflows/build-catalogue.yml` runs both on GitHub on the 3rd of each month and replaces
  `vinyl/current.pack` in Cloudflare R2 (the Worker notices the new file by itself).
- `worker/` is the Cloudflare Worker that answers lookups from the file:
  `/release/:id`, `/master/:id`, `/barcode/:digits`, `/search?q=`.

User data (collections, wishlists, sync) does not live here.

Run locally on a sample:

    python3 build_catalogue.py --dump 20261101 --limit 200000 --out sample.sqlite
    python3 pack_catalogue.py --db sample.sqlite --out sample.pack
