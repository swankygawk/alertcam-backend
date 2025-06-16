import time
import os
from datetime import datetime, timezone
from flask import request, jsonify, current_app, send_from_directory
from flask_jwt_extended import jwt_required, get_jwt_identity
from . import api_bp
from .. import db
from ..models import Alarm, AlarmEvent, User, TelegramVerificationCode

SHARED_DATA = {
    'last_processed_bboxes': None,
    'active_alarms': None
}

def initialize_shared_data(last_bboxes_mp_list, active_alarms_mp_dict):
    """Инициализирует общие данные, переданные из главного процесса."""
    SHARED_DATA['last_processed_bboxes'] = last_bboxes_mp_list
    SHARED_DATA['active_alarms'] = active_alarms_mp_dict
    current_app.logger.info('Shared data (bboxes, active_alarms) initialized in API module')

@api_bp.route('/alarms/<int:vehicle_track_id>', methods=['POST'], endpoint='set_alarm_ep')
@jwt_required() 
def set_alarm(vehicle_track_id):
    current_user_id = int(get_jwt_identity())
    # data = request.get_json()
    # if not data:
    #     current_app.logger.warning(f'User {current_user_id}: Set alarm attempt with no data')
    #     return jsonify({'msg': 'No input data provided'}), 400

    # vehicle_track_id = data.get('vehicle_track_id')
    if vehicle_track_id is None:
        current_app.logger.warning(f'User {current_user_id}: Set alarm attempt missing vehicle_track_id')
        return jsonify({'msg': 'vehicle_track_id is required'}), 400

    vehicle_exists_in_last_detection = False
    last_detection_timestamp = 0.0

    if SHARED_DATA['last_processed_bboxes'] is not None and len(SHARED_DATA['last_processed_bboxes']) == 2:
        try:
            current_detections = SHARED_DATA['last_processed_bboxes'][0]
            last_detection_timestamp = SHARED_DATA['last_processed_bboxes'][1]

            if current_detections:
                for vehicle_data in current_detections:
                    if vehicle_data.get('track_id') == vehicle_track_id:
                        vehicle_exists_in_last_detection = True
                        break
        except Exception as e:
            current_app.logger.error(f'Error accessing shared bboxes during set_alarm: {e}', exc_info=True)

    MAX_DETECTION_AGE_SECONDS = 10
    current_time = time.time()
    detection_data_is_stale = (current_time - last_detection_timestamp > MAX_DETECTION_AGE_SECONDS)

    if not vehicle_exists_in_last_detection:
        if detection_data_is_stale:
            current_app.logger.warning(f'User {current_user_id}: Cannot verify existence of track_id {vehicle_track_id}. Detection data is stale ({(current_time - last_detection_timestamp):.1f})s old')
            return jsonify({'msg': f'Cannot set alarm: detection data is too old ({int(current_time - last_detection_timestamp)}s)'}), 400
        else:
            current_app.logger.warning(f'User {current_user_id}: Attempt to set alarm for non-currently-detected vehicle_track_id {vehicle_track_id}')
            return jsonify({'msg': f'Vehicle with track ID {vehicle_track_id} is not currently detected'}), 404

    existing_alarm = Alarm.query.filter_by(
        user_id=current_user_id,
        vehicle_track_id=vehicle_track_id,
        is_active=True
    ).first()

    if existing_alarm:
        current_app.logger.info(f'User {current_user_id}: Alarm already active for vehicle track ID {vehicle_track_id}')
        return jsonify({'msg': 'Alarm already active for this vehicle by this user', 'alarm_id': existing_alarm.id}), 409

    try:
        new_alarm = Alarm(
            user_id = current_user_id,
            vehicle_track_id=vehicle_track_id,
            set_at=datetime.now(timezone.utc),
            is_active=True
        )
        db.session.add(new_alarm)
        db.session.commit()
        current_app.logger.info(f'User {current_user_id}: Alarm (ID: {new_alarm.id}) set for vehicle_track_id {vehicle_track_id}')
        
        if SHARED_DATA['active_alarms'] is not None:
            SHARED_DATA['active_alarms'][new_alarm.id] = {
                'track_id': new_alarm.vehicle_track_id,
                'user_id': new_alarm.user_id
            }
            current_app.logger.info(f'Added Alarm ID {new_alarm.id} to shared active alarms')
        else:
            current_app.logger.warning('SHARED_DATA[\'active_alarms\'] is not initialized. Cannot update for detector')

        return jsonify({
            'msg': 'Alarm set successfully',
            'alarm_id': new_alarm.id,
            'vehicle_track_id': new_alarm.vehicle_track_id,
            'user_id': new_alarm.user_id
        }), 201
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'User {current_user_id}: Error setting alarm for vehicle_track_id {vehicle_track_id}: {e}')
        return jsonify({'msg': 'An error occurred while setting the alarm'}), 500

