#!/usr/bin/env python3
"""Build Lovegroove's vinyl catalogue from Discogs' monthly data dump.

Reads discogs_YYYYMMDD_releases.xml.gz as it downloads (never stored whole),
keeps vinyl releases only and only the fields the app shows, and writes a
SQLite database for pack_catalogue.py (which also builds the search index).

Discogs' data dumps are CC0 ("No Rights Reserved"): https://data.discogs.com/
No images, user collections or marketplace data are in the dumps or kept here.

Usage:
  python3 build_catalogue.py --dump 20261101 --out catalogue.sqlite [--limit 200000]
  python3 build_catalogue.py --file releases.xml.gz --out catalogue.sqlite
"""
import argparse
import gzip
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

DUMP_URL = "https://data.discogs.com/?download=data%2F{year}%2Fdiscogs_{date}_releases.xml.gz"
USER_AGENT = "LovegrooveCatalogue/1.0 (+https://lovegroove.app)"

SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
PRAGMA page_size = 4096;

CREATE TABLE releases (
  id         INTEGER PRIMARY KEY,   -- Discogs release id (one pressing)
  master_id  INTEGER,               -- Discogs master id (the album), NULL if none
  is_main    INTEGER NOT NULL DEFAULT 0,
  title      TEXT NOT NULL,
  artist     TEXT NOT NULL,         -- display credit, e.g. "Daft Punk" or "A & B"
  year       INTEGER,
  released   TEXT,                  -- as Discogs gives it, e.g. "1999-03-00"
  country    TEXT,
  format     TEXT,                  -- e.g. "2×Vinyl, 12\", 33 ⅓ RPM, Album"
  labels     TEXT,                  -- JSON [[name, catno, label_id], ...]
  genres     TEXT,                  -- "Electronic, Rock"
  styles     TEXT,
  tracks     TEXT                   -- JSON [[position, title, duration], ...]
) WITHOUT ROWID;

