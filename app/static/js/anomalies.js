(async () => {
  const { api, esc, num, date, tag, table, productLink } = DL;
  const el = (id) => document.getElementById(id);
  let status = "open";

  async function load() {
    const q = new URLSearchParams({ status, kind: el("f-kind").value, severity: el("f-sev").value });
    const d = await api("/api/anomalies?" + q);
    document.querySelectorAll("#tabs button").forEach(b => {
      const n = d.counts[b.dataset.status] || 0;
      b.textContent = { open: "To review", confirmed: "Confirmed", dismissed: "Dismissed" }[b.dataset.status] + ` (${num(n)})`;
    });
    el("table").innerHTML = table([
      { label: "Week of", render: r => esc(date(r.week_start)) },
      { label: "Product", cls: "name", render: r => productLink(r.product_id, r.name, r.sku) },
      { label: "What happened", render: r => r.kind === "transaction"
          ? `One sale of ${num(r.actual)} units, ${num(r.score, 0)}× the usual ${num(r.expected, 0)}`
          : `${num(r.actual)} units sold; about ${num(r.expected, 0)} expected` },
      { label: "Severity", render: r => tag(r.severity === "high" ? "High" : "Moderate", r.severity) },
      { label: "", render: r => status === "open"
          ? `<button class="btn quiet small" data-id="${r.anomaly_id}" data-set="confirmed">Confirm</button>
             <button class="btn quiet small" data-id="${r.anomaly_id}" data-set="dismissed">Dismiss</button>`
          : `<button class="btn quiet small" data-id="${r.anomaly_id}" data-set="open">Move back to review</button>` },
    ], d.items, status === "open" ? "Nothing to review." : "None yet.");
    el("table").querySelectorAll("[data-set]").forEach(b => b.addEventListener("click", async () => {
      b.disabled = true;
      await api(`/api/anomalies/${b.dataset.id}`, { method: "PATCH", json: { status: b.dataset.set } });
      load();
    }));
  }
  document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => {
    status = b.dataset.status;
    document.querySelectorAll("#tabs button").forEach(x => x.setAttribute("aria-selected", x === b));
    load();
  }));
  ["f-kind", "f-sev"].forEach(id => el(id).addEventListener("change", load));
  load().catch(e => el("table").innerHTML = `<div class="notice error">${esc(e.message)}</div>`);
})();
