import logging

from flask import Blueprint, current_app, flash, redirect, request, url_for
from flask_login import login_required, login_user, logout_user
from sqlalchemy.exc import SQLAlchemyError

from .models import User, db, normalize_email

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


def _resolve_user(email, is_admin_email):
    """Return the User this Access email logs in as, or None if it is unlinked.

    A player is whoever the admin linked this email to. The admin email also
    falls back to the row keyed on the email itself (how the admin was stored
    before emails could be linked to players) and is created on first login.
    That fallback row is deliberately left without `email`, so the admin can
    still link the address to their own player row.
    """
    user = User.query.filter_by(email=email).first()
    if user is None and is_admin_email:
        user = User.query.filter_by(username=email).first()
        if user is None:
            user = User(username=email, display_name=email.split("@")[0])
            db.session.add(user)
    return user


@auth_bp.route("/login")
def login():
    """Log in via Cloudflare Access identity, as a player or as the admin.

    Cloudflare Access authenticates the visitor (email one-time PIN) at the
    edge and forwards their verified email in a request header. The origin is
    only reachable through the Cloudflare Tunnel behind Access, so this header
    is trusted. (For defence-in-depth you can additionally validate the
    `Cf-Access-Jwt-Assertion` JWT against the team's public keys.)

    The email logs in as the player it is linked to. ADMIN_EMAIL is the sole
    admin: the admin flag is set from it on every login, so a stale flag in
    the database never grants admin access.
    """
    header = current_app.config["ACCESS_EMAIL_HEADER"]
    email = normalize_email(request.headers.get(header))
    if not email:
        flash(
            "No Cloudflare Access identity found — open the site through its "
            "Access-protected URL.",
            "error",
        )
        return redirect(url_for("main.index"))

    admin_email = current_app.config["ADMIN_EMAIL"]
    is_admin_email = bool(admin_email) and email == admin_email

    try:
        user = _resolve_user(email, is_admin_email)
        if user is None:
            flash(
                f"{email} is not linked to a player yet. Ask the league admin "
                "to link it.",
                "error",
            )
            return redirect(url_for("main.index"))
        if bool(user.is_admin) != is_admin_email:
            user.is_admin = is_admin_email
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Login failed for an Access-authenticated email")
        flash("Login failed. Try again.", "error")
        return redirect(url_for("main.index"))

    login_user(user)
    flash(f"Logged in as {user.display_name or user.username}!", "success")
    return redirect(url_for("main.index"))


@auth_bp.route("/dev-login")
def dev_login():
    """Dev-only: log in without Access. Controlled by DEV_LOGIN config.

    Logs in as the admin, or as the player named by `?user=<username>`.
    """
    if not current_app.config.get("DEV_LOGIN"):
        flash("Dev login is disabled.", "error")
        return redirect(url_for("main.index"))

    username = request.args.get("user", "").strip()
    if username:
        user = User.query.filter_by(username=username).first()
        if not user:
            flash(f'No player with username "{username}".', "error")
            return redirect(url_for("main.index"))
    else:
        user = User.query.filter_by(is_admin=True).first()
        if not user:
            flash("No admin user found. Run seed.py first.", "error")
            return redirect(url_for("main.index"))

    login_user(user)
    flash(f"Dev login as {user.display_name or user.username}!", "success")
    return redirect(url_for("main.index"))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.index"))
