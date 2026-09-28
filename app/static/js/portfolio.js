(async () => {
  const { api, esc, num, money, pct, tag, table, chart, C, PATTERN, productLink } = DL;
  const body = document.getElementById("body");
  let d;
  try { d = await api("/api/portfolio"); } catch (e) { body.innerHTML = `<div class="notice error">${esc(e.message)}</div>`; return; }
  if (d.run === null) { DL.emptyState(body, "No analysis yet", "Run the analysis to see the portfolio."); return; }
  const cur = d.currency;
  document.getElementById("headline").textContent =
    `${num(d.a_count)} of ${num(d.live_count)} selling products (${pct(d.a_count / d.live_count)}) bring in ${pct(d.a_share)} of revenue.`;

  const cell = (abc, xyz) => d.matrix.find(m => m.abc_class === abc && m.xyz === xyz) || { products: 0, revenue: 0 };
  const XYZ = { X: "Steady", Y: "Variable", Z: "Highly variable" };
  const matrix = `<div class="matrix" role="table" aria-label="Products by revenue class and variability">
    <div></div>${["X", "Y", "Z"].map(x => `<div class="h" role="columnheader">${x}: ${XYZ[x]}</div>`).join("")}
    ${["A", "B", "C"].map(a => `<div class="h" role="rowheader">${a}</div>` + ["X", "Y", "Z"].map(x => {
      const c = cell(a, x), share = d.total_revenue ? c.revenue / d.total_revenue : 0;
      const alpha = Math.min(0.9, 0.08 + share * 2.2);
      return `<div class="cell" role="cell" style="background:rgba(30,107,92,${alpha});color:${alpha > 0.45 ? "#fff" : "inherit"}">
        <strong>${num(c.products)}</strong>${pct(share)} of revenue</div>`; }).join("")).join("")}
  </div>`;

  const list = (rows, metric) => table([
    { label: "Product", cls: "name", render: r => productLink(r.product_id, r.name, r.sku) },
    { label: "Class", render: r => tag(r.abc_class, r.abc_class) },
    metric,
  ], rows, "None in this group.");

  body.innerHTML = `
    <div class="grid-2e">
      <section class="panel">
        <div class="panel-head"><div><h2>Revenue concentration</h2><p>Share of revenue earned by the top share of products (Pareto curve)</p></div></div>
        <div class="chart-box"><canvas id="c-pareto"></canvas></div>
      </section>
      <section class="panel">
        <div class="panel-head"><div><h2>Value and predictability</h2>
          <p>ABC: revenue class. XYZ: how much weekly sales vary (coefficient of variation). A–X products are the easiest to plan; C–Z the hardest.</p></div></div>
        ${matrix}
      </section>
      <section class="panel">
        <div class="panel-head"><div><h2>Demand patterns</h2><p>Syntetos–Boylan classes. The forecasting method is chosen separately for each.</p></div></div>
        <div class="chart-box short"><canvas id="c-patterns"></canvas></div>
      </section>
      <section class="panel">
        <div class="panel-head"><div><h2>Sales trends</h2><p>Mann–Kendall test over the last 26 weeks, 5% significance</p></div></div>
        <div class="chart-box short"><canvas id="c-trends"></canvas></div>
      </section>
      <section class="panel"><h2>Growing fastest</h2><p class="small muted">A and B products with a significant upward trend</p>
        ${list(d.growing, { label: "Change per week", num: true, render: r => "+" + num(r.trend_slope, 1) })}</section>
      <section class="panel"><h2>Declining fastest</h2><p class="small muted">A and B products with a significant downward trend</p>
        ${list(d.declining, { label: "Change per week", num: true, render: r => num(r.trend_slope, 1) })}</section>
      <section class="panel"><h2>Fast movers</h2><p class="small muted">Highest average weekly sales over 12 weeks</p>
        ${list(d.fast, { label: "Units a week", num: true, render: r => num(r.velocity_12w, 1) })}</section>
      <section class="panel"><h2>Slow and dormant</h2><p class="small muted">Longest since a sale, excluding A products</p>
        ${list(d.slow, { label: "Weeks since a sale", num: true, render: r => num(r.weeks_since_sale) })}</section>
    </div>
    ${d.categories.length > 1 ? `<section class="panel" style="margin-top:20px"><h2>Revenue by category</h2><div class="chart-box short"><canvas id="c-cat"></canvas></div></section>` : ""}`;

  chart("c-pareto", { type: "line", data: { datasets: [
      { label: "Revenue earned", data: d.pareto.map(p => ({ x: p.share_products * 100, y: p.share_revenue * 100 })), borderColor: C.green, backgroundColor: "rgba(30,107,92,.12)", fill: true, pointRadius: 0, borderWidth: 2 },
      { label: "Equal share", data: [{ x: 0, y: 0 }, { x: 100, y: 100 }], borderColor: C.line, borderDash: [4, 4], pointRadius: 0, borderWidth: 1 }] },
    options: { maintainAspectRatio: false, scales: { x: { type: "linear", min: 0, max: 100, title: { display: true, text: "% of products (highest revenue first)" } },
      y: { min: 0, max: 100, title: { display: true, text: "% of revenue" } } },
      plugins: { tooltip: { callbacks: { label: (it) => `${num(it.raw.x)}% of products earn ${num(it.raw.y)}% of revenue` } } } } });
  const order = ["smooth", "erratic", "intermittent", "lumpy", "insufficient"];
  chart("c-patterns", { type: "bar", data: { labels: order.map(p => PATTERN[p]), datasets: [{ label: "Products", data: order.map(p => d.patterns[p] || 0), backgroundColor: C.green }] },
    options: { maintainAspectRatio: false, indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true } } } });
  const tl = [["growing", "Growing", C.green], ["stable", "Stable", C.band], ["declining", "Declining", C.brick], ["insufficient data", "Not enough data", "#C4CCC7"]];
  chart("c-trends", { type: "bar", data: { labels: tl.map(t => t[1]), datasets: [{ label: "Products", data: tl.map(t => d.trends[t[0]] || 0), backgroundColor: tl.map(t => t[2]) }] },
    options: { maintainAspectRatio: false, indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true } } } });
  if (d.categories.length > 1) chart("c-cat", { type: "bar", data: { labels: d.categories.map(c => c.category), datasets: [{ label: "Revenue", data: d.categories.map(c => c.revenue), backgroundColor: C.green }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } } } });
})();
