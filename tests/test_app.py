"""The web application end to end: accounts, data isolation, the ledger, import and a full analysis."""
import io

from analytics.synthetic import generic
from conftest import signup


def test_pages_require_sign_in(client):
    assert client.get("/").status_code == 302
    assert client.get("/api/overview").status_code == 401


def test_users_cannot_see_each_others_data(app):
    a, b = app.test_client(), app.test_client()
    signup(a, "alice"); signup(b, "bob")
    pid = a.post("/api/catalogue", json={"sku": "P1", "name": "Pen", "opening_stock": 10}).get_json()["product_id"]
    assert b.get(f"/api/products/{pid}").status_code == 404
    assert b.post("/api/movements", json={"product_id": pid, "type": "SALE", "quantity": 1}).status_code == 404
    assert b.get("/api/catalogue").get_json()["total"] == 0


def test_ledger_validation(client):
    signup(client)
    pid = client.post("/api/catalogue", json={"sku": "P1", "name": "Pen", "selling_price": 20, "opening_stock": 5}).get_json()["product_id"]
    bad = client.post("/api/movements", json={"product_id": pid, "type": "SALE", "quantity": -3})
    assert bad.status_code == 400
    too_many = client.post("/api/movements", json={"product_id": pid, "type": "SALE", "quantity": 6})
    assert too_many.status_code == 400 and "Only 5 in stock" in too_many.get_json()["error"]
    ok = client.post("/api/movements", json={"product_id": pid, "type": "SALE", "quantity": 2})
    assert ok.status_code == 201 and ok.get_json()["stock_on_hand"] == 3
    zero = client.put(f"/api/catalogue/{pid}", json={"name": "Pen", "selling_price": 0})   # SIMS could not save 0
    assert zero.status_code == 200


def test_import_and_full_analysis(client):
    signup(client)
    df, truth = generic(n_products=12, n_weeks=60, seed=5)
    data = {"file": (io.BytesIO(df.to_csv(index=False).encode()), "synthetic.csv"), "format": "generic", "synthetic": "1"}
    job = client.post("/api/imports", data=data, content_type="multipart/form-data").get_json()
    assert client.get(f"/api/jobs/{job['job_id']}").get_json()["status"] == "done"

    run = client.post("/api/analysis/run").get_json()
    status = client.get(f"/api/jobs/{run['job_id']}").get_json()
    assert status["status"] == "done", status["message"]

    s = client.get("/api/status").get_json()
    assert s["synthetic"] is True and s["run"] is not None
    model = client.get("/api/model").get_json()
    assert set(model["summary"]["overall"]) >= {"seasonal_naive", "moving_average", "ses", "sba"}
    for path in ("/api/overview", "/api/products", "/api/portfolio", "/api/risk", "/api/anomalies", "/api/recommendations"):
        assert client.get(path).status_code == 200, path
    products = client.get("/api/products?sort=name").get_json()["items"]
    detail = client.get(f"/api/products/{products[0]['product_id']}").get_json()
    assert len(detail["forecast"]) == 4 and all(f["p90"] >= f["p50"] for f in detail["forecast"])
    for page in ("/", "/products", "/portfolio", "/stock-risk", "/unusual-sales", "/recommendations", "/model", "/data"):
        assert client.get(page).status_code == 200, page


def test_switching_datasets_in_one_account(client):
    """Import one dataset, analyse, delete it, import another: no trace of the first remains."""
    signup(client)
    first, _ = generic(n_products=6, n_weeks=40, seed=1)
    up = {"file": (io.BytesIO(first.to_csv(index=False).encode()), "first.csv"), "format": "generic"}
    client.post("/api/imports", data=up, content_type="multipart/form-data")
    client.post("/api/analysis/run")
    assert client.get("/api/status").get_json()["run"] is not None
    batch = client.get("/api/imports").get_json()[0]["batch_id"]

    removed = client.delete(f"/api/imports/{batch}").get_json()
    assert removed["products_removed"] == 6 and removed["runs_cleared"] == 1
    status = client.get("/api/status").get_json()
    assert status["products"] == 0 and status["movements"] == 0 and status["run"] is None
    assert client.get("/api/overview").get_json()["run"] is None        # old results are gone

    second, _ = generic(n_products=5, n_weeks=40, seed=9)
    second["sku"] = "NEW" + second["sku"]
    up = {"file": (io.BytesIO(second.to_csv(index=False).encode()), "second.csv"), "format": "generic"}
    client.post("/api/imports", data=up, content_type="multipart/form-data")
    assert client.post("/api/analysis/run").status_code == 202
    items = client.get("/api/products").get_json()["items"]
    assert len(items) == 5 and all(i["sku"].startswith("NEW") for i in items)


def test_clear_all_data_needs_confirmation(client):
    signup(client)
    client.post("/api/catalogue", json={"sku": "P1", "name": "Pen", "opening_stock": 4})
    assert client.post("/api/data/clear", json={"confirm": "yes"}).status_code == 400
    assert client.post("/api/data/clear", json={"confirm": "DELETE"}).status_code == 200
    assert client.get("/api/status").get_json()["products"] == 0
