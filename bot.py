"""Qonaq: школьный Telegram-гид. Запуск на Render: python bot.py."""
import argparse
import hashlib
import hmac
import logging
import os
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit

import telebot
from flask import Flask, jsonify, request
from telebot import apihelper, types
from waitress import serve

LOG = logging.getLogger('qonaq')
VERSION = 'qonaq-webhook-2'
# Отпечаток опубликованного токена. Самого секрета в коде нет.
COMPROMISED_TOKEN_HASHES = {
    '3f05b93d6584ec427cf0b9775bcc4ec5d9a127902dc82677a75caaef0717c2cf',
    'f9f5b0e0c4382a2ca47cb2d81eea4645706d45688d1da2227ae6035ac299e8e7',
}
apihelper.RETRY_ON_ERROR = False

WELCOME = (
    'Здравствуйте! Я Qonaq AI Bot — школьный гид по Павлодару и Баянаулу. 🤖\n\n'
    'Выберите тему или напишите вопрос:\n'
    '• Что взять на Жасыбай?\n'
    '• Где поесть до 4000 тенге?\n'
    '• Маршрут на 1 день в Павлодаре.'
)
LAKE = (
    '🏖 Что взять на Жасыбай:\n'
    '• Воду, перекус и пакет для мусора.\n'
    '• Головной убор и солнцезащитный крем.\n'
    '• Купальные принадлежности и полотенце.\n'
    '• Тёплую одежду на вечер и удобную обувь.\n'
    '• Заряженный телефон, пауэрбанк и немного наличных.\n\n'
    'Перед поездкой уточните погоду, условия въезда и стоимость парковки. '
    'Купайтесь в разрешённых местах; детям нужна помощь взрослых.'
)
FOOD = (
    '🍽 Как поесть в пределах 4000 ₸:\n'
    '• Сначала посмотрите актуальное меню и цены.\n'
    '• Уточните, включена ли плата за обслуживание.\n'
    '• Спросите про комплексный обед и способы оплаты.\n\n'
    'В базе пока нет проверенных адресов и актуальных меню кафе. '
    'Напишите название заведения, если хотите добавить его в проект.'
)
ROUTE = (
    '🚶 Пример маршрута на 1 день в Павлодаре:\n\n'
    'Утро — прогулка по набережной Иртыша.\n'
    'День — музей или выставка по вашим интересам, затем обед.\n'
    'Вечер — прогулка в парке или по центру города.\n\n'
    'Перед выходом проверьте погоду, часы работы выбранных мест и цены.'
)
HELP = (
    'Доступны темы: Жасыбай, питание и маршрут по Павлодару.\n'
    'Команды: /start — меню, /help — помощь, /about — о проекте.'
)
ABOUT = (
    'Qonaq AI Bot — школьный проект туристического помощника.\n'
    'Эта версия выбирает готовые ответы по ключевым словам. '
    'Подключения к нейросети пока нет. Цены и адреса добавляются после проверки.'
)


def make_reply(text):
    """Один ответ на один запрос; никакой рассылки."""
    text = ' '.join(text.casefold().replace('ё', 'е').split())
    command = text.split()[0].split('@')[0] if text else ''
    if command == '/start':
        return WELCOME
    if command == '/help':
        return HELP
    if command == '/about':
        return ABOUT
    if command.startswith('/'):
        return HELP
    if any(word in text for word in ('поесть', 'кафе', '4000', 'обед', 'еда')):
        return FOOD
    if any(word in text for word in ('жасыбай', 'жасибай', 'взять', 'озеро')):
        return LAKE
    if any(word in text for word in ('маршрут', '1 день', 'павлодар')):
        return ROUTE
    if any(word in text for word in ('привет', 'здравствуй', 'салем', 'сәлем')):
        return WELCOME
    return 'По этому вопросу информации в базе пока нет.\n\n' + HELP