@api_bp.route('/alarms/<int:alarm_id>', methods=['DELETE'], endpoint='unset_alarm_ep')
@jwt_required()
def unset_alarm(alarm_id):
    current_user_id = int(get_jwt_identity())
    alarm = Alarm.query.get(alarm_id)

    if not alarm:
        current_app.logger.warning(f'User {current_user_id}: Attempt to unset non-existing alarm ID {alarm_id}')
        return jsonify({'msg': 'Alarm not found'}), 404

    if alarm.user_id != current_user_id:
        current_app.logger.warning(f'User {current_user_id}: Attempt to unset alarm ID {alarm_id} belonging to another user (user_id: {alarm.user_id})')
        return jsonify({'msg': 'You are not authorized to unset this alarm'}), 403

    if not alarm.is_active:
        current_app.logger.info(f'User {current_user_id}: Alarm ID {alarm_id} is already inactive')
        return jsonify({'msg': 'Alarm is already inactive', 'alarm_id': alarm.id}), 200

    try:
        alarm.is_active = False
        alarm.unset_at = datetime.now(timezone.utc)
        db.session.commit()
        current_app.logger.info(f'User {current_user_id}: Alarm (ID: {alarm.id}) unset for vehicle_track_id {alarm.vehicle_track_id}')

        if SHARED_DATA['active_alarms'] is not None:
            if alarm_id in SHARED_DATA['active_alarms']:
                del SHARED_DATA['active_alarms'][alarm_id]
                current_app.logger.info(f'Removed Alarm ID {alarm_id} from shared active_alarms')
            else:
                current_app.logger.warning(f'Attempted to remove non-existent Alarm ID {alarm_id} from shared active_alarms')
        else:
            current_app.logger.warning('SHARED_DATA[\'active_alarms\'] is not initialized. Cannot update for detector')

        return jsonify({'msg': 'Alarm unset successfully', 'alarm_id': alarm.id}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'User {current_user_id}: Error unsetting alarm ID {alarm_id}: {e}')
        return jsonify({'msg': 'An error occurred while unsetting the alarm'}), 500

@api_bp.route('/alarms', methods=['GET'], endpoint='get_active_alarms_ep')
@jwt_required()
def get_active_alarms():
    current_user_id = int(get_jwt_identity())

    try:
        alarms = Alarm.query.filter_by(user_id=current_user_id, is_active=True).all()
        alarms_data = [{
            'alarm_id': alarm.id,
            'vehicle_track_id': alarm.vehicle_track_id,
            'set_at': alarm.set_at.isoformat()
        } for alarm in alarms]
        current_app.logger.debug(f'User {current_user_id}: Fetched {len(alarms_data)} active alarms')
        return jsonify(alarms_data), 200
    except Exception as e:
        current_app.logger.error(f'User {current_user_id}: Error fetching active alarms: {e}')
        return jsonify({'msg': 'An error occurred while fetching alarms'}), 500

