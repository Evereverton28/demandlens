(async () => {
  const { api, esc, num, pct, date, table, chart, C, METHOD, PATTERN } = DL;
  const body = document.getElementById("body");
  let d;
  try { d = await api("/api/model"); } catch (e) { body.innerHTML = `<div class="notice error">${esc(e.message)}</div>`; return; }
  const history = () => table([
    { label: "Run", render: r => `#${r.run_id}` }, { label: "Started", render: r => esc(date(r.trained_at)) },
    { label: "Status", render: r => esc(r.status) + (r.error ? `<div class="small muted">${esc(r.error)}</div>` : "") },
    { label: "Data to", render: r => esc(date(r.data_end)) }, { label: "Products", num: true, render: r => num(r.n_products) },
    { label: "Modelled by ML", num: true, render: r => num(r.n_modelled) },
    { label: "Duration", num: true, render: r => r.duration_s ? num(r.duration_s) + " s" : "–" },
    { label: "Data notes", render: r => [r.uses_synthetic ? "synthetic" : "", r.uses_scenario ? "simulated stock" : ""].filter(Boolean).join(", ") || "–" },
  ], d.history, "No runs yet.");
  if (!d.run) { DL.emptyState(body, "No analysis yet", "Run the analysis to see how the forecasting methods compare."); return; }

  const s = d.summary, cfg = d.config || {};
  if (!s.overall) {
    body.innerHTML = `<div class="notice info">${esc(s.note || "No backtest was possible with this amount of history.")}</div><section class="panel"><h2>Run history</h2>${history()}</section>`;
    return;
  }
  const methods = Object.keys(s.overall);
  const bestOf = (key, fn = (v) => v) => methods.reduce((b, m) => (s.overall[m][key] !== null && (b === null || fn(s.overall[m][key]) < fn(s.overall[b][key]))) ? m : b, null);
  const best = bestOf("mase");
  const bestBase = methods.filter(m => m !== "gbm").reduce((b, m) => s.overall[m].mase < s.overall[b].mase ? m : b);
  const gain = s.overall.gbm ? 1 - s.overall.gbm.mase / s.overall[bestBase].mase : null;
  document.getElementById("headline").textContent = best === "gbm"
    ? `Gradient boosting beat every simpler method, with ${pct(gain)} lower scaled error than the best of them.`
    : `${METHOD[best]} was the most accurate method overall.`;
  document.getElementById("lede").innerHTML = `Tested on ${num(s.n_points)} product-weeks from ${num(s.n_products)} products, forecasting from
    ${s.origin_weeks.length} points in time between ${esc(date(s.origin_weeks[0]))} and ${esc(date(s.origin_weeks[s.origin_weeks.length - 1]))}.
    Each forecast used only data available at that time.`;

  const metricTable = (rows, cols) => {
    const lo = {};
    cols.forEach(c => { const vals = rows.map(r => c.get(r)).filter(v => v !== null && v !== undefined); lo[c.key] = c.best ? c.best(vals) : Math.min(...vals); });
    return table([{ label: "Method", render: r => esc(METHOD[r.m] || r.m) + (r.m === "gbm" ? " <span class='muted small'>(the model)</span>" : "") },
      ...cols.map(c => ({ label: c.label, num: true, cls: "", render: r => { const v = c.get(r); const isBest = v === lo[c.key];
        return `<span class="${isBest ? "best" : ""}" ${isBest ? 'style="font-weight:700;color:var(--green)"' : ""}>${c.fmt(v)}</span>`; } }))], rows);
  };
  const overallRows = methods.map(m => ({ m, ...s.overall[m] }));
  const closestToOne = (vals) => vals.reduce((b, v) => Math.abs(v - 1) < Math.abs(b - 1) ? v : b);

  const pats = Object.keys(s.by_pattern).filter(p => s.by_pattern[p].products);
  const cal = s.calibration || {};
  const ae = s.anomaly_evaluation || {};
  const mu = s.methods_used || {};

  body.innerHTML = `
    <div class="grid-2">
      <section class="panel">
        <div class="panel-head"><div><h2>Accuracy of each method</h2><p>Lower is better for MAE, WAPE and MASE; a bias of 1.00 means forecasts add up to actual sales. Best value in each column in green.</p></div></div>
        <div class="table-wrap metric-table">${metricTable(overallRows, [
          { key: "mae", label: "MAE (units)", get: r => r.mae, fmt: v => num(v, 2) },
          { key: "wape", label: "WAPE", get: r => r.wape, fmt: v => num(v, 3) },
          { key: "mase", label: "MASE", get: r => r.mase, fmt: v => num(v, 3) },
          { key: "bias", label: "Bias", get: r => r.bias, fmt: v => num(v, 2), best: closestToOne },
        ])}</div>
      </section>
      <section class="panel explain">
        <h2>What these measure</h2>
        <p style="margin-top:8px"><strong>MAE</strong> is the average number of units a weekly forecast was off by.
          <strong>WAPE</strong> is total error divided by total sales, so 0.70 means errors added up to 70% of what sold.</p>
        <p><strong>MASE</strong> divides each product's error by how much its sales normally change from week to week, then averages
          across products, so small and large sellers count equally. Below 1 means better than simply repeating last week.</p>
        <p><strong>Bias</strong> is total forecast divided by total sales. The model's median forecasts run low (for skewed demand the
          typical week is below the average week), so cover and run-out dates use its separate expected-value forecast instead.</p>
      </section>
    </div>

    <div class="grid-2e" style="margin-top:20px">
      <section class="panel">
        <div class="panel-head"><div><h2>Accuracy by weeks ahead</h2><p>MASE for forecasts 1 to 4 weeks ahead</p></div></div>
        <div class="chart-box"><canvas id="c-horizon"></canvas></div>
      </section>
      <section class="panel">
        <div class="panel-head"><div><h2>Uncertainty and expected demand</h2><p>How well the model's range and expected values held up</p></div></div>
        ${cal.gbm_p90_coverage !== undefined ? `
        <dl class="facts">
          <div><dt>Weeks at or below the 90th percentile</dt><dd>${pct(cal.gbm_p90_coverage, 1)} <small>target 90%</small></dd></div>
          <div><dt>Expected-value bias</dt><dd>${cal.gbm_mean ? num(cal.gbm_mean.bias, 2) : "–"} <small>median: ${num(s.overall.gbm.bias, 2)}</small></dd></div>
          <div><dt>Pinball loss at 0.9</dt><dd>${num(cal.gbm_p90_pinball, 2)}</dd></div>
        </dl>
        <div class="table-wrap" style="margin-top:14px">${table([{ label: "Demand pattern", render: r => esc(PATTERN[r[0]] || r[0]) },
          { label: "Coverage of the 90th percentile", num: true, render: r => pct(r[1], 1) }], Object.entries(cal.by_pattern || {}))}</div>` : `<p class="muted">The model was not used in this run.</p>`}
      </section>
    </div>

    <section class="panel" style="margin-top:20px">
      <div class="panel-head"><div><h2>Accuracy by demand pattern, and the method chosen</h2>
        <p>MASE per pattern. Each pattern uses whichever method was most accurate for it; young products without 26 weeks of history use the best simple method.</p></div></div>
      <div class="table-wrap">${table([
        { label: "Demand pattern", render: r => esc(PATTERN[r.p] || r.p) },
        { label: "Products", num: true, render: r => num(s.by_pattern[r.p].products) },
        ...methods.map(m => ({ label: METHOD[m], num: true, render: r => { const v = s.by_pattern[r.p][m] && s.by_pattern[r.p][m].mase;
          const chosen = s.selection[r.p] && s.selection[r.p].method === m; return `<span style="${chosen ? "font-weight:700;color:var(--green)" : ""}">${num(v, 3)}</span>`; } })),
        { label: "Chosen", render: r => esc(METHOD[(s.selection[r.p] || {}).method] || "–") },
      ], pats.map(p => ({ p })))}</div>
      <p class="small muted" style="margin-top:8px">“Too few sales” products sold at most once in the last year, so a forecast
        near zero is almost always right, which is why errors there can be close to 0.</p>
      <p class="small muted">Products forecast by each method in this run: ${Object.entries(mu).map(([m, n]) => `${esc(METHOD[m] || m)} ${num(n)}`).join(", ")}.</p>
    </section>

    <div class="grid-2" style="margin-top:20px">
      <section class="panel">
        <div class="panel-head"><div><h2>Unusual-sales detector</h2><p>Tested by planting anomalies of known size in a copy of the tested weeks</p></div></div>
        ${ae.spikes ? `
        <dl class="facts" style="margin-bottom:12px">
          <div><dt>Weeks checked</dt><dd>${num(ae.weeks_checked)}</dd></div>
          <div><dt>Flagged before planting</dt><dd>${num(ae.flagged_before_injection)} <small>${pct(ae.flag_rate, 1)}</small></dd></div>
          <div><dt>Zero-sales week caught</dt><dd>${ae.drops ? pct(ae.drops.recall) : "–"} <small>smooth and erratic products</small></dd></div>
        </dl>
        <div class="table-wrap">${table([{ label: "Planted spike", render: r => `Sales × ${esc(r[0].slice(1))}` },
          { label: "Caught (recall)", num: true, render: r => pct(r[1].recall) },
          { label: "Precision, lower bound", num: true, render: r => r[1].precision_lower_bound === null ? "–" : pct(r[1].precision_lower_bound, 1) }],
          Object.entries(ae.spikes).sort((a, b) => Number(a[0].slice(1)) - Number(b[0].slice(1))))}</div>
        ${ae.threshold_sensitivity ? `<h3 style="margin:16px 0 4px">Choosing the threshold</h3>
          <p class="small muted">A spike must reach this multiple of a busy (90th percentile) week. Higher multiples mean fewer false alarms but more missed spikes. Tested with 5× planted spikes.</p>
          <div class="table-wrap">${table([
            { label: "Multiple of a busy week", render: r => `${num(r.multiple, 1)}×${r.chosen ? " <span class='tag A'>used</span>" : ""}` },
            { label: "Weeks flagged", num: true, render: r => pct(r.flag_rate, 1) },
            { label: "Recall", num: true, render: r => pct(r.recall_x5) },
            { label: "Precision, lower bound", num: true, render: r => pct(r.precision_lower_bound_x5, 1) }], ae.threshold_sensitivity)}</div>` : ""}` : `<p class="muted">Not enough tested weeks to evaluate.</p>`}
      </section>
      <section class="panel explain">
        <h2>Reading the detector results</h2>
        <p style="margin-top:8px">Recall is the share of planted anomalies the detector caught. Precision counts every other flagged week as a
          false alarm, including weeks that really were unusual, so it understates true precision.</p>
        <p>When weekly sales swing widely, as with wholesale orders, a week with no sales at all can look like an ordinary quiet week.
          The zero-sales figure shows how detectable such drops are in this data. Flag rates by pattern:
          ${Object.entries(ae.flag_rate_by_pattern || {}).map(([p, v]) => `${esc(PATTERN[p] || p)} ${pct(v, 1)}`).join(", ")}.</p>
      </section>
    </div>

    <div class="grid-2e" style="margin-top:20px">
      <section class="panel"><h2>Settings used</h2>
        <dl class="facts" style="margin-top:10px">
          <div><dt>Forecast horizon</dt><dd>${num(cfg.horizons)} weeks</dd></div>
          <div><dt>Test window</dt><dd>${num(cfg.backtest_weeks)} weeks <small>new origin every ${num(cfg.origin_step)}</small></dd></div>
          <div><dt>Training window</dt><dd>${num(cfg.train_window_weeks)} weeks of origins</dd></div>
          <div><dt>History for the model</dt><dd>${num(cfg.min_history_weeks)}+ weeks</dd></div>
          <div><dt>Boosting iterations</dt><dd>${num(cfg.gbm_params && cfg.gbm_params.max_iter)}</dd></div>
          <div><dt>Learning rate</dt><dd>${cfg.gbm_params ? cfg.gbm_params.learning_rate : "–"}</dd></div>
          <div><dt>Leaf nodes, min leaf size</dt><dd>${cfg.gbm_params ? `${cfg.gbm_params.max_leaf_nodes}, ${cfg.gbm_params.min_samples_leaf}` : "–"}</dd></div>
          <div><dt>Anomaly threshold</dt><dd>|z| &gt; ${cfg.anomaly_z}</dd></div>
        </dl></section>
      <section class="panel"><h2>Data in this run</h2>
        <dl class="facts" style="margin-top:10px">
          <div><dt>Weeks of sales</dt><dd>${num(s.data.weeks)}</dd></div>
          <div><dt>From</dt><dd>${esc(date(s.data.first_week))}</dd></div>
          <div><dt>To (last full week)</dt><dd>${esc(date(s.data.last_week))}</dd></div>
          <div><dt>Products</dt><dd>${num(s.data.products)}</dd></div>
          <div><dt>Sold in the last 26 weeks</dt><dd>${num(s.data.active_products)}</dd></div>
          <div><dt>Run time</dt><dd>${num(d.run.duration_s)} s</dd></div>
        </dl></section>
    </div>
    <section class="panel" style="margin-top:20px"><h2>Run history</h2><div class="table-wrap" style="margin-top:8px">${history()}</div></section>`;

  const hz = Object.keys(s.by_horizon);
  const colors = { gbm: C.green, moving_average: C.band, ses: "#7C8A96", sba: C.amber, seasonal_naive: C.brick };
  chart("c-horizon", { type: "line", data: { labels: hz.map(h => `${h} week${h === "1" ? "" : "s"} ahead`),
    datasets: methods.map(m => ({ label: METHOD[m], data: hz.map(h => s.by_horizon[h][m]), borderColor: colors[m], backgroundColor: colors[m],
      borderWidth: m === "gbm" ? 3 : 1.5, pointRadius: 3 })) },
    options: { maintainAspectRatio: false, scales: { y: { title: { display: true, text: "MASE" } } } } });
})();
