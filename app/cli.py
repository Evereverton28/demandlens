"""Command-line tools:  flask --app run <command>

  create-user      create an account
  import-data      import a sales file into a user's ledger
  create-scenario  add simulated stock levels (for datasets without stock data)
  analyse          run the full analysis
  tune             tune model hyperparameters with time-series cross-validation
  migrate-sims     bring data over from the old SIMS inventory.db
"""
import json
import time

import click

from .db import connect


def _user(conn, username):
    row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if row is None:
        raise click.ClickException(f"No user called {username}. Create one with: flask --app run create-user")
    return row["id"]


def register(app):
    db_path = lambda: app.config["DATABASE"]

    @app.cli.command("create-user")
    @click.argument("username")
    @click.argument("email")
    @click.password_option()
    def create_user_cmd(username, email, password):
        from .auth import create_user
        conn = connect(db_path())
        uid = create_user(conn, username, email, password)
        click.echo(f"Created user {username} (id {uid}).")

    @app.cli.command("import-data")
    @click.argument("path", type=click.Path(exists=True))
    @click.option("--user", "username", required=True)
    @click.option("--format", "fmt", type=click.Choice(["online_retail", "generic"]), default="online_retail")
    @click.option("--synthetic", is_flag=True, help="Flag the data as synthetic test data.")
    def import_cmd(path, username, fmt, synthetic):
        from .importer import import_file
        conn = connect(db_path())
        t = time.time()
        rep = import_file(conn, _user(conn, username), path, fmt, synthetic, progress=click.echo)
        click.echo(json.dumps(rep, indent=2))
        click.echo(f"Finished in {time.time() - t:.0f} s.")

    @app.cli.command("create-scenario")
    @click.option("--user", "username", required=True)
    @click.option("--seed", default=7)
    def scenario_cmd(username, seed):
        from .importer import create_scenario
        conn = connect(db_path())
        click.echo(create_scenario(conn, _user(conn, username), seed))

    @app.cli.command("analyse")
    @click.option("--user", "username", required=True)
    def analyse_cmd(username):
        from analytics.pipeline import run_analysis
        conn = connect(db_path())
        t = time.time()
        run_id = run_analysis(conn, _user(conn, username), progress=lambda m: click.echo(f"[{time.time() - t:5.0f}s] {m}"),
                              model_dir=app.config["MODEL_DIR"])
        click.echo(f"Run {run_id} completed in {time.time() - t:.0f} s.")

    @app.cli.command("tune")
    @click.option("--user", "username", required=True)
    def tune_cmd(username):
        from analytics.tuning import tune
        conn = connect(db_path())
        best = tune(conn, _user(conn, username), model_dir=app.config["MODEL_DIR"], progress=click.echo)
        click.echo("Saved. The next analysis run will use these settings.")
        click.echo(json.dumps(best, indent=2))

    @app.cli.command("migrate-sims")
    @click.argument("path", type=click.Path(exists=True))
    def migrate_cmd(path):
        from .importer import migrate_sims
        conn = connect(db_path())
        click.echo(migrate_sims(conn, path))
