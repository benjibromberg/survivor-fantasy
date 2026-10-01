import os

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    _secret = os.environ.get("SECRET_KEY")
    _is_prod = os.environ.get("DEV_LOGIN", "1") == "0"
    if not _secret and _is_prod:
        raise RuntimeError("SECRET_KEY must be set in production (add to .env)")
    SECRET_KEY = _secret or "dev-secret-only"
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(basedir, 'survivor_fantasy.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Admin login via Cloudflare Access (email OTP). ADMIN_EMAIL is the
    # Access-authenticated email that is treated as admin. Cloudflare Access
    # forwards the verified email in ACCESS_EMAIL_HEADER; the origin is only
    # reachable through the tunnel behind Access, so the header is trusted.
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").lower()
    ACCESS_EMAIL_HEADER = os.environ.get(
        "ACCESS_EMAIL_HEADER", "Cf-Access-Authenticated-User-Email"
    )
    # Enable /dev-login for local development (no Access in front)
    DEV_LOGIN = os.environ.get("DEV_LOGIN", "1") == "1"
