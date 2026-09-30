(async () => {
  const { api, esc, num, days, tag, table, productLink } = DL;
  const tabs = document.getElementById("tabs"), box = document.getElementById("table");
  let action = new URLSearchParams(location.search).get("action") || "";
  const ORDER = ["REORDER_URGENT", "REORDER_SOON", "INCREASE_STOCK", "INVESTIGATE", "REDUCE", "PAUSE_REORDER", "REVIEW_RANGE"];

  async function load() {
    const d = await api("/api/recommendations" + (action ? `?action=${action}` : ""));
    if (d.run === null) { DL.emptyState(box.parentElement, "No analysis yet", "Run the analysis to get recommendations."); return; }
    const total = Object.values(d.counts).reduce((a, b) => a + b, 0);
    tabs.innerHTML = [["", "All", total], ...ORDER.filter(a => d.counts[a]).map(a => [a, d.labels[a], d.counts[a]])]
      .map(([a, l, n]) => `<button role="tab" data-a="${a}" aria-selected="${a === action}">${esc(l)} (${num(n)})</button>`).join("");
    tabs.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
      action = b.dataset.a; history.replaceState(null, "", action ? `?action=${action}` : location.pathname); load();
    }));
    box.innerHTML = table([
      { label: "Product", cls: "name", render: r => productLink(r.product_id, r.name, r.sku) },
      { label: "Suggestion", render: r => `${tag(r.label, r.priority === 1 ? "urgent" : r.priority === 2 ? "warn" : "")}` },
      { label: "Units", num: true, render: r => r.quantity ? num(r.quantity) : "–" },
      { label: "Why", render: r => `<div class="reason">${esc(r.reason)}</div>` },
    ], d.items, "No recommendations of this kind.") + (d.items.length >= 300 ? `<p class="small muted">Showing the 300 highest-priority items.</p>` : "");
  }
  load().catch(e => box.innerHTML = `<div class="notice error">${esc(e.message)}</div>`);
})();
