# import os
from datetime import datetime, timezone
from threading import Thread
# import asyncio
from multiprocessing import freeze_support, Process, Manager
from app import create_app
from app import db
from app.api.routes import initialize_shared_data
from app.detection.detector import detect_vehicles
from app.event_processor import event_processor_worker
from app.video_writer import video_writer_worker
from app.models import Alarm
from app.telegram_bot import run_telegram_bot
# from dotenv import load_dotenv

# dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
# if os.path.exists(dotenv_path):
#     load_dotenv(dotenv_path)

flask_app = create_app()

if __name__ == '__main__':
    freeze_support() # for running on Windows

    with Manager() as manager:
        last_processed_bboxes_shared = manager.list([None, 0.0])
        active_alarms_shared = manager.dict()
        event_queue_shared = manager.Queue()
        running_flag_shared = manager.Value('b', True)
        video_writer_queue_shared = manager.Queue()
        # alarms_lock_shared = manager.Lock()

        with flask_app.app_context():
            initialize_shared_data(last_processed_bboxes_shared, active_alarms_shared)
            flask_app.logger.info('Deactivating all previously active alarms due to system restart...')
            updated_count = Alarm.query.filter_by(is_active=True).update({
                Alarm.is_active: False,
                Alarm.unset_at: datetime.now(timezone.utc)
            })
            db.session.commit()
            if updated_count > 0:
                flask_app.logger.info(f'Deactivated {updated_count} alarm(s)')

        detector_config = {
            'rtsp_source': flask_app.config.get('RTSP_SOURCE'),
            'yolo_model_path': flask_app.config.get('YOLO_MODEL_PATH'),
            'img_height': flask_app.config.get('YOLO_IMG_HEIGHT'),
            'img_width': flask_app.config.get('YOLO_IMG_WIDTH'),
            'conf_thresh': flask_app.config.get('YOLO_CONF_THRESH'),
            'iou_thresh': flask_app.config.get('YOLO_IOU_THRESH'),
            'verbose': flask_app.config.get('YOLO_VERBOSE'),
            'detection_time_window': flask_app.config.get('DETECTION_TIME_WINDOW'),
            'detection_min_distance': flask_app.config.get('DETECTION_MIN_DISTANCE'),
            'disappearance_thresh_s': flask_app.config.get('DISAPPEARANCE_THRESH_S'),
            'detector_debug_draw': flask_app.config.get('DETECTOR_DEBUG_DRAW'),
            'log_level': flask_app.config.get('LOG_LEVEL'),
            'video_save_path': flask_app.config.get('VIDEO_SAVE_PATH'),
            'video_fps': flask_app.config.get('VIDEO_FPS'),
            'camera_fps': flask_app.config.get('CAMERA_FPS'),
            'video_seconds_before_event': flask_app.config.get('VIDEO_SECONDS_BEFORE_EVENT'),
            'video_seconds_after_event': flask_app.config.get('VIDEO_SECONDS_AFTER_EVENT')
        }

        flask_app.logger.info('Starting detection process...')
        detection_process = Process(
            target=detect_vehicles,
            args=(
                running_flag_shared,
                detector_config,
                last_processed_bboxes_shared,
                active_alarms_shared,
                event_queue_shared,
                video_writer_queue_shared
                # alarms_lock_shared
            ),
            name='VehicleDetectorProcess'
        )
        detection_process.start()

        flask_app.logger.info('Starting Event Processor Worker thread...')
        event_processor_thread = Thread(
            target=event_processor_worker,
            args=(
                flask_app,
                event_queue_shared,
                active_alarms_shared,
                running_flag_shared
            ),
            name='EventProcessorThread'
        )
        event_processor_thread.daemon = True
        event_processor_thread.start()

        if flask_app.config.get('VIDEO_SAVE_PATH'):
            flask_app.logger.info('Starting Video Writer worker proces...')
            video_writer_process = Process(
                target=video_writer_worker,
                args=(
                    flask_app.config.get('SQLALCHEMY_DATABASE_URI'),
                    flask_app.config.get('LOG_LEVEL', 'INFO'),
                    video_writer_queue_shared,
                    running_flag_shared
                ),
                name='VideoWriterProcess'
            )
            video_writer_process.daemon = True
            video_writer_process.start()
        else:
            flask_app.logger.warning('VIDEO_SAVE_PATH is not set up. Video Writer worker will not start')
            video_writer_process = None

        if flask_app.config.get('TELEGRAM_BOT_TOKEN'):
            flask_app.logger.info('Starting Telegram Bot thread...')
            telegram_bot_thread = Thread(
                target=run_telegram_bot,
                args=(
                    flask_app,
                    running_flag_shared,
                    active_alarms_shared
                ),
                name='TelegramBotThread'
            )
            telegram_bot_thread.daemon = True
            telegram_bot_thread.start()
        else:
            flask_app.logger.warning('TELEGRAM_BOT_TOKEN was not found. The bot will not start')
            telegram_bot_thread = None

        host = flask_app.config.get('FLASK_RUN_HOST', '127.0.0.1')
        port = flask_app.config.get('FLASK_RUN_PORT', '5000')
        debug = flask_app.config.get('FLASK_DEBUG', False)

        try:
            flask_app.logger.info(f'Starting Flask app on http://{host}:{port}/')
            flask_app.logger.info(f'Flask Debug Mode: {debug}')
            # flask_app.logger.info(f'Reloader: {'Enabled' if debug else 'Disabled'}')
            flask_app.logger.info(f'Reloader: Disabled')
            flask_app.logger.info(f'Threaded: True')

            flask_app.run(host=host, port=port, threaded=True, debug=debug, use_reloader=False)
        except KeyboardInterrupt:
            flask_app.logger.info('Flask app interrupted by user')
        finally:
            flask_app.logger.info('Shutting down application...')

            flask_app.logger.info('Signaling all worker processes/threads to stop...')
            running_flag_shared.value = False

            flask_app.logger.info('Waiting for detection process to join...')
            detection_process.join(timeout=10)

            flask_app.logger.info('Waiting for Event Processor Worker thread to join...')
            event_processor_thread.join(timeout=10)

            if detection_process.is_alive():
                flask_app.logger.warning('Detection process did not join in time, terminating...')
                detection_process.terminate()
                detection_process.join(timeout=5)
                if detection_process.is_alive():
                    flask_app.logger.error('Detection process could not be terminated')
            else:
                flask_app.logger.info('Detection process finished gracefully')

            if event_processor_thread.is_alive():
                event_processor_thread.join(timeout=5)
            if event_processor_thread.is_alive():
                flask_app.logger.warning('Event Processor Worker thread did not join in time')
            else:
                flask_app.logger.info('Event Processor Worker thread finished')

            if video_writer_process and video_writer_process.is_alive():
                flask_app.logger.info('Waiting for Video Writer worker process to join...')
                video_writer_process.join(timeout=10)
            if video_writer_process and video_writer_process.is_alive():
                flask_app.logger.warning('Video Writer worker process did not join in time, terminating...')
                video_writer_process.terminate()
                video_writer_process.join(timeout=5)
            elif video_writer_process:
                flask_app.logger.info('Video Writer worker process finished')

            if telegram_bot_thread and telegram_bot_thread.is_alive():
                flask_app.logger.info('Waiting for Telegram Bot thread to join...')
                telegram_bot_thread.join(timeout=10)
                if telegram_bot_thread.is_alive():
                    flask_app.logger.warning('Telegram Bot thread did not join in time')
                else:
                    flask_app.logger.info('Telegram Bot thread finished')

            flask_app.logger.info('Application shutdown complete')