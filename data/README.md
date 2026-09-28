# Data

* `synthetic_template.csv`: a small **synthetic** file in the DemandLens template layout (40 fictional products,
  90 weeks, with deliveries and stock counts). For a quick demonstration only; import it with the synthetic flag.
* The real dataset, UCI Online Retail II (Chen, 2012, CC BY 4.0, DOI 10.24432/C5CG6D), is not included.
  Run `python scripts/download_data.py --csv` to fetch it into this folder.
* `scripts/generate_synthetic.py` writes more synthetic files, plus the list of anomalies planted in them.
