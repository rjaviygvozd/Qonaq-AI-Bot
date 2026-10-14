import copy
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import telebot
from telebot import apihelper
import bot as project

SECRET = 'test_secret_for_webhook_1234567890'
HEADERS = {'X-Telegram-Bot-Api-Secret-Token': SECRET}


def update(update_id=1, text='/start'):
    return {'update_id': update_id, 'message': {
        'message_id': update_id, 'date': 1,
        'from': {'id': 10, 'is_bot': False, 'first_name': 'Test'},
        'chat': {'id': 10, 'type': 'private'}, 'text': text}}


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, 'updates.sqlite3')
        self.bot = telebot.TeleBot('123456:TEST_TOKEN_ONLY_NO_NETWORK', threaded=False)
        self.app = project.create_app(self.bot, SECRET, self.db_path)
        self.app.config.update(TESTING=True, BOT_READY=True, BOT_STATE='ready')
        self.outbound = patch.object(apihelper, '_make_request', return_value={
            'message_id': 100, 'date': 1, 'chat': {'id': 10, 'type': 'private'}, 'text': 'test'})
        self.api = self.outbound.start()

    def tearDown(self):
        self.outbound.stop()
        self.app.extensions['update_store'].db.close()
        self.tmp.cleanup()

    def post(self, data, headers=None):
        with self.app.test_client() as client:
            return client.post('/telegram', json=data, headers=HEADERS if headers is None else headers)

    def test_repeated_start_sends_one_message(self):
        for _ in range(25):
            self.assertEqual(self.post(update()).status_code, 200)
        self.assertEqual(self.api.call_count, 1)
        self.assertEqual(self.api.call_args.args[1], 'sendMessage')
        payload = self.api.call_args.kwargs['params']
        self.assertEqual(payload['text'], project.WELCOME)
        self.assertEqual(len(json.loads(payload['reply_markup'])['keyboard']), 3)

    def test_concurrent_duplicates_send_once(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(lambda _: self.post(update()).status_code, range(24)))
        self.assertEqual(set(codes), {200})
        self.assertEqual(self.api.call_count, 1)

    def test_different_messages_and_buttons(self):
        topics = [('/start', project.WELCOME), ('/help', project.HELP),
                  ('/about', project.ABOUT), ('🏖 Что взять на Жасыбай?', project.LAKE),
                  ('🍽 Где поесть до 4000 тенге?', project.FOOD),
                  ('🚶 Маршрут на 1 день в Павлодаре', project.ROUTE)]
        for number, (text, expected) in enumerate(topics, 1):
            self.assertEqual(self.post(update(number, text)).status_code, 200)
            self.assertEqual(self.api.call_args.kwargs['params']['text'], expected)
        self.assertEqual(self.api.call_count, len(topics))

    def test_untrusted_requests_do_not_send(self):
        self.assertEqual(self.post(update(), {}).status_code, 403)
        self.assertEqual(self.post(update(), {
            'X-Telegram-Bot-Api-Secret-Token': 'wrong'}).status_code, 403)
        self.assertEqual(self.post(update(), {
            'X-Telegram-Bot-Api-Secret-Token': 'неверный'}).status_code, 403)
        self.assertEqual(self.post([]).status_code, 400)
        self.assertEqual(self.post({'update_id': '1'}).status_code, 400)
        self.assertEqual(self.api.call_count, 0)

    def test_ignore_service_events_photos_bots_and_groups(self):
        photo = update()
        del photo['message']['text']
        photo['message']['photo'] = []
        from_bot = copy.deepcopy(update(2))
        from_bot['message']['from']['is_bot'] = True
        group = update(3)
        group['message']['chat']['type'] = 'group'
        service = update(4)
        del service['message']['text']
        service['message']['pinned_message'] = update()['message']
        edited = {'update_id': 5, 'edited_message': update()['message']}
        for data in [photo, from_bot, group, service, edited]:
            self.assertEqual(self.post(data).status_code, 200)
        self.assertEqual(self.api.call_count, 0)

    def test_timeout_never_triggers_blind_resend(self):
        self.api.side_effect = TimeoutError('network result unknown')
        self.assertEqual(self.post(update()).status_code, 200)
        self.assertEqual(self.post(update()).status_code, 200)
        self.assertEqual(self.api.call_count, 1)

    def test_429_can_retry_without_duplicate_send(self):
        error = apihelper.ApiTelegramException('sendMessage', None, {
            'ok': False, 'error_code': 429, 'description': 'Too Many Requests'})
        success = self.api.return_value
        self.api.side_effect = [error, success]
        self.assertEqual(self.post(update()).status_code, 503)
        self.assertEqual(self.post(update()).status_code, 200)
        self.assertEqual(self.post(update()).status_code, 200)
        self.assertEqual(self.api.call_count, 2)

    def test_no_side_effects_from_opening_site(self):
        with self.app.test_client() as client:
            for path in ['/', '/health']:
                self.assertEqual(client.get(path).status_code, 200)
            self.assertEqual(client.get('/telegram').status_code, 405)
        self.assertEqual(self.api.call_count, 0)

    def test_no_configuration_means_no_messages(self):
        self.app.config['BOT_READY'] = False
        with self.app.test_client() as client:
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(client.get('/health').status_code, 503)
        self.assertEqual(self.post(update()).status_code, 503)
        self.api.assert_not_called()

    def test_dedup_survives_app_restart_if_database_exists(self):
        self.post(update())
        other = project.create_app(self.bot, SECRET, self.db_path)
        other.config['BOT_READY'] = True
        try:
            with other.test_client() as client:
                self.assertEqual(client.post('/telegram', json=update(), headers=HEADERS).status_code, 200)
            self.assertEqual(self.api.call_count, 1)
        finally:
            other.extensions['update_store'].db.close()

    def test_setup_replaces_foreign_webhook_and_preserves_queue_on_wake(self):
        fake = MagicMock()
        fake.get_me.return_value = SimpleNamespace(username='Qonaq_Pavlodar_bot')
        for previous, drop in [('https://foreign.invalid/webhook', True),
                               ('https://example.invalid/telegram', False)]:
            fake.get_webhook_info.return_value = SimpleNamespace(url=previous)
            project.configure_telegram(self.app, fake, 'https://example.invalid',
                                       SECRET, 'Qonaq_Pavlodar_bot')
            self.assertEqual(fake.set_webhook.call_args.kwargs['drop_pending_updates'], drop)
            self.assertEqual(fake.set_webhook.call_args.kwargs['secret_token'], SECRET)
            self.assertTrue(self.app.config['BOT_READY'])
        self.assertIn(unittest.mock.call('Qonaq AI Bot'), fake.set_my_name.call_args_list)

    def test_wrong_bot_token_does_not_change_webhook(self):
        fake = MagicMock()
        fake.get_me.return_value = SimpleNamespace(username='Wrong_bot')
        self.app.config['BOT_READY'] = False
        project.configure_telegram(self.app, fake, 'https://example.invalid', SECRET,
                                   'Qonaq_Pavlodar_bot')
        self.assertFalse(self.app.config['BOT_READY'])
        self.assertEqual(self.app.config['BOT_STATE'], 'wrong_bot_token')
        fake.set_webhook.assert_not_called()

    def test_profile_name_failure_does_not_block_commands_and_description(self):
        fake = MagicMock()
        fake.set_my_name.side_effect = apihelper.ApiTelegramException('setMyName', None, {
            'ok': False, 'error_code': 400, 'description': 'Name temporarily restricted'})
        project.restore_profile(self.app, fake, 'Ikhdev API — AI Gateway')
        fake.set_my_description.assert_called_once()
        fake.set_my_short_description.assert_called_once()
        fake.set_my_commands.assert_called_once()
        fake.remove_my_profile_photo.assert_not_called()
        self.assertEqual(self.app.config['PROFILE_STATUS']['commands'], 'restored')

    def test_foreign_avatar_removed_after_name_is_restored(self):
        fake = MagicMock()
        project.restore_profile(self.app, fake, 'Ikhdev API — AI Gateway')
        fake.remove_my_profile_photo.assert_called_once()
        fake.reset_mock()
        project.restore_profile(self.app, fake, 'Qonaq AI Bot')
        fake.remove_my_profile_photo.assert_not_called()


if __name__ == '__main__':
    unittest.main()
