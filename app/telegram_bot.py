from datetime import datetime, timezone
import asyncio
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, MessageHandler, filters, ContextTypes, ApplicationBuilder, CallbackQueryHandler
from .notifications import escape_markdown_v2
from .models import User, Alarm, AlarmEvent, TelegramVerificationCode
from . import db

STATE_AWAITING_USERNAME = 1
STATE_AWAITING_CODE = 2

user_states = {}

def get_setting_keyboard(user: User) -> InlineKeyboardMarkup:
    """Формирует inline-клавиатуру для настроек уведомлений."""

    keyboard = [
        [
            InlineKeyboardButton(
                f'↔️ Движение: {'Включено ✅' if user.notify_telegram_movement else 'Выключено ❌'}',
                callback_data=f'toggle_setting:movement'
            )
        ],
        [
            InlineKeyboardButton(
                f'⚠️ Пропажа: {'Включено ✅' if user.notify_telegram_disappearance else 'Выключено ❌'}',
                callback_data=f'toggle_setting:disappearance'
            )
        ],
        [
            InlineKeyboardButton(
                f'Готово',
                callback_data=f'settings_done'
            )
        ]
    ]

    return InlineKeyboardMarkup(keyboard)

def event_details_json_to_str(event: AlarmEvent) -> str:
    details_str = ''

    if not event.details_json:
        return 'Детали отсутствуют'

    try:
        details_obj = json.loads(event.details_json)
        if not isinstance(details_obj, dict):
            return str(details_obj)

        if event.event_type == 'movement':
            dist = details_obj.get('distance_px', 'N/A')
            time_s = details_obj.get('time_seconds', 'N/A')
            details_str = f'Движение: {dist}px за {time_s}с'
        elif event.event_type == 'disappearance':
            time_not_seen = details_obj.get('time_seconds', 'N/A')
            details_str = f'Пропажа: не видна {time_not_seen}с'
        else:
            details_items = [f'{key.replace('_', ' ').capitalize()}: {value}' for key, value in details_obj.items()]
            details_str = '\n'.join(details_items) if details_items else "Нет специфичных деталей"

    except json.JSONDecodeError:
        details_str = event.details_json
    except Exception as e_json_parse:
        details_str = "Ошибка при разборе деталей"

    return details_str

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение и запрашивает username."""

    chat_id = update.effective_chat.id
    flask_app = context.bot_data['flask_app']
    flask_app.logger.info(f'Telegram bot: /start command from chat_id {chat_id}')

    with flask_app.app_context():
        user_with_this_chat_id = User.query.filter_by(telegram_chat_id=str(chat_id)).first()
        if user_with_this_chat_id:
            await update.message.reply_text(
                f'Привет, {user_with_this_chat_id.username}! Твой Telegram уже привязан к аккаунту.\n'
                'Если хочешь отвязать, используй команду /stop.'
            )
            user_states.pop(chat_id, None)
            return

    user_states[chat_id] = {'state': STATE_AWAITING_USERNAME}
    await update.message.reply_text(
        'Привет! Я бот для уведомлений от системы сигнализации.\n'
        'Чтобы привязать свой Telegram к аккаунту в приложении, пожалуйста, '
        'отправь мне свой логин (username) от приложения'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовые сообщения в зависимости от состояния пользователя"""

    chat_id = update.effective_chat.id
    text = update.message.text
    flask_app = context.bot_data['flask_app']
    # db_session = context.bot_data['db_session']

    current_state_data = user_states.get(chat_id)

    if not current_state_data:
        await update.message.reply_text(escape_markdown_v2('Используй команду /help для справки'), parse_mode='MarkdownV2')
        return

    state = current_state_data.get('state')

    with flask_app.app_context():
        if state == STATE_AWAITING_USERNAME:
            flask_app.logger.info(f'Telegram bot: Received username \'{text}\' from chat_id {chat_id}')
            user = User.query.filter_by(username=text).first()
            if not user:
                await update.message.reply_text(
                    f'Пользователь с логином \'{text}\' не найден. Пожалуйста, проверь логин и попробуй снова, или зарегистрируйся в приложении'
                )
                user_states.pop(chat_id, None)
                return

            if user.telegram_chat_id and user.telegram_chat_id == str(chat_id):
                await update.message.reply_text(f'Этот Telegram уже привязан к пользователю {user.username}')
                # user_states.pop(chat_id, None)
                return
            elif user.telegram_chat_id:
                await update.message.reply_text(f'Логин {user.username} уже привязан к другому Telegram аккаунту.\nЕсли это ошибка, обратись в поддержку или отвяжи его в приложении')
                user_states.pop(chat_id, None)
                return

            user_states[chat_id]['state'] = STATE_AWAITING_CODE
            user_states[chat_id]['user_id_to_verify'] = user.id
            await update.message.reply_text(f'Отлично, {text}! Теперь, пожалуйста, сгенерируй код верификации в мобильном приложении и отправь его мне')
        elif state == STATE_AWAITING_CODE:
            verification_code = text.strip().upper()
            user_id_to_verify = current_state_data.get('user_id_to_verify')
            flask_app.logger.info(f'Telegram bot: Received code \'{verification_code}\' for user_id {user_id_to_verify} from chat_id {chat_id}')

            if not user_id_to_verify:
                await update.message.reply_text('Произошла ошибка. Пожалуйста, начни сначала с /start')
                user_states.pop(chat_id, None)
                return

            code_entry = TelegramVerificationCode.query.filter_by(
                user_id=user_id_to_verify,
                code=verification_code
            ).first()

            if code_entry and not code_entry.is_expired():
                user = db.session.get(User, user_id_to_verify)
                if user:
                    user.telegram_chat_id = str(chat_id)
                    db.session.delete(code_entry)
                    db.session.commit()
                    await update.message.reply_text(f'Успешно! Теперь твой Telegram аккаунт привязан к пользователю {user.username}')
                    flask_app.logger.info(f'Telegram chat_id {chat_id} successfully linked to user {user.username} (ID: {user.id})')
                    user_states.pop(chat_id, None)
                else:
                    await update.message.reply_text('Ошибка: пользователь не найден. Попробуй /start')
                    user_states.pop(chat_id, None)
            elif code_entry and code_entry.is_expired():
                await update.message.reply_text('Этот код истёк. Пожалуйста, сгенерируй новый код в приложении и попробуй снова')
            else:
                await update.message.reply_text('Неверный код. Пожалуйста, проверь код и попробуй снова')

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда для отвязки Telegram от аккаунта."""

    chat_id = str(update.effective_chat.id)
    flask_app = context.bot_data['flask_app']
    flask_app.logger.info(f'Telegram bot: /stop command from chat_id {chat_id}')

    with flask_app.app_context():
        user = User.query.filter_by(telegram_chat_id=chat_id).first()
        if user:
            user.telegram_chat_id = None
            db.session.commit()
            await update.message.reply_text(f'Аккаунт {user.username} отвязан от этого Telegram. Ты можешь снова привязать аккаунт командой /start')
            flask_app.logger.info(f'Telegram chat_id {chat_id} unlinked from user {user.username}')
        else:
            await update.message.reply_text('Этот Telegram не привязан ни к одному аккаунту')

        user_states.pop(update.effective_chat.id, None)

async def send_history_page(
    update_or_query,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    page:int = 1
) -> None:
    """Отправляет или редактирует сообщение с указанной страницей истории."""

    ITEMS_PER_PAGE_HISTORY = 5
    flask_app = context.bot_data['flask_app']

    with flask_app.app_context():
        offset = (page - 1) * ITEMS_PER_PAGE_HISTORY

        user_alarm_events = db.session.query(AlarmEvent)\
            .join(AlarmEvent.alarm)\
            .filter(Alarm.user_id == user_id)\
            .order_by(AlarmEvent.timestamp.desc())\
            .limit(ITEMS_PER_PAGE_HISTORY)\
            .offset(offset)\
            .all()
        total_events_count = db.session.query(db.func.count(AlarmEvent.id))\
            .join(AlarmEvent.alarm)\
            .filter(Alarm.user_id == user_id)\
            .scalar() or 0

        if not user_alarm_events and page == 1:
            message_text = escape_markdown_v2('Для твоих сигнализаций пока нет зарегистрированных событий')
            reply_markup = None
        elif not user_alarm_events and page > 1:
            message_text = escape_markdown_v2('Больше событий нет')
            reply_markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton('⏪ Назад', callback_data=f'history_page:{page-1}')
                ]
            ])
        else:
            response_text_parts = []
            response_text_parts.append(f'История событий (страница {page}):\n\n')
            for event in reversed(user_alarm_events):
                card_text = '--------------------\n'

                event_time_str = event.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
                alarm_id_str = str(event.alarm_id)
                event_id_str = str(event.id)
                track_id_str = str(event.alarm.vehicle_track_id)
                event_type_str = event.event_type
                details_str = event_details_json_to_str(event)

                card_text += (
                    f'🗓️ {event_time_str}\n'
                    f'🚨 ID сигнализации: {alarm_id_str}\n'
                    f'🆔 ID события: {event_id_str}\n'
                    f'🚗 Трек ID т/с: {track_id_str}\n'
                )

                if event_type_str == 'movement':
                    card_text += f'↔️ Тип события: {event_type_str}\n'
                elif event_type_str == 'disappearance':
                    card_text += f'⚠️ Тип события: {event_type_str}\n'
                else:
                    card_text += f'❔ Тип события: {event_type_str}\n'

                if details_str:
                    card_text += f'ℹ️ Детали: {details_str}\n'
                response_text_parts.append(card_text)
            response_text_parts[-1] = response_text_parts[-1] + '--------------------\n'

            message_text = ''.join(response_text_parts)
            keyboard_row = []
            if page > 1:
                keyboard_row.append(
                    InlineKeyboardButton(f'⏪ Предыдущая', callback_data=f'history_page:{page-1}')
                )
            total_pages = (total_events_count + ITEMS_PER_PAGE_HISTORY - 1) // ITEMS_PER_PAGE_HISTORY
            if page < total_pages:
                keyboard_row.append(
                    InlineKeyboardButton(f'Следующая ⏩', callback_data=f'history_page:{page+1}')
                )
            reply_markup = InlineKeyboardMarkup([keyboard_row]) if keyboard_row else None

        if isinstance(update_or_query, Update):
            await update_or_query.message.reply_text(escape_markdown_v2(message_text), reply_markup=reply_markup, parse_mode='MarkdownV2')
        elif hasattr(update_or_query, 'edit_message_text'):
            try:
                await update_or_query.edit_message_text(escape_markdown_v2(message_text), reply_markup=reply_markup, parse_mode='MarkdownV2')
            except Exception as e_edit:
                flask_app.logger.warning(f'History: Could not edit message, probably no change: {e_edit}')
                if hasattr(update_or_query, 'answer'):
                    await update_or_query.answer()

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /history, показывая первую страницу истории."""

    chat_id = str(update.effective_chat.id)
    flask_app = context.bot_data['flask_app']
    flask_app.logger.info(f'Telegram bot: /history command from chat_id {chat_id}')

    with flask_app.app_context():
        user = User.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            await update.message.reply_text(escape_markdown_v2('Твой Telegram не привязан к аккаунту. Используй /start для привязки'), parse_mode='MarkdownV2')
            return

        MAX_EVENTS_TO_SHOW = 10
        user_alarm_events = db.session.query(AlarmEvent)\
            .join(AlarmEvent.alarm)\
            .filter(Alarm.user_id == user.id)\
            .order_by(AlarmEvent.timestamp.desc())\
            .limit(MAX_EVENTS_TO_SHOW)\
            .all()

        if not user_alarm_events:
            await update.message.reply_text(escape_markdown_v2('Для твоих сигнализаций пока нет зарегистрированных событий'))
            return

        await send_history_page(update, context, user.id, page=1)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает текущие настройки уведомлений и кнопки для их изменения."""
    chat_id = str(update.effective_chat.id)
    flask_app = context.bot_data['flask_app']
    flask_app.logger.info(f'Telegram bot: /settings command from chat_id {chat_id}')

    with flask_app.app_context():
        user = User.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            await update.message.reply_text(
                escape_markdown_v2('Твой Telegram не привязан к аккаунту. Используй /start для привязки'),
                parse_mode='MarkdownV2'
            )
            return

        settings_text = '⚙️ Твои настройки уведомлений в Telegram\n\n'
        reply_markup = get_setting_keyboard(user)

        await update.message.reply_text(
            escape_markdown_v2(settings_text),
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет видеофайл для указанного event_id."""
    chat_id = str(update.effective_chat.id)
    flask_app = context.bot_data['flask_app']

    if not context.args or len(context.args) != 1:
        await update.message.reply_text(escape_markdown_v2('Пожалуйста, укажи ID события после команды. Пример: `/video 123`'), parse_mode='MarkdownV2')
        return
    
    try:
        event_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(escape_markdown_v2('ID события должен быть числом. Пример: `/video 123`'), parse_mode='MarkdownV2')
        return
    
    flask_app.logger.info(f'Telegram bot: /video command for event_id {event_id} from chat_id {chat_id}')
    
    with flask_app.app_context():
        user = User.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            await update.message.reply_text(escape_markdown_v2('Твой Telegram не привязан к аккаунту. Используй /start для привязки'), parse_mode='MarkdownV2')
            return
        
        alarm_event = db.session.get(AlarmEvent, event_id)
        if not alarm_event:
            await update.message.reply_text(escape_markdown_v2('Событие не найдено'), parse_mode='MarkdownV2')
            return
        
        if alarm_event.alarm.user_id != user.id:
            await update.message.reply_text(escape_markdown_v2('У тебя нет доступа к этому событию'), parse_mode='MarkdownV2')
            return
        
        if not alarm_event.video_path:
            await update.message.reply_text(escape_markdown_v2('Для этого события видеофайл отсутствует'), parse_mode='MarkdownV2')
            return
        
        video_path = os.path.join(flask_app.config.get('VIDEO_SAVE_PATH'), alarm_event.video_path)
        if os.path.exists(video_path):
            flask_app.logger.info(f'User {user.username} (chat_id {chat_id}): Sending video \'{alarm_event.video_path}\' for event {event_id} via /video command')
            try:
                await update.message.reply_text(escape_markdown_v2('Загружаю видео...'), parse_mode='MarkdownV2')
                await update.message.reply_chat_action(action='upload_video')
                with open(video_path, 'rb') as video:
                    await update.message.reply_video(
                        video=video,
                        caption=escape_markdown_v2(f'Видео для события ID: {alarm_event.id}'),
                        filename=alarm_event.video_path
                    )
            except Exception as e_send_video:
                flask_app.logger.error(f'Failed to send video {video_path} to chat_id {chat_id} via /video command: {e_send_video}', exc_info=True)
                await update.message.reply_text(escape_markdown_v2('Не удалось отправить видеофайл'), parse_mode='MarkdownV2')
        else:
            flask_app.logger.error(f'Video file not found on disk: {video_path} for event ID {event_id} (requsted by /video command)')
            await update.message.reply_text(escape_markdown_v2('Видеофайл не найден на сервере'), parse_mode='MarkdownV2')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: 
    """Отправляет справку."""

    await update.message.reply_text(
        escape_markdown_v2(
            f'Доступные команды:\n\n'
            f'🚀 /start – привязать аккаунт\n\n'
            f'🚫 /stop – отвязать аккаунт\n\n'
            f'📘 /history – история событий\n\n'
            f'🎬 /video [ID события] – видео события\n(пример: `/video 5`)\n\n'
            f'⚙️ /settings – настройки уведомлений\n\n'
            f'❓ /help – справка'
        ), 
        parse_mode='MarkdownV2'
    )

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатия на inline-кнопки."""

    query = update.callback_query
    await query.answer()

    callback_data = query.data
    chat_id = str(query.from_user.id)
    flask_app = context.bot_data['flask_app']
    flask_app.logger.info(f'Telegram bot: Received button callback_data \'{callback_data}\' from chat_id {chat_id}')

    action_parts = callback_data.split(':')
    action = action_parts[0]

    with flask_app.app_context():
        user = User.query.filter_by(telegram_chat_id=chat_id).first()
        if not user:
            await query.edit_message_text(text=escape_markdown_v2('Ошибка: ваш Telegram не привязан к аккаунту'))
            flask_app.logger.warning(f'Callback received from unlinked telegram_chat_id: {chat_id}')
            return

        # if action == 'event_details':
        #     if len(action_parts) < 3:
        #         await query.edit_message_text(text=escape_markdown_v2('Ошибка: неверные данные для деталей события'))
        #         return
        #     try:
        #         event_id = int(action_parts[2])
        #         alarm_event = db.session.get(AlarmEvent, event_id)

        #         if alarm_event and alarm_event.alarm.user_id == user.id:
        #             details_text = f'Детали события ID: {alarm_event.id} (Тип: {alarm_event.event_type})\n'
        #             details_text += f'Время: {alarm_event.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}\n'
        #             if alarm_event.details_json:
        #                 details_text += event_details_json_to_str(alarm_event)

        #             keyboard = []
        #             if alarm_event.video_path:
        #                 keyboard.append([
        #                     InlineKeyboardButton('🎬 Запросить видео', callback_data=f'get_event_video:{alarm_event.id}')
        #                 ])
        #             reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        #             await query.edit_message_text(
        #                 text=escape_markdown_v2(details_text),
        #                 reply_markup=reply_markup,
        #                 parse_mode='MarkdownV2'
        #             )
        #         else:
        #             await query.edit_message_text(text=escape_markdown_v2('Событие не найдено или у вас нет к нему доступа'))
        #     except ValueError:
        #         await query.edit_message_text(text=escape_markdown_v2('Ошибка: неверный ID события'))
        #     except Exception as e:
        #         flask_app.logger.error(f'Error processing event_details callback: {e}', exc_info=True)
        #         await query.edit_message_text(text=escape_markdown_v2('Произошла ошибка при получении деталей'))
        # elif action == 'unarm_alarm':
        #     if len(action_parts) < 2:
        #         await query.edit_message_text(text=escape_markdown_v2('Ошибка: неверные данные для снятий с сигнализации'))
        #         return
        #     try:
        #         alarm_id_to_unarm = int(action_parts[1])
        #         alarm_to_unarm = db.session.get(Alarm, alarm_id_to_unarm)
        #         active_alarms_mp_dict = context.bot_data.get('active_alarms_shared')

        #         if alarm_to_unarm and alarm_to_unarm.user_id == user.id:
        #             if alarm_to_unarm.is_active:
        #                 alarm_to_unarm.is_active = False
        #                 alarm_to_unarm.unset_at = datetime.now(timezone.utc)
        #                 db.session.commit()

        #                 if active_alarms_mp_dict is not None and alarm_id_to_unarm in active_alarms_mp_dict:
        #                     try:
        #                         del active_alarms_mp_dict[alarm_id_to_unarm]
        #                         flask_app.logger.info(f'Removed Alarm ID {alarm_id_to_unarm} from shared active_alarms via Telegram button')
        #                     except Exception as e_del:
        #                         flask_app.logger.error(f'Error removing {alarm_id_to_unarm} from shared_dict: {e_del}')

        #                 await query.edit_message_text(text=escape_markdown_v2(f'Сигнализация ID {alarm_id_to_unarm} для машины (трек ID {alarm_to_unarm.vehicle_track_id}) снята'), parse_mode='MarkdownV2')
        #                 flask_app.logger.info(f'User {user.username} (chat_id {chat_id}) unarmed alarm ID {alarm_id_to_unarm} via Telegram button')
        #             else:
        #                 await query.edit_message_text(text=escape_markdown_v2('Эта сигнализация уже неактивна'))
        #         else:
        #             await query.edit_message_text(text=escape_markdown_v2('Сигнализация не найдена или у вас нет к ней доступа'))
        #     except ValueError:
        #         await query.edit_message_text(text=escape_markdown_v2('Ошибка: неверный ID сигнализации'), parse_mode='MarkdownV2')
        #     except Exception as e:
        #         flask_app.logger.error(f'Error processing unarm_alarm callback: {e}', exc_info=True)
        #         await query.edit_message_text(text=escape_markdown_v2('Произошла ошибка при снятии с сигнализации'))
        if action == 'toggle_setting':
            if len(action_parts) < 2:
                await query.edit_message_text(text=escape_markdown_v2(f'Ошибка: неверные данные для изменения настройки'), parse_mode='MarkdownV2')
                return

            setting_type = action_parts[1]
            try:
                if setting_type == 'movement':
                    user.notify_telegram_movement = not user.notify_telegram_movement
                    db.session.commit()
                    new_status_text = f'↔️ Движение: {'Включено ✅' if user.notify_telegram_movement else 'Выключено ❌'}'
                elif setting_type == 'disappearance':
                    user.notify_telegram_disappearance = not user.notify_telegram_disappearance
                    db.session.commit()
                    new_status_text = f'⚠️ Пропажа: {'Включено ✅' if user.notify_telegram_disappearance else 'Выключено ❌'}'
                else:
                    await query.edit_message_text(text=escape_markdown_v2(f'Неизвестный тип настройки: {setting_type}'), parse_mode='MarkdownV2')
                    return

                flask_app.logger.info(f'User {user.username} (chat_id {chat_id}) toggled setting {setting_type} to {getattr(user, f'notify_telegram_{setting_type}')}')
                settings_text = '⚙️ Твои настройки уведомлений в Telegram\n\n'
                reply_markup = get_setting_keyboard(user)
                await query.edit_message_text(
                    text=escape_markdown_v2(settings_text),
                    reply_markup=reply_markup,
                    parse_mode='MarkdownV2'
                )
                await query.answer(text=f'Настройка \'{new_status_text}\' изменена')
            except Exception as e:
                db.session.rollback()
                flask_app.logger.error(f'Error processing toggle_setting callback for user {user.username}, setting {setting_type}: {e}', exc_info=True)
                await query.edit_message_text(text=escape_markdown_v2('Произошла ошибка при изменении настройки'), parse_mode='MarkdownV2')
        elif action == 'settings_done':
            await query.edit_message_text(text=escape_markdown_v2(f'Настройки сохранены'), parse_mode='MarkdownV2')
        elif action == 'history_page':
            if len(action_parts) < 2:
                await query.edit_message_text(text=escape_markdown_v2('Ошибка: неверные данные для страницы истории'), parse_mode='MarkdownV2')
                return
            try:
                page_to_show = int(action_parts[1])
                if page_to_show < 1: page_to_show = 1
                await send_history_page(query, context, user.id, page=page_to_show)
            except ValueError:
                await query.edit_message_text(text=escape_markdown_v2('Ошибка: неверный номер страницы'), parse_mode='MarkdownV2')
            except Exception as e:
                flask_app.logger.error(f'Error processing history_page callback: {e}', exc_info=True)
                await query.edit_message_text(text=escape_markdown_v2('Произошла ошибка при загрузке страницы истории'), parse_mode='MarkdownV2')
        # elif action == 'get_event_video':
        #     if len(action_parts) < 2:
        #         await query.edit_message_text(text=escape_markdown_v2('Ошибка: ID события для видео не указан'), parse_mode='MarkdownV2')
        #         return
        #     try:
        #         event_id = int(action_parts[1])
        #         alarm_event = db.session.get(AlarmEvent, event_id)
        #         if not alarm_event:
        #             await query.edit_message_text(text=escape_markdown_v2('Ошибка: это событие не существует'), parse_mode='MarkdownV2')
        #             return
                
        #         if not alarm_event.video_path:
        #             await query.edit_message_text(text=escape_markdown_v2('Ошибка: для этого события видеофайл отсутствует'), parse_mode='MarkdownV2')
        #             return
                
        #         video_path = os.path.join(flask_app.config.get('VIDEO_SAVE_PATH'), alarm_event.video_path)
        #         if os.path.exists(video_path):
        #             flask_app.logger.info(f'User {user.username} (chat_id {chat_id}): Sending video \'{alarm_event.video_path}\' for event {event_id}')
        #             try:
        #                 await update.message.reply_text(escape_markdown_v2('Загружаю видео...'), parse_mode='MarkdownV2')
        #                 await update.message.reply_chat_action(action='upload_video')
        #                 with open(video_path, 'rb') as video:
        #                     await update.message.reply_video(
        #                         video=video,
        #                         caption=escape_markdown_v2(f'Видео для события ID: {alarm_event.id}'),
        #                         filename=alarm_event.video_path
        #                     )
        #             except Exception as e_send_video:
        #                 flask_app.logger.error(f'Failed to send video {video_path} to chat_id {chat_id}: {e_send_video}', exc_info=True)
        #                 await update.message.reply_text(escape_markdown_v2('Не удалось отправить видеофайл'), parse_mode='MarkdownV2')
        #         else:
        #             flask_app.logger.error(f'Video file not found on disk: {video_path} for event ID {event_id}')
        #             await query.edit_message_text(escape_markdown_v2('Видеофайл не найден на сервере'), parse_mode='MarkdownV2')
        #     except ValueError:
        #         await query.edit_message_text(escape_markdown_v2('Ошибка: неверный ID события'), parse_mode='MarkdownV2')
        #     except Exception as e:
        #         flask_app.logger.error(f'Error processing get_event_video callback: {e}', exc_info = True)
        #         await update.message.reply_text(escape_markdown_v2('Произошла ошибка при запросе видео'), parse_mode='MarkdownV2')
        else:
            await query.edit_message_text(text=escape_markdown_v2(f'Неизвестное действие: {action}'), parse_mode='MarkdownV2')

def run_telegram_bot(flask_app_instance, running_flag, active_alarms_shared):
    """ Синхронная обёртка для запуска асинхронного бота в отдельном потоке."""

    bot_logger = flask_app_instance.logger
    bot_logger.info('Telegram Bot runner thread initiated (sync wrapper)')

    token = flask_app_instance.config.get('TELEGRAM_BOT_TOKEN')
    if not token:
        bot_logger.error('TELEGRAM_BOT_TOKEN not found. Bot will not start')
        return

    try:
        async def async_bot_main_with_flag(token, flask_app_instance, running_flag, active_alarms_shared):
            bot_logger = flask_app_instance.logger
            bot_logger.info('async_bot_main_with_flag starting...')
            application = ApplicationBuilder().token(token).build()
            application.bot_data['flask_app'] = flask_app_instance
            application.bot_data['running_flag'] = running_flag
            application.bot_data['active_alarms_shared'] = active_alarms_shared

            application.add_handler(CommandHandler('start', start_command))
            application.add_handler(CommandHandler('stop', stop_command))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            application.add_handler(CommandHandler('history', history_command))
            application.add_handler(CommandHandler('settings', settings_command))
            application.add_handler(CommandHandler('video', video_command))
            application.add_handler(CommandHandler('help', help_command))
            application.add_handler(CallbackQueryHandler(button_callback_handler))

            try:
                await application.initialize()
                await application.updater.start_polling(allowed_updates=Update.ALL_TYPES, poll_interval=1.0)
                await application.start()
                bot_logger.info('Bot application started and polling')

                while running_flag.value:
                    await asyncio.sleep(0.5)

                bot_logger.info('Running flag is False, initiating bot shutdown...')
            except (KeyboardInterrupt, SystemExit):
                bot_logger.info("Bot received KeyboardInterrupt/SystemExit")
            except Exception as e:
                bot_logger.error(f"Error in async_bot_main_with_flag: {e}", exc_info=True)
            finally:
                bot_logger.info("Bot shutting down...")
                if application.updater and application.updater.running:
                    await application.updater.stop()
                if application.running:
                    await application.stop()
                await application.shutdown()
                bot_logger.info("Bot shutdown complete.")

        asyncio.run(async_bot_main_with_flag(token, flask_app_instance, running_flag, active_alarms_shared))
    except Exception as e:
        bot_logger.error(f"Could not execute asyncio.run for Telegram Bot: {e}", exc_info=True)
    finally:
        bot_logger.info('Telegram Bot Runner thread finished')
