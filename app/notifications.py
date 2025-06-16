import requests
from flask import current_app
from telegram import InlineKeyboardMarkup

def send_telegram_message(chat_id: str, text: str, inline_keyboard: list | None = None) -> bool:
    """
    Отправляет сообщение в Telegram указанному chat_id
    """

    token = current_app.config.get('TELEGRAM_BOT_TOKEN')
    if not token:
        current_app.logger.error('Telegram Bot Token is not configured')
        return False
    if not chat_id:
        current_app.logger.error('Chat ID was not given')
        return False

    send_url = f'https://api.telegram.org/bot{token}/sendMessage'
    payload = {
        'chat_id': chat_id,
        'text': escape_markdown_v2(text),
        'parse_mode': 'MarkdownV2'
    }

    if inline_keyboard:
        reply_markup = InlineKeyboardMarkup(inline_keyboard)
        payload['reply_markup'] = reply_markup.to_json()

    try:
        response = requests.post(send_url, json=payload, timeout=10)
        response.raise_for_status()

        response_json = response.json()
        if response_json.get('ok'):
            current_app.logger.info(f'Message to Telegram chat {chat_id} sent successfully')
            return True
        else:
            current_app.logger.error(f'Telegram API error for chat {chat_id}: {response_json.get('description')}')
            return False
    except requests.exceptions.Timeout:
        current_app.logger.error(f'Timeout while sending message to Telegram chat {chat_id}')
        return False
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f'Error occurred while sending message to Telegram chat {chat_id}: {e}\nRequest: {payload}')
        return False
    except Exception as e:
        current_app.logger.exception(f'Unexpected error occurred while sending message to Telegram chat {chat_id}')
        return False

def escape_markdown_v2(text: str) -> str:
    if not text:
        return ''
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(['\\' + char if char in escape_chars else char for char in text])
