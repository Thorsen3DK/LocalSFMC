"""
DE name mapping routes.
"""

from flask import jsonify, redirect, render_template, request, url_for

from data.excel_loader import list_all_de_names, load_de_mapping, save_de_mapping
from web.routes import bp
from web.services.content_blocks import de_dir


@bp.route("/mapping")
def mapping_page():
    """DE name mapping page."""
    current_mapping = load_de_mapping(de_dir())
    available_sheets = list_all_de_names(de_dir())
    return render_template(
        "mapping.html",
        mapping=current_mapping,
        available_sheets=available_sheets,
    )


@bp.route("/mapping/save", methods=["POST"])
def save_mapping():
    """Save DE name mappings from form."""
    aliases = request.form.getlist("alias")
    targets = request.form.getlist("target")
    mapping = {}
    for alias, target in zip(aliases, targets):
        alias = alias.strip()
        target = target.strip()
        if alias and target:
            mapping[alias] = target
    save_de_mapping(de_dir(), mapping)
    return redirect(url_for("main.mapping_page"))


@bp.route("/mapping/data")
def mapping_data():
    """AJAX endpoint — return current mapping and available sheets."""
    current_mapping = load_de_mapping(de_dir())
    available_sheets = list_all_de_names(de_dir())
    return jsonify({"mapping": current_mapping, "sheets": available_sheets})
