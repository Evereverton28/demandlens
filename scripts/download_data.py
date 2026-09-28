"""Download the UCI Online Retail II dataset (Chen, 2012; CC BY 4.0).

    python scripts/download_data.py

Saves data/online_retail_II.xlsx (about 45 MB), then import it with:
    flask --app run import-data data/online_retail_II.xlsx --user <you> --format online_retail

If the download fails, get the file manually from
https://archive.ics.uci.edu/dataset/502/online+retail+ii  (DOI 10.24432/C5CG6D)
and place the .xlsx in the data/ folder. Reading the Excel file takes a few
minutes; saving it once as CSV makes later imports much faster.
"""
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
out = Path(__file__).resolve().parents[1] / "data"
out.mkdir(exist_ok=True)
target = out / "online_retail_II.xlsx"
if target.exists():
    sys.exit(f"{target} already exists.")
print(f"Downloading {URL} ...")
try:
    with urllib.request.urlopen(URL, timeout=120) as r:
        payload = r.read()
except Exception as exc:
    sys.exit(f"Download failed ({exc}). Download it manually - see the instructions at the top of this script.")
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    name = next(n for n in z.namelist() if n.lower().endswith(".xlsx"))
    target.write_bytes(z.read(name))
print(f"Saved {target} ({target.stat().st_size / 1e6:.0f} MB).")

if "--csv" in sys.argv:
    import pandas as pd
    print("Converting to CSV (both sheets) ...")
    sheets = pd.read_excel(target, sheet_name=None, dtype={"Invoice": str, "StockCode": str})
    pd.concat(sheets.values(), ignore_index=True).to_csv(out / "online_retail_II.csv", index=False)
    print(f"Saved {out / 'online_retail_II.csv'}.")
