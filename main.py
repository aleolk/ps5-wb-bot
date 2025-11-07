import telebot
import requests
from bs4 import BeautifulSoup
import schedule
import time
import threading
import json
import os
from dotenv import load_dotenv

# Загрузка токена
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("ОШИБКА: Добавь BOT_TOKEN в .env")
    exit()

bot = telebot.TeleBot(BOT_TOKEN)

# Файлы
SEEN_FILE = 'seen_products.json'
RANGES_FILE = 'user_ranges.json'

# Загрузка seen
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, 'r', encoding='utf-8') as f:
            return set(json.load(f).get('seen_nm_ids', []))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, 'w', encoding='utf-8') as f:
        json.dump({'seen_nm_ids': list(seen)}, f, ensure_ascii=False, indent=2)

seen_nm_ids = load_seen()

# Загрузка настроек пользователей
def load_user_settings():
    if os.path.exists(RANGES_FILE):
        with open(RANGES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_user_settings(settings):
    with open(RANGES_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

user_settings = load_user_settings()  # {chat_id: {'min': int, 'max': int, 'filters': ['digital', 'disc']}}

# Ключевые слова для фильтрации
FILTER_KEYWORDS = {
    'digital': ['digital', 'цифровая', 'цифровое', 'без привода', 'без диска', 'диск-free'],
    'disc': ['disc', 'диск', 'привод', 'с приводом', 'blu-ray', 'blu ray', 'с диском', 'дисковая'],
    'slim': ['slim', 'тонкая', 'slim edition', 'slim версия', 'компактная'],
    '1tb': ['1tb', '1 tb', '1тб', '1000 гб', '1 тб', '1тб ссд'],
    'standard': ['825', 'standard', 'обычная', 'оригинал', '825 гб', 'стандартная']
}

def matches_filter(name_lower, filters):
    if not filters:
        return True
    for f in filters:
        for keyword in FILTER_KEYWORDS.get(f, []):
            if keyword in name_lower:
                return True
    return False

def scrape_ps5_new_listings(min_price=None, max_price=None, filters=None):
    url = 'https://www.wildberries.ru/catalog/0/search.aspx?search=playstation%205'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    new_products = []
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        cards = soup.find_all('div', class_='product-card__wrapper')[:30]

        for card in cards:
            try:
                name_elem = card.find('span', class_='product-card__name')
                name = name_elem.text.strip().lower() if name_elem else ''
                if not name:
                    continue

                # nm_id
                nm_id = card.get('data-nm-id')
                if not nm_id:
                    link = card.find('a', href=True)
                    if link and '/catalog/' in link['href']:
                        nm_id = link['href'].split('/')[2]
                if not nm_id or not nm_id.isdigit():
                    continue
                nm_id = int(nm_id)

                # Цена
                price_elem = card.find('ins', class_='price-block__final-price') or \
                             card.find('span', class_='price__lower-price')
                price_text = price_elem.text.strip().replace('₽', '').replace(' ', '') if price_elem else '0'
                price = int(''.join(filter(str.isdigit, price_text))) if price_text else 0

                # URL
                link_elem = card.find('a', href=True)
                product_url = 'https://www.wildberries.ru' + link_elem['href'] if link_elem else f'https://www.wildberries.ru/catalog/{nm_id}/detail.aspx'

                # Фото
                img = card.find('img')
                photo = 'https:' + img['src'] if img and img.get('src') else None

                # Фильтры
                if filters and not matches_filter(name, filters):
                    continue
                if (min_price and price < min_price) or (max_price and price > max_price):
                    continue

                if nm_id not in seen_nm_ids:
                    new_products.append({
                        'name': name_elem.text.strip(),
                        'nm_id': nm_id,
                        'price': price,
                        'url': product_url,
                        'photo': photo
                    })
                    seen_nm_ids.add(nm_id)
            except:
                continue

        save_seen(seen_nm_ids)
        return new_products
    except Exception as e:
        print(f"Ошибка парсинга: {e}")
        return []

def check_new_ps5():
    for chat_id, settings in user_settings.items():
        min_p = settings.get('min_price')
        max_p = settings.get('max_price')
        filters = settings.get('filters', [])
        new_prods = scrape_ps5_new_listings(min_p, max_p, filters)

        for prod in new_prods:
            filter_label = ', '.join([f.upper() for f in filters]) if filters else 'любой'
            message = (
                f"НОВАЯ PS5!\n\n"
                f"Название: {prod['name']}\n"
                f"Цена: {prod['price']} ₽\n"
                f"Фильтр: {filter_label}\n"
                f"Ссылка: {prod['url']}"
            )
            try:
                if prod['photo']:
                    bot.send_photo(chat_id, prod['photo'], caption=message, parse_mode='HTML')
                else:
                    bot.send_message(chat_id, message)
            except Exception as e:
                print(f"Ошибка отправки: {e}")

# === КОМАНДЫ ===
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, (
        "PS5 ОХОТНИК\n\n"
        "Команды:\n"
        "/ps5_range 40000 60000 — ценовой диапазон\n"
        "/ps5_filter digital disc slim — фильтр по типу\n"
        "/list — твои настройки\n"
        "/clear — сбросить всё\n\n"
        "Уведомления каждые 5 мин!"
    ))

@bot.message_handler(commands=['ps5_range'])
def set_range(message):
    try:
        parts = message.text.split()
        if len(parts) != 3:
            bot.reply_to(message, "Формат: /ps5_range <min> <max>")
            return
        min_p, max_p = int(parts[1]), int(parts[2])
        chat_id = str(message.chat.id)
        if chat_id not in user_settings:
            user_settings[chat_id] = {}
        user_settings[chat_id]['min_price'] = min_p
        user_settings[chat_id]['max_price'] = max_p
        save_user_settings(user_settings)
        bot.reply_to(message, f"Диапазон: {min_p}–{max_p} ₽")
    except:
        bot.reply_to(message, "Ошибка. Только цифры!")

@bot.message_handler(commands=['ps5_filter'])
def set_filter(message):
    try:
        parts = message.text.lower().split()
        if len(parts) < 2:
            bot.reply_to(message, "Формат: /ps5_filter digital disc slim\nВарианты: digital, disc, slim, 1tb, standard")
            return
        filters = parts[1:]
        valid = ['digital', 'disc', 'slim', '1tb', 'standard']
        filters = [f for f in filters if f in valid]
        if not filters:
            bot.reply_to(message, "Неверные фильтры. Доступно: digital, disc, slim, 1tb, standard")
            return

        chat_id = str(message.chat.id)
        if chat_id not in user_settings:
            user_settings[chat_id] = {}
        user_settings[chat_id]['filters'] = filters
        save_user_settings(user_settings)
        bot.reply_to(message, f"Фильтр: {', '.join([f.upper() for f in filters])}")
    except:
        bot.reply_to(message, "Ошибка фильтра")

@bot.message_handler(commands=['list'])
def list_settings(message):
    chat_id = str(message.chat.id)
    if chat_id not in user_settings:
        bot.reply_to(message, "Настроек нет. Используй /ps5_range и /ps5_filter")
        return
    s = user_settings[chat_id]
    min_p = s.get('min_price', '—')
    max_p = s.get('max_price', '—')
    filters = ', '.join([f.upper() for f in s.get('filters', [])]) if s.get('filters') else 'любой'
    bot.reply_to(message, f"Диапазон: {min_p}–{max_p} ₽\nФильтр: {filters}")

@bot.message_handler(commands=['clear'])
def clear(message):
    chat_id = str(message.chat.id)
    if chat_id in user_settings:
        del user_settings[chat_id]
        save_user_settings(user_settings)
        bot.reply_to(message, "Настройки сброшены")
    else:
        bot.reply_to(message, "Нечего сбрасывать")

# === ЗАПУСК ===
def run_scheduler():
    schedule.every(5).minutes.do(check_new_ps5)
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    threading.Thread(target=run_scheduler, daemon=True).start()
    print("PS5 Бот запущен с фильтрами...")
    bot.infinity_polling()

# === 24/7 FLASK СЕРВЕР + ПИНГЕР (НЕ СПИТ) ===
from flask import Flask
import threading
import requests
import time

app = Flask('')

@app.route('/')
def home():
    return "PS5 Bot is alive! 🕹️"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    url = f"https://{os.getenv('REPL_SLUG', 'ps5-wb-bot')}.{os.getenv('REPL_OWNER', 'aleolk')}.repl.co"
    while True:
        try:
            requests.get(url, timeout=5)
            print(f"Пинг отправлен: {url}")
        except Exception as e:
            print(f"Пинг ошибка: {e}")
        time.sleep(240)  # Каждые 4 минуты

threading.Thread(target=run_flask, daemon=True).start()
threading.Thread(target=keep_alive, daemon=True).start()

print("Flask сервер запущен — бот НЕ СПИТ 24/7")
print("URL: " + f"https://{os.getenv('REPL_SLUG', 'ps5-wb-bot')}.{os.getenv('REPL_OWNER', 'aleolk')}.repl.co")

# Запуск Telegram бота
bot.infinity_polling()