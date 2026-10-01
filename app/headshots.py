"""Serve self-hosted, resized headshots from the data volume.

Filenames are content hashes (see app.data._store_headshot), so a changed image
gets a new URL and the old one can be cached forever.
"""

from flask import Blueprint, send_from_directory

from .data import HEADSHOT_URL_PREFIX, headshots_dir

headshots_bp = Blueprint("headshots", __name__)

IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


@headshots_bp.route(f"{HEADSHOT_URL_PREFIX}/<int:season>/<filename>")
def headshot(season, filename):
    resp = send_from_directory(
        headshots_dir(), f"{season}/{filename}", mimetype="image/webp"
    )
    resp.headers["Cache-Control"] = IMMUTABLE_CACHE_CONTROL
    return resp
