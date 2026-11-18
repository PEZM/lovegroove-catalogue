# Using the Lovegroove catalogue in the iPhone app

How the iPhone app gets vinyl pressing data from Lovegroove's own catalogue, and how to test
it. Written 18 November 2026. **iOS only for now**; Android comes later and will follow the
same design.

## Where things stand

- **Discogs' API is switched off** in the app (`Services.discogsCatalogue = false`, since
  24 October 2026). Its terms want written permission for a paid app; asked on 30 October
  (Help Center request 1292535), no decision yet. Records, tracklists, barcodes and pressings
  come from **MusicBrainz and Apple** instead.
- **The catalogue is built from Discogs' monthly data dumps, which are CC0** (public domain).
  Using them needs no permission, and the data can be stored and shown freely. That is why it
  exists: it gives back Discogs-quality vinyl data while the API stays off.
- All the Discogs API code is still in the app, behind `Services.discogsCatalogue`.

## The rule

A new switch, `Services.lovegrooveCatalogue`, next to the others in `Lovegroove/Services.swift`.
For each catalogue lookup (finding a record, its tracklist, pressing facts, other pressings,
genres):

| `discogsCatalogue` | `lovegrooveCatalogue` | Sources, in order |
| --- | --- | --- |
| off (now) | **on** | **Lovegroove catalogue** → MusicBrainz → Apple |
| off | off | MusicBrainz → Apple (exactly today's app) |
| on (if Discogs gives permission) | either | Discogs API → MusicBrainz → Apple (exactly the app before 24 Oct). The catalogue isn't used. |

So the catalogue takes Discogs' place now, and if permission ever comes, turning
`discogsCatalogue` back on switches to Discogs with no other change. The catalogue can
stay compiled in as a later fallback, but that isn't part of this work.

Within "Lovegroove catalogue first":
- **Vinyl only.** The catalogue has only vinyl. For a CD or cassette (the form's format, or the
  record's), skip it and go straight to MusicBrainz.
- **Never an error because of it.** A 404, 429, 5xx, network error or 5-second timeout means
  "not found here": carry on to MusicBrainz, as if the catalogue weren't there.
- **Same models.** Catalogue results become the existing `DiscogsRelease`, `DiscogsTrack`,
  `Pressing` and `PressingChoice`, with the **Discogs release id as `id`** (positive, as for
  Discogs records today), so screens, plays, saved tracklists and "Wrong record?" all work as
  they do for Discogs records.
- **Keep what it returns.** CC0 data can be saved like MusicBrainz data: saved tracklists,
  `Library.shared.keep(pressing)` and so on, so records work offline.
- **Covers unchanged.** The catalogue has no images; covers stay with Apple and the Cover Art
  Archive as today.

## What it gives the app that it lacks today

- **Exact pressings.** Today a record found by name links to a MusicBrainz release, which often
  has no vinyl pressing details. The catalogue has 8.1 million vinyl pressings with label,
  catalogue number, country, year, format and side-by-side tracklists.
- **Barcodes.** 855,000 vinyl barcodes, checked before MusicBrainz.
- **Records imported from a Discogs CSV.** They carry real Discogs release ids. Today their
  tracklists are guessed through MusicBrainz and Apple by name; the catalogue answers by id,
  for the exact pressing.
- **Other pressings of an album**, "Wrong record?", genres for older listens, and the release
  details panel, which today only work with Discogs on.

## Which code changes (`Lovegroove/CollectionProvider.swift` unless noted)

Names and line numbers as of 18 November 2026; check before starting. "Today" is the path
with Discogs off.

| What | Where | Today (Discogs off) | With the catalogue |
| --- | --- | --- | --- |
| Barcode → pressings | `releases(barcode:)`, called first by `search(barcode:)` in `Shelf.swift` | Returns nothing; Shelf then tries MusicBrainz, then Apple | `/barcode/:digits` → `DiscogsRelease`s (Shelf shows them as `.discogs` results, already supported) |
| Name → pressings (Add a record) | `searchReleases(artist:album:)`, called by `byName()` in `Shelf.swift` | Returns nothing; Shelf uses `MusicBrainz.lookup` | `/search?q=artist album`, keep results whose artist matches (`Normalize.artistsMatch`), then `/release/:id` for the top few (format, label, country). Only when the wanted format is Vinyl. *As built:* the first matching album's `/master/:id` instead (its pressings already carry format, label and country; 2 lookups); `/release` only for matching pressings with no album |
| Tracklist | `rawTracklist(for:)` | `id < 0 \|\| !discogsCatalogue` → saved, MusicBrainz, Apple | `id > 0`: saved, then `/release/:id` → `tracks` (and `recordOnly`), then MusicBrainz, Apple. Missing durations: borrow from the album's main pressing (`/master/:id` → `main_release_id` → `/release`), then today's `fillLengths` |
| Pressing facts (record page) | `fetchPressing(of:)` | MusicBrainz | `id > 0`: `/release/:id` → `Pressing` (formats, "Label – catno", country, year, genres, styles; no notes or credits), URL `discogs.com/release/:id`. 404 → MusicBrainz |
| Other pressings / Wrong record? | `pressings(like:search:)` | MusicBrainz | `id > 0`, no search: `/release/:id` → `master_id` → `/master/:id` → `PressingChoice`s (main first, then by year, at most 100). With search words, or a MusicBrainz record: `/search?q=` (vinyl). Nothing → MusicBrainz. *As built:* typed words cost 2 lookups at most (the search and the first album's `/master`); pressings with no album show the search's title and year only |
| Recognised song not on the shelf | `findRecord(for:)` step 3 (`skipDiscogs`), `currentRelease()` in `ListeningStateMachine.swift` | Step 3 skipped; MusicBrainz `album(for:)` | Step 3's album search uses the catalogue's `searchReleases`. The song search (`releasesContaining`) has no catalogue equivalent and stays off |
| Genres for older listens | `fillInMissingGenres()` in `HistoryView.swift`, `genreAndStyle(artist:album:)` | Skipped | `/search?q=` → `/release/:id` → first genre and style (keep the 10-a-visit cap) |
| Release details panel | `releaseDetails(artist:album:)`, used by `ListeningStateMachine.swift` | Nothing | `/search?q=` → `/release/:id`: styles, genres, tracklist, country, format, year; no notes or credits |

Stays as it is: covers (`cover(for:)`, `ShelfCovers.swift`), artist photos, song search
(`releasesContaining`, `MusicBrainz.records(withSong:)`), Discogs sign-in, collection and
wantlist, `musicBrainzID(for:)`.

A tidy way to build it: a small `LovegrooveCatalogue` client (its own file) that returns the
app's models, and in each function above one branch: `if Services.discogsCatalogue { today's
Discogs code } else if Services.lovegrooveCatalogue, let found = await catalogue… { found }`,
falling through to today's MusicBrainz code. `get(_:)` keeps refusing Discogs calls while
`discogsCatalogue` is off.

## Decisions for Pablo before release

**Made 18 Nov 2026:** 1 as suggested (and in Spanish); 2 kept; 3 kept.

1. **Credit line.** Onboarding says "Record details from MusicBrainz, the open music
   encyclopedia." CC0 needs no credit, and `DiscogsCredit` ("Data provided by Discogs") belongs
   to the API, so it stays off. Suggested: "Record details from MusicBrainz and Discogs' open
   data." Naming Discogs as a source is factual; nothing should suggest Discogs endorses the app.
2. **"Add it on Discogs too" prompt.** After adding a `.discogs` result without a Discogs
   account, Shelf offers Discogs' page (`addedFromDiscogs`). It's only a link to discogs.com,
   which is still allowed, but with the catalogue it will show much more often. Keep it or hide it.
3. **Discogs links on pressings** (`discogs.com/release/:id`): fine as plain links; keep.

## The API

**Address for the app: `https://catalogue.lovegroove.app`** (permanent, live since
18 November 2026; Worker `lovegroove-catalogue`, config `worker/wrangler.production.jsonc`).

Test address, for TestFlight builds made before the switch only:
`https://lovegroove-catalogue-test.hapticmatter-site.workers.dev` (Worker
`lovegroove-catalogue-test`, config `worker/wrangler.jsonc`). Both serve the same R2 file with the
same code, limit (120 a minute, separate counters) and `Cache-Control`. Deploy code changes to
both: `npx wrangler deploy` and `npx wrangler deploy -c wrangler.production.jsonc` in `worker/`.

Keep the address in one constant. All replies are JSON; every success also carries `source`
("Discogs data dump (CC0)"), `catalogue` (the dump date, e.g. "20261101") and `timing`
(debugging only).

### `GET /release/:id` — one pressing

```json
{"release": {
  "id": 249504, "title": "Never Gonna Give You Up", "artist": "Rick Astley",
  "year": 1987, "released": "1987-07-00", "country": "UK",
  "format": "Vinyl, 7\", 45 RPM, Single, Stereo",
  "master_id": 96559, "is_main": false,
  "genres": "Electronic, Pop", "styles": "Euro-Disco",
  "labels": [{"name": "RCA", "catno": "PB 41447", "id": 895}],
  "tracks": [{"position": "A", "title": "Never Gonna Give You Up", "duration": "3:32"},
             {"position": "B", "title": "Never Gonna Give You Up (Instrumental)", "duration": "3:30"}]
}}
```

`master_id` is null for a pressing with no album. `genres` and `styles` are comma-separated
strings. `duration` may be empty. Tracklists hold songs only (no headings).

### `GET /master/:id` — an album and its vinyl pressings

```json
{"album": {
  "master_id": 96559, "title": "Never Gonna Give You Up", "artist": "Rick Astley", "year": 1987,
  "main_release_id": 221824,
  "pressings": [
    {"id": 182213, "title": "...", "artist": "Rick Astley", "year": 1987, "country": "US",
     "format": "Vinyl, 12\", 33 ⅓ RPM, Maxi-Single", "label": "RCA Victor", "catno": "6784-1-RD",
     "master_id": 96559, "is_main": false}
  ]
}}
```

`main_release_id` may be a non-vinyl pressing that isn't in `pressings`. Big albums have
hundreds of pressings (The Dark Side Of The Moon: about 670).

### `GET /barcode/:digits` — pressings with that barcode

8–14 digits; leading zeros don't matter (`0009998101701` = `9998101701`). Returns
`{"barcode": "...", "pressings": [ ...as in /master... ]}`; empty `pressings` (200) when
nothing matches.

### `GET /search?q=words` — albums and pressings

Up to 25 results, most pressings first; up to 6 words; accents and case don't matter; the last
word can be partial.

```json
{"results": [
  {"type": "album", "master_id": 26647, "release_id": 2879, "artist": "Daft Punk",
   "title": "Discovery", "year": 2001, "pressings": 14},
  {"type": "pressing", "master_id": null, "release_id": 135671, "artist": "Daft Punk",
   "title": "Homework / Discovery / Alive 1997", "year": 2001, "pressings": 1}
]}
```

An `album` result's `release_id` is its main vinyl pressing. No format, country or label here;
fetch `/release/:id` or `/master/:id` for those.

### Errors

| Reply | Meaning | App |
| --- | --- | --- |
| 404 `no such record` | Not in the catalogue (often: not vinyl) | MusicBrainz |
| 429, `Retry-After: 60` | Over 120 lookups a minute from this phone | MusicBrainz for that call; don't retry in a loop |
| 5xx, network error, 5 s timeout | | MusicBrainz |

Label names can keep Discogs' namesake suffix ("Harvest (2)"); use `DiscogsRelease.plainName`.
Replies carry `Cache-Control: public, max-age=86400`. Send the User-Agent
`Lovegroove/<version> +https://lovegroove.app`.

## As built in the iPhone app (18 Nov 2026)

Branch `lovegroove-catalogue` in `Code/iOS` (not merged into main yet). Client:
`Lovegroove/LovegrooveCatalogue.swift`; tests: `LovegrooveTests/CatalogueTests.swift`.

- Barcode results: official pressings first, then those with "Unofficial Release".
- Search rows and other pressings show their album's cover (Apple or the Cover Art Archive,
  found once per album; no Cloudflare calls).
