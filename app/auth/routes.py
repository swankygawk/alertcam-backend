from flask import request, jsonify, current_app
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required, get_jwt_identity
from . import auth_bp
from .. import db
from ..models import User

@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json()

    if not data:
        current_app.logger.warning('Registration attempt with no data')
        return jsonify({'msg': 'No input data provided'}), 400

    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        current_app.logger.warning(f'Registration attempt with missing username and/or password. Username: {username}')
        return jsonify({'msg': 'Missing username and/or password'}), 400

    if User.query.filter_by(username=username).first():
        current_app.logger.warning(f'Registration attempt for existing username: {username}')
        return jsonify({'msg': 'Username already taken'}), 409

    try:
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        current_app.logger.info(f'User {username} registered successfully')
        return jsonify({'msg': 'User created successfully', 'user_id': new_user.id}), 201
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error during user registration for {username}: {e}')
        return jsonify({'msg': 'An error occured during registration'}), 500

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()

    if not data:
        current_app.logger.warning('Login attempt with no data')
        return jsonify({'msg': 'No input data provided'}), 400

    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        current_app.logger.warning(f'Login attempt with missing username and/or password. Username: {username}')
        return jsonify({'msg': 'Missing username and/or password'}), 400

    user = User.query.filter_by(username=username).first()

    if not user:
        current_app.logger.warning(f'Login attempt for non-existing username: {username}')
        return jsonify({'msg': 'User does not exist'}), 401
    if user.check_password(password):
        current_app.logger.info(f'User {username} (ID: {user.id}) logged in successfully')
        access_token = create_access_token(identity=str(user.id))
        refresh_token = create_refresh_token(identity=str(user.id))
        return jsonify(access_token=access_token, refresh_token=refresh_token), 200
    else:
        current_app.logger.warning(f'Failed login attempt for username {username}')
        return jsonify({'msg': 'Incorrect password'}), 401

@auth_bp.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh_access_token():
    current_user_id = get_jwt_identity()

    new_access_token = create_access_token(identity=str(current_user_id))
    current_app.logger.info(f'Tokens refreshed for user ID: {current_user_id}')
    return jsonify(access_token=new_access_token), 200
