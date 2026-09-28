"""Page routes. Pages are thin shells; each loads its data from the JSON API."""
from flask import Blueprint, render_template

from .auth import login_required

bp = Blueprint("views", __name__)

PAGES = [
    ("overview", "/", "Overview"),
    ("products", "/products", "Products"),
    ("portfolio", "/portfolio", "Portfolio"),
    ("risk", "/stock-risk", "Stock risk"),
    ("anomalies", "/unusual-sales", "Unusual sales"),
    ("recommendations", "/recommendations", "Recommendations"),
    ("model", "/model", "Model performance"),
    ("data", "/data", "Data"),
]


def _page(name):
    @login_required
    def view():
        return render_template(f"{name}.html", page=name, pages=PAGES)
    view.__name__ = name
    return view


for _name, _rule, _ in PAGES:
    bp.add_url_rule(_rule, _name, _page(_name))


@bp.route("/products/<int:product_id>")
@login_required
def product(product_id):
    return render_template("product.html", page="products", pages=PAGES, product_id=product_id)
