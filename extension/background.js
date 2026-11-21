/* The reporter. Everything a collector gathers leaves through here.
 *
 * This file is deliberately the only part that knows Zipper exists. A collector
 * knows one website and returns JSON; adding a site is writing one content
 * script, never touching this. That split is the whole design -- the browser is
 * a place to *read* things a logged-out server cannot, and nothing more. No
 * parsing, no judgement, no vault knowledge. The backend concludes.
 *
 * Written for Chrome's service worker, which is the stricter of the two: it is
 * killed aggressively and keeps no globals between wake-ups. So there is no
 * module-level mutable state here -- every fact that has to outlive a message
 * lives in chrome.storage. Firefox runs the same file as an event page, where
 * that discipline is merely unnecessary rather than wrong. Code written the
 * other way round breaks on Chrome, which is why the constraint is honoured
 * even though half the targets do not need it.
 */

const api = globalThis.browser ?? globalThis.chrome;

/* A floor, not a throttle.
 *
 * What stops the chatter is the hash below: a collector that fires on every
 * page load now posts only when the reading actually differs from the one
 * Zipper already has. This is the backstop for the case the hash cannot catch
 * -- a field that flaps between two values, or two tabs loading together, both
 * finding no stored hash and both deciding to send. It only gates
 * change-driven sends; the scheduled heartbeat is never suppressed, because
 * the whole point of the heartbeat is that Zipper hears from us on a known
 * cadence whether or not anything moved.
 */
const FLOOR_MS = 60 * 1000;

async function config() {
  const { endpoint } = await api.storage.sync.get('endpoint');
  return (endpoint || '').replace(/\/+$/, '');
}

/* Fields that change without anything changing.
 *
 * `new_activity` flips the moment they *looks* at an item, which is not news and
 * would make every visit to Canvas a fresh POST -- exactly the traffic the hash
 * exists to remove. Anything found to flap on its own belongs here.
 */
const VOLATILE = new Set(['new_activity']);

/* JSON with its keys in a fixed order, so the same reading always hashes the
 * same. `JSON.stringify` preserves insertion order, and Canvas has no
 * obligation to serialize an object's keys the same way twice; without this a
 * reshuffle upstream would read as a change and defeat the whole mechanism.
 */
function canonical(v) {
  if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
  if (v && typeof v === 'object') {
    return '{' + Object.keys(v).sort()
      .filter((k) => !VOLATILE.has(k))
      .map((k) => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}';
  }
  return JSON.stringify(v === undefined ? null : v);
}

async function digest(payload) {
  const bytes = new TextEncoder().encode(canonical(payload));
  const buf = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(buf)]
    .map((b) => b.toString(16).padStart(2, '0')).join('');
}

/* Per collector, never global: Canvas being unchanged says nothing about
 * Onshape, and one shared hash would mean a quiet site's first reading could
 * be swallowed by a chatty one's.
 */
async function lastSeen(name) {
  const keys = ['lastHash:' + name, 'lastSent:' + name];
  const store = await api.storage.local.get(keys);
  return { hash: store[keys[0]] || '', at: store[keys[1]] || 0 };
}

/* Written only after Zipper has acknowledged the POST.
 *
 * If a failed send stored the hash, the next identical reading would compare
 * equal and be skipped, and the reading Zipper never received would never be
 * offered again -- the data would sit wrong until something happened to change
 * it. So the hash means "Zipper has this", not "we tried".
 */
async function markSent(name, hash) {
  await api.storage.local.set({ ['lastSent:' + name]: Date.now(),
                                ['lastHash:' + name]: hash });
}

/* POST to Zipper.
 *
 * This is the one cross-origin request in the extension, and it has to happen
 * here. A content script's fetch carries the *page's* origin, so posting to the
 * tailnet from inside a Canvas host would be a genuine cross-origin request
 * and CORS would refuse it. From the background, host permissions apply
 * instead and the browser does not interpose. It is also why the endpoint is
 * an optional permission granted on the options page: the URL is not known at
 * build time and nobody should ship a personal tailnet address in a manifest.
 */
async function call(path, body) {
  const endpoint = await config();
  if (!endpoint) {
    console.warn('[zipper] no endpoint configured; open the extension options');
    return { ok: false, error: 'no endpoint' };
  }
  const res = await fetch(endpoint + path, body === undefined
    ? { method: 'GET' }
    : { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) });
  if (!res.ok) {
    return { ok: false, error: 'zipper returned ' + res.status };
  }
  return await res.json().catch(() => ({ ok: true }));
}

function send(name, payload) {
  // A collector hands over the body it wants posted; the reporter only stamps
  // where it came from. An array is wrapped for the older `{source, items}`
  // shape so a collector that has nothing but a list stays a one-liner.
  const body = Array.isArray(payload) ? { items: payload } : { ...payload };
  body.source = 'extension';
  return call('/api/' + name, body);
}

