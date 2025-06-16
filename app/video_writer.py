import cv2
import queue
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

video_writer_logger = logging.getLogger('VideoWriterProcess')

def setup_video_writer_logging(level_str='INFO'):
    level = getattr(logging, level_str.upper(), logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    video_writer_logger.addHandler(handler)
    video_writer_logger.setLevel(level)
    video_writer_logger.propagate = False

def video_writer_worker(
    db_uri: str,
    log_level: str,
    video_writer_queue_shared,
    running_flag_shared
):
    setup_video_writer_logging(log_level)
    video_writer_logger.info('Video Writer worker started')

    engine = None
    SessionLocal = None
    if db_uri:
        try:
            engine = create_engine(db_uri)
            SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
            video_writer_logger.info(f'DB engine created for Video Writer using URI: {db_uri}')
        except Exception as e_engine:
            video_writer_logger.error(f'Failed to create DB engine for Video Writer: {e_engine}', exc_info=True)
    else:
        video_writer_logger.warning('DB URI not provided to Video Writer. Video will not be written. Terminating...')
        return

    fourcc = cv2.VideoWriter.fourcc(*'mp4v')
    while running_flag_shared.value:
        try:
            video_task = video_writer_queue_shared.get(timeout=1)
            video_writer_logger.info(f'Received video task for: {video_task.get('video_filepath')}')

            filepath = video_task.get('video_filepath')
            frames_data = video_task.get('frames_data')
            frame_size = video_task.get('frame_size')
            fps = video_task.get('fps')
            event_info_for_db = video_task.get('event_data')

            if not all([filepath, frames_data, frame_size, fps, event_info_for_db]):
                video_writer_logger.error(f'Incomplete video task received: {filepath if filepath else 'path_missing'}. Skipping')
                continue

            if not frames_data:
                video_writer_logger.warning(f'No frames to write for video: {filepath}. Skipping')
                continue

            # video_dir = os.path.dirname(filepath)
            video_writer_logger.info(f'Starting to write video: {filepath}, Size: {frame_size}, FPS: {fps}, Frames: {len(frames_data)}')
            out = cv2.VideoWriter(filepath, fourcc, float(fps), frame_size)

            if not out.isOpened():
                video_writer_logger.error(f'Failed to open VideoWriter for: {filepath}. Skipping')
                continue

            for frame_np, _ in frames_data:
                if frame_np is not None:
                    if (frame_np.shape[1], frame_np.shape[0]) != frame_size:
                        frame_np = cv2.resize(frame_np, frame_size)
                    out.write(frame_np)
                else:
                    video_writer_logger.warning(f'Encountered a None frame for video {filepath}, skipping frame')
            out.release()
            video_writer_logger.info(f'Successfully wrote video: {filepath}')

            if SessionLocal and filepath:
                db_session = SessionLocal()
                try:
                    from app.models import AlarmEvent

                    alarm_db_id_from_event = event_info_for_db.get('alarm_db_id')
                    event_type_from_event = event_info_for_db.get('type')
                    event_timestamp_from_detector = event_info_for_db.get('timestamp')

                    if alarm_db_id_from_event and event_type_from_event and event_timestamp_from_detector:
                        dt_from_detector = datetime.fromtimestamp(event_timestamp_from_detector, tz=timezone.utc)
                        time_window_for_even_match_seconds = 5

                        target_alarm_event = db_session.query(AlarmEvent).filter(
                            AlarmEvent.alarm_id == alarm_db_id_from_event,
                            AlarmEvent.event_type == event_type_from_event,
                            AlarmEvent.timestamp >= (dt_from_detector - timedelta(seconds=time_window_for_even_match_seconds)),
                            AlarmEvent.timestamp <= (dt_from_detector + timedelta(seconds=time_window_for_even_match_seconds))
                        ).order_by(AlarmEvent.timestamp.desc()).first()

                        if target_alarm_event:
                            relative_video_path = os.path.basename(filepath)
                            target_alarm_event.video_path = relative_video_path
                            db_session.commit()
                            video_writer_logger.info(f'Updated AlarmEvent ID {target_alarm_event.id} with video_path: {relative_video_path}')
                        else:
                            video_writer_logger.warning(
                                f'Could not find matching AlarmEvent for video {filepath}. '
                                f'Criteria: alarm_id={alarm_db_id_from_event}, type={event_type_from_event}, '
                                f'approx_ts={dt_from_detector.isoformat()}'
                            )
                    else:
                        video_writer_logger.warning(f'Not enough info in event_data to update AlarmEvent for video {filepath}')
                except Exception as e_db_update:
                    video_writer_logger.error(f'Error updating AlarmEvent for video {filepath}: {e_db_update}', exc_info=True)
                    db_session.rollback()
                finally:
                    db_session.close()
            elif not SessionLocal:
                video_writer_logger.warning(f'DB session not available. Cannot update AlarmEvent for video {filepath}')
        except queue.Empty:
            continue
        except Exception as e:
            video_writer_logger.error(f'Error in Video Writer worker: {e}', exc_info=True)
            if 'out' in locals() and out.isOpened():
                out.release()
            if 'filepath' in locals() and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    video_writer_logger.info(f'Remove partially written/failed video file: {filepath}')
                except Exception as e_remove:
                    video_writer_logger.error(f'Failed to remove video file {filepath} after error: {e_remove}')
            time.sleep(1)

    video_writer_logger.info('Video Writer worker stopped')
