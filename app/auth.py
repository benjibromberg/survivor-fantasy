import logging

from flask import Blueprint, current_app, flash, redirect, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import SQLAlchemyError

from .models import User, db, normalize_email

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

# Session keys: the Access email this session was signed in from, and the
# opt-out set by Logout so the next page does not sign the visitor back in
SESSION_ACCESS_EMAIL = "access_email"
SESSION_NO_AUTO_LOGIN = "no_auto_login"

# Requests that never sign anyone in: assets, and the two routes that manage
# sign-in themselves
_NO_AUTO_LOGIN_ENDPOINTS = {"static", "headshots.headshot", "auth.login", "auth.logout"}


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


def _access_email():
    """The visitor's Cloudflare Access email, normalized, or None."""
    return normalize_email(
        request.headers.get(current_app.config["ACCESS_EMAIL_HEADER"])
    )


def _sign_in(email):
    """Log in whoever this verified Access email belongs to. Returns the User or None.

    The admin flag is set from ADMIN_EMAIL every time, so a stale flag in the
    database never grants admin access. Raises SQLAlchemyError.
    """
    admin_email = current_app.config["ADMIN_EMAIL"]
    is_admin_email = bool(admin_email) and email == admin_email
    user = _resolve_user(email, is_admin_email)
    if user is None:
        return None
    if bool(user.is_admin) != is_admin_email:
        user.is_admin = is_admin_email
    db.session.commit()
    login_user(user)
    session[SESSION_ACCESS_EMAIL] = email
    return user


@auth_bp.before_app_request
def auto_login():
    """Sign visitors in from their Access email, so nobody has to click Login.

    Every visitor has already proved their email to Cloudflare Access, the
    same identity /login uses. A visitor whose email is not linked to a
    player stays anonymous and sees the public pages. If Access reports a
    different email than the one this session signed in with (a shared
    browser), the session follows Access. After Logout nothing happens until
    the visitor clicks Login again.
    """
    if request.endpoint in _NO_AUTO_LOGIN_ENDPOINTS:
        return
    email = _access_email()
    if not email or session.get(SESSION_NO_AUTO_LOGIN):
        return
    if current_user.is_authenticated:
        signed_in_as = session.get(SESSION_ACCESS_EMAIL)
        if signed_in_as is None or signed_in_as == email:
            return  # same person (or signed in another way, e.g. dev login)
        logout_user()
        session.pop(SESSION_ACCESS_EMAIL, None)
    try:
        _sign_in(email)
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Automatic sign-in failed for an Access-authenticated email")


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
    email = _access_email()
    if not email:
        flash(
            "No Cloudflare Access identity found — open the site through its "
            "Access-protected URL.",
            "error",
        )
        return redirect(url_for("main.index"))

    session.pop(SESSION_NO_AUTO_LOGIN, None)  # clicking Login turns it back on
    try:
        user = _sign_in(email)
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Login failed for an Access-authenticated email")
        flash("Login failed. Try again.", "error")
        return redirect(url_for("main.index"))
    if user is None:
        flash(
            f"{email} is not linked to a player yet. Ask the league admin to link it.",
            "error",
        )
        return redirect(url_for("main.index"))

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
    session.pop(SESSION_ACCESS_EMAIL, None)
    # Otherwise the next page would sign the visitor straight back in
    session[SESSION_NO_AUTO_LOGIN] = True
    return redirect(url_for("main.index"))
