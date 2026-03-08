import secrets

import requests as http_requests
from flask import (Blueprint, redirect, url_for, flash, session,
                   current_app, request)
from flask_login import login_user, logout_user, login_required

from .models import db, User

auth_bp = Blueprint('auth', __name__)

GITHUB_AUTHORIZE_URL = 'https://github.com/login/oauth/authorize'
GITHUB_TOKEN_URL = 'https://github.com/login/oauth/access_token'
GITHUB_USER_URL = 'https://api.github.com/user'


@auth_bp.route('/login')
def login():
    client_id = current_app.config['GITHUB_CLIENT_ID']
    if not client_id:
        flash('GitHub OAuth not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET.', 'error')
        return redirect(url_for('main.index'))

    state = secrets.token_urlsafe(32)
    session['oauth_state'] = state

    return redirect(
        f'{GITHUB_AUTHORIZE_URL}?client_id={client_id}&state={state}&scope=read:user'
    )


@auth_bp.route('/auth/callback')
def github_callback():
    # Verify state to prevent CSRF
    request_state = request.args.get('state')
    if request_state != session.pop('oauth_state', None):
        flash('OAuth state mismatch.', 'error')
        return redirect(url_for('main.index'))

    code = request.args.get('code')
    if not code:
        flash('GitHub login cancelled.', 'error')
        return redirect(url_for('main.index'))

    # Exchange code for access token
    resp = http_requests.post(GITHUB_TOKEN_URL, json={
        'client_id': current_app.config['GITHUB_CLIENT_ID'],
        'client_secret': current_app.config['GITHUB_CLIENT_SECRET'],
        'code': code,
    }, headers={'Accept': 'application/json'}, timeout=10)

    token_data = resp.json()
    access_token = token_data.get('access_token')
    if not access_token:
        flash('Failed to get access token from GitHub.', 'error')
        return redirect(url_for('main.index'))

    # Fetch GitHub user info
    user_resp = http_requests.get(GITHUB_USER_URL, headers={
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json',
    }, timeout=10)
    github_user = user_resp.json()
    github_username = github_user.get('login', '')

    # Check if this user is the allowed admin
    admin_username = current_app.config['ADMIN_GITHUB_USERNAME']
    if github_username.lower() != admin_username.lower():
        flash('You are not authorized to log in.', 'error')
        return redirect(url_for('main.index'))

    # Find or create admin user
    user = User.query.filter_by(github_username=github_username).first()
    if not user:
        user = User(
            username=github_username,
            github_username=github_username,
            display_name=github_user.get('name') or github_username,
            is_admin=True,
        )
        db.session.add(user)
        db.session.commit()

    login_user(user)
    flash(f'Logged in as {user.display_name or user.username}!', 'success')
    return redirect(url_for('main.index'))


@auth_bp.route('/dev-login')
def dev_login():
    """Dev-only: log in as admin without OAuth. Controlled by DEV_LOGIN config."""
    if not current_app.config.get('DEV_LOGIN'):
        flash('Dev login is disabled.', 'error')
        return redirect(url_for('main.index'))

    admin = User.query.filter_by(is_admin=True).first()
    if not admin:
        flash('No admin user found. Run seed.py first.', 'error')
        return redirect(url_for('main.index'))

    login_user(admin)
    flash(f'Dev login as {admin.display_name or admin.username}!', 'success')
    return redirect(url_for('main.index'))


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.index'))
