(async () => {
  const { api, esc, num, money, days, tag, table, productLink, runway, runwayLegend } = DL;
  const body = document.getElementById("body");
  let d;
  try { d = await api("/api/risk"); } catch (e) { body.innerHTML = `<div class="notice error">${esc(e.message)}</div>`; return; }
  if (d.run === null) { DL.emptyState(body, "No analysis yet", "Run the analysis to see stock risk."); return; }
  const cur = d.currency;
  document.getElementById("headline").textContent = d.at_risk
    ? `${num(d.at_risk)} ${d.at_risk === 1 ? "product" : "products"} could run out before a restock arrives.`
    : "No product is expected to run out before a restock could arrive.";
  if (d.stock_unknown) DL.notice(`${num(d.stock_unknown)} active products have no recorded stock, so their risk can't be assessed. Record a stock count on the Data page.`, "info");
  const scale = Math.max(28, ...d.stockout.map(r => (r.lead_time_days || 0) * 2));

  body.innerHTML = `
    <section class="panel" style="margin-bottom:20px">
      <div class="panel-head"><div><h2>Could run out soon</h2>
        <p>${num(d.within_horizon)} products that are still selling could run out within ${esc(days(d.horizon_days))} in a busy period${d.within_horizon > d.stockout.length ? `; the ${num(d.stockout.length)} soonest are shown` : ""}. Red bars run out before a restock ordered today would arrive.</p></div></div>
      ${runwayLegend()}
      <div class="table-wrap">${table([
        { label: "Product", cls: "name", render: r => productLink(r.product_id, r.name, r.sku) },
        { label: "Class", render: r => tag(r.abc_class, r.abc_class) },
        { label: "Stock", num: true, render: r => num(r.stock_on_hand) },
        { label: `Runway (0–${scale} days)`, render: r => runway(r, scale) },
        { label: "Worst case", num: true, render: r => esc(days(r.runout_worst_days)) },
        { label: "Expected", num: true, render: r => esc(days(r.runout_expected_days)) },
        { label: "Suggested order", num: true, render: r => r.reorder_qty ? num(r.reorder_qty) : "–" },
      ], d.stockout, "No product is close to running out.")}</div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><h2>Overstocked</h2>
        <p>${num(d.overstock_total)} products hold stock beyond ${num(d.overstock_weeks)} weeks of expected demand${d.overstock_value_total ? `, about ${esc(money(d.overstock_value_total, cur))} in total` : ""}. Largest value first${d.overstock_total > d.overstock.length ? `; the top ${num(d.overstock.length)} are shown` : ""}.</p></div></div>
      <div class="table-wrap">${table([
        { label: "Product", cls: "name", render: r => productLink(r.product_id, r.name, r.sku) },
        { label: "Class", render: r => tag(r.abc_class, r.abc_class) },
        { label: "Trend", render: r => r.trend === "declining" ? tag("Declining", "declining") : esc(r.trend === "growing" ? "Growing" : r.trend === "stable" ? "Stable" : "–") },
        { label: "Stock", num: true, render: r => num(r.stock_on_hand) },
        { label: "Days of cover", num: true, render: r => esc(days(r.days_of_cover)) },
        { label: "Excess units", num: true, render: r => num(r.overstock_units) },
        { label: "Value of excess", num: true, render: r => r.overstock_value ? `${esc(money(r.overstock_value, cur))} <span class="muted small">at ${esc(r.overstock_basis)}</span>` : "–" },
      ], d.overstock, "No product is overstocked.")}</div>
    </section>`;
})();
