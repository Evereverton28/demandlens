"""Builds the two analysis notebooks (run once; the .ipynb files are committed)."""
import nbformat as nbf
from pathlib import Path

NB = Path(__file__).resolve().parents[1] / "notebooks"
md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell

SETUP = '''import os, sys, warnings
from pathlib import Path
ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore", category=RuntimeWarning)

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from analytics.config import AnalysisConfig
from analytics.ingest import read_online_retail, clean_online_retail
from analytics.preprocess import build_panel

plt.rcParams.update({"figure.figsize": (10, 4), "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.alpha": .3})
GREEN, INK, AMBER, BRICK = "#1E6B5C", "#1B2B3A", "#B7791F", "#A63D2F"

# The UCI Online Retail II file (CSV or the original .xlsx). Set DEMANDLENS_DATA to use another file,
# for example the synthetic file from scripts/generate_synthetic.py.
candidates = [os.environ.get("DEMANDLENS_DATA"), ROOT / "data/online_retail_II.csv", ROOT / "data/online_retail_II.xlsx"]
DATA = next(Path(p) for p in candidates if p and Path(p).exists())
print("Using", DATA)'''

LOAD = '''raw = read_online_retail(DATA)
movements, products, report = clean_online_retail(raw)
movements["product_id"] = pd.factorize(movements["sku"])[0] + 1     # stand-in ids (the app uses database ids)
sku_of = movements.drop_duplicates("product_id").set_index("product_id")["sku"]
name_of = products.set_index("sku")["name"]
panel = build_panel(movements)
cfg = AnalysisConfig()
T = len(panel.weeks)
print(f"{len(movements):,} ledger movements, {panel.units.shape[0]:,} products, {T} complete weeks "
      f"({panel.weeks[0].date()} to {panel.last_week.date()})")'''

