(async () => {
  const { api, esc, num, money, days, tag, earnTag, productLink, table, pager, PATTERN, MOVE, METHOD } = DL;
  const el = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  ["abc", "pattern", "trend", "movement", "sort"].forEach(k => { if (params.get(k)) el("f-" + k).value = params.get(k); });
  let page = 1, timer;

  async function load() {
    const q = new URLSearchParams({ page, search: el("f-search").value, abc: el("f-abc").value, pattern: el("f-pattern").value,
                                    trend: el("f-trend").value, movement: el("f-movement").value, sort: el("f-sort").value });
    const d = await api("/api/products?" + q);
    if (d.run === null) { DL.emptyState(document.querySelector(".main .panel"), "No analysis yet", "Run the analysis to see product metrics."); return; }
    el("table").innerHTML = table([
      { label: "Product", cls: "name", render: r => productLink(r.product_id, r.name, r.sku) },
      { label: "Revenue, 52 weeks", num: true, render: r => money(r.revenue_52w, d.currency) },
      { label: "Earnings", render: r => earnTag(r.abc_class) },
      { label: "How it sells", render: r => esc(PATTERN[r.demand_pattern] || r.demand_pattern) },
      { label: "Sales direction", render: r => r.trend === "growing" || r.trend === "declining" ? tag(r.trend === "growing" ? "Growing" : "Declining", r.trend) : `<span class="muted">${esc(r.trend === "stable" ? "Stable" : "Too new to tell")}</span>` },
      { label: "Weekly sales", num: true, render: r => num(r.velocity_12w, 1) },
      { label: "Stock", num: true, render: r => r.stock_on_hand === null ? "–" : num(r.stock_on_hand) },
      { label: "Could run out in", num: true, render: r => r.stock_on_hand === null ? "–" : `<span class="${r.runout_worst_days !== null && r.runout_worst_days <= r.lead_time_days ? "tag urgent" : ""}">${esc(days(r.runout_worst_days))}</span>` },
    ], d.items, "No products match these filters.");
    pager(el("pager"), d.page, d.pages, d.total, (p) => { page = p; load(); });
  }
  ["f-abc", "f-pattern", "f-trend", "f-movement", "f-sort"].forEach(id => el(id).addEventListener("change", () => { page = 1; load(); }));
  el("f-search").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { page = 1; load(); }, 300); });
  load().catch(e => el("table").innerHTML = `<div class="notice error">${esc(e.message)}</div>`);
})();
