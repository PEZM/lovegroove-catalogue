#!/usr/bin/env python3
"""Turn catalogue.sqlite (from build_catalogue.py) into one packed file shaped
for R2: every lookup is a single range read of one block.

File layout (all numbers little-endian):
  bytes 0-7    magic b"LGCAT002"
  bytes 8-11   header length H (uint32)
  bytes 12-15  binary index length B (uint32)
  bytes 16..   header: gzip(JSON) with meta and, per section, where its
               arrays sit in the binary index (text first keys stay in JSON)
  then         binary index: raw float64 arrays (block first keys for numeric
               sections, block offsets for all), read without decoding
  then         sections, each a run of gzip(JSON) blocks

Each section is a list of (key, value) pairs sorted by key and cut into
blocks of about BLOCK_TARGET bytes, always at a key boundary. The header
keeps each block's first key and its byte offset, so a reader finds the one
block that can hold a key and fetches only that block.

Sections:
  release  key: Discogs release id      value: the full pressing
  master   key: Discogs master id       value: the album and all its vinyl pressings (summaries)
  barcode  key: barcode digits (text)   value: pressings with that barcode (summaries)
  search   key: word (text)             value: [total, [albums...]] best first, capped
"""
import argparse
import gzip
import json
import os
import re
import sqlite3
import struct
import sys
import tempfile
import time
import unicodedata

MAGIC = b"LGCAT002"
BLOCK_TARGET = 96 * 1024
SEARCH_CAP = 1500  # albums kept per word, most-pressed first