@api_bp.route('/vehicles/detected', methods=['GET'], endpoint='get_detected_vehicles_ep')
@jwt_required()
def get_detected_vehicles():
    current_user_id = int(get_jwt_identity())

    processed_bboxes_list = []
    timestamp = 0.0

    if SHARED_DATA['last_processed_bboxes'] is not None and len(SHARED_DATA['last_processed_bboxes']) == 2:
        try:
            raw_data_from_detector = SHARED_DATA['last_processed_bboxes'][0]
            timestamp = SHARED_DATA['last_processed_bboxes'][1]

            if raw_data_from_detector is not None:
                processed_bboxes_list = list(raw_data_from_detector)
            else:
                processed_bboxes_list = []
        except Exception as e:
            current_app.logger.error(f'Error accessing shared bboxes: {e}', exc_info=True)
            processed_bboxes_list = []
            timestamp = 0.0
    else:
        current_app.logger.warning('SHARED_DATA[\'last_processed_bboxes\'] is not initialized or has incorrect format')

    active_user_alarms_track_ids = {
        alarm.vehicle_track_id: alarm.id for alarm in Alarm.query.filter_by(user_id=current_user_id, is_active=True).all()
    }

    enriched_vehicles = []
    if processed_bboxes_list:
        for vehicle_data in processed_bboxes_list:
            track_id = vehicle_data.get('track_id')
            is_alarmed_by_current_user = False
            current_alarm_id = None
            if track_id is not None and track_id in active_user_alarms_track_ids:
                is_alarmed_by_current_user = True
                current_alarm_id = active_user_alarms_track_ids[track_id]
            enriched_vehicles.append({
                **vehicle_data,
                'alarmed_by_user': is_alarmed_by_current_user,
                'alarm_id': current_alarm_id
            })
    # TODO

    current_app.logger.debug(f'User {current_user_id}: Fetched {len(enriched_vehicles)} detected vehicles (timestamp: {timestamp})')
    return jsonify({
        'detected_vehicles': enriched_vehicles,
        'timestamp': timestamp
    }), 200

@api_bp.route('/alarms/history', methods=['GET'], endpoint='get_alarm_history_ep')
@jwt_required()
def get_alarm_history():
    current_user_id = int(get_jwt_identity())
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)

    if per_page > 100:
        per_page = 100

    try:
        alarms_query = Alarm.query.filter_by(user_id=current_user_id).order_by(Alarm.set_at.desc())
        paginated_alarms = alarms_query.paginate(page=page, per_page=per_page, error_out=False)
        
        alarms_on_page = paginated_alarms.items
        history_data = []
        for alarm in alarms_on_page:
            events_data = [{
                'event_id': event.id,
                'event_type': event.event_type,
                'timestamp': event.timestamp.isoformat(),
                'details': event.details_json
            } for event in alarm.events.order_by(AlarmEvent.timestamp.asc()).all()]

            history_data.append({
                'alarm_id': alarm.id,
                'vehicle_track_id': alarm.vehicle_track_id,
                'set_at': alarm.set_at.isoformat(),
                'unset_at': alarm.unset_at.isoformat() if alarm.unset_at else None,
                'is_active': alarm.is_active,
                'events': events_data
            })

        current_app.logger.debug(f'User {current_user_id}: Fetched alarm history ({len(history_data)} items)')
        return jsonify({
            "alarms_history": history_data,
            "pagination": {
                "page": paginated_alarms.page,
                "per_page": paginated_alarms.per_page,
                "total_pages": paginated_alarms.pages, # Общее количество страниц
                "total_items": paginated_alarms.total, # Общее количество записей
                "has_next": paginated_alarms.has_next, # Есть ли следующая страница
                "has_prev": paginated_alarms.has_prev, # Есть ли предыдущая страница
                "next_page_num": paginated_alarms.next_num if paginated_alarms.has_next else None,
                "prev_page_num": paginated_alarms.prev_num if paginated_alarms.has_prev else None
            }
        }), 200
    except Exception as e:
        current_app.logger.error(f'User {current_user_id}: Error fetching alarm history: {e}')
        current_app.logger.exception('Exception details for alarm history error:')
        return jsonify({'msg': 'An error occurred while fetching alarm history'}), 500

