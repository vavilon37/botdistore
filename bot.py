import asyncio
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage

import database as db
import keyboards as kb
from handlers import router as user_router
from admin import router as admin_router

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(int(i) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit())


async def main():
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
        total = await db.count_active_items()
        await message.answer(
            f"Привет, {name}! 👋\n\n"
            f"Добро пожаловать в магазин техники.{admin_note}\n\n"
            f"Сейчас в наличии <b>{total}</b> товаров. Выберите раздел:",
            parse_mode="HTML",
            reply_markup=menu
        )

    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
