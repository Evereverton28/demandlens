/* Shared helpers: API access, formatting, the stock runway, charts and the analysis status. */
const DL = (() => {
  const C = { ink: "#1B2B3A", muted: "#5F6B75", green: "#1E6B5C", band: "#9CC5B8", amber: "#B7791F",
              brick: "#A63D2F", line: "#DCE1DE", soft: "#DCEBE6" };

  async function api(path, opts = {}) {
    const init = { credentials: "same-origin", ...opts };
    if (opts.json !== undefined) {
      init.method = init.method || "POST";
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(opts.json);
    }
    const res = await fetch(path, init);
    if (res.status === 401) { window.location = "/login?next=" + encodeURIComponent(location.pathname); throw new Error("Signed out"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `The server returned an error (${res.status}).`);
    return data;
  }

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v)) ? "–" :
    Number(v).toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
  const compact = (v) => (v === null || v === undefined) ? "–" :
    Math.abs(v) >= 1e6 ? num(v / 1e6, 2) + "m" : Math.abs(v) >= 1e4 ? num(v / 1e3, 0) + "k" : num(v, 0);
  const moneyShort = (v, cur) => (v === null || v === undefined) ? "–" : `${cur ? cur + " " : ""}${compact(v)}`;
  const money = (v, cur, d = 0) => (v === null || v === undefined) ? "–" : `${cur ? cur + " " : ""}${num(v, d)}`;
  const pct = (v, d = 0) => (v === null || v === undefined) ? "–" : num(v * 100, d) + "%";
  const days = (v) => (v === null || v === undefined) ? "beyond a year" : (Math.round(v) === 1 ? "1 day" : `${num(v, 0)} days`);
  const date = (s) => s ? new Date(s.replace(" ", "T")).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "–";
  const shortDate = (s) => new Date(s).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  const PATTERN = { smooth: "Smooth", erratic: "Erratic", intermittent: "Intermittent", lumpy: "Lumpy", insufficient: "Too few sales" };
  const METHOD = { gbm: "Gradient boosting", seasonal_naive: "Seasonal naive", moving_average: "Moving average",
                   ses: "Exponential smoothing", sba: "Croston (SBA)", inactive: "No recent sales" };
  const MOVE = { fast: "Fast", medium: "Medium", slow: "Slow", dormant: "Dormant" };
  const tag = (text, cls = "") => text ? `<span class="tag ${esc(cls)}">${esc(text)}</span>` : "";
  const productLink = (id, name, sku) => `<a href="/products/${id}">${esc(name)}</a>${sku ? `<span class="sku">${esc(sku)}</span>` : ""}`;

  /* The stock runway: days of stock until the expected run-out (green bar), the worst case
     (amber mark) and when a restock ordered today would arrive (dashed line). */
  function runway(m, scaleDays) {
    if (m.stock_on_hand === null || m.stock_on_hand === undefined) return `<span class="muted small">Stock not recorded</span>`;
    const lead = m.lead_time_days || 0;
    const exp = m.runout_expected_days, worst = m.runout_worst_days;
    const cap = (v) => Math.max(0, Math.min(v ?? scaleDays, scaleDays)) / scaleDays * 100;
    const short = worst !== null && worst !== undefined && worst <= lead;
    const label = `Expected to run out in ${days(exp)}, worst case ${days(worst)}; restock takes ${days(lead)}`;
    return `<div class="runway" role="img" aria-label="${esc(label)}" title="${esc(label)}">
      <div class="fill ${short ? "short" : ""}" style="width:${cap(exp)}%"></div>
      ${worst !== null && worst !== undefined ? `<div class="worst" style="left:calc(${cap(worst)}% - 1px)"></div>` : ""}
      <div class="lead" style="left:${cap(lead)}%"></div>
      ${exp === null || exp === undefined || exp > scaleDays ? `<span class="over">${exp === null || exp === undefined ? "1y+" : num(exp) + "d"}</span>` : ""}
    </div>`;
  }
  const runwayLegend = () => `<div class="runway-legend">
    <span><i class="l-fill"></i>Stock at the expected sales rate</span>
    <span><i class="l-worst"></i>Runs out if sales are high (90th percentile)</span>
    <span><i class="l-lead"></i>A restock ordered today arrives</span></div>`;

  function table(cols, rows, empty = "Nothing to show.") {
    if (!rows.length) return `<p class="muted">${esc(empty)}</p>`;
    return `<table><thead><tr>${cols.map(c => `<th class="${c.num ? "num" : ""}" scope="col">${esc(c.label)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(r => `<tr>${cols.map(c => `<td class="${c.num ? "num" : ""} ${c.cls || ""}">${c.render ? c.render(r) : esc(r[c.key])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  }

  function pager(el, page, pages, total, go) {
    el.innerHTML = `<span>${num(total)} products</span>
      <button class="btn quiet small" ${page <= 1 ? "disabled" : ""} data-go="${page - 1}">Previous</button>
      <span>Page ${page} of ${pages}</span>
      <button class="btn quiet small" ${page >= pages ? "disabled" : ""} data-go="${page + 1}">Next</button>`;
    el.querySelectorAll("[data-go]").forEach(b => b.addEventListener("click", () => go(Number(b.dataset.go))));
  }

  /* Charts */
  if (window.Chart) {
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 12;
    Chart.defaults.color = C.muted;
    Chart.defaults.borderColor = C.line;
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
    Chart.defaults.plugins.tooltip.backgroundColor = C.ink;
    Chart.defaults.animation = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : { duration: 300 };
  }
  const charts = {};
  function chart(id, config) {
    if (charts[id]) charts[id].destroy();
    const el = document.getElementById(id);
    if (!el) return null;
    charts[id] = new Chart(el, config);
    return charts[id];
  }

  function notice(html, kind = "") {
    const box = document.getElementById("notices");
    box.insertAdjacentHTML("beforeend", `<div class="notice ${kind}">${html}</div>`);
  }
  function emptyState(el, title, text) {
    el.innerHTML = `<div class="panel empty"><h2>${esc(title)}</h2><p>${text}</p>
      <p><a class="btn" href="/data">Go to Data</a></p></div>`;
  }

  /* Analysis status and the Run analysis button */
  let status = null;
  const statusEl = document.getElementById("run-status");
  const runBtn = document.getElementById("run-btn");

  function renderStatus(s) {
    status = s;
    if (!statusEl) return;
    if (s.job) {
      statusEl.innerHTML = `<strong>Analysis running.</strong><br>${esc(s.job.progress || "Starting")}`;
      runBtn.disabled = true; runBtn.textContent = "Running…";
      return;
    }
    runBtn.disabled = false; runBtn.textContent = s.run ? "Run analysis again" : "Run analysis";
    if (s.last_job && s.last_job.status === "failed" && (!s.run || s.last_job.job_id)) {
      const failedAfterRun = !s.run || s.last_job.started_at > s.run.trained_at;
      if (failedAfterRun) {
        statusEl.innerHTML = `<strong>Last run failed.</strong><br>${esc(s.last_job.message)}`;
        return;
      }
    }
    statusEl.innerHTML = s.run
      ? `Analysed ${date(s.run.trained_at)}<br>Data up to week of ${date(s.run.data_end)}${s.stale ? "<br><strong>New entries since.</strong>" : ""}`
      : `No analysis yet. ${s.movements ? "Run it to see forecasts." : "Add data first."}`;
  }

  async function loadStatus() {
    const s = await api("/api/status");
    renderStatus(s);
    if (s.job) setTimeout(pollJob, 2000, s.job.job_id);
    return s;
  }

  async function pollJob(jobId) {
    const j = await api(`/api/jobs/${jobId}`);
    if (j.status === "running") {
      statusEl.innerHTML = `<strong>Analysis running.</strong><br>${esc(j.progress || "")}`;
      setTimeout(pollJob, 2000, jobId);
    } else {
      await loadStatus();
      if (j.status === "done") { window.location.reload(); }
    }
  }

  runBtn && runBtn.addEventListener("click", async () => {
    runBtn.disabled = true;
    try {
      const { job_id } = await api("/api/analysis/run", { method: "POST" });
      statusEl.innerHTML = "<strong>Analysis running.</strong><br>Starting";
      runBtn.textContent = "Running…";
      setTimeout(pollJob, 1500, job_id);
    } catch (e) { statusEl.textContent = e.message; runBtn.disabled = false; }
  });

  const ready = loadStatus().then((s) => {
    if (s.synthetic) notice("Some of this data is <strong>synthetic test data</strong>. Results describe the simulation, not a real business.");
    if (s.scenario) notice("Stock levels are <strong>simulated</strong> because the imported sales data has no stock records. Stock risk results show how the features work, not real shelf levels.");
    if (s.stale) notice("New entries have been recorded since the last analysis. Run the analysis again to include them.", "info");
    return s;
  }).catch(() => null);

  return { C, api, esc, num, compact, money, moneyShort, pct, days, date, shortDate, tag, productLink, runway, runwayLegend,
           table, pager, chart, notice, emptyState, ready, PATTERN, METHOD, MOVE };
})();
