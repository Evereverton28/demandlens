(async () => {
  const { api, esc, num, money, date, table, pager } = DL;
  const el = (id) => document.getElementById(id);
  const msg = (id, text, ok = true) => { const m = el(id); m.textContent = text; m.className = `form-msg ${ok ? "ok" : "err"}`; };
  const TYPE = { SALE: "Sale", RESTOCK: "Delivery", RETURN: "Return", ADJUSTMENT: "Adjustment", STOCKTAKE: "Stock count" };

  /* ---------------- tabs */
  const show = (tab) => {
    document.querySelectorAll("#tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === tab));
    document.querySelectorAll("[data-panel]").forEach(p => p.hidden = p.dataset.panel !== tab);
    history.replaceState(null, "", `#${tab}`);
    ({ record: loadMovements, products: loadProducts, import: loadImports, settings: loadSettings })[tab]();
  };
  document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => show(b.dataset.tab)));

  /* ---------------- record an entry */
  const productIds = new Map();
  let searchTimer;
  el("mv-product-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      const d = await api("/api/catalogue?search=" + encodeURIComponent(el("mv-product-search").value));
      el("mv-product-list").innerHTML = d.items.map(p => {
        const label = `${p.name} (${p.sku})`; productIds.set(label, p.product_id);
        return `<option value="${esc(label)}">${p.stock_on_hand === null ? "" : "In stock: " + num(p.stock_on_hand)}</option>`;
      }).join("");
    }, 200);
  });

  async function loadMovements() {
    const rows = await api("/api/movements?limit=25");
    el("mv-table").innerHTML = table([
      { label: "When", cls: "nowrap", render: r => esc(date(r.occurred_at)) },
      { label: "Product", cls: "name", render: r => DL.productLink(r.product_id, r.product, r.sku) },
      { label: "Type", render: r => esc(TYPE[r.type]) },
      { label: "Qty", num: true, render: r => num(r.quantity) },
      { label: "Source", render: r => `<span class="muted small">${esc(r.source)}</span>` },
    ], rows, "No entries yet.");
  }

  el("mv-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const pid = productIds.get(el("mv-product-search").value);
    if (!pid) { msg("mv-msg", "Choose a product from the list.", false); return; }
    try {
      const r = await api("/api/movements", { json: { product_id: pid, type: f.get("type"), quantity: f.get("quantity"),
        unit_price: f.get("unit_price"), occurred_at: f.get("occurred_at") || null, note: f.get("note") } });
      msg("mv-msg", `Saved. Stock is now ${r.stock_on_hand === null ? "not recorded" : num(r.stock_on_hand)}.`);
      e.target.quantity.value = ""; e.target.note.value = "";
      loadMovements();
    } catch (err) { msg("mv-msg", err.message, false); }
  });

  /* ---------------- products */
  let pPage = 1, pTimer, editing = null;
  async function loadProducts() {
    const d = await api(`/api/catalogue?page=${pPage}&archived=${el("p-archived").value}&search=${encodeURIComponent(el("p-search").value)}`);
    const archived = el("p-archived").value === "1";
    el("p-table").innerHTML = table([
      { label: "Product", cls: "name", render: r => DL.productLink(r.product_id, r.name, r.sku) },
      { label: "Category", render: r => esc(r.category || "–") },
      { label: "Cost", num: true, render: r => num(r.cost_price, 2) },
      { label: "Price", num: true, render: r => num(r.selling_price, 2) },
      { label: "Lead time", num: true, render: r => r.lead_time_days === null ? "Default" : `${num(r.lead_time_days)} d` },
      { label: "Stock", num: true, render: r => r.stock_on_hand === null ? "–" : num(r.stock_on_hand) },
      { label: "", render: r => `<button class="btn quiet small" data-edit='${esc(JSON.stringify(r))}'>Edit</button>
          <button class="btn quiet small" data-archive="${r.product_id}" data-to="${archived ? 0 : 1}">${archived ? "Restore" : "Archive"}</button>` },
    ], d.items, "No products yet.");
    pager(el("p-pager"), d.page, d.pages, d.total, (p) => { pPage = p; loadProducts(); });
    el("p-table").querySelectorAll("[data-archive]").forEach(b => b.addEventListener("click", async () => {
      await api(`/api/catalogue/${b.dataset.archive}/archive`, { json: { archived: b.dataset.to === "1" } });
      loadProducts();
    }));
    el("p-table").querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click", () => startEdit(JSON.parse(b.dataset.edit))));
    const cats = await api("/api/categories");
    el("cat-list").innerHTML = cats.map(c => `<option value="${esc(c.name)}">`).join("");
  }
  function startEdit(p) {
    editing = p.product_id;
    const f = el("p-form");
    ["sku", "name", "category", "cost_price", "selling_price", "lead_time_days"].forEach(k => f[k].value = p[k] ?? "");
    f.sku.disabled = true; f.opening_stock.disabled = true;
    f.querySelector("button[type=submit]").textContent = "Save changes";
    f.closest(".panel").querySelector("h2").textContent = `Edit ${p.name}`;
    msg("p-msg", "Stock can't be edited here; record a stock count instead.");
    f.scrollIntoView({ behavior: "smooth" });
  }
  function stopEdit() {
    editing = null;
    const f = el("p-form"); f.reset(); f.sku.disabled = false; f.opening_stock.disabled = false;
    f.querySelector("button[type=submit]").textContent = "Add product";
    f.closest(".panel").querySelector("h2").textContent = "Add a product";
  }
  el("p-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    try {
      if (editing) { await api(`/api/catalogue/${editing}`, { method: "PUT", json: data }); stopEdit(); msg("p-msg", "Changes saved."); }
      else { await api("/api/catalogue", { json: data }); e.target.reset(); msg("p-msg", "Product added."); }
      loadProducts();
    } catch (err) { msg("p-msg", err.message, false); }
  });
  el("p-archived").addEventListener("change", () => { pPage = 1; loadProducts(); });
  el("p-search").addEventListener("input", () => { clearTimeout(pTimer); pTimer = setTimeout(() => { pPage = 1; loadProducts(); }, 300); });

  /* ---------------- import */
  async function loadImports() {
    const rows = await api("/api/imports");
    el("imp-table").innerHTML = table([
      { label: "Imported", cls: "nowrap", render: r => esc(date(r.imported_at)) },
      { label: "File", render: r => esc(r.file_name) + (r.is_synthetic ? " <span class='tag warn'>synthetic</span>" : "") + (r.is_scenario ? " <span class='tag warn'>simulated</span>" : "") },
      { label: "Entries", num: true, render: r => num(r.rows_imported) },
      { label: "Set aside", num: true, render: r => `<span title="${esc(r.report_json)}">${num(r.rows_rejected)}</span>` },
      { label: "", render: r => `<button class="btn quiet small" data-remove="${r.batch_id}" data-name="${esc(r.file_name)}">Remove</button>` },
    ], rows, "Nothing imported yet.");
    el("imp-table").querySelectorAll("[data-remove]").forEach(b => b.addEventListener("click", async () => {
      if (!confirm(`Remove "${b.dataset.name}"?\n\nIts entries and any product left with no history will be deleted, and stored analysis results will be cleared.`)) return;
      b.disabled = true;
      try {
        const r = await api(`/api/imports/${b.dataset.remove}`, { method: "DELETE" });
        msg("imp-msg", `Removed ${r.file_name}: ${num(r.movements)} entries and ${num(r.products_removed)} products.`);
        loadImports(); loadMovements();
      } catch (err) { msg("imp-msg", err.message, false); b.disabled = false; }
    }));
  }

  el("clear-btn").addEventListener("click", async () => {
    const confirmText = el("clear-confirm").value.trim();
    if (confirmText !== "DELETE") { msg("clear-msg", "Type DELETE in the box to confirm.", false); return; }
    if (!confirm("Delete every product, entry, import and analysis result in this account?")) return;
    try {
      const r = await api("/api/data/clear", { json: { confirm: "DELETE" } });
      msg("clear-msg", `Deleted ${num(r.products)} products and ${num(r.movements)} entries. Import a new file to start again.`);
      el("clear-confirm").value = "";
      loadImports(); loadMovements(); loadProducts();
    } catch (err) { msg("clear-msg", err.message, false); }
  });
  async function pollImport(jobId) {
    const j = await api(`/api/jobs/${jobId}`);
    if (j.status === "running") { msg("imp-msg", j.progress || "Working…"); setTimeout(pollImport, 1500, jobId); return; }
    msg("imp-msg", j.message, j.status === "done");
    el("imp-form").querySelector("button").disabled = false;
    loadImports();
  }
  el("imp-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector("button"); btn.disabled = true;
    msg("imp-msg", "Uploading…");
    try {
      const res = await fetch("/api/imports", { method: "POST", body: new FormData(e.target), credentials: "same-origin" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      pollImport(data.job_id);
    } catch (err) { msg("imp-msg", err.message, false); btn.disabled = false; }
  });
  el("scenario-btn").addEventListener("click", async () => {
    try { const r = await api("/api/scenario", { method: "POST" }); msg("scenario-msg", `Added simulated stock for ${num(r.products)} products.`); loadImports(); }
    catch (err) { msg("scenario-msg", err.message, false); }
  });

  /* ---------------- settings */
  async function loadSettings() {
    const s = await api("/api/settings");
    const f = el("s-form");
    ["default_lead_time_days", "review_period_days", "overstock_weeks", "holiday_country", "currency"].forEach(k => f[k].value = s[k]);
  }
  el("s-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/settings", { method: "PUT", json: Object.fromEntries(new FormData(e.target)) }); msg("s-msg", "Settings saved. Run the analysis again to apply them."); }
    catch (err) { msg("s-msg", err.message, false); }
  });

  show((location.hash || "#record").slice(1));
})();
