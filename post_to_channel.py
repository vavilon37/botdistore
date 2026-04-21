import asyncio
import os
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # добавь в .env: CHANNEL_ID=-100xxxxxxxxxx

POST_TEXT = """🛍 distore — техника Apple

Новая и б/у техника по выгодным ценам.
Всё проверено, фото и описание к каждому товару.

━━━━━━━━━━━━━━━━━━━━
📱 СМАРТФОНЫ NEW
iPhone 12 / 13 / 14 / 15 / 16 / 17
iPhone Air · iPhone 17 Pro · iPhone 17 Pro Max
Все цвета · все объёмы памяти

♻️ СМАРТФОНЫ Б/У
iPhone с рук — проверенные, с фото
Состояния: Как новое · Хорошее · Удовлетворительное
━━━━━━━━━━━━━━━━━━━━
📟 ПЛАНШЕТЫ
iPad 10 · iPad mini 6
iPad Air M1 / M2 / M3 / M4 (11" и 13")
iPad Pro M2 / M4 / M5 (11" и 13")
+ Стилусы Apple Pencil и клавиатуры Magic Keyboard

🖥 МАКИ
MacBook Air 13" / 15" — M1, M2, M3, M5
MacBook Pro 14" / 16" — M1–M5 Pro/Max
iMac 24" — M1, M3, M4
Mac mini M1 / M2 / M4 · Mac Studio M1–M4

🎧 НАУШНИКИ
AirPods 2 / 3 / 4 / 4 ANC
AirPods Pro 1 / 2 / 3
AirPods Max — Lightning и USB-C (все цвета)

🔌 АКСЕССУАРЫ
Кабели, чехлы, зарядки и прочее
━━━━━━━━━━━━━━━━━━━━
🔧 Ремонт техники — пишите, разберёмся

🎁 Реферальная программа
Приведи друга — получи 500 ₽"""

KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="✍️ Написать", url="https://t.me/idistoreman"),
        InlineKeyboardButton(text="🤖 Каталог", url="https://t.me/idistor_bot"),
    ]
])


async def main():
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=POST_TEXT,
        reply_markup=KEYBOARD,
    )
    print("Пост отправлен!")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
