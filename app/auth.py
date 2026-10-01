from flask import Blueprint, current_app, flash, redirect, request, url_for
from flask_login import login_required, login_user, logout_user

from .models import User, db

auth_bp = Blueprint("auth", __name__)


def _get_or_create_admin(email):
    """Return the admin User for this email, creating/promoting as needed."""
    user = User.query.filter_by(username=email).first()
    if not user:
        user = User(
            username=email,
            display_name=email.split("@")[0],
            is_admin=True,
        )
        db.session.add(user)
        db.session.commit()
    elif not user.is_admin:
        user.is_admin = True
        db.session.commit()
    return user


@auth_bp.route("/login")
def login():
    """Admin login via Cloudflare Access identity.

    Cloudflare Access authenticates the visitor (email one-time PIN) at the
    edge and forwards their verified email in a request header. The origin is
    only reachable through the Cloudflare Tunnel behind Access, so this header
    is trusted. (For defence-in-depth you can additionally validate the
    `Cf-Access-Jwt-Assertion` JWT against the team's public keys.)
    """
    admin_email = current_app.config["ADMIN_EMAIL"]
    if not admin_email:
        flash("Admin login is not configured (set ADMIN_EMAIL).", "error")
        return redirect(url_for("main.index"))

    header = current_app.config["ACCESS_EMAIL_HEADER"]
    email = (request.headers.get(header) or "").lower()
    if not email:
        flash(
            "No Cloudflare Access identity found — open the site through its "
            "Access-protected URL.",
            "error",
        )
        return redirect(url_for("main.index"))
    if email != admin_email:
        flash("You are not authorized to log in as admin.", "error")
        return redirect(url_for("main.index"))

    user = _get_or_create_admin(email)
    login_user(user)
    flash(f"Logged in as {user.display_name or user.username}!", "success")
    return redirect(url_for("main.index"))


@auth_bp.route("/dev-login")
def dev_login():
    """Dev-only: log in as admin without Access. Controlled by DEV_LOGIN config."""
    if not current_app.config.get("DEV_LOGIN"):
        flash("Dev login is disabled.", "error")
        return redirect(url_for("main.index"))

    admin = User.query.filter_by(is_admin=True).first()
    if not admin:
        flash("No admin user found. Run seed.py first.", "error")
        return redirect(url_for("main.index"))

    login_user(admin)
    flash(f"Dev login as {admin.display_name or admin.username}!", "success")
    return redirect(url_for("main.index"))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.index"))
