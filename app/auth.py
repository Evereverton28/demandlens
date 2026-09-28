"""Accounts and sessions.

SIMS trusted a user_id sent by the browser, so anyone could read another
user's data by changing a number. Here identity lives in a signed server-side
session cookie and every query is scoped to session["user_id"].
"""
import functools
import sqlite3

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)


@bp.before_app_request
def load_user():
    uid = session.get("user_id")
    g.user = None
    if uid is not None:
        g.user = get_db().execute("SELECT id, username, email FROM users WHERE id=?", (uid,)).fetchone()
        if g.user is None:
            session.clear()


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def api_login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return jsonify(error="Sign in to continue."), 401
        return view(*args, **kwargs)
    return wrapped


def create_user(db, username: str, email: str, password: str) -> int:
    cur = db.execute("INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                     (username, email, generate_password_hash(password)))
    db.execute("INSERT INTO user_settings (user_id) VALUES (?)", (cur.lastrowid,))
    db.commit()
    return cur.lastrowid


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        error = None
        if not username or not email or not password:
            error = "Fill in your username, email and password."
        elif len(password) < 8:
            error = "Use a password of at least 8 characters."
        elif password != confirm:
            error = "The two passwords don't match."
        if error is None:
            try:
                uid = create_user(get_db(), username, email, password)
            except sqlite3.IntegrityError:
                error = "That username or email is already registered."
            else:
                session.clear()
                session["user_id"] = uid
                return redirect(url_for("views.data"))
        flash(error)
    return render_template("auth.html", mode="signup")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user is None or not check_password_hash(user["password"], password):
            flash("The username or password is incorrect.")
        else:
            session.clear()
            session["user_id"] = user["id"]
            nxt = request.args.get("next")
            return redirect(nxt if nxt and nxt.startswith("/") else url_for("views.overview"))
    return render_template("auth.html", mode="login")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
