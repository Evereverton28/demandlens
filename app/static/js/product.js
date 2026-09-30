(async () => {
  const { api, esc, num, money, pct, days, date, shortDate, tag, earnTag, table, chart, C, PATTERN, PATTERN_TECH, METHOD, METHOD_TECH, EARN, PREDICT, MOVE, runway, runwayLegend } = DL;
  const body = document.getElementById("body");
  const id = body.dataset.product;
  let d;
  try { d = await api(`/api/products/${id}`); } catch (e) { body.innerHTML = `<div class="notice error">${esc(e.message)}</div>`; return; }
  const p = d.product, m = d.metrics, cur = d.currency;
  document.title = `${p.name} · DemandLens`;
  document.getElementById("p-name").textContent = p.name;

  if (!m) {
    document.getElementById("p-lede").textContent = `Code ${p.sku}. This product was not part of the latest analysis.`;
  } else {
    const cls = { A: "one of your top earners", B: "a mid earner", C: "a low earner" }[m.abc_class] || "a product";
    const trend = { growing: "Sales are growing.", declining: "Sales are declining.", stable: "Sales are stable." }[m.trend] || "There are too few recent sales to test for a trend.";
    document.getElementById("p-lede").innerHTML =
      `Code ${esc(p.sku)}${p.category ? ` in ${esc(p.category)}` : ""}. It is ${cls}, bringing in ${esc(pct(m.revenue_share, 2))} of revenue over the last year. ` +
      `How it sells: ${esc((PATTERN[m.demand_pattern] || "").toLowerCase())}. ${esc(trend)}`;
  }

  const recs = (d.recommendations || []).map(r => `<div class="rec p${r.priority}"><h3>${esc(r.label)}${r.quantity ? `: ${num(r.quantity)} units` : ""}</h3>
    <div class="reason">${esc(r.reason)}</div></div>`).join("");
  const f = d.forecast || [];
  body.innerHTML = `
    <div class="grid-2">
      <div class="stack">
        <section class="panel">
          <div class="panel-head"><div><h2>Weekly sales and forecast</h2>
            <p>Black: what actually sold. Green: the forecast. Shaded: how high sales could go in a busy week. The dotted line shows what the system would have predicted at the time, so you can see how close it got.</p></div></div>
          <div class="chart-box tall"><canvas id="c-product"></canvas></div>
        </section>
        <section class="panel">
          <h2>Next four weeks</h2>
          <div class="table-wrap" style="margin-top:8px">${table([
            { label: "Week of", render: r => esc(date(r.week_start)) },
            { label: "Expected sales", num: true, render: r => num(r.mean, 1) },
            { label: "In a busy week", num: true, render: r => num(r.p90, 1) },
          ], f, "No forecast in the latest analysis.")}</div>
          ${f.length ? `<p class="small muted" style="margin-top:8px">Forecast by the ${esc((METHOD[f[0].method] || f[0].method).toLowerCase())}. “In a busy week” is the level sales stay below about nine weeks in ten; order quantities are based on it so a busy week does not empty the shelf.</p>` : ""}
        </section>
      </div>
      <div class="stack">
        <section class="panel">
          <h2>Stock position</h2>
          ${m ? `<div style="margin:14px 0 4px">${runway(m, Math.max(28, (m.lead_time_days || 7) * 3))}</div>${m.stock_on_hand !== null ? runwayLegend() : ""}
          <dl class="facts">
            <div><dt>Stock on hand</dt><dd>${m.stock_on_hand === null ? "Not recorded" : num(m.stock_on_hand)}</dd></div>
            <div><dt>Days of cover</dt><dd>${m.stock_on_hand === null ? "–" : esc(days(m.days_of_cover))}</dd></div>
            <div><dt>Runs out at the usual rate</dt><dd>${m.stock_on_hand === null ? "–" : esc(days(m.runout_expected_days))}</dd></div>
            <div><dt>Runs out if weeks are busy</dt><dd>${m.stock_on_hand === null ? "–" : esc(days(m.runout_worst_days))}</dd></div>
            <div><dt>Restock lead time</dt><dd>${esc(days(m.lead_time_days))}</dd></div>
            <div><dt>Suggested order</dt><dd>${m.reorder_qty ? num(m.reorder_qty) + " units" : "None"}</dd></div>
            ${m.overstock_units ? `<div><dt>Beyond expected demand</dt><dd>${num(m.overstock_units)} <small>units</small></dd></div>` : ""}
          </dl>` : `<p class="muted">Run the analysis to see this product's stock position.</p>`}
        </section>
        ${recs ? `<section class="panel"><h2 style="margin-bottom:10px">Recommendations</h2>${recs}</section>` : ""}
        ${m ? `<section class="panel"><h2>About this product</h2>
          <dl class="facts" style="margin-top:10px">
            <div><dt>Earnings</dt><dd>${earnTag(m.abc_class)} <small>${esc(money(m.revenue_52w, cur))} in a year</small></dd></div>
            <div><dt>How it sells</dt><dd>${esc(PATTERN[m.demand_pattern] || "–")}</dd></div>
            <div><dt>How predictable</dt><dd>${esc(PREDICT[m.xyz_class] || "–")}</dd></div>
            <div><dt>Sales direction</dt><dd>${esc({ growing: "Growing", declining: "Declining", stable: "Stable" }[m.trend] || "Too new to tell")}${m.trend === "growing" || m.trend === "declining" ? ` <small>${m.trend_slope > 0 ? "+" : ""}${num(m.trend_slope, 1)} a week</small>` : ""}</dd></div>
            <div><dt>Sells per week</dt><dd>${num(m.velocity_12w, 1)} <small>last 12 weeks</small></dd></div>
            <div><dt>Last sale</dt><dd>${m.weeks_since_sale ? `${num(m.weeks_since_sale)} weeks ago` : "This week"}</dd></div>
          </dl>
          <details class="tech"><summary>Technical details</summary>
            <dl class="facts" style="margin-top:8px">
              <div><dt>ABC class</dt><dd>${esc(m.abc_class || "–")}</dd></div>
              <div><dt>XYZ class</dt><dd>${esc(m.xyz_class || "–")} <small>CV ${num(m.cv, 2)}</small></dd></div>
              <div><dt>Demand pattern</dt><dd>${esc(PATTERN_TECH[m.demand_pattern] || "–")} <small>ADI ${num(m.adi, 2)}, CV² ${num(m.cv2, 2)}</small></dd></div>
              <div><dt>Mann–Kendall trend</dt><dd>${m.trend_p !== null ? `p = ${num(m.trend_p, 3)}` : "–"} <small>Sen's slope ${num(m.trend_slope, 2)}/wk</small></dd></div>
              ${f.length ? `<div><dt>Forecast method</dt><dd>${esc(METHOD_TECH[f[0].method] || f[0].method)}</dd></div>` : ""}
            </dl></details></section>` : ""}
      </div>
    </div>
    <div class="grid-2e" style="margin-top:20px">
      <section class="panel"><h2>Unusual sales</h2><div class="table-wrap" style="margin-top:8px">${table([
        { label: "Week of", render: r => esc(date(r.week_start)) },
        { label: "What", render: r => r.kind === "transaction" ? "Unusually large sale" : (r.direction === "spike" ? "Sales far above expected" : "Sales far below expected") + (r.weeks > 1 ? ` for ${num(r.weeks)} weeks` : "") },
        { label: "Actual", num: true, render: r => num(r.actual) }, { label: "Expected", num: true, render: r => num(r.expected, 1) },
        { label: "Status", render: r => esc(r.status) }], d.anomalies, "None found.")}</div></section>
      <section class="panel"><h2>Latest entries</h2><div class="table-wrap" style="margin-top:8px">${table([
        { label: "Date", render: r => esc(date(r.occurred_at)) }, { label: "Type", render: r => esc(r.type.toLowerCase()) },
        { label: "Quantity", num: true, render: r => num(r.quantity) }, { label: "Source", render: r => esc(r.source) }], d.movements, "No entries.")}</div></section>
    </div>`;

  // Chart: last 52 weeks, backtest forecasts and the next four weeks.
  const hist = d.history.slice(-52);
  const labels = hist.map(h => h.week).concat(f.map(x => x.week_start));
  const bt = Object.fromEntries((d.backtest || []).map(b => [b.week_start, b]));
  const n = hist.length;
  const series = (fn, fut) => hist.map(h => fn(bt[h.week])).concat(f.map(fut));
  chart("c-product", {
    type: "line",
    data: { labels, datasets: [
      { label: "Units sold", data: hist.map(h => h.units).concat(f.map(() => null)), borderColor: C.ink, backgroundColor: C.ink, borderWidth: 1.8, pointRadius: 0, tension: 0.15 },
      { label: "Predicted at the time", data: series(b => b ? b.p50 : null, x => null), borderColor: C.green, borderDash: [2, 3], borderWidth: 2, pointRadius: 0, spanGaps: false },
      { label: "Busy week", data: series(b => b ? b.p90 : null, x => x.p90), borderColor: "rgba(183,121,31,.6)", backgroundColor: "rgba(156,197,184,.28)", borderWidth: 1, pointRadius: 0, fill: "-1" },
      { label: "Forecast", data: hist.map((h, i) => i === n - 1 ? h.units : null).concat(f.map(x => x.mean)), borderColor: C.green, borderWidth: 2.2, pointRadius: 2 },
    ]},
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      scales: { x: { ticks: { maxTicksLimit: 9, callback: (v, i) => shortDate(labels[i]) }, grid: { display: false } }, y: { beginAtZero: true } },
      plugins: { tooltip: { callbacks: { title: (it) => "Week of " + date(labels[it[0].dataIndex]), label: (it) => it.raw === null ? null : `${it.dataset.label}: ${num(it.raw, 1)}` } } } },
  });
})();