class UpdateStore:
    """Только ID обновлений: без текста и персональных данных."""
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS seen (id INTEGER PRIMARY KEY, at REAL)')
        self.db.commit()

    def claim(self, update_id):
        with self.lock, self.db:
            self.db.execute('DELETE FROM seen WHERE at < ?', (time.time() - 172800,))
            cursor = self.db.execute(
                'INSERT OR IGNORE INTO seen VALUES (?, ?)', (update_id, time.time())
            )
            return cursor.rowcount == 1

    def release(self, update_id):
        with self.lock, self.db:
            self.db.execute('DELETE FROM seen WHERE id = ?', (update_id,))


def create_app(bot=None, secret='', db_path='updates.sqlite3'):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
    app.config['BOT_READY'] = False
    app.config['BOT_STATE'] = 'waiting_configuration'
    store = UpdateStore(db_path)
    app.extensions['update_store'] = store
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row('🏖 Что взять на Жасыбай?')
    keyboard.row('🍽 Где поесть до 4000 тенге?')
    keyboard.row('🚶 Маршрут на 1 день в Павлодаре')

    @app.get('/')
    def index():
        # Живой веб-сервер и состояние Telegram проверяются отдельно.
        return jsonify(project='Qonaq AI Bot', version=VERSION,
                       state=app.config['BOT_STATE'])

    @app.get('/health')
    def health():
        return jsonify(ready=app.config['BOT_READY'], version=VERSION,
                       state=app.config['BOT_STATE']), (200 if app.config['BOT_READY'] else 503)

    @app.post('/telegram')
    def telegram():
        provided = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
        if not secret or not hmac.compare_digest(provided.encode(), secret.encode()):
            return 'Forbidden', 403
        if not app.config['BOT_READY'] or bot is None:
            return 'Not ready', 503
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get('update_id')) is not int:
            return 'Bad update', 400
        message = data.get('message')
        if not isinstance(message, dict):
            return 'OK', 200  # Не отвечать на закрепления и редактирования.
        sender, chat = message.get('from'), message.get('chat')
        text = message.get('text')
        if (not isinstance(sender, dict) or sender.get('is_bot') is not False
                or not isinstance(chat, dict) or chat.get('type') != 'private'
                or type(chat.get('id')) is not int or not isinstance(text, str) or not text.strip()):
            return 'OK', 200
        update_id = data['update_id']
        # Записать ID ДО отправки, чтобы повторный запрос не вызвал второй ответ.
        if not store.claim(update_id):
            return 'OK', 200
        try:
            bot.send_message(chat['id'], make_reply(text), reply_markup=keyboard,
                             timeout=15, disable_web_page_preview=True)
        except apihelper.ApiTelegramException as exc:
            LOG.warning('sendMessage failed: update=%s code=%s', update_id, exc.error_code)
            if exc.error_code == 429:
                # Telegram явно отклонил отправку: повтор здесь безопасен.
                store.release(update_id)
                return 'Retry later', 503
        except Exception as exc:
            # При тайм-ауте ответ мог дойти. Не повторять отправку вслепую.
            LOG.warning('sendMessage result uncertain: update=%s type=%s',
                        update_id, type(exc).__name__)
        return 'OK', 200

    return app


