import time
import queue
import json
from datetime import datetime, timezone
from . import db
from .models import Alarm, AlarmEvent, User
from .notifications import send_telegram_message

def event_processor_worker(
        flask_app,
        event_queue_shared,
        active_alarms_shared,
        running_flag_shared
):
    worker_logger = flask_app.logger
    worker_logger.info('Event Processor Worker started')

    while running_flag_shared.value:
        try:
            event_data = event_queue_shared.get(timeout=1)
            worker_logger.info(f'Event Processor received event: {event_data}')

            with flask_app.app_context():
                alarm_db_id = event_data.get('alarm_db_id')
                event_type = event_data.get('type')
                user_id = event_data.get('user_id')
                track_id = event_data.get('track_id', 'N/A')
                timestamp_from_event = event_data.get('timestamp')
                details = event_data.get('details', {})

                if not all([alarm_db_id, event_type, user_id, track_id]):
                    worker_logger.error(f'Received incomplete event data: {event_data}')
                    continue

                alarm_instance = db.session.get(Alarm, alarm_db_id)

                if not alarm_instance:
                    worker_logger.warning(f'Alarm ID {alarm_db_id} not found in DB for event: {event_type}. Skipping')
                    continue

                if alarm_instance.user_id != user_id:
                    worker_logger.error(f'User ID mismatch for event! Event UserID: {user_id}. Alarm Owner UserID: {alarm_instance.user_id}. Alarm ID: {alarm_db_id}. Skipping')
                    continue

                if not alarm_instance.is_active and event_type != 'disappearance':
                    worker_logger.info(f'Alarm ID {alarm_db_id} is already inactive in DB. Skipping event: {event_type} (unless it\'s disappearance)')
                    continue

                new_alarm_event = AlarmEvent(
                    alarm_id=alarm_db_id,
                    event_type=event_type,
                    timestamp=datetime.fromtimestamp(timestamp_from_event, tz=timezone.utc) if timestamp_from_event else datetime.now(timezone.utc),
                    details_json=json.dumps(details) if details else None
                )
                db.session.add(new_alarm_event)
                worker_logger.info(f'Created AlarmEvent for Alarm ID {alarm_db_id}, Type: {event_type}')

                NOTIFICATION_COOLDOWN_SECONDS = flask_app.config.get('NOTIFICATION_COOLDOWN_SECONDS')
                notification_sent_this_cycle = False
                user_to_notify = db.session.get(User, alarm_instance.user_id)

                if event_type == 'disappearance':
                    if alarm_instance.is_active:
                        alarm_instance.is_active = False
                        alarm_instance.unset_at = datetime.now(timezone.utc)
                        worker_logger.info(f'Deactivating Alarm ID {alarm_db_id} in DB due to disappearance')

                        if active_alarms_shared is not None:
                            if alarm_db_id in active_alarms_shared:
                                try:
                                    del active_alarms_shared[alarm_db_id]
                                    worker_logger.info(f'Removed Alarm ID {alarm_db_id} from shared active_alarms dict.')
                                except KeyError:
                                    worker_logger.warning(f'KeyError when trying to remove Alarm ID {alarm_db_id} from shared dict (already removed?)')
                            else:
                                worker_logger.warning(f'Alarm ID {alarm_db_id} (disappeared) not found in shared active_alarms dict to remove')
                    else:
                        worker_logger.info(f'Received disappearance for already inactive Alarm ID {alarm_db_id}. Event logged')

                if not user_to_notify:
                    worker_logger.warning(f'User for Alarm ID {alarm_db_id} was not found in DB (User ID: {alarm_instance.user_id})')
                    db.session.commit()
                    continue
                elif not user_to_notify.telegram_chat_id:
                    worker_logger.info(f'User ID {user_to_notify.id} does not have linked telegram_chat_id for Alarm ID {alarm_db_id}')
                    db.session.commit()
                    continue

                send_notification = False
                message = ''
                inline_keyboard_buttons = []
                vehicle_identifier = f'Машина (трек ID: {track_id}, ID сигнализации: {alarm_db_id})'

                if event_type == 'movement':
                    if user_to_notify.notify_telegram_movement:
                        can_send_notification_now = True
                        if alarm_instance.last_notification_at:
                            last_notification_at_db = alarm_instance.last_notification_at
                            if last_notification_at_db.tzinfo is None or last_notification_at_db.tzinfo.utcoffset(last_notification_at_db) is None:
                                last_notification_at_aware = last_notification_at_db.replace(tzinfo=timezone.utc)
                            else:
                                last_notification_at_aware = last_notification_at_db
                            time_since_last = (datetime.now(timezone.utc) - last_notification_at_aware).total_seconds()
                            if time_since_last < NOTIFICATION_COOLDOWN_SECONDS:
                                worker_logger.info(f'Movement notification for Alarm ID {alarm_db_id} (type: {event_type}) throttled due to cooldown')
                                can_send_notification_now = False

                        if can_send_notification_now:
                            send_notification = True
                            dist = details.get('distance_px', 'N/A')
                            time_s = details.get('time_seconds', 'N/A')
                            message = f'↔️ ОБНАРУЖЕНО ДВИЖЕНИЕ ↔️\n{vehicle_identifier} начала движение.\nСмещение: {dist}px за {time_s}с.'
                            # inline_keyboard_buttons.append(
                            #     [InlineKeyboardButton('Детали события', callback_data=f'event_details:movement:{new_alarm_event.id}')]
                            # )
                            # inline_keyboard_buttons.append(
                            #     [InlineKeyboardButton('Снять с сигнализации', callback_data=f'unarm_alarm:{alarm_db_id}')]
                            # )
                    else:
                        worker_logger.info(f'User ID {user_to_notify.id} disabled Telegram movement notifications')
                elif event_type == 'disappearance':
                    if user_to_notify.notify_telegram_disappearance:
                        send_notification = True
                        time_not_seen = details.get('time_seconds', 'N/A')
                        message = f'⚠️ МАШИНА ПРОПАЛА ⚠️\n{vehicle_identifier} пропала из виду.\nНе видна в течение: {time_not_seen}с.'
                        # inline_keyboard_buttons.append(
                        #     [InlineKeyboardButton('Детали события', callback_data=f'event_details:disappearance:{new_alarm_event.id}')]
                        # )
                    else:
                        worker_logger.info(f'User ID {user_to_notify.id} disabled Telegram disappearance notifications')

                if send_notification and message:
                    if send_telegram_message(user_to_notify.telegram_chat_id, message, inline_keyboard_buttons if inline_keyboard_buttons else None):
                        alarm_instance.last_notification_at = datetime.now(timezone.utc)
                        notification_sent_this_cycle = True
                    else:
                        worker_logger.error(f'Unable to send Telegram notification for Alarm ID {alarm_db_id}')

                db.session.commit()
                worker_logger.info(f'Commited DB changes for event related to Alarm ID {alarm_db_id}')

                if notification_sent_this_cycle:
                    worker_logger.info(f'Notification successfully processed for Alarm ID {alarm_db_id}')

        except queue.Empty:
            continue
        except Exception as e:
            worker_logger.error(f'Error in Event Processor Worker: {e}', exc_info=True)
            with flask_app.app_context():
                try:
                    db.session.rollback()
                    worker_logger.info('DB session rolled back due to error in event processor')
                except Exception as rb_exc:
                    worker_logger.error(f'Error during rollback: {rb_exc}', exc_info=True)
            time.sleep(1)

    worker_logger.info('Event Processor Worker stopped')
