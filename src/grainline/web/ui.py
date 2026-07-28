"""The single-page web interface, embedded as a module constant.

Deliberately dependency-free: no CDN, no bundler, no npm. Every byte the browser
needs ships inside this string.

That is not minimalism for its own sake. The target customer runs this on a shop
PC on an isolated CNC network with no route to the internet. A page that pulls
HTMX or Tailwind from a CDN renders as unstyled wreckage on exactly the machines
this product exists to serve. Embedding the page in Python rather than shipping
a template file also removes package-data configuration as a failure mode: if
the module imports, the UI is present.
"""

from __future__ import annotations

__all__ = ["INDEX_HTML"]

INDEX_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GRAINLINE - nesting</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #fbfaf8; --panel: #ffffff; --ink: #1c1a17; --muted: #6b635a;
    --line: #e3ddd4; --accent: #8a5a2b; --good: #2f7d32; --warn: #b26a00;
    --bad: #b3261e;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#161513; --panel:#201e1b; --ink:#ece7df; --muted:#a49a8d;
            --line:#37332d; --accent:#d9a066; --good:#7bc47f; --warn:#e0a44a;
            --bad:#f2857c; }
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
         background: var(--bg); color: var(--ink); }
  header { padding: 1rem 1.5rem; border-bottom: 1px solid var(--line);
           display:flex; align-items:baseline; gap:1rem; flex-wrap:wrap; }
  header h1 { font-size:1.05rem; margin:0; letter-spacing:.08em; }
  header .edition { color: var(--muted); font-size:.85rem; }
  main { display:grid; grid-template-columns: 22rem 1fr; gap:1.5rem;
         padding:1.5rem; align-items:start; }
  @media (max-width: 60rem) { main { grid-template-columns: 1fr; } }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:10px;
           padding:1.1rem 1.25rem; }
  .panel h2 { font-size:.75rem; text-transform:uppercase; letter-spacing:.07em;
              color:var(--muted); margin:0 0 .9rem; }
  label { display:block; font-size:.8rem; color:var(--muted); margin:.7rem 0 .2rem; }
  input, select, button {
    font: inherit; width:100%; padding:.45rem .6rem; border-radius:6px;
    border:1px solid var(--line); background:var(--bg); color:var(--ink);
  }
  .row { display:grid; grid-template-columns:1fr 1fr; gap:.6rem; }
  button.primary { background:var(--accent); color:#fff; border-color:var(--accent);
                   font-weight:600; margin-top:1.1rem; cursor:pointer; }
  button.primary:disabled { opacity:.55; cursor:progress; }
  .drop { border:2px dashed var(--line); border-radius:8px; padding:1.4rem .8rem;
          text-align:center; color:var(--muted); font-size:.85rem; cursor:pointer; }
  .drop.hot { border-color:var(--accent); color:var(--ink); }
  .files { list-style:none; padding:0; margin:.6rem 0 0; font-size:.8rem; }
  .files li { display:flex; justify-content:space-between; gap:.5rem;
              padding:.22rem 0; border-bottom:1px solid var(--line); }
  .files button { width:auto; padding:0 .35rem; border:none; background:none;
                  color:var(--bad); cursor:pointer; }
  .cards { display:flex; gap:.8rem; flex-wrap:wrap; margin-bottom:1.2rem; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
          padding:.8rem 1.1rem; min-width:8rem; }
  .card .v { font-size:1.5rem; font-weight:650; }
  .card .k { font-size:.7rem; text-transform:uppercase; letter-spacing:.05em;
             color:var(--muted); }
  .sheet { background:var(--panel); border:1px solid var(--line); border-radius:10px;
           padding:.9rem; margin-bottom:1rem; }
  .sheet h3 { margin:0 0 .6rem; font-size:.85rem; color:var(--muted);
              font-weight:600; }
  .sheet .frame { overflow-x:auto; }
  .sheet svg { max-width:100%; height:auto; display:block; }
  .msg { border-radius:8px; padding:.7rem .9rem; margin-bottom:1rem; font-size:.85rem; }
  .msg.err { background:color-mix(in srgb, var(--bad) 14%, transparent);
             border:1px solid var(--bad); }
  .msg.warn { background:color-mix(in srgb, var(--warn) 14%, transparent);
              border:1px solid var(--warn); }
  .msg.info { background:color-mix(in srgb, var(--accent) 12%, transparent);
              border:1px solid var(--line); }
  .empty { color:var(--muted); font-size:.9rem; padding:3rem 1rem; text-align:center; }
  .hint { font-size:.72rem; color:var(--muted); margin-top:.25rem; }
</style></head>
<body>
<header>
  <h1>GRAINLINE</h1>
  <span class="edition" id="edition">checking licence...</span>
  <span class="edition" style="margin-left:auto">files stay on this machine</span>
</header>

