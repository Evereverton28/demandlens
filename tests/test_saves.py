"""Saves: several datasets per account, each with its own analysis, switched without re-running."""
import io
import sqlite3

from analytics.synthetic import generic
from conftest import signup


def _import(client, n, seed, prefix):
    df, _ = generic(n_products=n, n_weeks=40, seed=seed)
    df["sku"] = prefix + df["sku"]
    up = {"file": (io.BytesIO(df.to_csv(index=False).encode()), f"{prefix}.csv"), "format": "generic"}
    assert client.post("/api/imports", data=up, content_type="multipart/form-data").status_code in (200, 202)


def _skus(client):
    return {i["sku"] for i in client.get("/api/catalogue?per_page=100").get_json()["items"]}


def _runs(app):
    with sqlite3.connect(app.config["DATABASE"]) as c:
        return c.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0]


def test_saves_keep_datasets_apart_and_load_without_rerunning(app):
    client = app.test_client()
    signup(client)
    _import(client, 6, 1, "SHOP")
    client.post("/api/analysis/run")
    runs = _runs(app)
    first = client.get("/api/saves").get_json()
    assert len(first) == 1 and first[0]["is_active"] and first[0]["products"] == 6

    # a new save is an empty workspace; the first one is untouched
    assert client.post("/api/saves", json={"name": "Pharmacy"}).status_code == 200
    status = client.get("/api/status").get_json()
    assert status["products"] == 0 and status["run"] is None
    _import(client, 5, 9, "PHARM")
    assert all(s.startswith("PHARM") for s in _skus(client)) and len(_skus(client)) == 5

    # loading the first save brings back its data and its analysis, without running anything
    saves = {s["name"]: s for s in client.get("/api/saves").get_json()}
    assert client.post(f"/api/saves/{saves['My first save']['save_id']}/load").status_code == 200
    assert all(s.startswith("SHOP") for s in _skus(client)) and len(_skus(client)) == 6
    assert client.get("/api/overview").get_json()["run"] is not None
    assert _runs(app) == runs

    # the loaded save cannot be deleted; another one can, and it takes everything with it
    first_id = saves["My first save"]["save_id"]
    assert client.delete(f"/api/saves/{first_id}").status_code == 400
    assert client.delete(f"/api/saves/{saves['Pharmacy']['save_id']}").status_code == 200
    assert [s["name"] for s in client.get("/api/saves").get_json()] == ["My first save"]
    with sqlite3.connect(app.config["DATABASE"]) as c:
        assert c.execute("SELECT COUNT(*) FROM products WHERE sku LIKE 'PHARM%'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM users WHERE username LIKE '\\_\\_save%' ESCAPE '\\'").fetchone()[0] == 0
    assert len(_skus(client)) == 6


def test_saves_are_private_and_hidden_owners_cannot_sign_in(app):
    a, b = app.test_client(), app.test_client()
    signup(a, "alice"); signup(b, "bob")
    a.post("/api/saves", json={"name": "Alice extra"})
    alice_save = [s for s in a.get("/api/saves").get_json() if s["name"] == "Alice extra"][0]["save_id"]
    assert b.post(f"/api/saves/{alice_save}/load").status_code == 400
    assert b.delete(f"/api/saves/{alice_save}").status_code == 400
    with sqlite3.connect(app.config["DATABASE"]) as c:
        hidden = c.execute("SELECT username FROM users WHERE username LIKE '\\_\\_save%' ESCAPE '\\'").fetchone()[0]
    r = app.test_client().post("/login", data={"username": hidden, "password": "!"})
    assert b"incorrect" in r.data or r.status_code == 200 and b"Sign out" not in r.data
    r = app.test_client().post("/signup", data={"username": "__sneaky", "email": "s@x.com", "password": "password123",
                                               "confirm": "password123"})
    assert b"two underscores" in r.data


def test_save_names_are_checked(client):
    signup(client)
    assert client.post("/api/saves", json={"name": "  "}).status_code == 400
    assert client.post("/api/saves", json={"name": "Shop A"}).status_code == 200
    assert client.post("/api/saves", json={"name": "shop a"}).status_code == 400
    sid = [s for s in client.get("/api/saves").get_json() if s["name"] == "My first save"][0]["save_id"]
    assert client.patch(f"/api/saves/{sid}", json={"name": "Stationery"}).get_json()["name"] == "Stationery"