CREATE TABLE barcodes (
  code       TEXT NOT NULL,         -- digits only
  release_id INTEGER NOT NULL,
  PRIMARY KEY (code, release_id)
) WITHOUT ROWID;

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID;
"""

INDEXES = """
CREATE INDEX releases_master ON releases(master_id) WHERE master_id IS NOT NULL;
"""


def text(elem, path):
    found = elem.find(path)
    if found is None or found.text is None:
        return None
    value = found.text.strip()
    return value or None


ARTIST_SUFFIX = re.compile(r" \(\d+\)$")  # Discogs adds "(2)" to tell namesakes apart


def artist_credit(release):
    parts = []
    for artist in release.findall("artists/artist"):
        name = text(artist, "anv") or text(artist, "name") or ""
        name = ARTIST_SUFFIX.sub("", name)
        join = text(artist, "join")
        parts.append(name)
        if join:
            parts.append(" " + join + " " if join != "," else ", ")
        else:
            parts.append(" ")
    return re.sub(r"\s+", " ", "".join(parts)).strip(" ,") or "Unknown Artist"


def format_line(formats):
    pieces = []
    for f in formats:
        qty = f.get("qty") or "1"
        name = f.get("name") or ""
        head = f"{qty}×{name}" if qty not in ("", "0", "1") else name
        bits = [head] + [d.text.strip() for d in f.findall("descriptions/description") if d.text]
        free = (f.get("text") or "").strip()
        if free:
            bits.append(free)
        pieces.append(", ".join(bits))
    return "; ".join(pieces)


def year_of(released):
    if released and len(released) >= 4 and released[:4].isdigit():
        y = int(released[:4])
        return y if y > 0 else None
    return None


def tracklist(release):
    out = []
    for t in release.findall("tracklist/track"):
        title = text(t, "title")
        if not title:
            continue
        out.append([text(t, "position") or "", title, text(t, "duration") or ""])
    return out


def open_stream(args):
    if args.file:
        return gzip.open(args.file, "rb")
    url = DUMP_URL.format(year=args.dump[:4], date=args.dump)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    resp = urllib.request.urlopen(req, timeout=120)
    return gzip.GzipFile(fileobj=resp)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dump", help="dump date, e.g. 20261101")
    p.add_argument("--file", help="local releases.xml.gz instead of downloading")
    p.add_argument("--out", required=True)
    p.add_argument("--limit", type=int, default=0, help="stop after this many releases read (test runs)")
    args = p.parse_args()
    if not (args.dump or args.file):
        p.error("give --dump or --file")

    if os.path.exists(args.out):
        os.remove(args.out)
    db = sqlite3.connect(args.out)
    db.executescript(SCHEMA)

    seen = kept = 0
    started = time.time()
    batch_r, batch_b = [], []

    def flush():
        db.executemany("INSERT OR REPLACE INTO releases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch_r)
        db.executemany("INSERT OR IGNORE INTO barcodes VALUES (?,?)", batch_b)
        batch_r.clear(); batch_b.clear()

    stream = open_stream(args)
    context = ET.iterparse(stream, events=("start", "end"))
    _, root = next(context)
    for event, elem in context:
        if event != "end" or elem.tag != "release":
            continue
        seen += 1
        formats = elem.findall("formats/format")
        # Recent dumps carry no status attribute; older ones mark drafts/deleted.
        if elem.get("status", "Accepted") == "Accepted" and any(f.get("name") == "Vinyl" for f in formats):
            rid = int(elem.get("id"))
            master = elem.find("master_id")
            master_id = int(master.text) if master is not None and master.text and master.text.strip().isdigit() else None
            master_id = master_id or None  # the dump uses 0 for "no master"
            is_main = 1 if master is not None and master.get("is_main_release") == "true" else 0
            released = text(elem, "released")
            labels = [[l.get("name") or "", l.get("catno") or "", int(l.get("id") or 0)]
                      for l in elem.findall("labels/label")]
            artist = artist_credit(elem)
            title = text(elem, "title") or "Untitled"
            batch_r.append((
                rid, master_id, is_main, title, artist, year_of(released), released,
                text(elem, "country"), format_line(formats),
                json.dumps(labels, ensure_ascii=False, separators=(",", ":")),
                ", ".join(g.text for g in elem.findall("genres/genre") if g.text) or None,
                ", ".join(s.text for s in elem.findall("styles/style") if s.text) or None,
                json.dumps(tracklist(elem), ensure_ascii=False, separators=(",", ":")),
            ))
            for ident in elem.findall("identifiers/identifier"):
                if ident.get("type") in ("Barcode", "UPC", "EAN"):
                    digits = re.sub(r"\D", "", ident.get("value") or "")
                    if 8 <= len(digits) <= 14:
                        batch_b.append((digits, rid))
            kept += 1
            if len(batch_r) >= 5000:
                flush()
        root.clear()  # keep memory flat
        if seen % 100000 == 0:
            rate = seen / max(time.time() - started, 1)
            print(f"read {seen:,} releases, kept {kept:,} vinyl ({rate:,.0f}/s)", file=sys.stderr, flush=True)
        if args.limit and seen >= args.limit:
            break
    flush()
    try:
        stream.close()
    except Exception:
        pass

    db.executescript(INDEXES)
    db.executemany("INSERT INTO meta VALUES (?,?)", [
        ("source", "Discogs data dump (CC0), https://data.discogs.com/"),
        ("dump", args.dump or os.path.basename(args.file)),
        ("built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        ("releases_read", str(seen)),
        ("vinyl_kept", str(kept)),
    ])
    db.commit()
    db.execute("VACUUM")
    db.close()
    size = os.path.getsize(args.out)
    mins = (time.time() - started) / 60
    print(f"done: read {seen:,}, kept {kept:,} vinyl, {size/1e6:,.1f} MB, {mins:.1f} min", file=sys.stderr)


if __name__ == "__main__":
    main()