def restore_profile(app, bot, previous_name=''):
    """Ошибка одного поля не мешает восстановить остальные."""
    actions = [
        ('name', lambda: bot.set_my_name('Qonaq AI Bot')),
        ('name_ru', lambda: bot.set_my_name('Qonaq AI Bot', language_code='ru')),
        ('name_en', lambda: bot.set_my_name('Qonaq AI Bot', language_code='en')),
        ('name_kk', lambda: bot.set_my_name('Qonaq AI Bot', language_code='kk')),
        ('description', lambda: bot.set_my_description(
            'Школьный гид по Павлодару и Баянаулу. '
            'Советы для поездки, питание и прогулки. Начните с /start.')),
        ('short_description', lambda: bot.set_my_short_description(
            'Школьный туристический помощник: Павлодар и Баянаул.')),
        ('commands', lambda: bot.set_my_commands([
            types.BotCommand('start', 'Открыть меню'),
            types.BotCommand('help', 'Помощь'),
            types.BotCommand('about', 'О проекте')]))
    ]
    statuses = {label: 'pending' for label, _ in actions}
    app.config['PROFILE_STATUS'] = statuses

    def apply(label, action, attempt=0):
        try:
            action()
            statuses[label] = 'restored'
            LOG.info('Profile %s restored', label)
            if label == 'name' and 'ikhdev' in previous_name.casefold():
                try:
                    bot.remove_my_profile_photo()
                    LOG.info('Foreign advertising profile photo removed')
                except Exception as exc:
                    LOG.warning('Profile photo cleanup failed: type=%s', type(exc).__name__)
        except apihelper.ApiTelegramException as exc:
            detail = re.sub(r'\b\d{6,}:[A-Za-z0-9_-]{20,}', '<TOKEN_REMOVED>', exc.description)
            statuses[label] = 'failed'
            LOG.warning('Profile %s failed: code=%s detail=%s', label, exc.error_code, detail[:250])
            retry_after = exc.result_json.get('parameters', {}).get('retry_after')
            if exc.error_code == 429 and type(retry_after) is int and retry_after > 0 and attempt < 2:
                statuses[label] = 'rate_limited'
                timer = threading.Timer(retry_after + 1, apply, args=(label, action, attempt + 1))
                timer.daemon = True
                timer.start()
        except Exception as exc:
            statuses[label] = 'failed'
            LOG.warning('Profile %s failed: type=%s', label, type(exc).__name__)

    for label, action in actions:
        apply(label, action)


def configure_telegram(app, bot, base_url, secret, expected_username):
    try:
        me = bot.get_me()
        if (me.username or '').casefold() != expected_username.casefold():
            app.config['BOT_STATE'] = 'wrong_bot_token'
            LOG.error('Token belongs to a different bot. Check BOT_TOKEN.')
            return
        url = base_url.rstrip('/') + '/telegram'
        current = bot.get_webhook_info()
        # При восстановлении очистить старую очередь; при пробуждении сохранить.
        bot.set_webhook(url=url, secret_token=secret, max_connections=1,
                        allowed_updates=['message'], drop_pending_updates=current.url != url,
                        timeout=15)
        app.config['BOT_READY'] = True
        app.config['BOT_STATE'] = 'ready'
        LOG.info('Webhook ready for @%s', me.username)
        restore_profile(app, bot, getattr(me, 'first_name', ''))
    except Exception as exc:
        app.config['BOT_STATE'] = 'telegram_setup_failed'
        LOG.error('Telegram setup failed: type=%s. Check token and restart service.',
                  type(exc).__name__)


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=10000)
    args = parser.parse_args()
    port = int(os.environ.get('PORT', args.port))
    token = os.environ.get('BOT_TOKEN', '').strip()
    base_url = os.environ.get('WEBHOOK_URL', os.environ.get('RENDER_EXTERNAL_URL', '')).strip()
    secret = os.environ.get('WEBHOOK_SECRET', '').strip()
    expected = os.environ.get('EXPECTED_BOT_USERNAME', 'qonaq_pavl_tour_bot').lstrip('@')
    parts = urlsplit(base_url)
    valid = bool(token and re.fullmatch(r'[A-Za-z0-9_-]{32,256}', secret)
                 and parts.scheme == 'https' and parts.hostname
                 and not parts.username and not parts.password and not parts.query and not parts.fragment)
    if token and hashlib.sha256(token.encode()).hexdigest() in COMPROMISED_TOKEN_HASHES:
        valid = False
        LOG.error('Published token rejected. Revoke it in BotFather and set a NEW BOT_TOKEN.')
    bot = None
    if valid:
        try:
            bot = telebot.TeleBot(token, threaded=False)
        except ValueError:
            valid = False
    app = create_app(bot, secret, os.environ.get('UPDATE_DB', 'updates.sqlite3'))
    if valid:
        app.config['BOT_STATE'] = 'connecting'
        threading.Thread(target=configure_telegram,
                         args=(app, bot, base_url, secret, expected), daemon=True).start()
    else:
        LOG.warning('Waiting for configuration. Set NEW BOT_TOKEN, WEBHOOK_SECRET and WEBHOOK_URL in Render.')
    LOG.info('%s listening on port %s', VERSION, port)
    serve(app, host='0.0.0.0', port=port, threads=4)


if __name__ == '__main__':
    main()
