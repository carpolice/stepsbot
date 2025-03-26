import logging
from datetime import datetime, time
import pytz
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
    JobQueue
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials

TELEGRAM_TOKEN = 'your-token'
GOOGLE_CREDS = 'google.json'
SPREADSHEET_NAME = "stepsbot"
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

FIRST_NAME, LAST_NAME, BADGE = range(3)
AWAIT_PHOTO, AWAIT_STEPS = range(3, 5)

scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS, scope)
client = gspread.authorize(creds)
sheet = client.open(SPREADSHEET_NAME).sheet1

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class UserCache:
    def __init__(self):
        self.users = set()
        self.entries = {}
        self.badges = {}
        self.expected_headers = [
            'Имя', 'Фамилия', 'Telegram ID',
            'Username', 'Табельный номер',
            'Шаги', 'ID фото', 'Дата'
        ]

    def update_cache(self):
        try:
            records = sheet.get_all_records(expected_headers=self.expected_headers)
            self.users = set()
            self.badges.clear()
            self.entries.clear()

            for row in records:
                user_id = str(row['Telegram ID'])
                self.users.add(user_id)
                self.badges[user_id] = row['Табельный номер']
                self.entries.setdefault(user_id, set()).add(row['Дата'])
        except Exception as e:
            logger.error(f"Ошибка кеша: {e}")

    def get_all_users(self):
        return list(self.users)


user_cache = UserCache()


def get_moscow_date():
    return datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")


async def handle_general_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    today = get_moscow_date()

    if user_id in user_cache.entries and today in user_cache.entries[user_id]:
        await update.message.reply_text(
            "⏳ Вы уже отправили данные сегодня. Следующую отправку можно будет сделать завтра!"
        )
        return

    await update.message.reply_text(
        "📸 Для отправки данных сначала пришлите фото, затем количество шагов"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if str(user.id) in user_cache.users:
        await update.message.reply_text(
            "✅ Вы зарегистрированы! Отправьте фото и количество шагов"
        )
        return ConversationHandler.END

    await update.message.reply_text("📝 Введите ваше имя:")
    return FIRST_NAME


async def handle_first_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['first_name'] = update.message.text.strip()
    await update.message.reply_text("📝 Введите фамилию:")
    return LAST_NAME


async def handle_last_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['last_name'] = update.message.text.strip()
    await update.message.reply_text("🔢 Введите табельный номер:")
    return BADGE


async def handle_badge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user = update.effective_user
        user_id = str(user.id)
        badge = update.message.text.strip()

        sheet.append_row([
            context.user_data['first_name'],
            context.user_data['last_name'],
            user_id,
            user.username or '',
            badge,
            '', '', get_moscow_date()
        ])

        # Сохраняем в кеш
        user_cache.users.add(user_id)
        user_cache.badges[user_id] = badge
        await update.message.reply_text(
            "✅ Регистрация завершена!\n"
            "📸 Теперь отправьте фото:"
        )
        return AWAIT_PHOTO
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка регистрации")
        return ConversationHandler.END


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("❌ Это не фото! Отправьте фото:")
        return AWAIT_PHOTO

    context.user_data['photo_id'] = update.message.photo[-1].file_id
    await update.message.reply_text("🔢 Теперь введите количество шагов:")
    return AWAIT_STEPS


async def handle_steps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.text.isdigit():
        await update.message.reply_text("❌ Введите число!")
        return AWAIT_STEPS

    try:
        user = update.effective_user
        user_id = str(user.id)
        today = get_moscow_date()

        # Получаем табельный номер из кеша
        badge = user_cache.badges.get(user_id, 'НЕИЗВЕСТНО')

        sheet.append_row([
            context.user_data.get('first_name', ''),
            context.user_data.get('last_name', ''),
            user_id,
            user.username or '',
            badge,
            int(update.message.text),
            context.user_data['photo_id'],
            today
        ])

        user_cache.entries.setdefault(user_id, set()).add(today)
        await update.message.reply_text(
            f"✅ Данные сохранены!\n"
            f"🕒 Следующую отправку можно будет сделать завтра"
        )

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка сохранения")

    context.user_data.clear()
    return ConversationHandler.END


async def send_daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    try:
        user_cache.update_cache()
        today = get_moscow_date()
        users = user_cache.get_all_users()

        for user_id in users:
            if user_id not in user_cache.entries or today not in user_cache.entries[user_id]:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⏰ Напоминание: не забудьте отправить сегодняшние данные!"
                )
    except Exception as e:
        logger.error(f"Ошибка уведомлений: {e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Операция отменена")
    return ConversationHandler.END


def main():
    try:
        user_cache.update_cache()
        app = Application.builder().token(TELEGRAM_TOKEN).build()

        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", start)],
            states={
                FIRST_NAME: [MessageHandler(filters.TEXT, handle_first_name)],
                LAST_NAME: [MessageHandler(filters.TEXT, handle_last_name)],
                BADGE: [MessageHandler(filters.TEXT, handle_badge)],
                AWAIT_PHOTO: [MessageHandler(filters.PHOTO, handle_photo)],
                AWAIT_STEPS: [MessageHandler(filters.TEXT & filters.Regex(r'^\d+$'), handle_steps)]
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            allow_reentry=True
        )

        app.add_handler(conv_handler)
        app.add_handler(MessageHandler(filters.ALL, handle_general_messages))

        # Планировщики
        app.job_queue.run_repeating(
            lambda _: user_cache.update_cache(),
            interval=3600,
            first=10
        )

        app.job_queue.run_daily(
            send_daily_reminder,
            time=time(22, 0, tzinfo=MOSCOW_TZ),
            days=tuple(range(7)),
            name="daily_reminder"
        )

        logger.info("Бот запущен")
        app.run_polling()

    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")


if __name__ == "__main__":
    main()
