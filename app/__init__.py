"""DemandLens web application."""
import os
from pathlib import Path

from flask import Flask

from .db import close_db, init_db


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("DEMANDLENS_SECRET_KEY", "change-this-secret-key"),
        DATABASE=os.environ.get("DEMANDLENS_DB", os.path.join(app.instance_path, "demandlens.db")),
        MODEL_DIR=os.path.join(app.instance_path, "models"),
        UPLOAD_DIR=os.path.join(app.instance_path, "uploads"),
        MAX_CONTENT_LENGTH=200 * 1024 * 1024,
        RUN_JOBS_INLINE=False,        # tests run jobs synchronously
    )
    if test_config:
        app.config.update(test_config)

    init_db(app.config["DATABASE"])
    from .jobs import recover_interrupted
    recover_interrupted(app.config["DATABASE"])
    app.teardown_appcontext(close_db)

    from . import api_analytics, api_data, auth, cli, views
    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    app.register_blueprint(api_data.bp)
    app.register_blueprint(api_analytics.bp)
    cli.register(app)
    return app
