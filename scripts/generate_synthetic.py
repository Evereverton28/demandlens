"""Write synthetic sales files for testing and demonstrations.

    python scripts/generate_synthetic.py                  # both layouts into data/
    python scripts/generate_synthetic.py --products 60 --weeks 104

Import them with the "synthetic" flag so the interface labels the results:
    flask --app run import-data data/synthetic_template.csv --user demo --format generic --synthetic
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analytics.synthetic import generic, online_retail  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--products", type=int, default=40)
ap.add_argument("--weeks", type=int, default=90)
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--out", default="data")
a = ap.parse_args()
out = Path(a.out); out.mkdir(exist_ok=True)

df, truth = generic(a.products, a.weeks, a.seed)
df.to_csv(out / "synthetic_template.csv", index=False)
df2, truth2 = online_retail(a.products, a.weeks, a.seed)
df2.to_csv(out / "synthetic_online_retail.csv", index=False)
truth["planted"].to_csv(out / "synthetic_planted_anomalies.csv", index=False)
truth["products"].to_csv(out / "synthetic_products_truth.csv", index=False)
print(f"Wrote {len(df):,} template rows and {len(df2):,} Online Retail rows to {out}/ (all synthetic).")
