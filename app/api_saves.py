"""Saves API: list, start a new save, load, rename and delete."""
from flask import Blueprint, current_app, g, jsonify, request

from . import saves
from .auth import api_login_required
from .db import get_db

bp = Blueprint("api_saves", __name__, url_prefix="/api/saves")


def _call(fn, *args):
    try:
        return jsonify(fn(get_db(), g.user["id"], *args))
    except saves.SaveError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("")
@api_login_required
def list_():
    return jsonify(saves.list_saves(get_db(), g.user["id"]))


@bp.post("")
@api_login_required
def create():
    return _call(saves.create, (request.get_json(force=True) or {}).get("name", ""))


@bp.post("/<int:save_id>/load")
@api_login_required
def load(save_id):
    return _call(saves.load, save_id)


@bp.patch("/<int:save_id>")
@api_login_required
def rename(save_id):
    return _call(saves.rename, save_id, (request.get_json(force=True) or {}).get("name", ""))


@bp.delete("/<int:save_id>")
@api_login_required
def delete(save_id):
    try:
        return jsonify(saves.delete(get_db(), g.user["id"], save_id, current_app.config.get("MODEL_DIR")))
    except saves.SaveError as e:
        return jsonify({"error": str(e)}), 400
