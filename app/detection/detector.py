import cv2
import time
import math
import logging
import os
import uuid
from collections import deque
from ultralytics import YOLO

detector_logger = logging.getLogger('VehicleDetectorProcess')

def setup_detector_logging(level_str='INFO'):
    """Настраивает логгирование для процесса детектора."""
    level = getattr(logging, level_str.upper(), logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    detector_logger.addHandler(handler)
    detector_logger.setLevel(level)
    detector_logger.propagate = False

def distance_calc(p1, p2):
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])

def detect_vehicles(
        running_flag_shared,
        config: dict,
        last_bboxes_shared,
        active_alarms_shared,
        event_queue_shared,
        video_writer_queue_shared
):
    setup_detector_logging(config.get('log_level', 'INFO'))
    detector_logger.info('Detection process started with event generation logic')

    model_path = config.get('yolo_model_path', 'yolo11m.pt')
    rtsp_source = config.get('rtsp_source')
    img_height = config.get('img_height')
    img_width = config.get('img_width')
    conf_thresh = config.get('conf_thresh')
    iou_thresh = config.get('iou_thresh')
    draw_frame = config.get('detector_debug_draw')
    verbose = config.get('verbose')

    detection_time_window = config.get('detection_time_window')
    detection_min_distance = config.get('detection_min_distance')
    disappearance_thresh_s = config.get('disappearance_thresh_s')

    video_save_path = config.get('video_save_path')
    video_fps = config.get('video_fps')
    camera_fps = config.get('camera_fps')
    seconds_before = config.get('video_seconds_before_event')
    seconds_after = config.get('video_seconds_after_event')

    try:
        detector_logger.info(f'Loading YOLO model from: {model_path}')
        model = YOLO(model_path)
        detector_logger.info('YOLO model loaded successfully')
    except Exception as e:
        detector_logger.error(f'Failed to load YOLO model: {e}', exc_info=True)
        running_flag_shared.value = False
        return

    cap = None
    consecutive_read_failures = 0
    max_read_failures = 5
    base_retry_delay = 2

    target_detection_width = 1280

    class_map = {2: 'car', 3: 'motorcycle', 7: 'truck'}
    detector_logger.info(f'Using class names: {class_map}')

    frame_buffer_size = camera_fps * (seconds_before + 2)
    frame_buffer = deque(maxlen=frame_buffer_size)

    vehicle_position_history = {}
    alarmed_vehicles_last_seen = {}
    disappeared_event_sent = set()

    pending_video_recordings = {}

    detector_logger.info(f'Attempting to connect to RTSP source: {rtsp_source}')
    while running_flag_shared.value:
        current_frame_for_video_task = None

        try:
            if cap is None or not cap.isOpened():
                detector_logger.info(f'Opening video capture for {rtsp_source}...')
                if cap:
                    cap.release()
                cap = cv2.VideoCapture(rtsp_source)
                if not cap.isOpened():
                    detector_logger.warning('Failed to open video capture. Retrying...')
                    time.sleep(5)
                    continue
                detector_logger.info('Video capture opened successfully')
                consecutive_read_failures = 0

            ret, frame = cap.read()
            if ret:
                consecutive_read_failures = 0
                current_frame_timestamp = time.time()

                original_height, original_width = frame.shape[:2]
                if original_width > target_detection_width:
                    ratio = target_detection_width / original_width
                    target_detection_height = int(original_height * ratio)
                    resized_frame = cv2.resize(frame, (target_detection_width, target_detection_height), interpolation=cv2.INTER_AREA)
                else:
                    resized_frame = frame

                frame_copy_for_buffer = resized_frame.copy()
                frame_buffer.append((frame_copy_for_buffer, current_frame_timestamp))
                current_frame_for_video_task = frame_copy_for_buffer

                results = model.track(
                    resized_frame,
                    imgsz=(img_height, img_width),
                    classes=list(class_map.keys()),
                    persist=True,
                    half=True,
                    conf=conf_thresh,
                    iou=iou_thresh,
                    verbose=verbose,
                    stream_buffer=True
                )

                processed_results_for_api = []
                detected_track_ids_in_frame = set()
                current_active_alarms_snapshot = dict(active_alarms_shared)


                if results and results[0].boxes.id is not None:
                    boxes = results[0].boxes
                    for i in range(len(boxes)):
                        box = boxes[i]
                        track_id = int(box.id.item())
                        detected_track_ids_in_frame.add(track_id)
                        cls_id = int(box.cls.item())
                        class_name = class_map.get(cls_id, f'class_{cls_id}')
                        confidence = float(box.conf.item())
                        x1, y1, x2, y2 = map(float, box.xyxy[0])
                        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                        current_position = (cx, cy)

                        processed_results_for_api.append({
                            'name': class_name,
                            'class_id': cls_id,
                            'confidence': round(confidence, 3),
                            'track_id': track_id,
                            'box': {
                                'x1': round(x1),
                                'y1': round(y1),
                                'x2': round(x2),
                                'y2': round(y2)
                            }
                        })

                        is_on_active_alarm = False
                        alarm_info_for_this_track_id = None

                        # current_active_alarms_snapshot = dict(active_alarms_shared)
                        for alarm_db_id, alarm_data in current_active_alarms_snapshot.items():
                            if alarm_data.get('track_id') == track_id:
                                is_on_active_alarm = True
                                alarm_info_for_this_track_id = {**alarm_data, 'alarm_db_id': alarm_db_id}
                                break

                        if is_on_active_alarm:
                            alarmed_vehicles_last_seen[track_id] = current_frame_timestamp
                            if track_id in disappeared_event_sent:
                                disappeared_event_sent.remove(track_id)
                                # TODO
                                detector_logger.info(
                                    f'Vehicle with Track ID {track_id} (Alarm DB ID: {alarm_info_for_this_track_id['alarm_db_id']} reappeared;\n'
                                    f'User ID: {alarm_info_for_this_track_id['user_id']}'
                                )
                            # TODO
                            if track_id not in vehicle_position_history:
                                vehicle_position_history[track_id] = []

                            history = vehicle_position_history[track_id]
                            history.append((current_frame_timestamp, current_position))
                            vehicle_position_history[track_id] = [
                                (ts, pos) for ts, pos in history if current_frame_timestamp - ts <= detection_time_window
                            ]

                            actual_history = vehicle_position_history[track_id]
                            if len(actual_history) > 1:
                                start_ts, start_pos = actual_history[0]
                                end_ts, end_pos = actual_history[-1]
                                if end_ts - start_ts >= detection_time_window * 0.8:
                                    dist = distance_calc(start_pos, end_pos)
                                    if dist >= detection_min_distance:
                                        detector_logger.info(f'[EVENT] Vehicle Track ID {track_id} (Alarm DB ID: {alarm_info_for_this_track_id['alarm_db_id']}) MOVED: {dist:.0f}px in {(end_ts - start_ts):.2f}s')
                                        event_data = {
                                            'type': 'movement',
                                            'alarm_db_id': alarm_info_for_this_track_id['alarm_db_id'],
                                            'user_id': alarm_info_for_this_track_id['user_id'],
                                            'track_id': track_id,
                                            'timestamp': current_frame_timestamp,
                                            'details': {
                                                'distance_px': round(dist, 2),
                                                'time_seconds': round(end_ts - start_ts, 2),
                                                'start_pos': [round(p, 2) for p in start_pos],
                                                'end_pos': [round(p, 2) for p in end_pos]
                                            }
                                        }
                                        event_queue_shared.put(event_data)
                                        vehicle_position_history[track_id] = [(end_ts, end_pos)]
                                        detector_logger.debug(f'Movement event sent to queue. History for track_id {track_id} reset')

                                        temp_event_id_for_video = str(uuid.uuid4())
                                        video_filename = f'movement_{alarm_info_for_this_track_id['alarm_db_id']}_{track_id}_{int(current_frame_timestamp)}.mp4'
                                        full_video_path = os.path.join(video_save_path, video_filename)

                                        frames_before_event = list(frame_buffer)
                                        pending_video_recordings[temp_event_id_for_video] = {
                                            'frames_to_capture': int(camera_fps * seconds_after),
                                            'captured_frames': frames_before_event,
                                            'event_data': event_data,
                                            'video_filepath': full_video_path,
                                            'frame_size': (frame_copy_for_buffer.shape[1], frame_copy_for_buffer.shape[0]),
                                            'fps': video_fps
                                        }
                                        detector_logger.info(f'Movement: Queued video recording for {video_filename}. Need {pending_video_recordings[temp_event_id_for_video]['frames_to_capture']} more frames')

                        if draw_frame:
                            color = (0, 0, 255) if is_on_active_alarm else (0, 255, 0)
                            cv2.rectangle(resized_frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
                            label_suffix = ' ALARM!' if is_on_active_alarm else ''
                            label = f"{class_name} #{track_id}{label_suffix} C:{confidence:.2f}"
                            cv2.putText(resized_frame, label, (int(x1), int(y1) - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

                last_bboxes_shared[0] = processed_results_for_api
                last_bboxes_shared[1] = current_frame_timestamp

                for alarm_db_id, alarm_data in current_active_alarms_snapshot.items():
                    alarmed_track_id = alarm_data['track_id']
                    if alarmed_track_id not in detected_track_ids_in_frame:
                        if alarmed_track_id not in alarmed_vehicles_last_seen:
                            alarmed_vehicles_last_seen[alarmed_track_id] = current_frame_timestamp

                        time_since_last_seen = current_frame_timestamp - alarmed_vehicles_last_seen.get(alarmed_track_id, current_frame_timestamp)
                        if time_since_last_seen > disappearance_thresh_s:
                            if alarmed_track_id not in disappeared_event_sent:
                                detector_logger.info(f'[EVENT] Vehicle Track ID {alarmed_track_id} (Alarm DB ID: {alarm_db_id}) disappeared. Not seen for {time_since_last_seen:.0f}s')

                                event_data = {
                                    'type': 'disappearance',
                                    'alarm_db_id': alarm_db_id,
                                    'user_id': alarm_data['user_id'],
                                    'track_id': alarmed_track_id,
                                    'timestamp': current_frame_timestamp,
                                    'details': {
                                        'time_seconds': round(time_since_last_seen, 2)
                                    }
                                }
                                event_queue_shared.put(event_data)
                                disappeared_event_sent.add(alarmed_track_id)
                                detector_logger.debug(f'Disappearance event sent to queue for track_id {alarmed_track_id}')
                                if alarmed_track_id in vehicle_position_history:
                                    del vehicle_position_history[alarmed_track_id]

                                temp_event_id_for_video = str(uuid.uuid4())
                                video_filename = f'disappearance_{alarm_db_id}_{alarmed_track_id}_{int(current_frame_timestamp)}.mp4'
                                full_video_path = os.path.join(video_save_path, video_filename)

                                frames_for_disappearance_video = list(frame_buffer)

                                if frames_for_disappearance_video:
                                    video_task = {
                                        'video_filepath': full_video_path,
                                        'frames_data': [(f.copy(), ts) for f, ts in frames_for_disappearance_video],
                                        'frame_size': (frames_for_disappearance_video[0][0].shape[1], frames_for_disappearance_video[0][0].shape[0]),
                                        'fps': video_fps,
                                        'event_data': event_data
                                    }
                                    video_writer_queue_shared.put(video_task)
                                    detector_logger.info(f'Disappearance: Sent video recording task for {video_filename}')

                if current_frame_for_video_task is not None:
                    for event_placeholder_id in list(pending_video_recordings.keys()):
                        task = pending_video_recordings[event_placeholder_id]
                        if task['frames_to_capture'] > 0:
                            task['captured_frames'].append((current_frame_for_video_task.copy(), current_frame_timestamp))
                            task['frames_to_capture'] -= 1
                        if task['frames_to_capture'] <= 0:
                            detector_logger.info(f'Finished capturing frames for video: {task['video_filepath']}')
                            video_writer_task = {
                                'video_filepath': task['video_filepath'],
                                'frames_data': task['captured_frames'],
                                'frame_size': task['frame_size'],
                                'fps': task['fps'],
                                'event_data': task['event_data']
                            }
                            video_writer_queue_shared.put(video_writer_task)
                            detector_logger.info(f'Sent video task for {task['video_filepath']} to writer queue')
                            del pending_video_recordings[event_placeholder_id]

                if draw_frame:
                    cv2.imshow('Detection Debug View', resized_frame)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        detector_logger.info('Quit signal received from debug view')
                        running_flag_shared.value = False
                        break
                else:
                    pass
            else:
                consecutive_read_failures += 1
                detector_logger.warning(f'Failed to read frame from source. Attempt {consecutive_read_failures}/{max_read_failures}')
                if cap:
                    cap.release()
                cap = None
                if consecutive_read_failures >= max_read_failures:
                    detector_logger.error(f'Max read failures reached ({max_read_failures}). Waiting longer before retry')
                    time.sleep(30)
                    consecutive_read_failures = 0
                else:
                    time.sleep(base_retry_delay * consecutive_read_failures)
                continue
        except Exception as e:
            detector_logger.error(f'An error occurred in detection loop: {e}', exc_info=True)
            time.sleep(5)

    if cap:
        cap.release()
    if draw_frame:
        cv2.destroyAllWindows()
    detector_logger.info('Detection process stopped')