- Choosing another pressing saves its facts at once, so its page works offline.
- Settings credit while the catalogue is on: "Record details from MusicBrainz and Discogs'
  open data."
- Things the app has to allow for (Android too): formats spell "2×Vinyl" with a real ×, and
  write "Compilation" / "Maxi-Single" where Discogs exports abbreviate; many vinyl tracklists
  have no song lengths (Apple's fill them); the Worker splits "Don't" into two words.
- A new subdomain can be "not found" on a home network for up to 30 minutes (the router
  remembers asking before it existed; lovegroove.app's SOA negative TTL is 1800 s). The app
  falls back to MusicBrainz meanwhile.

## Testing

Debug builds should print which source answered (`📀 catalogue` / `📀 MusicBrainz`), like the
existing `📀 from MusicBrainz` line. A launch argument `-noLovegrooveCatalogue` (debug only, like
`-musicBrainzOnly`) gives today's behaviour for side-by-side checks.

### Known records

| Check | Lookup | Expect |
| --- | --- | --- |
| Pressing | release `249504` | Rick Astley, Never Gonna Give You Up, UK 1987, 7", RCA – PB 41447, 2 tracks, album 96559 |
| Album | master `96559` | Main pressing 221824; about 57 vinyl pressings |
| Big album | master `10362` | Pink Floyd, The Dark Side Of The Moon; main pressing 1873013; about 670 pressings |
| Barcode | `0009998101701`, also `9998101701` | The same 15 Dark Side Of The Moon pressings |
| Search | `pink floyd dark side` | The Dark Side Of The Moon album first |
| Search while typing | `daft punk disc` | Discovery (master 26647) first |
| No album | release `1630432` | Burial, Presumed Dead, `master_id` null |
| Not vinyl / unknown | release `999999999` | 404 → MusicBrainz |

Search for one short word like `bjo` ranks Jussi Bjoerling above Björk: a known catalogue
quirk, not an app bug.

### Checklist in the app (Simulator, then phone)

1. **Add by barcode.** `0009998101701`: Dark Side Of The Moon pressings with label and country,
   from the catalogue. Add one: tracklist with sides, cover from Apple.
2. **Add by name, Vinyl.** "Daft Punk" / "Discovery": vinyl pressings from the catalogue.
3. **Add by name, CD.** Same with format CD: straight to MusicBrainz, as today.
4. **Barcode the catalogue lacks** (a CD's): MusicBrainz, then Apple, as today.
5. **Record page.** A record added in 1: pressing facts (format, label – catno, country, year,
   genres, styles), no notes or credits section, works offline after one visit.
6. **Other pressings / Wrong record?** On that record: the album's vinyl pressings, main first.
   Pick another: it replaces the record, plays stay with it.
7. **Discogs CSV import.** Import a collection CSV with a few vinyl records: their tracklists
   and pressing facts now come from the catalogue by id.
8. **Existing shelf.** Records added before (MusicBrainz, negative ids): unchanged; "Wrong
   record?" offers catalogue vinyl pressings by name.
9. **Listening.** Recognise a song from an album not on the shelf: the album loads with the
   catalogue's tracklist; a song only on a compilation still loads via MusicBrainz.
10. **Genres** for older listens and the **release details** panel now fill in.
11. **Offline / airplane mode:** everything saved still shows, no errors.
12. **Catalogue down.** Point the constant at a wrong host (debug): everything falls back to
    MusicBrainz and Apple with no visible error.
13. **Switches.** `lovegrooveCatalogue = false`: the app is today's. `discogsCatalogue = true`
    (local build only, never shipped without permission): Discogs API paths run as before 24 Oct.
14. **Speed.** Adding by search or barcode is at least as quick as today.

### After a monthly build

From the 3rd (Mac notification "Catalogue … uploaded"): `catalogue` in replies shows the new
month, and checks 1, 2 and 5 still pass. The Worker picks up the new file by itself.

## Steps

| # | Chat (folder) | What | Status |
| --- | --- | --- | --- |
| 1 | iOS (`Code/iOS`) | Catalogue client and switch; the changes above; unit tests for the JSON with the sample replies | **Done 18 Nov 2026** on branch `lovegroove-catalogue` (commit 165ebfa), not merged into main; 13 catalogue unit tests in `LovegrooveTests/CatalogueTests.swift` |
| 2 | iOS | Checklist on the Simulator and phone; Pablo's decisions 1–3 | **Done 18 Nov 2026.** Checks 1–12 and 14 pass on the Simulator; check 13 (`discogsCatalogue = true`) checked by reading the code only, to keep Discogs' API untouched. Decisions made (above) |
| 3 | Catalogue (`Code/Catalogue`) | Permanent address `catalogue.lovegroove.app` | **Done 18 Nov 2026.** Own Worker `lovegroove-catalogue`; every known record checked there, unknown release 404, rate limit 429 with `Retry-After: 60`; $1 budget alert confirmed in place |
| 3a | iOS | Set the app's constant to `https://catalogue.lovegroove.app` | **Done 18 Nov 2026** on the branch (installed on Pablo's iPhone; to be committed) |
| 3b | Catalogue | Delete the `lovegroove-catalogue-test` Worker | **Wait** until Pablo says the TestFlight builds using it have expired |
| 4 | Web (`Code/Web`) | Privacy policy: record lookups now reach Lovegroove's catalogue server | **On hold** (Pablo, 18 Nov 2026): do it once the catalogue is going to ship, or before external TestFlight testers get a build |
| 5 | iOS | Docs (`CLAUDE.md`, README "Before release", `Services.swift` comment), TestFlight, then release | To do |

Steps 3a and 4 must be done before the App Store release.

## Prompts

**iOS chat** (`Code/iOS`), steps 1–2:

> Read `../Catalogue/APP-INTEGRATION.md`. Discogs' API stays off (`Services.discogsCatalogue`)
> until Discogs gives written permission; the Lovegroove catalogue (built from Discogs' CC0
> dumps) takes its place now. Implement step 1: a `LovegrooveCatalogue` client (address
> `https://catalogue.lovegroove.app` in one constant) and `Services.lovegrooveCatalogue = true`, and wire it into the functions in
> "Which code changes", in the order in "The rule": Discogs if switched on, else the catalogue
> for vinyl, else today's MusicBrainz and Apple. Add unit tests for the JSON with the sample
> replies, then run the testing checklist on the Simulator and tell me the results, with the
> three decisions for me. Don't commit until I say.

**Catalogue chat** (`Code/Catalogue`), step 3b (only when Pablo says):

> Read `APP-INTEGRATION.md`. Do step 3b: the TestFlight builds that used the test address
> have expired, so delete the `lovegroove-catalogue-test` Worker (not `lovegroove-catalogue`),
> then check `https://catalogue.lovegroove.app` still answers.

**Web chat** (`Code/Web`), step 4:

> Read `../Catalogue/APP-INTEGRATION.md`. Do step 4: update the privacy policy on
> lovegroove.app to say record lookups (search words, barcodes, release ids) go to
> Lovegroove's catalogue server on Cloudflare, which stores nothing about the user. Show me
> the wording before publishing.
