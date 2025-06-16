import os
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from .config import Config

db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()

def create_app(config_class=Config):
    """
    Фабрика для создания Flask-приложения.
    :param confg_class: Класс конфигурации для использования.
    :return: Экземпляр Flask-приложения.
    """

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    try:
        if not os.path.exists(app.instance_path):
            os.makedirs(app.instance_path)
        # app.config.from_pyfile('config.py', silent=True)
    except OSError:
        app.logger.error(f'Could not create instance folder at {app.instance_path}')

    if not app.debug and not app.testing:
        if not os.path.exists('logs'):
            try:
                os.mkdir('logs')
            except OSError:
                app.logger.error("Could not create logs directory")

        if os.path.exists('logs'):
            file_handler = RotatingFileHandler('logs/app.log', maxBytes=102400000, backupCount=10, encoding='utf-8')
            file_handler.setFormatter(logging.Formatter(
                '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
            ))
            file_handler.setLevel(app.config.get('LOG_LEVEL', 'INFO'))
            app.logger.addHandler(file_handler)

    if not os.path.exists(app.config.get('VIDEO_SAVE_PATH')):
        try:
            os.makedirs(app.config.get('VIDEO_SAVE_PATH'))
        except OSError:
            app.logger.error('Could not create video save directory')

    if app.config.get('LOG_TO_STDOUT'):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        stream_handler.setLevel(app.config.get('LOG_LEVEL', 'INFO'))
        app.logger.addHandler(stream_handler)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)

    app.logger.setLevel(app.config.get('LOG_LEVEL', 'INFO'))
    app.logger.info('Flask App startup')

    from . import models
    from .auth.routes import auth_bp
    app.register_blueprint(auth_bp, url_prefix='/api/auth')

    from .api.routes import api_bp
    app.register_blueprint(api_bp, url_prefix='/api')

    @app.route('/check')
    def check():
        return 'Success'

    return app