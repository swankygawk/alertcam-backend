import os
# import logging
from datetime import timedelta
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
project_root = os.path.dirname(basedir)
dotenv_path = os.path.join(project_root, '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path, override=True)

class Config:
    """Базовый класс конфигурации."""

    LOG_TO_STDOUT = os.environ.get('LOG_TO_STDOUT')
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()

    SECRET_KEY = os.environ.get('SECRET_KEY') or 'some_unsecure_key'

    FLASK_APP = os.environ.get('FLASK_APP') or 'run.py'
    FLASK_DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() in ['true', '1', 't']
    FLASK_RUN_HOST = os.environ.get('FLASK_RUN_HOST', '127.0.0.1')
    FLASK_RUN_PORT = int(os.environ.get('FLASK_RUN_PORT', 5000))

    RTSP_SOURCE = os.environ.get('RTSP_SOURCE', 'http://127.0.0.1:3393')

    YOLO_MODEL_PATH = os.environ.get('YOLO_MODEL_PATH', 'yolo11m.pt')
    YOLO_IMG_HEIGHT = int(os.environ.get('YOLO_IMG_HEIGHT', 704))
    YOLO_IMG_WIDTH = int(os.environ.get('YOLO_IMG_WIDTH', 576))
    YOLO_CONF_THRESH = float(os.environ.get('YOLO_CONF_THRESH', 0.675))
    YOLO_IOU_THRESH = float(os.environ.get('YOLO_IOU_THRESH', 0.7))
    YOLO_VERBOSE = os.environ.get('YOLO_VERBOSE', 'False').lower() in ['true', '1', 't']

    DETECTOR_DEBUG_DRAW = os.environ.get('DETECTOR_DEBUG_DRAW', 'False').lower() in ['true', '1', 't']

    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URI') or 'sqlite:///' + os.path.join(project_root, 'instance', 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = os.environ.get('SQLALCHEMY_ECHO', 'False').lower() in ['true', '1', 't']

    DETECTION_TIME_WINDOW = float(os.environ.get('DETECTION_TIME_WINDOW', 0.5))
    DETECTION_MIN_DISTANCE = int(os.environ.get('DETECTION_MIN_DISTANCE', 10))
    DISAPPEARANCE_THRESH_S = int(os.environ.get('DISAPPEARANCE_THRESH_S', 5))

    VIDEO_SAVE_PATH = os.environ.get('VIDEO_SAVE_PATH', 'instance/event_videos')
    VIDEO_FPS = int(os.environ.get('VIDEO_FPS', 10))
    CAMERA_FPS = int(os.environ.get('CAMERA_FPS', 25))
    VIDEO_SECONDS_BEFORE_EVENT = int(os.environ.get('VIDEO_SECONDS_BEFORE_EVENT', 5))
    VIDEO_SECONDS_AFTER_EVENT = int(os.environ.get('VIDEO_SECONDS_AFTER_EVENT', 15))

    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY') or 'another_really_unsecure_key'
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=1)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=30)

    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    NOTIFICATION_COOLDOWN_SECONDS = int(os.environ.get('NOTIFICATION_COOLDOWN_SECONDS', 60))