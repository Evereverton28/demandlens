import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def app(tmp_path):
    from app import create_app
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.db"), "MODEL_DIR": str(tmp_path / "models"),
                      "UPLOAD_DIR": str(tmp_path / "uploads"), "RUN_JOBS_INLINE": True, "SECRET_KEY": "test"})
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def signup(client, name="owner"):
    return client.post("/signup", data={"username": name, "email": f"{name}@example.com",
                                        "password": "password123", "confirm": "password123"})
