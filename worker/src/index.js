// Lovegroove catalogue lookups: vinyl pressings from Discogs' CC0 data dumps,
// stored as one packed file in R2 (see pack_catalogue.py).
//
//   GET /release/:id       one pressing, with tracklist
//   GET /master/:id        an album and all its vinyl pressings
//   GET /barcode/:digits   pressings with that barcode
//   GET /search?q=...      albums and pressings by artist / title (last word may be partial)
import { Pack, StaleError, words } from './pack.js';

let opening;    // Promise<Pack> for this Worker instance
let openedKey;

function pack(env, ctx) {
  if (!opening || openedKey !== env.CATALOGUE_KEY) {
    openedKey = env.CATALOGUE_KEY;
    opening = Pack.open(env.CATALOGUE, env.CATALOGUE_KEY, ctx).catch((e) => { opening = undefined; throw e; });
  }
  return opening;
}

const RESULTS = 25;

// Search entries are [master_id or 0, release_id, artist, title, year, pressings].
const ARTIST = 2, TITLE = 3, PRESSINGS = 5;
const toResult = ([master, release, artist, title, year, pressings]) => ({
  type: master ? 'album' : 'pressing', master_id: master || null, release_id: release, artist, title, year, pressings,
});

async function search(p, q, ctx) {
  const all = words(q).slice(0, 6);
  if (!all.length) return [];
  const exact = all.slice(0, -1);
  const last = all[all.length - 1];

  // One block read per word, all at once. The last word may still be being typed:
  // alone it is matched as a prefix; after other words it only filters their lists.
  const [exactLists, lastList, prefixEntries] = await Promise.all([
    Promise.all(exact.map((w) => p.get('search', w, ctx))),
    exact.length ? p.get('search', last, ctx) : undefined,
    exact.length ? [] : last.length >= 2 ? p.prefix('search', last, ctx) : p.get('search', last, ctx).then((v) => (v ? [[last, v]] : [])),
  ]);
  if (exactLists.some((v) => !v)) return [];

  if (!exact.length) {
    // "bjo" -> bjork, bjorn...: merge those words' lists, most pressings first.
    const merged = new Map();
    for (const [, [, docs]] of prefixEntries) for (const d of docs) merged.set(`${d[0]}:${d[1]}`, d);
    return [...merged.values()].sort((a, b) => b[PRESSINGS] - a[PRESSINGS]).slice(0, RESULTS).map(toResult);
  }
  // Filter the rarest complete word's list: keep entries whose artist + title hold every
  // word, the last as a prefix. If the last word is itself a word ("abbey" in "the beatles
  // abbey") its list may be rarer, so filter that too and merge.
  const matches = (list) => {
    const found = [];
    for (const d of list) {
      const ws = words(`${d[ARTIST]} ${d[TITLE]}`);
      if (exact.every((w) => ws.includes(w)) && ws.some((w) => w.startsWith(last))) found.push(d);
      if (found.length >= RESULTS) break;
    }
    return found;
  };
  const rarest = exactLists.reduce((a, b) => (b[0] < a[0] ? b : a));
  const merged = new Map();
  for (const d of matches(rarest[1])) merged.set(`${d[0]}:${d[1]}`, d);
  if (lastList && lastList[0] < rarest[0]) for (const d of matches(lastList[1])) merged.set(`${d[0]}:${d[1]}`, d);
  return [...merged.values()].sort((a, b) => b[PRESSINGS] - a[PRESSINGS]).slice(0, RESULTS).map(toResult);
}

async function route(url, env, ctx) {
  const p = await pack(env, ctx);
  const [kind, value] = url.pathname.split('/').filter(Boolean);
  if (kind === 'release' && /^\d+$/.test(value || '')) {
    const release = await p.get('release', Number(value), ctx);
    return release ? { release } : null;
  }
  if (kind === 'master' && /^\d+$/.test(value || '')) {
    const album = await p.get('master', Number(value), ctx);
    return album ? { album } : null;
  }
  if (kind === 'barcode' && /^\d{8,14}$/.test(value || '')) {
    // Stored without leading zeros, so a UPC-12 and its EAN-13 form find the same pressings.
    return { barcode: value, pressings: (await p.get('barcode', value.replace(/^0+/, ''), ctx)) || [] };
  }
  if (kind === 'search') {
    return { results: await search(p, url.searchParams.get('q') || '', ctx) };
  }
  return undefined;
}

// Lookups allowed per visitor (IP address) per minute, counted in each Cloudflare
// location. Set in wrangler.production.jsonc; keeps a flood of uncached lookups off R2's bill.
async function allowed(request, env) {
  if (!env.LIMITER) return true;
  const ip = request.headers.get('cf-connecting-ip') || 'unknown';
  const { success } = await env.LIMITER.limit({ key: ip });
  return success;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method !== 'GET') return new Response('Method not allowed', { status: 405 });
    if (!(await allowed(request, env))) {
      return Response.json({ error: 'too many requests, try again in a minute' }, { status: 429, headers: { 'Retry-After': '60' } });
    }
    const started = Date.now();
    const cold = !opening;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const p0 = await pack(env, ctx);
        const before = { ...p0.stats };
        const body = await route(url, env, ctx);
        if (body === undefined) return Response.json({ error: 'not found' }, { status: 404 });
        if (body === null) return Response.json({ error: 'no such record' }, { status: 404 });
        const p = await pack(env, ctx);
        body.source = 'Discogs data dump (CC0)';
        body.catalogue = p.meta?.dump;
        body.timing = {
          ms: Date.now() - started, cold,
          r2Reads: p.stats.r2Reads - (cold ? 0 : before.r2Reads),
          edgeHits: p.stats.edgeHits - before.edgeHits,
          memoryHits: p.stats.memoryHits - before.memoryHits,
        };
        return Response.json(body, { headers: { 'Cache-Control': 'public, max-age=86400' } });
      } catch (e) {
        if (e instanceof StaleError && attempt === 0) { opening = undefined; continue; } // new monthly file: reopen once
        return Response.json({ error: String((e && e.message) || e) }, { status: 500 });
      }
    }
  },
};