<main>
  <form class="panel" id="form" autocomplete="off">
    <h2>Parts</h2>
    <div class="drop" id="drop">
      Drop DXF or SVG files here, or click to choose
      <input type="file" id="picker" multiple accept=".dxf,.svg" hidden>
    </div>
    <ul class="files" id="filelist"></ul>

    <h2 style="margin-top:1.4rem">Sheet</h2>
    <div class="row">
      <div><label for="sw">Width (mm)</label>
        <input id="sw" type="number" value="2440" min="1" step="any"></div>
      <div><label for="sh">Height (mm)</label>
        <input id="sh" type="number" value="1220" min="1" step="any"></div>
    </div>
    <div class="row">
      <div><label for="cost">Sheet cost</label>
        <input id="cost" type="number" value="0" min="0" step="any"></div>
      <div><label for="qty">Quantity</label>
        <input id="qty" type="number" value="0" min="0" step="1">
        <div class="hint">0 = unlimited</div></div>
    </div>

    <h2 style="margin-top:1.4rem">Cutting</h2>
    <div class="row">
      <div><label for="kerf">Kerf (mm)</label>
        <input id="kerf" type="number" value="0.2" min="0" step="any"></div>
      <div><label for="spacing">Spacing (mm)</label>
        <input id="spacing" type="number" value="2" min="0" step="any"></div>
    </div>
    <div class="row">
      <div><label for="margin">Sheet margin (mm)</label>
        <input id="margin" type="number" value="5" min="0" step="any"></div>
      <div><label for="per">Copies per part</label>
        <input id="per" type="number" value="1" min="1" step="1"></div>
    </div>
    <label for="strategy">Strategy</label>
    <select id="strategy"></select>

    <button class="primary" id="go" type="submit">Nest</button>
  </form>

  <section id="results">
    <div class="empty">Add parts and press Nest.</div>
  </section>
</main>

<script>
const $ = (id) => document.getElementById(id);
let chosen = [];

function renderFiles() {
  const ul = $('filelist');
  ul.innerHTML = '';
  chosen.forEach((f, i) => {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.textContent = f.name;
    const del = document.createElement('button');
    del.type = 'button';
    del.textContent = 'remove';
    del.onclick = () => { chosen.splice(i, 1); renderFiles(); };
    li.append(name, del);
    ul.appendChild(li);
  });
}

function addFiles(list) {
  for (const f of list) {
    const ext = f.name.toLowerCase().slice(f.name.lastIndexOf('.'));
    if (ext === '.dxf' || ext === '.svg') chosen.push(f);
  }
  renderFiles();
}

$('drop').onclick = () => $('picker').click();
$('picker').onchange = (e) => { addFiles(e.target.files); e.target.value = ''; };
['dragenter', 'dragover'].forEach(ev =>
  $('drop').addEventListener(ev, e => {
    e.preventDefault(); $('drop').classList.add('hot');
  }));
['dragleave', 'drop'].forEach(ev =>
  $('drop').addEventListener(ev, e => {
    e.preventDefault(); $('drop').classList.remove('hot');
  }));
$('drop').addEventListener('drop', e => addFiles(e.dataTransfer.files));

function message(kind, text) {
  return `<div class="msg ${kind}">${text.replace(/[<>&]/g,
    c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}</div>`;
}

async function boot() {
  try {
    const lic = await (await fetch('./api/license')).json();
    $('edition').textContent = lic.description;
    const sel = $('strategy');
    const s = await (await fetch('./api/strategies')).json();
    sel.innerHTML = '';
    for (const item of s.strategies) {
      const opt = document.createElement('option');
      opt.value = item.name;
      opt.textContent = item.available
        ? `${item.name} (${item.tier})`
        : `${item.name} - requires ${item.tier}`;
      opt.disabled = !item.available;
      sel.appendChild(opt);
    }
  } catch (err) {
    $('edition').textContent = 'offline';
  }
}

$('form').onsubmit = async (e) => {
  e.preventDefault();
  if (!chosen.length) {
    $('results').innerHTML = message('warn', 'Add at least one DXF or SVG file.');
    return;
  }
  const btn = $('go');
  btn.disabled = true;
  btn.textContent = 'Nesting...';

  const data = new FormData();
  chosen.forEach(f => data.append('files', f));
  data.append('config', JSON.stringify({
    sheet_width: +$('sw').value, sheet_height: +$('sh').value,
    sheet_cost: +$('cost').value, sheet_quantity: +$('qty').value,
    kerf: +$('kerf').value, part_spacing: +$('spacing').value,
    sheet_margin: +$('margin').value, quantity_per_part: +$('per').value,
    strategy: $('strategy').value
  }));

  try {
    const res = await fetch('./api/nest', { method: 'POST', body: data });
    const payload = await res.json();
    if (!res.ok) {
      $('results').innerHTML = message('err', payload.detail || 'Nesting failed.');
    } else {
      render(payload);
    }
  } catch (err) {
    $('results').innerHTML = message('err', 'Could not reach the server.');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Nest';
  }
};

function render(payload) {
  const s = payload.report.summary;
  const sig = payload.report.upgrade_signal;
  let html = '';

  if (payload.report.unplaced && Object.keys(payload.report.unplaced).length) {
    const list = Object.entries(payload.report.unplaced)
      .map(([k, v]) => `${k} x${v}`).join(', ');
    html += message('err', 'Could not place: ' + list);
  }
  for (const note of payload.report.notes || []) html += message('warn', note);
  for (const w of payload.warnings || []) html += message('warn', w);

  html += `<div class="cards">
    <div class="card"><div class="v">${s.yield_percent.toFixed(1)}%</div>
      <div class="k">Yield</div></div>
    <div class="card"><div class="v">${s.sheets_used}</div>
      <div class="k">Sheets</div></div>
    <div class="card"><div class="v">${s.parts_placed}</div>
      <div class="k">Placed</div></div>
    <div class="card"><div class="v">${(s.waste_area_mm2/1e6).toFixed(3)}</div>
      <div class="k">Waste m&sup2;</div></div>
  </div>`;

  if (sig && sig.worthwhile) html += message('info', sig.message);

  for (const sheet of payload.sheets) {
    const meta = payload.report.sheets[sheet.index];
    html += `<div class="sheet"><h3>Sheet ${sheet.index + 1} &mdash;
      ${meta.part_count} parts &mdash; ${meta.yield_percent.toFixed(1)}% yield</h3>
      <div class="frame">${sheet.svg}</div></div>`;
  }
  $('results').innerHTML = html;
}

boot();
</script>
</body></html>
"""
