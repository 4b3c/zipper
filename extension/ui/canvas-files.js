/* "Send this course's files to Zipper", on a course's home page.
 *
 * On demand, never in the background: a press loads one course. The list is
 * read same-origin from Canvas' API (the session applies there); each file's
 * download goes through the background, which posts the bytes to Zipper.
 * Zipper keeps them outside the vault and says which it already has, so a
 * second press sends only new or changed files.
 *
 * Documents only (the server enforces the same list) and at most 50 MB each:
 * this is for reading slides and syllabi, not mirroring lecture video.
 */
(() => {
  const api = globalThis.browser ?? globalThis.chrome;
  const log = (...a) => console.info('[zipper files]', ...a);

  const KEEP = new Set(['pdf', 'ppt', 'pptx', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'md',
                        'csv', 'rtf', 'odt', 'odp', 'tex', 'ipynb', 'py', 'zip']);
  const MAX_BYTES = 50 * 1024 * 1024;

  const m = location.pathname.match(/^\/courses\/(\d+)\/?$/);
  if (!m) return;                       // the course home only
  const course = m[1];

  async function canvas(path) {
    const res = await fetch(path, { credentials: 'same-origin',
                                    headers: { Accept: 'application/json' } });
    if (!res.ok) {
      const e = new Error(path + ' returned ' + res.status);
      e.status = res.status;
      throw e;
    }
    let text = await res.text();
    if (text.startsWith('while(1);')) text = text.slice(9);
    return { data: JSON.parse(text), next: nextLink(res.headers.get('Link')) };
  }

  function nextLink(h) {
    const mm = h && h.match(/<([^>]+)>;\s*rel="next"/);
    return mm ? mm[1] : null;
  }

  async function all(path) {
    const out = [];
    let url = path;
    for (let i = 0; url && i < 50; i++) {
      const page = await canvas(url);
      out.push(...page.data);
      url = page.next;
    }
    return out;
  }

  /* Files the student can see. The Files tab is hidden in many courses (the
   * API then answers 401 or 403), and the documents are linked from Modules
   * instead, so that is the fallback.
   */
  async function listFiles() {
    try {
      const [files, folders] = await Promise.all([
        all(`/api/v1/courses/${course}/files?per_page=100`),
        all(`/api/v1/courses/${course}/folders?per_page=100`).catch(() => []),
      ]);
      const names = {};
      for (const f of folders) {
        names[f.id] = String(f.full_name || '').replace(/^course files\/?/, '');
      }
      return files.map((f) => ({ ...f, folder: names[f.folder_id] || '' }));
    } catch (e) {
      if (![401, 403, 404].includes(e.status)) throw e;
      log('files tab closed (' + e.status + '), reading modules instead');
    }
    const mods = await all(`/api/v1/courses/${course}/modules?include[]=items&per_page=100`);
    const out = [];
    for (const mod of mods) {
      for (const it of mod.items || []) {
        if (it.type !== 'File' || !it.url) continue;
        try {
          const f = (await canvas(it.url)).data;
          out.push({ ...f, folder: mod.name || '' });
        } catch (e) {
          log('could not read', it.title, e.message);
        }
      }
    }
    return out;
  }

  function extOf(name) {
    const i = String(name).lastIndexOf('.');
    return i < 0 ? '' : name.slice(i + 1).toLowerCase();
  }

  async function sendOne(f, courseName) {
    const meta = { course, courseName, id: f.id, name: f.display_name || f.filename,
                   folder: f.folder, updated_at: f.updated_at };
    let out = await api.runtime.sendMessage({ type: 'zipper:file', url: f.url, meta });
    if (out && out.ok === false && out.page) {
      // The background couldn't download it; try from the page, where the
      // Canvas session certainly applies, and hand the bytes over.
      try {
        const res = await fetch(f.url, { credentials: 'same-origin' });
        if (!res.ok) throw new Error('download ' + res.status);
        const bytes = await res.arrayBuffer();
        out = await api.runtime.sendMessage({ type: 'zipper:filebytes', meta, bytes });
      } catch (e) {
        out = { ok: false, error: out.error + '; from the page: ' + e.message };
      }
    }
    return out || { ok: false, error: 'no reply' };
  }

  let busy = false;

  async function run(btn, status) {
    if (busy) return;
    busy = true;
    btn.disabled = true;
    const say = (t) => { status.textContent = t; };
    try {
      say('Reading the course…');
      const info = (await canvas(`/api/v1/courses/${course}`)).data;
      const courseName = info.course_code || info.name || '';
      const files = await listFiles();
      const have = await api.runtime.sendMessage({ type: 'zipper:call',
        path: '/api/canvasfiles?course=' + course });
      if (!have || have.ok === false) throw new Error('Zipper unreachable: ' + ((have && have.error) || ''));
      const known = have.files || {};
      const seen = new Set();
      const todo = [];
      let skipped = 0;
      for (const f of files) {
        if (seen.has(f.id)) continue;
        seen.add(f.id);
        if (!KEEP.has(extOf(f.display_name || f.filename)) || f.size > MAX_BYTES || f.locked_for_user) {
          skipped++;
          continue;
        }
        if (known[String(f.id)] && known[String(f.id)] === f.updated_at) continue;
        todo.push(f);
      }
      const already = seen.size - skipped - todo.length;
      let sent = 0;
      const failed = [];
      for (const f of todo) {
        say(`Sending ${sent + failed.length + 1} of ${todo.length}: ${f.display_name}`);
        const out = await sendOne(f, courseName);
        if (out.ok === false) { failed.push(f.display_name); log('failed', f.display_name, out.error); }
        else sent++;
      }
      say(`Sent ${sent}` + (already ? `, ${already} already there` : '')
          + (skipped ? `, ${skipped} skipped (not a document or over 50 MB)` : '')
          + (failed.length ? `. ${failed.length} failed: ${failed.slice(0, 3).join(', ')}`
                              + (failed.length > 3 ? '…' : '') : '.'));
    } catch (e) {
      log(e);
      say('Stopped: ' + e.message);
    } finally {
      busy = false;
      btn.disabled = false;
    }
  }

  const CSS = `
  :host { all: initial; display: block; }
  * { box-sizing: border-box; font-family: LatoWeb, Lato, system-ui, sans-serif; }
  .wrap { margin: 0 0 1rem; padding: .75rem; border: 1px solid #c7cdd1;
          border-radius: 6px; color: #2d3b45; }
  button { width: 100%; padding: .5rem; font: inherit; font-size: .85rem; font-weight: 700;
           color: #fff; background: #0374b5; border: 0; border-radius: 4px; cursor: pointer; }
  button:disabled { opacity: .6; cursor: default; }
  .status { margin-top: .5rem; font-size: .75rem; color: #6b7780; word-wrap: break-word; }
  `;

  let host = null;

  function ensure() {
    if (host && document.contains(host)) return;
    const anchor = document.querySelector('#right-side') || document.querySelector('#content');
    if (!anchor) return;
    host = document.createElement('div');
    host.id = 'zipper-course-files';
    const root = host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = CSS;
    const wrap = document.createElement('div');
    wrap.className = 'wrap';
    const btn = document.createElement('button');
    btn.textContent = 'Send this course’s files to Zipper';
    const status = document.createElement('div');
    status.className = 'status';
    btn.addEventListener('click', () => run(btn, status));
    wrap.append(btn, status);
    root.append(style, wrap);
    anchor.prepend(host);
    log('button on course', course);
  }

  let pending = false;
  ensure();
  new MutationObserver(() => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; ensure(); });
  }).observe(document.body, { childList: true, subtree: true });
})();