/* What a page is allowed to ask Zipper for.
 *
 * A content script runs in a tab alongside whatever else that origin is
 * executing, so the reachable surface is spelled out here rather than left as
 * "any path the page names". These two are the panel's whole vocabulary: read
 * the ranked list, cross one thing off.
 */
const ALLOWED = {
  '/api/worklist': 'GET',
  '/api/done': 'POST',
  // The timesheet goes both ways: read what the sheet is missing, post back
  // what the sheet actually says. Both halves are the same path, so this map
  // holds a list where it used to hold one verb.
  '/api/hours': ['GET', 'POST'],
  // Which of a course's files Zipper already has.
  '/api/canvasfiles': 'GET',
};

/* The panel's channel.
 *
 * Same reason as the POST: from inside a Canvas host a request to the tailnet
 * is cross-origin and CORS refuses it, so every byte between the page and
 * Zipper goes through here. The panel therefore still knows nothing about
 * where Zipper lives -- it names an intent, and this file owns the address.
 */
api.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (!msg || msg.type !== 'zipper:call') return false;
  (async () => {
    try {
      // The query string is the caller's to choose (the panel's `?offset=`);
      // the path is what the allow-list is about.
      const want = ALLOWED[String(msg.path).split('?')[0]];
      const verbs = Array.isArray(want) ? want : (want ? [want] : []);
      if (!verbs.length) {
        respond({ ok: false, error: 'not an allowed path' });
        return;
      }
      // One path may allow both verbs, so the caller says which it meant; a
      // path with a single verb keeps working without naming it.
      const method = msg.method || (verbs.length === 1 ? verbs[0]
                                    : (msg.body ? 'POST' : 'GET'));
      if (!verbs.includes(method)) {
        respond({ ok: false, error: method + ' not allowed on ' + msg.path });
        return;
      }
      respond(await call(msg.path, method === 'POST' ? (msg.body || {}) : undefined));
    } catch (e) {
      respond({ ok: false, error: String(e) });
    }
  })();
  return true;
});

api.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (!msg || msg.type !== 'zipper:data') return false;

  // Not awaited inline: returning true keeps the message channel open, which is
  // the only way an async listener may reply in either browser.
  /* Two reasons to send, and only two.
   *
   *   reason: 'load'      the page opened and the reading differs from the one
   *                       Zipper already has. Changes arrive promptly.
   *   reason: 'interval'  the tab has simply been sitting there. Goes out
   *                       whether or not anything changed, so `canvas.json`'s
   *                       `fetched` stamp stays honest: a reading that is
   *                       genuinely unchanged should still be able to say it
   *                       was taken five minutes ago rather than this morning.
   *
   * Which is why the heartbeat ignores the hash instead of being a special
   * case of it. Unchanged-and-old and unchanged-and-fresh are different facts,
   * and only the heartbeat can tell them apart.
   */
  (async () => {
    try {
      const name = msg.collector;
      const hash = await digest(msg.payload);
      const heartbeat = msg.reason === 'interval';
      const seen = await lastSeen(name);

      if (msg.force !== true && !heartbeat) {
        if (seen.hash === hash) {
          respond({ ok: true, skipped: 'unchanged' });
          return;
        }
        if (Date.now() - seen.at < FLOOR_MS) {
          // Deliberately no hash write: the change is real and still unsent,
          // so the next load must find it again rather than inherit a skip.
          respond({ ok: true, skipped: 'floor' });
          return;
        }
      }

      const out = await send(name, msg.payload);
      if (out.ok !== false) await markSent(name, hash);
      respond(out);
    } catch (e) {
      respond({ ok: false, error: String(e) });
    }
  })();
  return true;
});

/* Time on watched sites.
 *
 * `collectors/sitetime.js` runs on each watched site and says "15 more seconds
 * here" while its tab is visible and focused. This adds the browser's own idle
 * check -- a focused tab with nobody at the keyboard is not time on the site --
 * and keeps running totals per day and domain in storage, because a Chrome
 * service worker keeps nothing between wake-ups.
 *
 * Totals, not deltas, go to Zipper, under a random id for this browser: a
 * resend after a lost reply rewrites the same number instead of adding it
 * twice, and two browsers don't overwrite each other.
 */
const TICK_MAX = 30;             // a tick claims at most this many seconds
const SITE_FLUSH_MS = 5 * 60 * 1000;
const SITE_KEEP_DAYS = 14;

function localDay(d = new Date()) {
  const z = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
}

async function deviceId() {
  const { siteDevice } = await api.storage.local.get('siteDevice');
  if (siteDevice) return siteDevice;
  const id = [...crypto.getRandomValues(new Uint8Array(9))]
    .map((b) => b.toString(16).padStart(2, '0')).join('');
  await api.storage.local.set({ siteDevice: id });
  return id;
}

