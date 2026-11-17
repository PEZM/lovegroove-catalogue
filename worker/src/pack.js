// Reader for the packed catalogue file made by pack_catalogue.py.
// One header read per Worker instance, then one block read per lookup.

const MAGIC = 'LGCAT002';
const HEAD_GUESS = 64 * 1024; // first read on open; a bigger header + index come from the edge cache
const MAX_BLOCKS = 300;        // decoded blocks kept in memory per instance

export class StaleError extends Error {}

async function gunzipJSON(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return JSON.parse(await new Response(stream).text());
}

// A float64 array straight from the binary index (copied once to align it, no decoding).
function f64(buf, at, count) {
  return new Float64Array(buf.slice(at, at + count * 8).buffer);
}

// Same word rules as pack_catalogue.py: strip accents, lower case, letters and digits.
export function words(text) {
  return (text || '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
}

export class Pack {
  constructor(bucket, key) {
    this.bucket = bucket;
    this.key = key;
    this.blocks = new Map();
    this.inflight = new Map();
    this.stats = { r2Reads: 0, edgeHits: 0, memoryHits: 0 };
  }

  static async open(bucket, key, ctx) {
    const pack = new Pack(bucket, key);
    await pack.load(ctx);
    return pack;
  }

  async load(ctx) {
    const first = await this.bucket.get(this.key, { range: { offset: 0, length: HEAD_GUESS } });
    if (!first) throw new Error(`catalogue ${this.key} not found`);
    this.stats.r2Reads++;
    this.etag = first.etag;
    let buf = new Uint8Array(await first.arrayBuffer());
    if (new TextDecoder().decode(buf.subarray(0, 8)) !== MAGIC) throw new Error('not a catalogue pack');
    const view = new DataView(buf.buffer, buf.byteOffset);
    const headLength = view.getUint32(8, true);
    const indexLength = view.getUint32(12, true);
    this.dataStart = 16 + headLength + indexLength;
    if (this.dataStart > buf.length) buf = await this.range(0, this.dataStart, ctx);
    const header = await gunzipJSON(buf.subarray(16, 16 + headLength));
    if (header.version !== 3) throw new Error(`catalogue format ${header.version}, this Worker reads 3`);
    const index = buf.subarray(16 + headLength, this.dataStart);
    this.meta = header.meta;
    this.builtAt = header.built_at;
    this.sections = {};
    for (const [name, s] of Object.entries(header.sections)) {
      this.sections[name] = {
        ...s,
        first_keys: s.numeric ? f64(index, s.first_keys_at, s.blocks) : s.first_keys,
        offsets: f64(index, s.offsets_at, s.blocks),
      };
    }
  }

  // Index of the last block whose first key <= key, or -1.
  blockIndex(section, key) {
    const keys = this.sections[section].first_keys;
    let lo = 0, hi = keys.length - 1, found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (keys[mid] <= key) { found = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return found;
  }

  async block(section, index, ctx) {
    const id = `${section}:${index}`;
    const hit = this.blocks.get(id);
    if (hit) {
      this.blocks.delete(id); this.blocks.set(id, hit);
      this.stats.memoryHits++;
      return hit;
    }
    let pending = this.inflight.get(id);
    if (!pending) {
      pending = this.fetchBlock(section, index, ctx).then((entries) => {
        this.blocks.set(id, entries);
        while (this.blocks.size > MAX_BLOCKS) this.blocks.delete(this.blocks.keys().next().value);
        return entries;
      }).finally(() => this.inflight.delete(id));
      this.inflight.set(id, pending);
    }
    return pending;
  }

  async fetchBlock(section, index, ctx) {
    const s = this.sections[section];
    const start = s.offsets[index];
    const end = index + 1 < s.offsets.length ? s.offsets[index + 1] : s.size;
    return gunzipJSON(await this.range(this.dataStart + s.start + start, end - start, ctx));
  }

  // Bytes of the file, from Cloudflare's edge cache (shared by everyone reaching
  // the same data centre) or else from R2.
  async range(offset, length, ctx) {
    const cacheKey = new Request(`https://catalogue-cache.lovegroove.app/${encodeURIComponent(this.etag)}/${offset}-${length}`);
    const cache = globalThis.caches?.default;
    if (cache) {
      const cached = await cache.match(cacheKey).catch(() => undefined);
      if (cached) {
        this.stats.edgeHits++;
        return new Uint8Array(await cached.arrayBuffer());
      }
    }
    const object = await this.bucket.get(this.key, { range: { offset, length }, onlyIf: { etagMatches: this.etag } });
    this.stats.r2Reads++;
    if (!object || !('body' in object) || !object.body) throw new StaleError('catalogue file changed');
    const bytes = new Uint8Array(await object.arrayBuffer());
    if (cache && ctx) {
      ctx.waitUntil(cache.put(cacheKey, new Response(bytes, { headers: { 'Cache-Control': 'public, max-age=2592000' } })).catch(() => {}));
    }
    return bytes;
  }

  // Exact lookup: one block read.
  async get(section, key, ctx) {
    const index = this.blockIndex(section, key);
    if (index < 0) return undefined;
    const entries = await this.block(section, index, ctx);
    let lo = 0, hi = entries.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const k = entries[mid][0];
      if (k === key) return entries[mid][1];
      if (k < key) lo = mid + 1; else hi = mid - 1;
    }
    return undefined;
  }

  // Entries whose text key starts with prefix, from the one block where the prefix sorts.
  async prefix(section, prefix, ctx) {
    const index = this.blockIndex(section, prefix);
    const entries = await this.block(section, Math.max(index, 0), ctx);
    return entries.filter(([k]) => k.startsWith(prefix));
  }
}
