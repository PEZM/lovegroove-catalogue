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

async function search(p, q, ctx) {
  const all = words(q).slice(0, 6);
  if (!all.length) return [];
  const exact = all.slice(0, -1);
  const last = all[all.length - 1];

  const [exactLists, prefixEntries] = await Promise.all([
    Promise.all(exact.map((w) => p.get('search', w, ctx))),
    last.length >= 2 ? p.prefix('search', last, ctx) : p.get('search', last, ctx).then((v) => (v ? [[last, v]] : [])),
  ]);
  if (exactLists.some((v) => !v) || !prefixEntries.length) return [];

  // The partial last word may match several words ("daft pu" -> punk, pure...): merge them.
  const merged = new Map();
  let prefixTotal = 0;
  for (const [, [total, docs]] of prefixEntries) {
    prefixTotal += total;
    for (const d of docs) merged.set(`${d.type}${d.master_id ?? d.release_id}`, d);
  }
  const lists = exactLists.map(([total, docs]) => ({ total, docs }));
  lists.push({ total: prefixTotal, docs: [...merged.values()].sort((a, b) => b.pressings - a.pressings) });

  // Start from the rarest word's list and keep entries whose artist + title hold every word.
  lists.sort((a, b) => a.total - b.total);
  const matches = (d) => {
    const ws = words(`${d.artist} ${d.title}`);
    return exact.every((w) => ws.includes(w)) && ws.some((w) => w.startsWith(last));
  };
  const out = [];
  for (const d of lists[0].docs) {
    if (all.length === 1 || matches(d)) out.push(d);
    if (out.length >= RESULTS) break;
  }
  return out;
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
    return { barcode: value, pressings: (await p.get('barcode', value, ctx)) || [] };
  }
  if (kind === 'search') {
    return { results: await search(p, url.searchParams.get('q') || '', ctx) };
  }
  return undefined;
}

// Raw R2 timings: sequential and parallel range reads of different sizes.
async function probe(env) {
  const key = env.CATALOGUE_KEY;
  const size = (await env.CATALOGUE.head(key)).size;
  const read = async (length) => {
    const o = await env.CATALOGUE.get(key, { range: { offset: Math.floor(Math.random() * (size - length)), length } });
    await o.arrayBuffer();
  };
  const time = async (fn) => { const t = Date.now(); await fn(); return Date.now() - t; };
  const out = {};
  for (const length of [4096, 65536, 262144]) {
    const seq = [];
    for (let i = 0; i < 5; i++) seq.push(await time(() => read(length)));
    out[`seq_${length / 1024}KB`] = seq;
    out[`parallel10_${length / 1024}KB`] = await time(() => Promise.all(Array.from({ length: 10 }, () => read(length))));
  }
  return Response.json(out);
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method !== 'GET') return new Response('Method not allowed', { status: 405 });
    if (url.pathname === '/_probe') return probe(env);
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
