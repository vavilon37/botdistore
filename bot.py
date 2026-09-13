import asyncio
import logging
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
import database as db
import keyboards as kb
from handlers import router as user_router, process_price_text
from admin import router as admin_router
from price_monitor import check_and_process, CHECK_INTERVAL, TRACKED_POSTS

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    datefmt="%d.%m %H:%M:%S",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(int(i) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit())
SOURCE_BOT_ID = int(os.getenv("SOURCE_BOT_ID", "0"))


async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан. Проверьте переменные окружения в панели хостинга."
        )
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS пуст — отчёты мониторинга отправлять некому")

    await db.init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(admin_router)
    dp.include_router(user_router)

    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        name = message.from_user.first_name
        is_admin = message.from_user.id in ADMIN_IDS
        menu = kb.admin_menu() if is_admin else kb.main_menu()
        admin_note = "\n\n🔑 Вы вошли как администратор." if is_admin else ""
        await db.register_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name
        )
        used = await db.count_used_items()
        await message.answer(
            f"Привет, {name}! 👋\n\n"
            f"Добро пожаловать в магазин техники.{admin_note}\n\n"
            f"♻️ Б/У в наличии: <b>{used}</b> шт.\n\n"
            f"🎁 Реферальная программа: приведи друга — получи 500 ₽\n\n"
            f"Выберите раздел:",
            parse_mode="HTML",
            reply_markup=menu
        )

    async def monitor_loop():
        silent_process = lambda b, a, t: process_price_text(b, a, t, silent=True)
        # Первый прогон — сразу force: после пересборки контейнера кэши пустые,
        # и без этого раздел цен простоял бы весь CHECK_INTERVAL.
        first = True
        while True:
            if not first:
                await asyncio.sleep(CHECK_INTERVAL)
            first = False
            try:
                stats = await check_and_process(bot, ADMIN_IDS, silent_process, force=True)
            except Exception:
                logger.exception("Сбой в цикле мониторинга, повтор через интервал")
                continue
            lines = [
                "🔄 <b>Автообновление цен</b>",
                f"📡 Постов получено: <b>{stats['fetched']}</b> / {len(TRACKED_POSTS)}",
                f"✅ Обработано: <b>{stats['processed']}</b>",
            ]
            if stats["failed_fetch"]:
                lines.append(f"⚠️ Недоступно: {len(stats['failed_fetch'])} постов")
            if stats.get("restored"):
                lines.append(
                    "🛑 Парсинг пуст, показываем прошлые цены: "
                    + ", ".join(stats["restored"])
                )
            if stats["errors"]:
                for err in stats["errors"][:3]:
                    lines.append(f"❌ {err}")
            report = "\n".join(lines)
            for aid in ADMIN_IDS:
                try:
                    await bot.send_message(aid, report, parse_mode="HTML")
                except Exception:
                    pass

    monitor_task = asyncio.create_task(monitor_loop())

    logger.info("Бот запущен, постов в мониторинге: %d", len(TRACKED_POSTS))
    try:
        await dp.start_polling(bot)
    finally:
        monitor_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