eda = nbf.v4.new_notebook()
eda.cells = [
    md("# DemandLens: exploratory data analysis\n\nThis notebook examines the UCI Online Retail II dataset (Chen, 2012) before modelling: "
       "its structure, its data-quality problems, and the demand characteristics that shape the choice of methods. "
       "Every step uses the same code as the DemandLens application (`analytics/`), so what is reported here is what the system does."),
    code(SETUP),
    md("## 1. The raw data"),
    code('''raw = read_online_retail(DATA)
raw["InvoiceDate"] = pd.to_datetime(raw["InvoiceDate"])
print(raw.shape)
display(raw.head())
summary = pd.DataFrame({"dtype": raw.dtypes.astype(str), "missing": raw.isna().sum(), "missing %": (raw.isna().mean() * 100).round(2),
                        "unique": raw.nunique()})
display(summary)
print("Date range:", raw["InvoiceDate"].min(), "to", raw["InvoiceDate"].max())'''),
    code('''display(raw.describe()[["Quantity", "Price"]].T)
top_countries = raw["Country"].value_counts(normalize=True).head(8).mul(100).round(1)
display(top_countries.rename("% of rows"))'''),
    md("## 2. Data-quality problems\n\nCancellations (invoice numbers starting with C), negative quantities, zero prices, "
       "non-product codes such as postage, and exact duplicates all need a rule before the data can be treated as demand."),
    code('''is_cancel = raw["Invoice"].astype(str).str.startswith("C")
non_product = ~raw["StockCode"].astype(str).str.match(r"^\\d{5}[A-Za-z]{0,3}$")
quality = pd.Series({
    "cancellation rows": is_cancel.sum(),
    "negative quantity, not a cancellation": ((raw["Quantity"] < 0) & ~is_cancel).sum(),
    "zero or negative price": (raw["Price"] <= 0).sum(),
    "non-product codes": non_product.sum(),
    "exact duplicate rows": raw.duplicated().sum(),
    "missing customer id": raw["Customer ID"].isna().sum(),
})
display(quality.to_frame("rows").assign(pct=lambda d: (d["rows"] / len(raw) * 100).round(2)))
display(raw.loc[non_product, "StockCode"].value_counts().head(12).rename("non-product codes"))'''),
    code('''# The largest single order lines, and whether they were cancelled soon after.
display(raw.nlargest(6, "Quantity")[["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Customer ID"]])'''),
    md("## 3. Cleaning into the ledger\n\nThe rules in `analytics/ingest.py`: drop non-product codes and duplicates; drop zero-price sales; "
       "void orders cancelled in full by the same customer within 90 days; treat other cancellations as returns; "
       "keep negative non-cancellation lines as stock adjustments (not demand); drop customer identifiers."),
    code(LOAD + '''
display(pd.Series({k: v for k, v in report.items() if isinstance(v, (int, float))}, name="count").to_frame())'''),
    md("## 4. Sales over time"),
    code('''rev = np.nansum(panel.revenue, axis=0); units = np.nansum(panel.units, axis=0)
fig, ax = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
ax[0].plot(panel.weeks, units, color=INK); ax[0].set_ylabel("units / week")
ax[1].plot(panel.weeks, rev, color=GREEN); ax[1].set_ylabel("revenue / week")
fig.suptitle("Weekly demand (net of returns)"); plt.tight_layout(); plt.show()'''),
    code('''wk = pd.DataFrame({"week": panel.weeks, "units": units})
wk["year"] = wk["week"].dt.isocalendar().year; wk["woy"] = wk["week"].dt.isocalendar().week
pivot = wk.pivot_table(index="woy", columns="year", values="units")
pivot.plot(title="Weekly units by week of the year: does the seasonal pattern repeat?", colormap="viridis"); plt.xlabel("ISO week"); plt.show()
print("Correlation between years on common weeks:")
display(pivot.corr().round(2))'''),
    code('''sales = movements[movements["type"] == "SALE"].copy()
sales["weekday"] = sales["occurred_at"].dt.day_name()
order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
sales.groupby("weekday")["quantity"].sum().reindex(order).plot.bar(color=GREEN, title="Units sold by weekday"); plt.show()'''),
    md("## 5. Revenue concentration (ABC)"),
    code('''from analytics.segmentation import profile
prof = profile(panel, T - 1, cfg)
live = prof[prof["revenue_52w"] > 0].sort_values("revenue_52w", ascending=False)
cum = live["revenue_52w"].cumsum() / live["revenue_52w"].sum()
share = np.arange(1, len(live) + 1) / len(live)
plt.plot(share * 100, cum.values * 100, color=GREEN); plt.plot([0, 100], [0, 100], "--", color="grey")
plt.xlabel("% of products (highest revenue first)"); plt.ylabel("% of revenue"); plt.title("Pareto curve, last 52 weeks"); plt.show()
display(live.groupby("abc_class").agg(products=("product_id", "size"), revenue_share=("revenue_share", "sum")).round(3))'''),
    md("## 6. How demand behaves week to week\n\nThe share of zero weeks and the Syntetos-Boylan classification decide which forecasting methods are appropriate."),
    code('''obs = ~np.isnan(panel.units[:, -52:])
zero_share = ((np.nan_to_num(panel.units[:, -52:]) == 0) & obs).sum(1) / np.maximum(obs.sum(1), 1)
plt.hist(zero_share[obs.sum(1) >= 26], bins=30, color=GREEN)
plt.xlabel("share of weeks with no sales"); plt.ylabel("products"); plt.title("Intermittency, products with 26+ weeks of history"); plt.show()'''),
    code('''p = prof[prof["demand_pattern"] != "insufficient"]
colors = {"smooth": GREEN, "erratic": AMBER, "intermittent": INK, "lumpy": BRICK}
for pat, g in p.groupby("demand_pattern"):
    plt.scatter(g["adi"], g["cv2"], s=6, alpha=.5, color=colors[pat], label=f"{pat} ({len(g)})")
plt.axvline(cfg.adi_cutoff, color="grey", ls="--"); plt.axhline(cfg.cv2_cutoff, color="grey", ls="--")
plt.xscale("log"); plt.yscale("log"); plt.xlabel("ADI (average weeks between sales)"); plt.ylabel("CV² of sale sizes")
plt.title("Syntetos-Boylan demand classes"); plt.legend(); plt.show()
display(prof["demand_pattern"].value_counts().rename("products"))'''),
    md("## 7. Price and quantity"),
    code('''long = panel.long.dropna(subset=["avg_price"]).copy()
long["rel_price"] = long["avg_price"] / long.groupby("product_id")["avg_price"].transform("median")
long["units_rel"] = long["units"] / long.groupby("product_id")["units"].transform("mean")
bins = pd.cut(long["rel_price"], [0, .7, .85, .95, 1.05, 1.15, 1.3, 5])
long.groupby(bins, observed=True)["units_rel"].median().plot.bar(color=GREEN)
plt.xlabel("price relative to the product's usual price"); plt.ylabel("median units vs product mean")
plt.title("Lower prices go with higher sales (a reason for the price feature)"); plt.show()'''),
    md("## 8. Returns and trends"),
    code('''by_type = movements.groupby("type")["quantity"].sum()
print(f"Returned units as a share of units sold: {by_type.get('RETURN', 0) / by_type['SALE']:.2%}")
from analytics.trends import classify_trends
tr = classify_trends(panel, T - 1, cfg)
display(tr["trend"].value_counts().rename("products (Mann-Kendall, last 26 weeks, 5%)"))'''),
    md("## 9. Implications for modelling\n\nUse the outputs above to write up the findings. The questions they answer:\n\n"
       "* **Seasonality** (section 4): if the pattern repeats across years, week-of-year and last-year's-sales features are justified.\n"
       "* **Concentration** (section 5): how few products carry most revenue, which is why forecast accuracy on A products matters most.\n"
       "* **Intermittency** (section 6): a large share of intermittent and lumpy products rules out per-product ARIMA and motivates "
       "Croston's method as a baseline and a global model that learns across products.\n"
       "* **Price** (section 7): whether relative price moves with demand, which supports the price feature.\n"
       "* **Data quality** (sections 2-3): every rule and its count, for the preprocessing chapter."),
]

