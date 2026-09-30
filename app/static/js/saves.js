(async () => {
  const { api, esc, num, date, table } = DL;
  const el = (id) => document.getElementById(id);
  const msg = (id, text, ok = true) => { const m = el(id); m.textContent = text; m.className = `form-msg ${ok ? "ok" : "err"}`; };
  const contents = (s) => s.products
    ? `${num(s.products)} products, ${num(s.entries)} entries`
    : `<span class="muted">Empty</span>`;
  const analysed = (s) => s.analysed_at ? esc(date(s.analysed_at)) : `<span class="muted">Not yet</span>`;

  async function load() {
    const rows = await api("/api/saves");
    const cur = rows.find(s => s.is_active);
    el("current").innerHTML = cur ? `
      <dl class="facts" style="margin-top:10px">
        <div><dt>Name</dt><dd>${esc(cur.name)} <button class="btn quiet small" id="rename-cur">Rename</button></dd></div>
        <div><dt>Contains</dt><dd>${contents(cur)}</dd></div>
        <div><dt>Data from</dt><dd>${cur.files ? esc(cur.files) : `<span class="muted">Nothing imported yet</span>`}</dd></div>
        <div><dt>Last analysed</dt><dd>${analysed(cur)}</dd></div>
      </dl>` : "";
    const rc = el("rename-cur");
    if (rc) rc.addEventListener("click", () => rename(cur));

    el("saves-table").innerHTML = table([
      { label: "Save", cls: "name", render: s => `<strong>${esc(s.name)}</strong>${s.is_active ? ` <span class="tag A">Loaded</span>` : ""}
          ${s.files ? `<div class="small muted">${esc(s.files)}</div>` : ""}` },
      { label: "Contains", render: s => contents(s) },
      { label: "Last analysed", render: s => analysed(s) },
      { label: "Created", render: s => esc(date(s.created_at)) },
      { label: "", render: s => s.is_active ? "" :
          `<button class="btn small" data-load="${s.save_id}">Load</button>
           <button class="btn quiet small" data-rename="${s.save_id}">Rename</button>
           <button class="btn danger small" data-delete="${s.save_id}">Delete</button>` },
    ], rows, "No saves yet.");

    const byId = Object.fromEntries(rows.map(s => [s.save_id, s]));
    document.querySelectorAll("[data-load]").forEach(b => b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        const r = await api(`/api/saves/${b.dataset.load}/load`, { method: "POST" });
        msg("list-msg", `Loaded ${r.name}. Opening the overview…`);
        setTimeout(() => { window.location.href = "/"; }, 500);
      } catch (e) { msg("list-msg", e.message, false); b.disabled = false; }
    }));
    document.querySelectorAll("[data-rename]").forEach(b => b.addEventListener("click", () => rename(byId[b.dataset.rename])));
    document.querySelectorAll("[data-delete]").forEach(b => b.addEventListener("click", async () => {
      const s = byId[b.dataset.delete];
      if (!confirm(`Delete the save "${s.name}"?\n\nIts data and analysis will be removed for good. Your other saves are not affected.`)) return;
      b.disabled = true;
      try { await api(`/api/saves/${s.save_id}`, { method: "DELETE" }); msg("list-msg", `Deleted ${s.name}.`); load(); }
      catch (e) { msg("list-msg", e.message, false); b.disabled = false; }
    }));
  }

  async function rename(s) {
    const name = prompt("New name for this save:", s.name);
    if (name === null || name.trim() === s.name) return;
    try { await api(`/api/saves/${s.save_id}`, { method: "PATCH", json: { name } }); msg("list-msg", "Renamed."); load(); }
    catch (e) { msg("list-msg", e.message, false); }
  }

  el("new-btn").addEventListener("click", async () => {
    const name = el("new-name").value.trim();
    if (!name) { msg("new-msg", "Give the new save a name.", false); return; }
    el("new-btn").disabled = true;
    try {
      await api("/api/saves", { json: { name } });
      msg("new-msg", `Started ${name}. Taking you to the import page…`);
      setTimeout(() => { window.location.href = "/data#import"; }, 600);
    } catch (e) { msg("new-msg", e.message, false); el("new-btn").disabled = false; }
  });
  el("new-name").addEventListener("keydown", (e) => { if (e.key === "Enter") el("new-btn").click(); });

  try { await load(); } catch (e) { el("saves-table").innerHTML = `<div class="notice error">${esc(e.message)}</div>`; }
})();
