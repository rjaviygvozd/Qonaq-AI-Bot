import telebot
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Оригинальный токен из проекта Кристины
BOT_TOKEN = "8734369365:AAGnuppzo749GUgkc4wnU0vTQEt2oFbP__M"
bot = telebot.TeleBot(BOT_TOKEN)

# --- МИКРО ВЕБ-СЕРВЕР ДЛЯ СТАТУСА LIVE НА RENDER ---
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        return  # Отключаем лишние логи в консоли

def run_web_server():
    server_address = ('0.0.0.0', 10000)  # Тот самый порт 10000, который ищет Render
    httpd = HTTPServer(server_address, PingHandler)
    print("=== ВЕБ-СЕРВЕР ДЛЯ ПОРТА 10000 ЗАПУЩЕН ===")
    httpd.serve_forever()
# --------------------------------------------------

# Приветственное сообщение на кнопку "Старт"
@bot.message_handler(commands=['start'])
def cmd_start(message):
    welcome_text = (
        "Здравствуйте! Я **Qonaq AI Bot** — ваш гид по Павлодарской области и Баянаулу. 🤖\n\n"
        "Задайте мне вопрос, например:\n"
        "• *Где поесть в Баянауле до 4000 тенге?*\n"
        "• *Хочу на Жасыбай, что взять с собой?*\n"
        "• *Составь маршрут на 1 день в Павлодаре.*"
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown")

# Обработка текстовых вопросов
@bot.message_handler(func=lambda message: True)
def handle_user_query(message):
    user_text = message.text.lower()
    bot.send_chat_action(message.chat.id, 'typing')
    time.sleep(1)

    if "жасыбай" in user_text or "взять" in user_text:
        bot_reply = (
            "👋 Привет! Для поездки на Жасыбай рекомендую взять:\n"
            "• Вход — 500₸\n"
            "• Парковка — 1000₸\n\n"
            "📦 Что взять с собой:\n"
            "• Купальник — озеро прохладное, но в солнечный день можно купаться\n"
            "• Тёплая одежда — вечером и ночью бывает ветер, прохладно\n"
            "• Наличка — Kaspi не ловит, терминалов почти нет\n\n"
            "⛺ Совет: солнцезащитный крем и вода, мест для покупок там нет. Хорошего отдыха!"
        )
    elif "поесть" in user_text or "4000" in user_text or "кафе" in user_text:
        bot_reply = (
            "Вот 2 отличных варианта до 4000 ₸ в Баянауле 🙂\n\n"
            "📍 **Кафе «Балдай»**\n"
            "• Лагман — 1600 ₸\n"
            "• Плов — 1800 ₸\n"
            "💵 Средний чек: ~3000–3500 ₸\n"
            "📍 Ул. Абая 21, центр, 5 мин от пляжа\n"
            "✅ Оплата: Kaspi QR доступен\n\n"
            "📍 **Кафе «Самал»**\n"
            "• Бешбармак — 2200 ₸\n"
            "• Самса — 350  ₸ / шт\n"
            "💵 Средний чек: ~2600–3000 ₸\n"
            "📍 Ул. Нурсултана 8, рядом с парком\n"
            "✅ Оплата: Kaspi QR доступен"
        )
    elif "маршрут" in user_text or "1 день" in user_text or "павлодар" in user_text:
        bot_reply = (
            "Готов маршрут на 1 день в Павлодаре! 🇰🇿\n\n"
            "🏛 **Утро — 09:00–12:00**\n"
            "• Набережная Иртыша — прогулка вдоль реки\n"
            "• Краеведческий музей им. Г. Потанина (Вход: 500 ₸)\n\n"
            "☀️ **Обед — 12:30–14:00**\n"
            "• Кафе \"Шанырак\" — средний чек: 2500–3500 ₸\n\n"
            "🏙 **Вечер — 15:00–19:00**\n"
            "• Парк Гагарина и ТРЦ \"Batyr Mall\""
        )
    else:
        bot_reply = "Я получил ваш запрос! Информации по этому объекту пока нет в базе. Рекомендую обратиться в ТИЦ Павлодара."

    bot.send_message(message.chat.id, bot_reply, parse_mode="Markdown")

if __name__ == "__main__":
    # Запускаем веб-сервер в отдельном потоке, чтобы он отвечал Render
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    print("=== ОРИГИНАЛЬНЫЙ БОТ КРИСТИНЫ ЗАПУЩЕН ===")
    bot.infinity_polling()
