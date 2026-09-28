(async () => {
  const { api, esc, num, compact, money, pct, days, date, shortDate, runway, runwayLegend, chart, C, emptyState } = DL;
  const body = document.getElementById("body");
  let d;
  try { d = await api("/api/overview"); } catch (e) { body.innerHTML = `<div class="notice error">${esc(e.message)}</div>`; return; }
  if (!d.run) {
    const s = await DL.ready;
    document.getElementById("headline").textContent = "Welcome to DemandLens";
    emptyState(body, s && s.movements ? "Your data is ready to analyse" : "Start by adding your sales data",
      s && s.movements ? "Press <strong>Run analysis</strong> in the sidebar. The first run backtests every forecasting method, so it can take a few minutes on a large dataset."
                       : "Import a sales history file or record sales on the Data page. At least eight complete weeks of sales are needed for an analysis.");
    return;
  }
  const k = d.kpi, cur = d.currency;
  const change = (a, b) => (a === null || b === null || !b) ? null : (a - b) / b;
  const revChange = change(k.revenue_4w, k.revenue_prev_4w);
  const unitsChange = change(k.units_4w, k.units_prev_4w);
  const fcUnits = d.forecast.reduce((s, f) => s + f.units, 0);

  // Headline: the single most important fact, in words.
  let headline;
  if (k.at_risk > 0) headline = `${num(k.at_risk)} ${k.at_risk === 1 ? "product" : "products"} could run out before a restock arrives.`;
  else if (k.with_stock === 0) headline = `Sales are forecast for ${num(k.active)} active products.`;
  else if (k.overstocked > 0) headline = `Nothing is about to run out; ${num(k.overstocked)} products hold more stock than they need.`;
  else headline = `Stock looks healthy across ${num(k.active)} active products.`;
  document.getElementById("headline").textContent = headline;
  const dir = revChange === null ? "" : revChange >= 0 ? `up ${pct(revChange)}` : `down ${pct(-revChange)}`;
  document.getElementById("lede").innerHTML =
    `Revenue over the last four weeks was ${esc(money(k.revenue_4w, cur))}${dir ? `, ${dir} on the four weeks before` : ""}. ` +
    `About ${esc(compact(fcUnits))} units are expected to sell in the next four weeks. ` +
    `<span class="muted">Data up to the week of ${esc(date(d.run.data_end))}.</span>`;

  const delta = (v) => v === null ? "" : `<div class="delta ${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : "−"}${pct(Math.abs(v))} on previous 4 weeks</div>`;
  body.innerHTML = `
    <section class="kpis" aria-label="Key figures">
      <div class="kpi"><div class="label">Revenue, last 4 weeks</div><div class="value">${esc(DL.moneyShort(k.revenue_4w, cur))}</div>${delta(revChange)}</div>
      <div class="kpi"><div class="label">Units sold, last 4 weeks</div><div class="value">${esc(compact(k.units_4w))}</div>${delta(unitsChange)}</div>
      <div class="kpi"><div class="label">Expected units, next 4 weeks</div><div class="value">${esc(compact(fcUnits))}</div><div class="delta">sum of product forecasts</div></div>
      <div class="kpi ${k.at_risk ? "alert" : ""}"><div class="label">Could run out before restock</div><div class="value">${num(k.at_risk || 0)}</div><div class="delta"><a href="/stock-risk">See stock risk</a></div></div>
      <div class="kpi ${k.overstocked ? "warn" : ""}"><div class="label">Overstocked</div><div class="value">${num(k.overstocked || 0)}</div><div class="delta">${k.overstock_value ? esc(DL.moneyShort(k.overstock_value, cur)) + " tied up" : "&nbsp;"}</div></div>
      <div class="kpi"><div class="label">Unusual sales to review</div><div class="value">${num(k.open_anomalies)}</div><div class="delta"><a href="/unusual-sales">Review</a></div></div>
    </section>
    <div class="grid-2">
      <div class="stack">
        <section class="panel">
          <div class="panel-head"><div><h2>Units sold each week</h2><p>History and the forecast for the next four weeks</p></div></div>
          <div class="chart-box"><canvas id="c-weekly" aria-label="Weekly units sold with forecast"></canvas></div>
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>Needs attention</h2><p>The most urgent recommendations from the latest analysis</p></div>
            <a href="/recommendations">All recommendations</a></div>
          ${d.attention.length ? runwayLegend() : ""}
          <ul class="attention" id="attention"></ul>
        </section>
      </div>
      <div class="stack">
        <section class="panel">
          <h2>What the analysis suggests</h2>
          <div id="actions" style="margin-top:10px"></div>
        </section>
        <section class="panel explain">
          <h2>How to read this</h2>
          <p style="margin-top:8px">Forecasts come from the method that was most accurate for each kind of demand when
            tested on past weeks. See <a href="/model">Model performance</a> for the test results.</p>
          <p>“Could run out” means stock would not last until a restock arrives if sales are at their
            90th percentile, that is, a busy week the model expects about one time in ten.</p>
        </section>
      </div>
    </div>`;

  // Weekly chart: the last 52 weeks and 4 forecast weeks.
  const hist = d.weekly.slice(-52);
  const labels = hist.map(w => w.week).concat(d.forecast.map(f => f.week));
  const actual = hist.map(w => w.units).concat(d.forecast.map(() => null));
  const fc = hist.map((w, i) => i === hist.length - 1 ? w.units : null).concat(d.forecast.map(f => f.units));
  chart("c-weekly", {
    type: "line",
    data: { labels, datasets: [
      { label: "Units sold", data: actual, borderColor: C.ink, backgroundColor: C.ink, borderWidth: 1.8, pointRadius: 0, tension: 0.2 },
      { label: "Forecast (expected)", data: fc, borderColor: C.green, borderDash: [6, 4], borderWidth: 2, pointRadius: 2, tension: 0.2 },
    ]},
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      scales: { x: { ticks: { maxTicksLimit: 9, callback: (v, i) => shortDate(labels[i]) }, grid: { display: false } },
                y: { beginAtZero: true, ticks: { callback: (v) => compact(v) } } },
      plugins: { tooltip: { callbacks: { title: (it) => "Week of " + date(labels[it[0].dataIndex]), label: (it) => `${it.dataset.label}: ${num(it.raw)}` } } } },
  });

  const scale = Math.max(28, ...d.attention.map(a => (a.lead_time_days || 0) * 2));
  document.getElementById("attention").innerHTML = d.attention.length ? d.attention.map(a => `
    <li>
      <div><div class="what"><a href="/products/${a.product_id}">${esc(a.name)}</a></div>
        <div class="small muted">${esc(a.label)}${a.quantity ? ` · ${num(a.quantity)} units` : ""}</div></div>
      <div>${runway(a, scale)}<div class="days">Worst case ${esc(days(a.runout_worst_days))}, restock ${esc(days(a.lead_time_days))}</div></div>
      <div class="why">${esc(a.reason)}</div>
    </li>`).join("") : `<li class="muted">Nothing urgent. Check back after the next analysis.</li>`;

  const LAB = { REORDER_URGENT: "Reorder now", INCREASE_STOCK: "Stock more", INVESTIGATE: "Check unusual sales",
                REDUCE: "Reduce or clear stock", PAUSE_REORDER: "Pause reordering", REVIEW_RANGE: "Review whether to keep stocking" };
  const order = Object.keys(LAB);
  document.getElementById("actions").innerHTML = DL.table(
    [{ label: "Suggestion", render: r => `<a href="/recommendations?action=${r.a}">${esc(LAB[r.a])}</a>` },
     { label: "Products", num: true, render: r => num(r.n) }],
    order.filter(a => d.actions[a]).map(a => ({ a, n: d.actions[a] })), "No recommendations in this run.");
})();
