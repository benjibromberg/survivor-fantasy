import os

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        f'sqlite:///{os.path.join(basedir, "survivor_fantasy.db")}'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # GitHub OAuth — create an app at https://github.com/settings/developers
    GITHUB_CLIENT_ID = os.environ.get('GITHUB_CLIENT_ID', '')
    GITHUB_CLIENT_SECRET = os.environ.get('GITHUB_CLIENT_SECRET', '')
    # Your GitHub username — only this user can log in as admin
    ADMIN_GITHUB_USERNAME = os.environ.get('ADMIN_GITHUB_USERNAME', '')
    # Enable /dev-login for local development (no OAuth needed)
    DEV_LOGIN = os.environ.get('DEV_LOGIN', '1') == '1'
