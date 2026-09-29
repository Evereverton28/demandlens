# DemandLens

**A machine learning–based demand forecasting and stock risk decision support system for small retail businesses.**

DemandLens turns a retailer's sales history into forward-looking decisions. It cleans and aggregates transactions,
profiles every product statistically, forecasts weekly demand with an uncertainty range, estimates when each
product will run out, finds overstocked, slow and declining products, flags unusual sales, and recommends what to
reorder, reduce or check — each recommendation with the reason behind it.

It was rebuilt from the SIMS inventory prototype. The Flask + SQLite foundation, password hashing and the idea of
a transaction ledger were kept; everything else was redesigned around analysis.

---

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

flask --app run create-user demo demo@example.com          # asks for a password
python run.py                                                # open http://127.0.0.1:5000
```

Sign in, go to **Data → Import**, choose `data/synthetic_template.csv`, format *DemandLens template*, tick
*synthetic*, and import. Then press **Run analysis** in the sidebar (under a minute on this small file).

### With the real dataset (UCI Online Retail II)

```bash
python scripts/download_data.py --csv                        # ~45 MB download, converted to CSV
flask --app run import-data data/online_retail_II.csv --user demo --format online_retail
flask --app run create-scenario --user demo                  # simulated stock levels (the dataset has none)
flask --app run analyse --user demo                          # 3–6 minutes on a laptop
python run.py
```

If the download fails, get the file from <https://archive.ics.uci.edu/dataset/502/online+retail+ii> and place it
in `data/`. The importer reads the original `.xlsx` too, but CSV is much faster.

### Your own data

Download the template from **Data → Import** (`date, sku, name, category, type, quantity, unit_price,
cost_price, lead_time_days`). Types: `SALE`, `RETURN`, `RESTOCK`, `ADJUSTMENT` (signed), `STOCKTAKE` (a counted
level). Dates may be ISO (`2025-01-13`) or day-first (`13/01/2025`). At least 8 complete weeks of sales are
needed; about a year gives the model enough history to learn seasonality.

---

## The data science pipeline

| Stage | Where | What happens |
|---|---|---|
| Collection | `analytics/ingest.py`, Data page | Online Retail II or template files; day-to-day entries in the app |
| Storage | `app/schema.sql` | Typed, dated ledger; stock is always derived from it, never stored |
| Preprocessing | `analytics/ingest.py`, `preprocess.py` | Voided orders, returns, non-product codes, duplicates; weekly panel; zero-filling after launch only; partial week excluded |
| Exploration | `notebooks/01_exploratory_analysis.ipynb` | Seasonality, concentration, intermittency, price effects, data quality |
| Features | `analytics/features.py` | Lags, rolling statistics, recency, relative price, calendar and holidays, product profile |
| Statistical analysis | `segmentation.py`, `trends.py` | ABC, XYZ, Syntetos–Boylan demand patterns, Mann–Kendall trend test with Sen's slope |
| Modelling | `baselines.py`, `forecasting.py` | Seasonal naive, moving average, exponential smoothing, Croston (SBA); global gradient boosting for P50, P90 and the mean |
| Evaluation | `evaluate.py`, `notebooks/02_model_development.ipynb` | Rolling-origin backtest; MAE, WAPE, MASE, bias; P90 coverage; planted-anomaly test |
| Prediction | `pipeline.py` | The most accurate method per demand pattern forecasts each product |
| Decision support | `risk.py`, `anomalies.py`, `recommend.py` | Run-out dates, overstock, reorder quantities, unusual sales, explained recommendations |
| Visualisation | `app/templates`, `app/static` | The dashboard |

### Key design decisions

* **A global model, not one model per product.** Most products have short, noisy, intermittent histories.
  One gradient-boosting model learns across all of them (as in the M5 competition), and is only used where it
  beats simpler methods in the backtest.
* **Three forecasts per product-week.** The median (P50) is the best weekly point forecast; the 90th percentile
  (P90) sizes the worst case and reorder quantities; the mean drives cumulative quantities (cover, run-out,
  overstock), because for skewed demand weekly medians add up to far less than total sales.
* **Nothing is assumed about accuracy.** Every run is backtested and the results are shown on the
  *Model performance* page. Fold results are cached, so re-running after a settings change is fast.
* **Rules, not a black box, for recommendations.** They are stated conditions on the model outputs, so each can
  explain itself.

### Results from a verification run

A full run on Online Retail II (1,067,371 rows; data to the week of 28 Nov 2011; 82,400 product-weeks tested):

| Method | MAE | WAPE | MASE |
|---|---|---|---|
| **Gradient boosting** | **21.32** | **0.700** | **0.759** |
| Moving average | 21.84 | 0.717 | 0.870 |
| Exponential smoothing | 21.85 | 0.718 | 0.884 |
| Croston (SBA) | 23.64 | 0.777 | 1.115 |
| Seasonal naive | 34.49 | 1.133 | 1.278 |

P90 coverage 90.3% (target 90%). Mean-forecast bias 1.04 against 0.53 for summed medians. Your own run will show
its figures on the *Model performance* page. Stock-risk results on this dataset use **simulated** stock
levels and are labelled as such in the interface.

---

## Commands

```bash
flask --app run create-user NAME EMAIL
flask --app run import-data FILE --user NAME --format online_retail|generic [--synthetic]
flask --app run create-scenario --user NAME     # simulated stock for datasets without stock records
flask --app run list-imports --user NAME            # what has been imported
flask --app run delete-import ID --user NAME       # remove one import and its data
flask --app run clear-data --user NAME             # empty the account, keeping the login
flask --app run analyse --user NAME
flask --app run tune --user NAME                # optional grid search; later runs use the best settings
flask --app run migrate-sims PATH/inventory.db  # bring users, items and transactions over from SIMS
python scripts/generate_synthetic.py            # synthetic files with planted anomalies
python -m pytest                                # 21 tests
```

## Project structure

```
analytics/          the data science engine (no web code; used by the app, notebooks and tests)
app/                Flask application: auth, JSON API, jobs, templates, static assets
notebooks/          01 exploratory analysis, 02 model development and evaluation
scripts/            dataset download, synthetic data, notebook builder
tests/              pytest suite
data/               datasets (the real one is downloaded, not committed)
instance/           created at run time: database, saved models, backtest cache
```

## Changes from SIMS

| SIMS | DemandLens |
|---|---|
| User id sent by the browser (anyone could read another user's data) | Server-side sessions; every query scoped to the signed-in user |
| Stock edited directly | Stock derived from an append-only ledger |
| One "OUT" type | Sales separated from returns, adjustments and deliveries |
| Prices overwritten | Price stored with every entry |
| Dates fixed to the time of entry | Any date; historical imports |
| Fixed "fewer than 5 units" alert | Forecast-based run-out dates against each product's lead time |
| Products deleted | Products archived, history kept |
| Negative quantities accepted; stock or price of 0 rejected | Validated entries; zero allowed |
| Item names inserted as HTML | All output escaped |

## References

Chen, D. (2012). *Online Retail II* [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5CG6D
— further references are in the project proposal.