@api_bp.route('/user/telegram_verification_code', methods=['POST'], endpoint='generate_telegram_verification_code')
@jwt_required()
def generate_telegram_verification_code():
    current_user_id = int(get_jwt_identity())

    TelegramVerificationCode.query.filter_by(user_id=current_user_id).delete()
    db.session.commit()

    try:
        verification_code_obj = TelegramVerificationCode(user_id=current_user_id, lifetime_minutes=5)
        db.session.add(verification_code_obj)
        db.session.commit()

        current_app.logger.info(f'Generated Telegram verification code {verification_code_obj.code} for user ID {current_user_id}')
        return jsonify({
            'msg': 'Verification code generated. Please send this code to our Telegram bot',
            'verification_code': verification_code_obj.code,
            'expires_at': verification_code_obj.expires_at.isoformat()
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception(f'Error occurred while generating Telegram verification code for user ID {current_user_id}')
        return jsonify({'msg': 'Failed to generate verification code'}), 500

@api_bp.route('/user/password', methods=['PUT'], endpoint='update_password')
@jwt_required()
def update_password():
    current_user_id = int(get_jwt_identity())
    user = db.session.get(User, current_user_id)
    if not user:
        current_app.logger.warning(f'User ID {current_user_id} from JWT not found in DB')
        return jsonify({'msg': 'User not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'msg': 'No input data provided'}), 400

    old_password = data.get('old_password')
    new_password = data.get('new_password')
    new_password_confirmation = data.get('new_password_confirmation')

    if not all([old_password, new_password, new_password_confirmation]):
        return jsonify({'msg': 'Missing old_password, new_password, and/or new_password_confirmation'}), 400
    
    if not user.check_password(old_password):
        current_app.logger.warning(f'User ID {current_user_id}: Incorrect old password during change attempt')
        return jsonify({'msg': 'Incorrect old password'}), 401

    if new_password != new_password_confirmation:
        return jsonify({'msg': 'New password and confirmation do not match'}), 400

    try:
        user.set_password(new_password)
        db.session.commit()
        current_app.logger.info(f'User ID {current_user_id}: Password changed successfully')
        return jsonify({'msg': 'Password updated successfully'}), 200
    except Exception as _:
        db.session.rollback()
        current_app.logger.exception(f'Error changing password for user ID {current_user_id}')
        return jsonify({'msg': 'Failed to update password'}), 500

@api_bp.route('/user/notification_preferences', methods=['GET'], endpoint='get_notification_preferences_ep')
@jwt_required()
def get_notification_preferences():
    current_user_id = int(get_jwt_identity())

    user = db.session.get(User, current_user_id)
    if not User:
        current_app.logger.warning(f'User ID {current_user_id} from JWT not found in DB')
        return jsonify({'msg': 'User not found'}), 404

    preferences = {
        'username': user.username,
        'telegram_chat_id': user.telegram_chat_id,
        'notify_telegram_movement': user.notify_telegram_movement,
        'notify_telegram_disappearance': user.notify_telegram_disappearance
    }
    return jsonify(preferences), 200

@api_bp.route('/user/notification_preferences', methods=['PUT'], endpoint='update_notification_preferences_ep')
@jwt_required()
def update_notification_preferences():
    current_user_id = int(get_jwt_identity())

    user = db.session.get(User, current_user_id)
    if not user:
        current_app.logger.warning(f'User ID {current_user_id} from JWT not found in DB')
        return jsonify({'msg': 'User not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'msg': 'No input data provided'}), 400

    updated = False
    if 'notify_telegram_movement' in data and isinstance(data['notify_telegram_movement'], bool):
        user.notify_telegram_movement = data['notify_telegram_movement']
        updated = True

    if 'notify_telegram_disappearance' in data and isinstance(data['notify_telegram_disappearance'], bool):
        user.notify_telegram_disappearance = data['notify_telegram_disappearance']
        updated = True

    if updated:
        try:
            db.session.commit()
            current_app.logger.info(f'User ID {current_user_id} updated notification preferences')
            updated_preferences = {
                'notify_telegram_movement': user.notify_telegram_movement,
                'notify_telegram_disappearance': user.notify_telegram_disappearance
            }
            return jsonify({'msg': 'Preferences updated successfully', 'preferences': updated_preferences}), 200
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception(f'Error updating notification preferences for user ID {current_user_id}')
            return jsonify({'msg': 'Failed to update preferences'}), 500
    else:
        return jsonify({'msg': 'No valid preference fields provided to update'}), 400

@api_bp.route('/user/telegram_link', methods=['DELETE'], endpoint='delete_telegram_link_ep')
@jwt_required()
def delete_telegram_link():
    current_user_id = int(get_jwt_identity())

    user = db.session.get(User, current_user_id)
    if not user:
        current_app.logger.warning(f'User ID {current_user_id} from JWT not found in DB (delete_telegram_link)')
        return jsonify({'msg': 'User not found'}), 404

    if user.telegram_chat_id is None:
        current_app.logger.info(f'User ID {current_user_id} attempted to unlink Telegram, but no chat_id was linked')
        return jsonify({'msg': 'Telegram account is not currently linked'}), 404

    try:
        old_chat_id = user.telegram_chat_id
        user.telegram_chat_id = None
        db.session.commit()

        current_app.logger.info(f'User ID {current_user_id} successfully unlinked Telegram chat_id: {old_chat_id}')
        return jsonify({'msg': 'Telegram account unbound successfully'}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception(f'Error occurred while unlinking Telegram for user ID {current_user_id}')
        return jsonify({'msg': 'Failed to unlink Telegram account'}), 500

@api_bp.route('/events/<int:event_id>/video', methods=['GET'], endpoint='get_event_video_ep')
@jwt_required()
def get_event_video(event_id):
    current_user_id = int(get_jwt_identity())

    alarm_event = db.session.get(AlarmEvent, event_id)
    if not alarm_event:
        current_app.logger.warning(f'User {current_user_id}: Event video request for non-existent event ID {event_id}')
        return jsonify({'msg': 'Event not found'}), 404

    if alarm_event.alarm.user_id != current_user_id:
        current_app.logger.warning(f'User {current_user_id}: Unauthorized attempt to access video for event ID {event_id}')
        return jsonify({'msg': 'You are not authorized to access this video'}), 403

    if not alarm_event.video_path:
        current_app.logger.warning(f'User {current_user_id}: Video not available for event ID {event_id}')
        return jsonify({'msg': 'Video not available for this event'}), 404

    saved_videos_directory = os.path.join(os.getcwd(), current_app.config.get('VIDEO_SAVE_PATH'))
    video_filename = alarm_event.video_path

    try:
        current_app.logger.info(f'User {current_user_id}: Sending video file \'{video_filename}\' for event ID {event_id} from directory {saved_videos_directory}')
        return send_from_directory(
            saved_videos_directory,
            video_filename,
            as_attachment=True,
            mimetype='video/mp4'
        )
    except FileNotFoundError:
        current_app.logger.error(f'Video file not found: {os.path.join(saved_videos_directory, video_filename)} for event ID {event_id}')
        return jsonify({'msg': 'Video file not found on server'}), 404
    except Exception as e:
        current_app.logger.exception(f'Error sending video file for event ID {event_id}')
        return jsonify({'msg': 'An error occurred while retrieving the video'}), 500