def words(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.findall(r"[^\W_]+", text)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class SectionWriter:
    def __init__(self, name, numeric):
        self.name = name
        self.numeric = numeric
        self.file = tempfile.TemporaryFile()
        self.first_keys, self.offsets = [], []
        self.pending, self.pending_size, self.count = [], 0, 0
        self.last_key = None

    def add(self, key, value):
        self.add_raw(key, dumps(value))

    def add_raw(self, key, value_json):
        if self.last_key is not None and key <= self.last_key:
            raise ValueError(f"{self.name}: keys out of order: {self.last_key!r} then {key!r}")
        self.last_key = key
        item = "[" + dumps(key) + "," + value_json + "]"
        if not self.pending:
            self.pending_first = key
        self.pending.append(item)
        self.pending_size += len(item.encode("utf-8")) + 1
        self.count += 1
        if self.pending_size >= BLOCK_TARGET:
            self.flush()

    def flush(self):
        if not self.pending:
            return
        self.first_keys.append(self.pending_first)
        self.offsets.append(self.file.tell())
        self.file.write(gzip.compress(("[" + ",".join(self.pending) + "]").encode("utf-8"), 6, mtime=0))
        self.pending, self.pending_size = [], 0

    def finish(self):
        self.flush()
        self.size = self.file.tell()


def f64(values):
    return struct.pack(f"<{len(values)}d", *values)


def summary(row):
    labels = json.loads(row["labels"] or "[]")
    return {
        "id": row["id"], "title": row["title"], "artist": row["artist"], "year": row["year"],
        "country": row["country"], "format": row["format"],
        "label": labels[0][0] if labels else None, "catno": labels[0][1] if labels else None,
        "master_id": row["master_id"], "is_main": bool(row["is_main"]),
    }


def full(row):
    out = summary(row)
    out.pop("label"); out.pop("catno")
    out.update({
        "released": row["released"], "genres": row["genres"], "styles": row["styles"],
        "labels": [{"name": n, "catno": c, "id": i} for n, c, i in json.loads(row["labels"] or "[]")],
        "tracks": [{"position": p, "title": t, "duration": d} for p, t, d in json.loads(row["tracks"] or "[]")],
    })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    started = time.time()
    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    meta = dict(db.execute("SELECT key, value FROM meta").fetchall())

    # 1. Pressings by id.
    release = SectionWriter("release", True)
    for row in db.execute("SELECT * FROM releases ORDER BY id"):
        release.add(row["id"], full(row))
    release.finish()
    print(f"release: {release.count:,} in {len(release.offsets):,} blocks", file=sys.stderr)

    # 2. Albums with their pressings; also collect search documents.
    work_path = args.out + ".work.sqlite"
    if os.path.exists(work_path):
        os.remove(work_path)
    work = sqlite3.connect(work_path)  # scratch database for search postings
    work.execute("PRAGMA journal_mode = OFF")
    work.execute("PRAGMA synchronous = OFF")
    work.execute("CREATE TABLE postings (word TEXT, score INTEGER, doc TEXT)")
    work.execute("CREATE TABLE docs (doc TEXT PRIMARY KEY, body TEXT) WITHOUT ROWID")

    def index_doc(doc, body, text, score):
        work.execute("INSERT INTO docs VALUES (?,?)", (doc, dumps(body)))
        work.executemany("INSERT INTO postings VALUES (?,?,?)", [(w, score, doc) for w in set(words(text))])

    master = SectionWriter("master", True)
    current, group = None, []

    def close_group():
        if not group:
            return
        main_row = next((g for g in group if g["is_main"]), group[0])
        pressings = [summary(g) for g in group]
        pressings.sort(key=lambda p: (p["year"] is None, p["year"] or 0, p["id"]))
        album = {"master_id": current, "title": main_row["title"], "artist": main_row["artist"],
                 "year": min((p["year"] for p in pressings if p["year"]), default=None),
                 "main_release_id": main_row["id"], "pressings": pressings}
        master.add(current, album)
        catnos = " ".join(p["catno"] or "" for p in pressings)
        index_doc(f"m{current}", {"type": "album", "master_id": current, "release_id": main_row["id"],
                                  "artist": album["artist"], "title": album["title"], "year": album["year"],
                                  "pressings": len(pressings)},
                  f'{album["artist"]} {album["title"]} {catnos}', len(pressings))

    for row in db.execute("SELECT * FROM releases WHERE master_id IS NOT NULL ORDER BY master_id, id"):
        if row["master_id"] != current:
            close_group()
            current, group = row["master_id"], []
        group.append(row)
    close_group()
    master.finish()
    print(f"master: {master.count:,} in {len(master.offsets):,} blocks", file=sys.stderr)

    # Pressings without an album are searchable on their own.
    for row in db.execute("SELECT * FROM releases WHERE master_id IS NULL"):
        s = summary(row)
        index_doc(f"r{row['id']}", {"type": "pressing", "master_id": None, "release_id": row["id"],
                                     "artist": s["artist"], "title": s["title"], "year": s["year"], "pressings": 1},
                  f'{s["artist"]} {s["title"]} {s["catno"] or ""}', 1)
    work.commit()

    # 3. Barcodes.
    barcode = SectionWriter("barcode", False)
    current, group = None, []
    for row in db.execute("SELECT b.code AS code, r.* FROM barcodes b JOIN releases r ON r.id = b.release_id ORDER BY b.code, r.id"):
        if row["code"] != current:
            if group:
                barcode.add(current, group)
            current, group = row["code"], []
        group.append(summary(row))
    if group:
        barcode.add(current, group)
    barcode.finish()
    print(f"barcode: {barcode.count:,} in {len(barcode.offsets):,} blocks", file=sys.stderr)

    # 4. Search: each word -> best albums first (most vinyl pressings), capped.
    work.execute("CREATE INDEX postings_word ON postings(word, score DESC, doc)")
    search = SectionWriter("search", False)
    current, total, hits = None, 0, []

    def close_word():
        if current is not None:
            search.add_raw(current, f"[{total},[{','.join(hits)}]]")

    rows = work.execute("SELECT p.word, d.body FROM postings p JOIN docs d ON d.doc = p.doc "
                        "ORDER BY p.word, p.score DESC, p.doc")
    for word, body in rows:
        if word != current:
            close_word()
            current, total, hits = word, 0, []
        total += 1
        if len(hits) < SEARCH_CAP:
            hits.append(body)
    close_word()
    search.finish()
    work.close()
    os.remove(work_path)
    print(f"search: {search.count:,} words in {len(search.offsets):,} blocks", file=sys.stderr)

    # 5. Assemble: header first, then the sections.
    sections = [release, master, barcode, search]
    header = {"version": 2, "meta": meta, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sections": {}}
    binary = bytearray()
    base = 0
    for s in sections:
        entry = {"numeric": s.numeric, "start": base, "size": s.size, "count": s.count, "blocks": len(s.offsets)}
        if s.numeric:
            entry["first_keys_at"] = len(binary)
            binary += f64(s.first_keys)
        else:
            entry["first_keys"] = s.first_keys
        entry["offsets_at"] = len(binary)
        binary += f64(s.offsets)
        header["sections"][s.name] = entry
        base += s.size
    head = gzip.compress(dumps(header).encode("utf-8"), 9, mtime=0)
    with open(args.out, "wb") as out:
        out.write(MAGIC + struct.pack("<II", len(head), len(binary)) + head + binary)
        for s in sections:
            s.file.seek(0)
            while True:
                chunk = s.file.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
    size = os.path.getsize(args.out)
    print(f"done: header {len(head)/1024:.0f} KB + index {len(binary)/1024:.0f} KB, file {size/1e6:,.1f} MB, {time.time()-started:.0f} s", file=sys.stderr)


if __name__ == "__main__":
    main()