// Ticks are serialized: a read-modify-write of the totals interleaved with
// another would lose one of them.
let siteChain = Promise.resolve();

function siteTick(domain, secs) {
  siteChain = siteChain.then(async () => {
    try {
      const state = api.idle?.queryState ? await api.idle.queryState(60) : 'active';
      if (state !== 'active') return;
      const day = localDay();
      const { siteTotals = {} } = await api.storage.local.get('siteTotals');
      const d = (siteTotals[day] = siteTotals[day] || {});
      d[domain] = (d[domain] || 0) + Math.max(0, Math.min(TICK_MAX, secs | 0));
      const cutoff = localDay(new Date(Date.now() - SITE_KEEP_DAYS * 86400000));
      for (const k of Object.keys(siteTotals)) if (k < cutoff) delete siteTotals[k];
      await api.storage.local.set({ siteTotals });
      await siteFlush(false);
    } catch (e) {
      console.warn('[zipper] site time:', e);
    }
  });
  return siteChain;
}

async function siteFlush(force) {
  const { siteFlushed = 0, siteTotals = {} } =
    await api.storage.local.get(['siteFlushed', 'siteTotals']);
  if (!force && Date.now() - siteFlushed < SITE_FLUSH_MS) return;
  // The last three days: enough to cover a laptop asleep over a weekend
  // without resending two weeks every five minutes.
  const since = localDay(new Date(Date.now() - 3 * 86400000));
  const totals = {};
  for (const [k, v] of Object.entries(siteTotals)) if (k >= since) totals[k] = v;
  if (!Object.keys(totals).length) return;
  const out = await call('/api/sitetime', { device: await deviceId(), totals });
  if (out && out.ok !== false) await api.storage.local.set({ siteFlushed: Date.now() });
}

/* Course files.
 *
 * The panel on a course page lists the files (same-origin, so the Canvas
 * session applies) and hands each one's download URL here. That URL carries
 * Canvas' own `verifier`, and redirects to Canvas' file store on another
 * host, which a page's fetch would hit CORS on; from here host permissions
 * apply. The bytes go to Zipper raw, described by headers.
 */
async function postFile(meta, buf) {
  const endpoint = await config();
  if (!endpoint) return { ok: false, error: 'no endpoint' };
  const enc = (s) => encodeURIComponent(String(s || ''));
  const res = await fetch(endpoint + '/api/canvasfiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream',
               'X-Course-Id': String(meta.course), 'X-Course-Name': enc(meta.courseName),
               'X-File-Id': String(meta.id), 'X-File-Name': enc(meta.name),
               'X-Folder': enc(meta.folder), 'X-Updated-At': String(meta.updated_at || ''),
               // What Canvas says the file weighs; the server refuses a body
               // that doesn't match, so a mangled transfer can't be stored as
               // the file and then skipped as "already there".
               'X-Expected-Size': String(meta.size ?? '') },
    body: buf,
  });
  const out = await res.json().catch(() => ({}));
  return res.ok ? out : { ok: false, error: out.error || 'zipper returned ' + res.status };
}

api.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (!msg || !['zipper:tick', 'zipper:file', 'zipper:filebytes'].includes(msg.type)) return false;
  // A file may only come from the site the asking tab is on (or Canvas' own
  // file store it redirects to): the panel names a URL, this checks it.
  if (msg.type === 'zipper:file') {
    let ok = false;
    try { ok = new URL(msg.url).origin === new URL(_sender.url).origin; } catch {}
    if (!ok) { respond({ ok: false, error: 'not this site' }); return false; }
  }
  (async () => {
    try {
      if (msg.type === 'zipper:tick') {
        await siteTick(String(msg.domain || '').toLowerCase(), msg.secs);
        respond({ ok: true });
      } else if (msg.type === 'zipper:file') {
        const res = await fetch(msg.url, { credentials: 'include' });
        if (!res.ok) return respond({ ok: false, error: 'download ' + res.status, page: true });
        const ctype = res.headers.get('Content-Type') || '';
        // Canvas answers an expired session with its login page at 200.
        if (ctype.includes('text/html')) return respond({ ok: false, error: 'got a web page', page: true });
        const buf = await res.arrayBuffer();
        respond(await postFile(msg.meta, buf));
      } else {
        // The page fetched it itself and sent base64 text: JSON-safe, so it
        // arrives intact in Chrome as well as Firefox.
        if (typeof msg.b64 !== 'string' || !msg.b64) return respond({ ok: false, error: 'no bytes' });
        const bin = atob(msg.b64);
        const buf = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
        respond(await postFile(msg.meta, buf));
      }
    } catch (e) {
      respond({ ok: false, error: String(e), page: msg.type === 'zipper:file' });
    }
  })();
  return true;
});