model = nbf.v4.new_notebook()
model.cells = [
    md("# DemandLens: model development and evaluation\n\nThe forecasting approach step by step: features, leakage check, rolling-origin backtest "
       "against baselines, calibration of the 90th percentile, why a separate mean forecast is needed, feature importance, "
       "and the anomaly detector's evaluation. It calls the same functions as the application's analysis run.\n\n"
       "**Run time:** the backtest trains 18 models on up to about 700,000 rows each; allow 5-15 minutes on a laptop. "
       "Fold results are cached in `data/cache`, so re-running is fast."),
    code(SETUP),
    code(LOAD),
    md("## 1. Features\n\nOne row per (product, forecast origin week *t*, horizon *h*). Features use data up to week *t* only; the target is demand in week *t+h*."),
    code('''from analytics.features import DesignBuilder, FEATURES, add_profile
from analytics.segmentation import profile
builder = DesignBuilder(panel, cfg, categories=None, holiday_country="GB")
rows = builder.rows(0, T - 1, require_target=False)
print(f"{len(rows):,} design rows, {len(FEATURES)} features:", ", ".join(FEATURES))
display(add_profile(rows[rows["_t"] == T - 5].head(5), profile(panel, T - 5, cfg), panel)[FEATURES + ["_y"]])'''),
    md("### Leakage check\nChange every value after week *t*: the features at *t* must not change (the same test runs in `tests/test_models.py`)."),
    code('''t = T - 10
before = DesignBuilder(panel, cfg).rows(t, t, require_target=True)
saved = panel.units.copy(); panel.units[:, t + 1:] = 9999
after = DesignBuilder(panel, cfg).rows(t, t, require_target=True); panel.units[:] = saved
cols = [c for c in before.columns if not c.startswith("_") and c != "y_last_year"]
print("Features unchanged by future data:", before[cols].equals(after[cols]))'''),
    md("## 2. Rolling-origin backtest\n\nSix forecast origins, four weeks apart, across the last 24 weeks. At each origin every method sees only earlier data."),
    code('''import time
from analytics.evaluate import run_backtest, summarise, origins_for
print("Origins:", [str(panel.weeks[o].date()) for o in origins_for(panel, cfg)])
t0 = time.time()
bt = run_backtest(panel, builder, rows, cfg, progress=print, cache_dir=ROOT / "data/cache", fingerprint=f"nb_{DATA.stem}_{T}_" + __import__("hashlib").sha1(repr(cfg).encode()).hexdigest()[:10])
patterns = profile(panel, T - 1, cfg).set_index("product_id")["demand_pattern"]
summary = summarise(bt, patterns)
print(f"{len(bt):,} forecast points in {time.time() - t0:.0f} s")'''),
    code('''overall = pd.DataFrame(summary["overall"]).T[["mae", "wape", "mase", "bias", "n"]]
display(overall.sort_values("mase").style.format({"mae": "{:.2f}", "wape": "{:.3f}", "mase": "{:.3f}", "bias": "{:.2f}"})
        .highlight_min(subset=["mae", "wape", "mase"], color="#DCEBE6"))'''),
    code('''pd.DataFrame(summary["by_horizon"]).T.plot(marker="o", title="MASE by weeks ahead"); plt.ylabel("MASE"); plt.show()
bp = pd.DataFrame({p: {m: v[m]["mase"] for m in overall.index if m in v} for p, v in summary["by_pattern"].items()}).T
bp["products"] = [summary["by_pattern"][p]["products"] for p in bp.index]
bp["chosen"] = [summary["selection"][p]["method"] for p in bp.index]
display(bp.round(3))'''),
    md("### Is the improvement consistent?\nA paired comparison per product: how often does the model beat the best simple method?"),
    code('''best_base = min((m for m in overall.index if m != "gbm"), key=lambda m: overall.loc[m, "mase"])
g = bt[bt["gbm"].notna() & (bt["scale"] > 0)]
per = g.assign(e_gbm=(g["actual"] - g["gbm"]).abs() / g["scale"], e_base=(g["actual"] - g[best_base]).abs() / g["scale"]) \\
       .groupby("product_id")[["e_gbm", "e_base"]].mean()
print(f"Model more accurate than {best_base} for {(per.e_gbm < per.e_base).mean():.1%} of {len(per):,} products")
from scipy.stats import wilcoxon
print("Wilcoxon signed-rank test on per-product MASE:", wilcoxon(per["e_gbm"], per["e_base"]))'''),
    md("## 3. Uncertainty and the mean forecast\n\nThe P90 should cover about 90% of weeks. The median forecast should not be used for totals: for skewed demand the sum of weekly medians falls well short of total demand."),
    code('''cal = summary["calibration"]
print(f"P90 coverage: {cal['gbm_p90_coverage']:.1%}   pinball loss: {cal['gbm_p90_pinball']:.2f}")
display(pd.Series(cal["by_pattern"], name="P90 coverage").round(3))
g = bt[bt["gbm"].notna()]
tot = pd.Series({"actual": g["actual"].sum(), "sum of weekly medians (P50)": g["gbm"].sum(), "sum of mean forecasts": g["gbm_mean"].sum()})
(tot / tot["actual"]).plot.bar(color=[INK, AMBER, GREEN], title="Total forecast relative to actual demand"); plt.axhline(1, color="grey", ls="--"); plt.show()'''),
    md("## 4. What drives the forecasts\n\nPermutation importance of the median model on the final origin: how much the error grows when one feature is shuffled."),
    code('''from sklearn.inspection import permutation_importance
from analytics.evaluate import training_rows
from analytics.forecasting import QuantileGBM
o = origins_for(panel, cfg)[-1]
prof_o = profile(panel, o, cfg)
tr = training_rows(rows, o, cfg)
model = QuantileGBM(cfg.gbm_params, cfg.random_state, with_mean=False).fit(add_profile(tr, prof_o, panel), tr["_y"].to_numpy())
test = rows[(rows["_t"] == o) & (rows["age"] >= cfg.min_history_weeks) & rows["_y"].notna()]
Xt = add_profile(test, prof_o, panel)[model.features].to_numpy(np.float32)
if model.dropped: print("Not used (no variation in the training data):", ", ".join(model.dropped))
yt = np.log1p(test["_y"].to_numpy())
imp = permutation_importance(model.models[0.5], Xt, yt, n_repeats=3, random_state=0, scoring="neg_mean_absolute_error")
pd.Series(imp.importances_mean, index=model.features).sort_values().plot.barh(color=GREEN, figsize=(8, 7), title="Permutation importance (P50 model)"); plt.show()'''),
    md("## 5. Example forecasts"),
    code('''top = profile(panel, T - 1, cfg).nlargest(4, "revenue_52w")["product_id"]
fig, axes = plt.subplots(2, 2, figsize=(12, 7))
for ax, pid in zip(axes.ravel(), top):
    i = panel.row(pid); b = bt[bt["product_id"] == pid].sort_values("target")
    ax.plot(panel.weeks[-40:], panel.units[i, -40:], color=INK, label="actual")
    ax.plot(panel.weeks[b["target"]], b["gbm"], ".", color=GREEN, label="P50 (at the time)")
    ax.fill_between(panel.weeks[b["target"]], 0, b["gbm_p90"], color=GREEN, alpha=.15, label="up to P90")
    ax.set_title(str(name_of.get(sku_of[pid], pid))[:40], fontsize=10)
axes[0, 0].legend(); plt.tight_layout(); plt.show()'''),
    md("## 6. Unusual-sales detector\n\nAnomalies of known size are planted in a copy of the backtest weeks (see `analytics/anomalies.py`)."),
    code('''from analytics.anomalies import injection_evaluation
from analytics.baselines import METHODS
sel = summary["selection"]
pts = bt.assign(pattern=bt["product_id"].map(patterns).fillna("insufficient"))
pts = pts.assign(p50=np.where(pts["gbm"].notna(), pts["gbm"], pts["moving_average"]),
                 p90=np.where(pts["gbm_p90"].notna(), pts["gbm_p90"], pts["moving_average"] * 2),
                 week_start=panel.weeks[pts["target"]])
ev = injection_evaluation(pts[["product_id", "week_start", "actual", "p50", "p90", "pattern"]], cfg)
print(f"Weeks flagged without planting: {ev['flag_rate']:.1%}")
display(pd.DataFrame(ev["spikes"]).T.round(3)); print("Zero-sales weeks caught:", ev["drops"])
display(pd.DataFrame(ev["threshold_sensitivity"]).round(3))'''),
    md("## 7. Optional: tuning\n\nThe `flask --app run tune --user <you>` command runs a small grid search with validation origins *before* the backtest window, "
       "so the backtest above stays an untouched test. Expect about 20-40 minutes on the full dataset."),
]
for nb in (eda, model):
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
nbf.write(eda, NB / "01_exploratory_analysis.ipynb")
nbf.write(model, NB / "02_model_development.ipynb")
print("written")
