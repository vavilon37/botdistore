import json
import os
import re
from datetime import datetime, timezone, timedelta

MSK = timezone(timedelta(hours=3))


def _now_msk() -> str:
    return datetime.now(MSK).strftime("%d.%m.%Y %H:%M")

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InputMediaPhoto
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

import database as db
import keyboards as kb

PRICE_CACHE_FILE = os.path.join(os.path.dirname(__file__), "price_cache.json")
PRICE_MARKUP = 2000


# Строки-пояснения которые нужно сохранять (в конце сообщения поставщика)
_KEEP_FOOTNOTE_PATTERNS = [
    r"🚘",
    r"^\*\s*—",
    r"^Act",
    r"📱\s*Только",
    r"eSIM\s*\+",
    r"физическ",
    r"🚩",
    r"^\s*🇧🇭",
    r"^\s*🇪🇺",
    r"^\s*🇨🇳",
    r"iPhone\s+1[2-9]\s+Air",
]


def _load_cache() -> dict:
    if os.path.exists(PRICE_CACHE_FILE):
        with open(PRICE_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict):
    with open(PRICE_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _detect_series(text: str) -> str | None:
    for series in ["17", "16", "15", "14", "13", "12"]:
        if re.search(rf"\b{series}\s*(Pro|Plus|Max|Air|mini|\d)", text):
            return series
    return None


def _is_iphone_price_line(line: str) -> bool:
    """Строка с ценой на iPhone: начинается с номера серии 12-17."""
    # Допустимые форматы начала строки:
    # "17 Pro", "17 Pro Max", "17 Air", "17е", "17e", "17 256", "16 128" и т.д.
    has_model_start = bool(re.match(
        r"^\s*1[2-7]\s*(Pro|Plus|Max|Air|mini|[еe]\b|\d{2,4}\b)",
        line, re.IGNORECASE
    ))
    has_price = bool(re.search(r"\d{2,3}[.]\d{3}", line))
    return has_model_start and has_price


def _is_footnote_line(line: str) -> bool:
    """Строки-пояснения в конце сообщения — сохраняем."""
    for pat in _KEEP_FOOTNOTE_PATTERNS:
        if re.search(pat, line):
            return True
    return False


def _filter_iphone_lines(text: str) -> str:
    """Оставляет только строки с ценами на iPhone + пояснения."""
    lines = text.split("\n")
    result = []
    in_footnote = False
    for line in lines:
        if _is_footnote_line(line):
            in_footnote = True
        if in_footnote or _is_iphone_price_line(line):
            result.append(line)
    # Убираем пустые строки в начале
    while result and not result[0].strip():
        result.pop(0)
    return "\n".join(result)


def _split_prices_and_footnotes(text: str) -> tuple[str, str]:
    """Разделяет текст на блок цен и блок пояснений."""
    lines = text.split("\n")
    price_lines = []
    footnote_lines = []
    in_footnote = False
    for line in lines:
        if not in_footnote and _is_footnote_line(line):
            in_footnote = True
        if in_footnote:
            footnote_lines.append(line)
        else:
            price_lines.append(line)
    return "\n".join(price_lines).strip(), "\n".join(footnote_lines).strip()



def _add_markup_to_prices(text: str) -> str:
    """Добавляет PRICE_MARKUP к ценам в строках с iPhone."""
    price_pattern = re.compile(r"(\d{2,3})[.\s](\d{3})")
    lines = text.split("\n")
    result = []
    for line in lines:
        if _is_iphone_price_line(line):
            def replace_price(m):
                price = int(m.group(1)) * 1000 + int(m.group(2))
                new_price = price + PRICE_MARKUP
                return f"{new_price // 1000}.{new_price % 1000:03d}"
            line = price_pattern.sub(replace_price, line)
        result.append(line)
    return "\n".join(result)

router = Router()

# Store filter state per user in memory
user_filters: dict = {}


async def safe_edit(call: CallbackQuery, text: str, **kwargs):
    try:
        await call.message.edit_text(text, **kwargs)
    except TelegramBadRequest:
        await call.answer()


async def safe_edit_markup(call: CallbackQuery, **kwargs):
    try:
        await call.message.edit_reply_markup(**kwargs)
    except TelegramBadRequest:
        await call.answer()


class SearchState(StatesGroup):
    waiting_query = State()


class PriceState(StatesGroup):
    waiting_range = State()


class FilterState(StatesGroup):
    choosing_category = State()
    choosing_condition = State()
    choosing_price = State()


def format_item(item) -> str:
    cond_emoji = {"Новое": "🆕", "Как новое": "✨", "Хорошее": "👍", "Удовлетворительное": "👌"}
    emoji = cond_emoji.get(item["condition"], "📦")
    text = (
        f"<b>{item['name']}</b>\n"
        f"📂 {item['category']} | {emoji} {item['condition']}\n"
        f"💰 <b>{item['price']:,} ₽</b>\n"
    )
    if item["description"]:
        text += f"\n{item['description']}"
    text += "\n\n✍️ Написать: @idistoreman"
    return text


MENU_CATEGORY_MAP = {
    "🔌 Аксессуары": "Аксессуары",
}


@router.message(F.text == "📱 Смартфоны New")
async def cmd_smartphones_new(message: Message):
    await message.answer(
        "📱 Смартфоны New — выберите категорию:",
        reply_markup=kb.new_smartphones_type_kb()
    )


@router.callback_query(F.data == "new_type:iphone")
async def cb_new_type_iphone(call: CallbackQuery):
    await call.message.edit_text(
        "🍎 iPhone — выберите серию:",
        reply_markup=kb.iphone_series_kb()
    )


@router.callback_query(F.data == "new_type:other")
async def cb_new_type_other(call: CallbackQuery):
    await call.answer("Раздел пока в разработке", show_alert=True)


@router.callback_query(F.data == "new_type:back")
async def cb_new_type_back(call: CallbackQuery):
    await call.message.edit_text(
        "📱 Смартфоны New — выберите категорию:",
        reply_markup=kb.new_smartphones_type_kb()
    )


@router.callback_query(F.data.startswith("new_series:"))
async def cb_new_series(call: CallbackQuery):
    from bot import ADMIN_IDS
    series = call.data.split(":")[1]
    cache = _load_cache()
    entry = cache.get(series)
    is_admin = call.from_user.id in ADMIN_IDS
    if not entry:
        if is_admin:
            await call.answer(
                f"⚠️ Цены iPhone {series} не загружены. Перешлите сообщение из канала поставщика.",
                show_alert=True
            )
        else:
            await call.answer(
                "Цены временно недоступны. Напишите администратору @idistoreman",
                show_alert=True
            )
        return

    updated = entry.get("updated_at", "—")
    disclaimer = (
        f"⚠️ Цены актуальны на момент последнего обновления. "
        f"Для уточнения пишите @idistoreman\n\n"
        f"📱 <b>iPhone {series}</b>  🕐 {updated}\n\n"
    )
    # Каждая часть — отдельное сохранённое сообщение
    msgs = entry.get("msgs", [entry["text"]])
    first = True
    for i, msg_text in enumerate(msgs):
        price_text, footnote_text = _split_prices_and_footnotes(msg_text)
        is_last = (i == len(msgs) - 1)
        body = (disclaimer if first else "") + price_text
        if first:
            await call.message.edit_text(body, parse_mode="HTML")
            first = False
        else:
            await call.message.answer(body, parse_mode="HTML")
        if footnote_text:
            await call.message.answer(footnote_text, parse_mode="HTML")
        if is_last:
            await call.message.answer("◀️", reply_markup=kb.new_series_back_kb())


@router.message(F.text == "📟 Планшеты")
async def cmd_tablets(message: Message):
    await message.answer(
        "📟 Актуальные iPad — цены у @idistoreman\n\n"
        "iPad 10 (10.9\", A14) 2022\n"
        "64 GB — ⚪ 🔵 🩷 🟡\n"
        "256 GB — ⚪ 🔵 🩷 🟡\n\n"
        "iPad mini 6 (8.3\", A15) 2021\n"
        "64 GB — 🟤 🤍 🩷 🟣\n"
        "256 GB — 🟤 🤍 🩷 🟣\n\n"
        "iPad Air M1 (10.9\") 2022\n"
        "64 GB — 🟤 🤍 🩷 🟣 🔵\n"
        "256 GB — 🟤 🤍 🩷 🟣 🔵\n\n"
        "iPad Air M2 11\" 2024\n"
        "128 GB — 🔵 🟣 🤍 🟤\n"
        "256 GB — 🔵 🟣 🤍 🟤\n"
        "512 GB — 🔵 🟣 🤍 🟤\n"
        "1 TB — 🔵 🟣 🤍 🟤\n\n"
        "iPad Air M2 13\" 2024\n"
        "128 GB — 🔵 🟣 🤍 🟤\n"
        "256 GB — 🔵 🟣 🤍 🟤\n"
        "512 GB — 🔵 🟣 🤍 🟤\n"
        "1 TB — 🔵 🟣 🤍 🟤\n\n"
        "iPad Air M3 11\" 2025\n"
        "128 GB — 🔵 🟣 🤍 🟤\n"
        "256 GB — 🔵 🟣 🤍 🟤\n"
        "512 GB — 🔵 🟣 🤍 🟤\n"
        "1 TB — 🔵 🟣 🤍 🟤\n\n"
        "iPad Air M3 13\" 2025\n"
        "128 GB — 🔵 🟣 🤍 🟤\n"
        "256 GB — 🔵 🟣 🤍 🟤\n"
        "512 GB — 🔵 🟣 🤍 🟤\n"
        "1 TB — 🔵 🟣 🤍 🟤\n\n"
        "iPad Air M4 11\" 2026\n"
        "128 GB — 🔵 🟣 🤍 🟤\n"
        "256 GB — 🔵 🟣 🤍 🟤\n"
        "512 GB — 🔵 🟣 🤍 🟤\n"
        "1 TB — 🔵 🟣 🤍 🟤\n\n"
        "iPad Air M4 13\" 2026\n"
        "128 GB — 🔵 🟣 🤍 🟤\n"
        "256 GB — 🔵 🟣 🤍 🟤\n"
        "512 GB — 🔵 🟣 🤍 🟤\n"
        "1 TB — 🔵 🟣 🤍 🟤\n\n"
        "iPad Pro M2 11\" 2022\n"
        "128 GB — ⚪ 🟤\n"
        "256 GB — ⚪ 🟤\n"
        "512 GB — ⚪ 🟤\n"
        "1 TB — ⚪ 🟤\n"
        "2 TB — ⚪ 🟤\n\n"
        "iPad Pro M2 12.9\" 2022\n"
        "128 GB — ⚪ 🟤\n"
        "256 GB — ⚪ 🟤\n"
        "512 GB — ⚪ 🟤\n"
        "1 TB — ⚪ 🟤\n"
        "2 TB — ⚪ 🟤\n\n"
        "iPad Pro M4 11\" 2024\n"
        "256 GB — ⚪ 🖤\n"
        "512 GB — ⚪ 🖤\n"
        "1 TB — ⚪ 🖤\n"
        "2 TB — ⚪ 🖤\n\n"
        "iPad Pro M4 13\" 2024\n"
        "256 GB — ⚪ 🖤\n"
        "512 GB — ⚪ 🖤\n"
        "1 TB — ⚪ 🖤\n"
        "2 TB — ⚪ 🖤\n\n"
        "iPad Pro M5 11\" 2025\n"
        "256 GB — ⚪ 🖤\n"
        "512 GB — ⚪ 🖤\n"
        "1 TB — ⚪ 🖤\n"
        "2 TB — ⚪ 🖤\n\n"
        "iPad Pro M5 13\" 2025\n"
        "256 GB — ⚪ 🖤\n"
        "512 GB — ⚪ 🖤\n"
        "1 TB — ⚪ 🖤\n"
        "2 TB — ⚪ 🖤\n\n"
        "✏️ Стилусы:\n"
        "Apple Pencil Pro — Pro M4/M5, Air M2/M3/M4, mini 7\n"
        "Apple Pencil USB-C — все актуальные iPad\n"
        "Apple Pencil 2 — Pro M2, Air M1, mini 6\n\n"
        "⌨️ Клавиатуры:\n"
        "Magic Keyboard — iPad Pro 11\"/13\" (M4, M5)\n"
        "Magic Keyboard — iPad Air 11\"/13\" (M2, M3, M4)\n"
        "Magic Keyboard Folio — iPad 10 (A14)",
        reply_markup=kb.main_menu()
    )


@router.message(F.text == "♻️ Смартфоны Б/У")
async def cmd_smartphones_bu(message: Message):
    uid = message.from_user.id
    user_filters.setdefault(uid, {})["phone_cond"] = "used"
    await message.answer("Б/У смартфоны — выберите раздел:", reply_markup=kb.smartphones_kb("used"))


@router.message(F.text == "🖥 Маки")
async def cmd_macs(message: Message):
    await message.answer(
        "🖥 Mac — цены у @idistoreman\n\n"
        "💻 MacBook Air\n"
        "MacBook Air 13\" M1 (2020) — 🩶 🟡 ⚪\n"
        "MacBook Air 13\" M2 (2022) — 🖤 🤍 🩶 ⚪\n"
        "MacBook Air 15\" M2 (2023) — 🖤 🤍 🩶 ⚪\n"
        "MacBook Air 13\" M3 (2024) — 🖤 🤍 🩶 ⚪ 🩵\n"
        "MacBook Air 15\" M3 (2024) — 🖤 🤍 🩶 ⚪ 🩵\n"
        "MacBook Air 13\" M5 (2026) — 🩵 🖤 🤍 ⚪\n"
        "MacBook Air 15\" M5 (2026) — 🩵 🖤 🤍 ⚪\n\n"
        "💻 MacBook Pro\n"
        "MacBook Pro 14\" M1 Pro/Max (2021) — 🩶 ⚪\n"
        "MacBook Pro 16\" M1 Pro/Max (2021) — 🩶 ⚪\n"
        "MacBook Pro 14\" M2 Pro/Max (2023) — 🩶 ⚪\n"
        "MacBook Pro 16\" M2 Pro/Max (2023) — 🩶 ⚪\n"
        "MacBook Pro 14\" M3/Pro/Max (2023) — 🖤 ⚪\n"
        "MacBook Pro 16\" M3 Pro/Max (2023) — 🖤 ⚪\n"
        "MacBook Pro 14\" M4/Pro/Max (2024) — 🖤 ⚪\n"
        "MacBook Pro 16\" M4 Pro/Max (2024) — 🖤 ⚪\n"
        "MacBook Pro 14\" M5/Pro/Max (2025-2026) — 🖤 ⚪\n"
        "MacBook Pro 16\" M5 Pro/Max (2026) — 🖤 ⚪\n\n"
        "🖥 iMac\n"
        "iMac 24\" M1 (2021) — 🔵 🟢 🩷 🟣 🟡 🟠 ⚪\n"
        "iMac 24\" M3 (2023) — 🔵 🟢 🩷 🟣 🟡 🟠 ⚪\n"
        "iMac 24\" M4 (2024) — 🔵 🟢 🩷 🟣 🟡 🟠 ⚪\n\n"
        "🖥 Mac mini\n"
        "Mac mini M1 (2020) — ⚪\n"
        "Mac mini M2/M2 Pro (2023) — ⚪\n"
        "Mac mini M4/M4 Pro (2024) — ⚪\n\n"
        "🖥 Mac Studio\n"
        "Mac Studio M1 Max/Ultra (2022) — ⚪\n"
        "Mac Studio M2 Max/Ultra (2023) — ⚪\n"
        "Mac Studio M4 Max/M3 Ultra (2025) — ⚪",
        reply_markup=kb.main_menu()
    )


@router.message(F.text == "🎧 Наушники")
async def cmd_headphones(message: Message):
    await message.answer(
        "🎧 Наушники Apple — цены у @idistoreman\n\n"
        "AirPods 2 (2019) — ⚪\n"
        "AirPods 3 (2021) — ⚪\n"
        "AirPods 4 (2024) — ⚪\n"
        "AirPods 4 ANC (2024) — ⚪\n\n"
        "AirPods Pro 1 (2019) — ⚪\n"
        "AirPods Pro 2 Lightning (2022) — ⚪\n"
        "AirPods Pro 2 USB-C (2023) — ⚪\n"
        "AirPods Pro 3 (2025) — ⚪\n\n"
        "AirPods Max 1 Lightning (2020) — 🟤 ⚪ 🩵 🟢 🩷\n"
        "AirPods Max 1 USB-C (2024) — 🖤 🤍 🔵 🟣 🟠\n"
        "AirPods Max 2 (2026) — 🖤 🤍 🔵 🟣 🟠",
        reply_markup=kb.main_menu()
    )


@router.message(F.text.in_(MENU_CATEGORY_MAP))
async def cmd_menu_category(message: Message):
    category = MENU_CATEGORY_MAP[message.text]
    uid = message.from_user.id
    items = await db.get_items(category=category)
    if not items:
        await message.answer(f"Товаров в категории «{category}» пока нет.", reply_markup=kb.main_menu())
        return

    user_filters[uid] = {"items": [dict(i) for i in items], "page": 0, "category": category}
    await message.answer(
        f"{message.text} — {len(items)} шт.:",
        reply_markup=kb.items_list_kb(user_filters[uid]["items"])
    )


@router.message(F.text == "🔍 Поиск по названию")
async def cmd_search(message: Message, state: FSMContext):
    await state.set_state(SearchState.waiting_query)
    await message.answer("Введите название товара для поиска:")


@router.message(SearchState.waiting_query)
async def process_search(message: Message, state: FSMContext):
    await state.clear()
    items = await db.search_items(message.text)
    if not items:
        await message.answer(
            "Ничего не найдено. Попробуйте другой запрос.\n\nНе нашли что искали? Пишите: @idistoreman",
            reply_markup=kb.main_menu()
        )
        return

    uid = message.from_user.id
    user_filters[uid] = {"items": [dict(i) for i in items], "page": 0}
    await message.answer(
        f"Найдено {len(items)} товаров:",
        reply_markup=kb.items_list_kb(user_filters[uid]["items"])
    )


@router.message(F.text == "🍎 Рынок б/у iPhone")
async def cmd_iphone_pricelist(message: Message):
    import random
    all_phones = await db.get_items(category="Смартфоны")
    items = [dict(i) for i in all_phones
             if dict(i)["condition"] in USED_CONDITIONS
             and "iphone" in dict(i)["name"].lower()]
    if not items:
        await message.answer("Товаров iPhone не найдено.", reply_markup=kb.main_menu())
        return
    uid = message.from_user.id
    shuffled = items
    random.shuffle(shuffled)
    user_filters[uid] = {"items": shuffled, "phone_index": 0, "card_msg_ids": []}

    is_admin = False
    try:
        from bot import ADMIN_IDS
        is_admin = message.from_user.id in ADMIN_IDS
    except Exception:
        pass

    await _send_phone_card(message, shuffled, 0, is_admin, uid)


@router.message(F.text == "🔧 Сервис")
async def cmd_service(message: Message):
    await message.answer("По поводу ремонта пишите: @idistoreman")


@router.message(F.text == "ℹ️ О боте")
async def cmd_about(message: Message):
    await message.answer(
        "🛍 <b>Магазин техники distore</b>\n\n"
        "Б/У и новая техника по выгодным ценам.\n\n"
        "♻️ <b>Смартфоны Б/У</b> — iPhone и другие смартфоны с рук\n"
        "📱 <b>Смартфоны</b> — новые смартфоны\n"
        "🎧 <b>Наушники</b> — AirPods и другие\n"
        "🔌 <b>Аксессуары</b> — кабели, чехлы, зарядки\n"
        "📟 <b>Планшеты</b> — iPad и другие\n"
        "📦 <b>Другое</b> — прочая техника\n"
        "🍎 <b>Рынок б/у iPhone</b> — все айфоны в наличии вперемешку\n"
        "🔍 <b>Поиск</b> — найти конкретный товар\n"
        "💰 <b>Фильтр по цене</b> — выбрать по бюджету\n"
        "❤️ <b>Избранное</b> — сохранённые товары\n"
        "🔧 <b>Сервис</b> — ремонт техники\n\n"
        "✍️ По всем вопросам: @idistoreman",
        parse_mode="HTML"
    )


@router.message(F.text == "/help")
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Полная инструкция по боту</b>\n\n"

        "━━━━━━━━━━━━━━━\n"
        "🗂 <b>РАЗДЕЛЫ КАТАЛОГА</b>\n"
        "━━━━━━━━━━━━━━━\n\n"

        "♻️ <b>Смартфоны Б/У</b>\n"
        "Подержанные смартфоны. Выберите состояние (новые / б/у / все), "
        "затем раздел — iPhone или другие смартфоны.\n"
        "Для iPhone: поколение → модель → память → цвет → карточка товара.\n\n"

        "📱 <b>Смартфоны</b>\n"
        "Новые смартфоны в наличии.\n\n"

        "🍎 <b>Рынок б/у iPhone</b>\n"
        "Все б/у айфоны из наличия в случайном порядке — листайте стрелками ◀️ ▶️ и выбирайте.\n\n"

        "🎧 <b>Наушники</b> — AirPods и другие наушники.\n"
        "🔌 <b>Аксессуары</b> — кабели, чехлы, зарядки и прочее.\n"
        "📟 <b>Планшеты</b> — iPad и другие планшеты.\n"
        "📦 <b>Другое</b> — прочая техника.\n\n"

        "━━━━━━━━━━━━━━━\n"
        "🔍 <b>ПОИСК И ФИЛЬТРЫ</b>\n"
        "━━━━━━━━━━━━━━━\n\n"

        "🔍 <b>Поиск по названию</b>\n"
        "Введите любое слово — модель, бренд, характеристику. "
        "Бот найдёт все подходящие товары.\n\n"

        "💰 <b>Фильтр по цене</b>\n"
        "Выберите готовый диапазон (от «до 5 000 ₽» до «от 80 000 ₽») "
        "или нажмите ✏️ <b>Ввести свой диапазон</b> и напишите, например: "
        "<code>15000-45000</code> или просто <code>30000</code> (до этой суммы).\n\n"

        "━━━━━━━━━━━━━━━\n"
        "🃏 <b>КАРТОЧКА ТОВАРА</b>\n"
        "━━━━━━━━━━━━━━━\n\n"

        "На карточке товара вы увидите:\n"
        "• Название, категория, состояние\n"
        "• Цена\n"
        "• Описание (если есть)\n"
        "• Фото (до 10 штук)\n"
        "• Контакт продавца\n\n"

        "◀️ ▶️ — листать товары\n"
        "🤍 <b>В избранное</b> — сохранить товар\n"
        "👁 — счётчик просмотров товара\n\n"

        "━━━━━━━━━━━━━━━\n"
        "❤️ <b>ИЗБРАННОЕ</b>\n"
        "━━━━━━━━━━━━━━━\n\n"

        "Сохраняйте понравившиеся товары кнопкой 🤍 на карточке. "
        "Все сохранённые товары доступны в разделе ❤️ <b>Избранное</b> в главном меню — "
        "они там останутся даже если выйти из бота.\n\n"

        "━━━━━━━━━━━━━━━\n"
        "🔧 <b>СЕРВИС</b>\n"
        "━━━━━━━━━━━━━━━\n\n"

        "Ремонт техники — напишите @idistoreman.\n\n"

        "━━━━━━━━━━━━━━━\n"
        "💬 <b>КОНТАКТ</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        "По любым вопросам, торгу, резерву и доставке:\n"
        "✍️ @idistoreman",
        parse_mode="HTML"
    )


NEW_CONDITIONS = {"Новое"}
USED_CONDITIONS = {"Как новое", "Хорошее", "Удовлетворительное"}


def _filter_by_cond(items: list, cond: str) -> list:
    if cond == "new":
        return [i for i in items if i["condition"] in NEW_CONDITIONS]
    elif cond == "used":
        return [i for i in items if i["condition"] in USED_CONDITIONS]
    return items


COND_LABEL = {"new": "🆕 Новые", "used": "♻️ Б/У", "all": "🔄 Все"}


async def _get_item_photos(item: dict) -> list:
    photos = await db.get_item_photos(item["id"])
    if not photos and item.get("photo_id"):
        photos = [item["photo_id"]]
    return photos


async def _delete_prev_card(bot, chat_id: int, uid: int):
    data = user_filters.get(uid, {})
    for msg_id in data.get("card_msg_ids", []):
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
    user_filters[uid]["card_msg_ids"] = []


async def _show_phone_card(call: CallbackQuery, index: int):
    uid = call.from_user.id
    data = user_filters.get(uid)
    if not data or not data.get("items"):
        await call.answer("Сессия устарела", show_alert=True)
        return

    items = data["items"]
    if index < 0 or index >= len(items):
        await call.answer()
        return

    item = items[index]
    user_filters[uid]["phone_index"] = index

    is_admin = False
    try:
        from bot import ADMIN_IDS
        is_admin = call.from_user.id in ADMIN_IDS
    except Exception:
        pass

    await db.increment_views(item["id"])
    is_fav = await db.is_favorite(uid, item["id"])
    views = await db.get_views(item["id"])
    is_sub = await db.is_subscribed(uid, item["name"])

    text = format_item(item)
    markup = kb.phone_card_kb(index, len(items), item["id"], is_admin, is_fav, views, item["name"], is_sub)
    photos = await _get_item_photos(item)
    photos = photos[:10]

    await _delete_prev_card(call.bot, call.message.chat.id, uid)

    msg_ids = []
    if len(photos) > 1:
        media = [InputMediaPhoto(media=photos[0], caption=text, parse_mode="HTML")]
        media += [InputMediaPhoto(media=p) for p in photos[1:]]
        sent_group = await call.message.answer_media_group(media)
        msg_ids += [m.message_id for m in sent_group]
        nav = await call.message.answer("⬆️ Фото товара", reply_markup=markup)
        msg_ids.append(nav.message_id)
    elif photos:
        sent = await call.message.answer_photo(photos[0], caption=text, parse_mode="HTML", reply_markup=markup)
        msg_ids.append(sent.message_id)
    else:
        sent = await call.message.answer(text, parse_mode="HTML", reply_markup=markup)
        msg_ids.append(sent.message_id)

    user_filters[uid]["card_msg_ids"] = msg_ids


async def _send_phone_card(message, items: list, index: int = 0, is_admin: bool = False, uid: int = None):
    item = items[index]

    await db.increment_views(item["id"])
    is_fav = await db.is_favorite(uid, item["id"]) if uid else False
    views = await db.get_views(item["id"])
    is_sub = await db.is_subscribed(uid, item["name"]) if uid else False

    text = format_item(item)
    markup = kb.phone_card_kb(index, len(items), item["id"], is_admin, is_fav, views, item["name"], is_sub)
    photos = await _get_item_photos(item)
    photos = photos[:10]

    msg_ids = []
    if len(photos) > 1:
        media = [InputMediaPhoto(media=photos[0], caption=text, parse_mode="HTML")]
        media += [InputMediaPhoto(media=p) for p in photos[1:]]
        sent_group = await message.answer_media_group(media)
        msg_ids += [m.message_id for m in sent_group]
        nav = await message.answer("⬆️ Фото товара", reply_markup=markup)
        msg_ids.append(nav.message_id)
    elif photos:
        sent = await message.answer_photo(photos[0], caption=text, parse_mode="HTML", reply_markup=markup)
        msg_ids.append(sent.message_id)
    else:
        sent = await message.answer(text, parse_mode="HTML", reply_markup=markup)
        msg_ids.append(sent.message_id)

    if uid is not None:
        user_filters.setdefault(uid, {})["card_msg_ids"] = msg_ids


@router.callback_query(F.data == "cat:Смартфоны")
async def cb_smartphones(call: CallbackQuery):
    await safe_edit(call, "Выберите состояние:", reply_markup=kb.smartphones_condition_kb())


@router.callback_query(F.data.startswith("scond:"))
async def cb_smartphones_cond(call: CallbackQuery):
    cond = call.data.split(":", 1)[1]
    label = COND_LABEL.get(cond, "")
    await safe_edit(call, f"{label} смартфоны — выберите раздел:", reply_markup=kb.smartphones_kb(cond))


@router.callback_query(F.data.startswith("scat:iphone"))
async def cb_iphone_groups(call: CallbackQuery):
    cond = call.data.split("|", 1)[1] if "|" in call.data else "all"
    user_filters[call.from_user.id] = user_filters.get(call.from_user.id, {})
    user_filters[call.from_user.id]["phone_cond"] = cond
    await safe_edit(call, "Выберите поколение iPhone:", reply_markup=kb.iphone_groups_kb(cond))


@router.callback_query(F.data.startswith("igrp:"))
async def cb_iphone_group(call: CallbackQuery):
    group = call.data.split(":", 1)[1]
    uid = call.from_user.id
    user_filters.setdefault(uid, {})["iphone_group"] = group
    user_filters[uid].pop("iphone_model", None)
    user_filters[uid].pop("iphone_storage", None)
    cond = user_filters[uid].get("phone_cond", "all")
    await safe_edit(call, f"Выберите модель ({group}):", reply_markup=kb.iphone_models_kb(group, cond))


@router.callback_query(F.data.startswith("igrp_back:"))
async def cb_igrp_back(call: CallbackQuery):
    cond = call.data.split(":", 1)[1]
    await safe_edit(call, "Выберите поколение iPhone:", reply_markup=kb.iphone_groups_kb(cond))


@router.callback_query(F.data.startswith("imodel:"))
async def cb_iphone_model(call: CallbackQuery):
    model = call.data.split(":", 1)[1]
    uid = call.from_user.id
    user_filters.setdefault(uid, {})["iphone_model"] = model
    user_filters[uid].pop("iphone_storage", None)
    cond = user_filters[uid].get("phone_cond", "all")
    await safe_edit(call, f"Выберите память для {model}:", reply_markup=kb.iphone_storage_kb(model, cond))


@router.callback_query(F.data.startswith("istorage:"))
async def cb_iphone_storage(call: CallbackQuery):
    _, rest = call.data.split(":", 1)
    model, storage = rest.split("|", 1)
    uid = call.from_user.id
    user_filters.setdefault(uid, {})["iphone_storage"] = storage
    cond = user_filters[uid].get("phone_cond", "all")
    await safe_edit(call, f"Выберите цвет {model} {storage}:", reply_markup=kb.iphone_colors_kb(model, storage, cond))


@router.callback_query(F.data.startswith("icolor:"))
async def cb_iphone_color(call: CallbackQuery):
    _, rest = call.data.split(":", 1)
    parts = rest.split("|", 2)
    model, storage, color = parts[0], parts[1], parts[2]
    uid = call.from_user.id
    cond = user_filters.get(uid, {}).get("phone_cond", "all")

    search_name = f"{model} {storage} {color}"
    all_items = await db.search_items(search_name)
    if not all_items:
        all_items = await db.search_items(f"{model} {storage}")

    if not all_items:
        await safe_edit(call, "Товаров не найдено.\n\nНе нашли что искали? Пишите: @idistoreman", reply_markup=kb.main_menu_inline())
        return

    all_items = [dict(i) for i in all_items]
    items = _filter_by_cond(all_items, cond)

    if not items:
        await safe_edit(call, f"Нет товаров в категории «{COND_LABEL[cond]}».\n\nНе нашли что искали? Пишите: @idistoreman", reply_markup=kb.main_menu_inline())
        return

    user_filters[uid]["items"] = items
    user_filters[uid]["phone_index"] = 0
    user_filters[uid]["card_msg_ids"] = []

    is_admin = False
    try:
        from bot import ADMIN_IDS
        is_admin = call.from_user.id in ADMIN_IDS
    except Exception:
        pass

    await call.message.delete()
    await _send_phone_card(call.message, items, 0, is_admin, uid)


@router.callback_query(F.data.startswith("scat:other"))
async def cb_smartphones_other(call: CallbackQuery):
    cond = call.data.split("|", 1)[1] if "|" in call.data else "all"
    uid = call.from_user.id
    all_phones = await db.get_items(category="Смартфоны")
    all_items = [dict(i) for i in all_phones if "iphone" not in dict(i)["name"].lower()]
    items = _filter_by_cond(all_items, cond)

    if not items:
        await call.answer("Товаров нет", show_alert=True)
        return

    user_filters[uid] = {"items": items, "phone_index": 0, "category": "Смартфоны", "card_msg_ids": []}

    is_admin = False
    try:
        from bot import ADMIN_IDS
        is_admin = call.from_user.id in ADMIN_IDS
    except Exception:
        pass

    await call.message.delete()
    await _send_phone_card(call.message, items, 0, is_admin, uid)


@router.callback_query(F.data == "back:categories")
async def cb_back_categories(call: CallbackQuery):
    await safe_edit(call, "Выберите категорию:", reply_markup=kb.categories_kb())


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(call: CallbackQuery):
    category = call.data.split(":", 1)[1]
    if category == "all":
        items = await db.get_items()
    else:
        items = await db.get_items(category=category)

    uid = call.from_user.id
    if not items:
        await call.answer("Товаров нет", show_alert=True)
        return

    user_filters[uid] = {"items": [dict(i) for i in items], "page": 0, "category": category}
    await safe_edit(call, f"{'Все товары' if category == 'all' else category} — {len(items)} шт.:", reply_markup=kb.items_list_kb(user_filters[uid]["items"]))


@router.callback_query(F.data.startswith("cond:"))
async def cb_condition(call: CallbackQuery):
    condition = call.data.split(":", 1)[1]
    uid = call.from_user.id
    category = user_filters.get(uid, {}).get("category")

    if condition == "all":
        items = await db.get_items(category=category if category != "all" else None)
    else:
        items = await db.get_items(
            category=category if category != "all" else None,
            condition=condition
        )

    if not items:
        await call.answer("Товаров нет", show_alert=True)
        return

    user_filters[uid] = {"items": [dict(i) for i in items], "page": 0}
    await safe_edit(call, f"Найдено {len(items)} товаров:", reply_markup=kb.items_list_kb(user_filters[uid]["items"]))


@router.callback_query(F.data.startswith("item:"))
async def cb_item(call: CallbackQuery):
    item_id = int(call.data.split(":")[1])
    item = await db.get_item(item_id)
    if not item:
        await call.answer("Товар не найден", show_alert=True)
        return

    text = format_item(item)
    is_admin = False
    try:
        from bot import ADMIN_IDS
        is_admin = call.from_user.id in ADMIN_IDS
    except Exception:
        pass

    photos = await db.get_item_photos(item_id)
    if not photos and item["photo_id"]:
        photos = [item["photo_id"]]
    photos = photos[:10]

    await call.message.delete()
    if len(photos) > 1:
        media = [InputMediaPhoto(media=photos[0], caption=text, parse_mode="HTML")]
        media += [InputMediaPhoto(media=p) for p in photos[1:]]
        await call.message.answer_media_group(media)
        await call.message.answer("⬆️ Фото товара", reply_markup=kb.item_kb(item_id, is_admin))
    elif photos:
        await call.message.answer_photo(photos[0], caption=text, parse_mode="HTML", reply_markup=kb.item_kb(item_id, is_admin))
    else:
        await call.message.answer(text, parse_mode="HTML", reply_markup=kb.item_kb(item_id, is_admin))


@router.callback_query(F.data.startswith("page:"))
async def cb_page(call: CallbackQuery):
    page = int(call.data.split(":")[1])
    uid = call.from_user.id
    data = user_filters.get(uid)
    if not data:
        await call.answer("Сессия устарела", show_alert=True)
        return

    user_filters[uid]["page"] = page
    await safe_edit_markup(call, reply_markup=kb.items_list_kb(data["items"], page))


@router.callback_query(F.data == "back:catalog")
async def cb_back_catalog(call: CallbackQuery):
    uid = call.from_user.id
    data = user_filters.get(uid)
    page = data.get("page", 0) if data else 0
    items = data.get("items", []) if data else []

    if not items:
        await safe_edit(call, "Каталог пуст.", reply_markup=kb.categories_kb())
        return

    await safe_edit(call, f"Найдено {len(items)} товаров:", reply_markup=kb.items_list_kb(items, page))


@router.callback_query(F.data == "back:main")
async def cb_back_main(call: CallbackQuery):
    await call.message.delete()
    await call.message.answer("Главное меню", reply_markup=kb.main_menu())


@router.callback_query(F.data.startswith("pcard:"))
async def cb_phone_card(call: CallbackQuery):
    index = int(call.data.split(":")[1])
    await _show_phone_card(call, index)
    await call.answer()


@router.callback_query(F.data == "isearch:now")
async def cb_isearch(call: CallbackQuery):
    uid = call.from_user.id
    data = user_filters.get(uid, {})
    cond = data.get("phone_cond", "all")
    model = data.get("iphone_model")
    storage = data.get("iphone_storage")
    group = data.get("iphone_group")

    if model and storage:
        all_items = [dict(i) for i in await db.search_items(f"{model} {storage}")]
    elif model:
        all_items = [dict(i) for i in await db.search_items(model)]
    elif group:
        from iphone_data import IPHONE_GROUPS as IG
        all_items = []
        seen = set()
        for m in IG.get(group, []):
            for i in await db.search_items(m):
                d = dict(i)
                if d["id"] not in seen:
                    seen.add(d["id"])
                    all_items.append(d)
    else:
        await call.answer("Выберите хотя бы поколение", show_alert=True)
        return

    items = _filter_by_cond(all_items, cond)
    if not items:
        await safe_edit(call, "Товаров не найдено.\n\nНе нашли что искали? Пишите: @idistoreman", reply_markup=kb.main_menu_inline())
        return

    user_filters[uid]["items"] = items
    user_filters[uid]["phone_index"] = 0
    user_filters[uid]["card_msg_ids"] = []

    is_admin = False
    try:
        from bot import ADMIN_IDS
        is_admin = uid in ADMIN_IDS
    except Exception:
        pass

    await call.answer()
    await call.message.delete()
    await _send_phone_card(call.message, items, 0, is_admin, uid)


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("del:"))
async def cb_delete_item(call: CallbackQuery):
    try:
        from bot import ADMIN_IDS
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("Нет прав", show_alert=True)
            return
    except Exception:
        pass

    item_id = int(call.data.split(":")[1])
    await db.delete_item(item_id)

    # Sync to Google Sheets — пометим как «Возврат» (история сохранится)
    try:
        import sheets_sync
        if sheets_sync.is_enabled():
            await sheets_sync.mark_deleted(item_id)
    except Exception:
        pass

    await call.answer("Товар удалён ✅")
    await call.message.delete()


# --- Избранное ---

@router.message(F.text == "❤️ Избранное")
async def cmd_favorites(message: Message):
    uid = message.from_user.id
    items = await db.get_favorites(uid)
    if not items:
        await message.answer("У вас пока нет избранных товаров.", reply_markup=kb.main_menu())
        return

    items = [dict(i) for i in items]
    user_filters[uid] = {"items": items, "phone_index": 0, "card_msg_ids": []}

    is_admin = False
    try:
        from bot import ADMIN_IDS
        is_admin = uid in ADMIN_IDS
    except Exception:
        pass

    await _send_phone_card(message, items, 0, is_admin, uid)


@router.callback_query(F.data.startswith("fav:"))
async def cb_toggle_favorite(call: CallbackQuery):
    uid = call.from_user.id
    item_id = int(call.data.split(":")[1])

    if await db.is_favorite(uid, item_id):
        await db.remove_favorite(uid, item_id)
        await call.answer("Убрано из избранного")
    else:
        await db.add_favorite(uid, item_id)
        await call.answer("Добавлено в избранное ❤️")

    data = user_filters.get(uid, {})
    index = data.get("phone_index", 0)
    items = data.get("items", [])
    if not items:
        return

    is_admin = False
    try:
        from bot import ADMIN_IDS
        is_admin = uid in ADMIN_IDS
    except Exception:
        pass

    is_fav = await db.is_favorite(uid, item_id)
    views = await db.get_views(item_id)
    markup = kb.phone_card_kb(index, len(items), item_id, is_admin, is_fav, views)

    try:
        await call.message.edit_reply_markup(reply_markup=markup)
    except TelegramBadRequest:
        pass


# --- Поделиться ---

@router.callback_query(F.data.startswith("share:"))
async def cb_share(call: CallbackQuery):
    item_id = int(call.data.split(":")[1])
    item = await db.get_item(item_id)
    if not item:
        await call.answer("Товар не найден", show_alert=True)
        return

    item = dict(item)
    text = (
        f"🛍 <b>{item['name']}</b>\n"
        f"💰 <b>{item['price']:,} ₽</b>\n"
        f"📦 {item['condition']}\n"
    )
    if item.get("description"):
        text += f"\n{item['description']}\n"
    text += "\n✍️ Купить: @idistoreman"

    await call.message.answer(text, parse_mode="HTML")
    await call.answer()


# --- Подписки ---

@router.message(F.text == "🔔 Подписки")
async def cmd_subscriptions(message: Message):
    uid = message.from_user.id
    subs = await db.get_user_subscriptions(uid)
    if not subs:
        await message.answer(
            "У вас нет активных подписок.\n\n"
            "Подписаться на модель можно из карточки товара — "
            "бот уведомит когда появится новый товар с этой моделью.",
            reply_markup=kb.main_menu()
        )
        return
    await message.answer(
        f"Ваши подписки ({len(subs)}):\nНажмите ❌ чтобы отписаться:",
        reply_markup=kb.subscriptions_kb(subs)
    )


@router.callback_query(F.data.startswith("sub:"))
async def cb_subscribe(call: CallbackQuery):
    uid = call.from_user.id
    model = call.data.split(":", 1)[1]

    if await db.is_subscribed(uid, model):
        await db.remove_subscription(uid, model)
        await call.answer(f"Отписались от «{model}»")
    else:
        await db.add_subscription(uid, model)
        await call.answer(f"Подписались на «{model}» 🔔")

    data = user_filters.get(uid, {})
    index = data.get("phone_index", 0)
    items = data.get("items", [])
    if not items:
        return

    item = items[index]
    is_admin = False
    try:
        from bot import ADMIN_IDS
        is_admin = uid in ADMIN_IDS
    except Exception:
        pass

    is_fav = await db.is_favorite(uid, item["id"])
    views = await db.get_views(item["id"])
    is_sub = await db.is_subscribed(uid, model)
    markup = kb.phone_card_kb(index, len(items), item["id"], is_admin, is_fav, views, model, is_sub)

    try:
        await call.message.edit_reply_markup(reply_markup=markup)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("unsub:"))
async def cb_unsubscribe(call: CallbackQuery):
    model = call.data.split(":", 1)[1]
    await db.remove_subscription(call.from_user.id, model)
    await call.answer(f"Отписались от {model}")
    subs = await db.get_user_subscriptions(call.from_user.id)
    if not subs:
        await safe_edit(call, "Подписок больше нет.", reply_markup=kb.main_menu_inline())
    else:
        await safe_edit(call, f"Ваши подписки ({len(subs)}):\nНажмите ❌ чтобы отписаться:", reply_markup=kb.subscriptions_kb(subs))


# --- Фильтр по цене ---

def _price_label(min_price, max_price) -> str:
    if min_price and max_price:
        return f"{min_price:,} — {max_price:,} ₽"
    elif max_price:
        return f"до {max_price:,} ₽"
    elif min_price:
        return f"от {min_price:,} ₽"
    return "любая цена"


def _pf_header_text(label: str, category: str, items: list, sort: str) -> str:
    sort_labels = {"price_asc": "💰 дешевле", "price_desc": "💎 дороже"}
    cat_label = "" if category == "all" else f" · {category}"
    actual_min = min(i["price"] for i in items)
    actual_max = max(i["price"] for i in items)
    return (
        f"🔍 <b>{label}{cat_label}</b>\n"
        f"Найдено: <b>{len(items)}</b> шт. · {actual_min:,} — {actual_max:,} ₽\n"
        f"Сортировка: {sort_labels.get(sort, '')}"
    )


async def _pf_delete_list(bot, chat_id: int, uid: int):
    """Удаляет сообщение со списком товаров фильтра."""
    pf = user_filters.get(uid, {})
    msg_id = pf.get("pf_list_id")
    if msg_id:
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
        pf["pf_list_id"] = None


async def _pf_send_list(bot_or_msg, chat_id: int, uid: int, items: list, is_call: bool = True):
    """Отправляет новый список товаров и сохраняет его id."""
    if is_call:
        msg = await bot_or_msg.send_message(chat_id, "Выберите товар:", reply_markup=kb.items_list_kb(items))
    else:
        msg = await bot_or_msg.answer("Выберите товар:", reply_markup=kb.items_list_kb(items))
    user_filters[uid]["pf_list_id"] = msg.message_id


@router.message(F.text.in_({"💰 Фильтр по цене", "💰 Фильтр"}))
async def cmd_price_filter(message: Message):
    await message.answer("Выберите категорию:", reply_markup=kb.price_filter_category_kb())


@router.callback_query(F.data == "pf_back")
async def cb_pf_back(call: CallbackQuery):
    await safe_edit(call, "Выберите категорию:", reply_markup=kb.price_filter_category_kb())


@router.callback_query(F.data.startswith("pf_cat:"))
async def cb_pf_category(call: CallbackQuery):
    category = call.data.split(":", 1)[1]
    await safe_edit(call, "Выберите ценовой диапазон:", reply_markup=kb.price_filter_kb(category))


@router.callback_query(F.data.startswith("price:custom:"))
async def cb_price_custom(call: CallbackQuery, state: FSMContext):
    category = call.data.split(":", 2)[2]
    await state.update_data(pf_category=category)
    await state.set_state(PriceState.waiting_range)
    await safe_edit(call, "Введите диапазон цен:\n<b>10000-50000</b>\nили одно число — до этой суммы.", parse_mode="HTML")


@router.message(PriceState.waiting_range)
async def process_price_range(message: Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("pf_category", "all")
    await state.clear()

    text = message.text.strip().replace(" ", "").replace(",", "")
    min_price = None
    max_price = None

    if "-" in text:
        parts = text.split("-", 1)
        if parts[0].isdigit() and parts[1].isdigit():
            min_price = int(parts[0])
            max_price = int(parts[1])
        else:
            await message.answer("Неверный формат. Пример: 15000-40000", reply_markup=kb.main_menu())
            return
    elif text.isdigit():
        max_price = int(text)
    else:
        await message.answer("Неверный формат. Пример: 15000-40000 или просто 30000", reply_markup=kb.main_menu())
        return

    uid = message.from_user.id
    cat = category if category != "all" else None
    items = [dict(i) for i in await db.get_items(category=cat, min_price=min_price, max_price=max_price, sort="price_asc")]
    label = _price_label(min_price, max_price)

    if not items:
        await message.answer(f"Товаров в диапазоне «{label}» нет.\n\nНе нашли что искали? Пишите: @idistoreman", reply_markup=kb.main_menu())
        return

    user_filters[uid] = {"items": items, "page": 0, "pf_min": min_price, "pf_max": max_price,
                         "pf_cat": category, "pf_sort": "price_asc", "pf_label": label, "pf_list_id": None}
    await message.answer(_pf_header_text(label, category, items, "price_asc"),
                         parse_mode="HTML", reply_markup=kb.price_sort_kb(category, "price_asc", label))
    await _pf_send_list(message, message.chat.id, uid, items, is_call=False)


@router.callback_query(F.data.startswith("price:") & ~F.data.startswith("price:custom"))
async def cb_price_filter(call: CallbackQuery):
    parts = call.data.split(":")
    min_p, max_p = int(parts[1]), int(parts[2])
    category = parts[3] if len(parts) > 3 else "all"
    min_price = min_p or None
    max_price = max_p or None
    uid = call.from_user.id
    cat = category if category != "all" else None
    label = _price_label(min_price, max_price)

    items = [dict(i) for i in await db.get_items(category=cat, min_price=min_price, max_price=max_price, sort="price_asc")]

    if not items:
        await safe_edit(call, f"Товаров в диапазоне «{label}» нет.\n\nНе нашли что искали? Пишите: @idistoreman",
                        reply_markup=kb.main_menu_inline())
        return

    # удаляем старый список если был
    await _pf_delete_list(call.bot, call.message.chat.id, uid)

    user_filters[uid] = {"items": items, "page": 0, "pf_min": min_price, "pf_max": max_price,
                         "pf_cat": category, "pf_sort": "price_asc", "pf_label": label, "pf_list_id": None}

    await safe_edit(call, _pf_header_text(label, category, items, "price_asc"),
                    parse_mode="HTML", reply_markup=kb.price_sort_kb(category, "price_asc", label))
    await _pf_send_list(call.bot, call.message.chat.id, uid, items)


@router.callback_query(F.data.startswith("psort:"))
async def cb_price_sort(call: CallbackQuery):
    parts = call.data.split(":", 3)
    sort, category, label = parts[1], parts[2], parts[3]
    uid = call.from_user.id
    pf = user_filters.get(uid, {})
    min_price = pf.get("pf_min")
    max_price = pf.get("pf_max")
    cat = category if category != "all" else None

    items = [dict(i) for i in await db.get_items(category=cat, min_price=min_price, max_price=max_price, sort=sort)]

    if not items:
        await safe_edit(call, "Товаров нет.\n\nНе нашли что искали? Пишите: @idistoreman", reply_markup=kb.main_menu_inline())
        return

    await _pf_delete_list(call.bot, call.message.chat.id, uid)
    user_filters[uid]["items"] = items
    user_filters[uid]["pf_sort"] = sort
    user_filters[uid]["pf_list_id"] = None

    await safe_edit(call, _pf_header_text(label, category, items, sort),
                    parse_mode="HTML", reply_markup=kb.price_sort_kb(category, sort, label))
    await call.answer()
    await _pf_send_list(call.bot, call.message.chat.id, uid, items)


@router.callback_query(F.data.startswith("pmodel_list:"))
async def cb_pmodel_list(call: CallbackQuery):
    parts = call.data.split(":", 2)
    category, label = parts[1], parts[2]
    uid = call.from_user.id
    items = user_filters.get(uid, {}).get("items", [])

    seen = set()
    models = []
    for item in items:
        name_parts = item["name"].split()
        model = " ".join(name_parts[:3]) if len(name_parts) >= 3 else item["name"]
        if model not in seen:
            seen.add(model)
            models.append(model)
    models.sort()

    if not models:
        await call.answer("Модели не найдены", show_alert=True)
        return

    await safe_edit(call, "Выберите модель:", reply_markup=kb.price_model_list_kb(models, category, label))


@router.callback_query(F.data.startswith("pmodel_back:"))
async def cb_pmodel_back(call: CallbackQuery):
    parts = call.data.split(":", 2)
    category, label = parts[1], parts[2]
    uid = call.from_user.id
    pf = user_filters.get(uid, {})
    sort = pf.get("pf_sort", "price_asc")
    items = pf.get("items", [])

    if not items:
        await safe_edit(call, "Сессия устарела.", reply_markup=kb.main_menu_inline())
        return

    await safe_edit(call, _pf_header_text(label, category, items, sort),
                    parse_mode="HTML", reply_markup=kb.price_sort_kb(category, sort, label))


@router.callback_query(F.data.startswith("pmodel:"))
async def cb_pmodel_select(call: CallbackQuery):
    parts = call.data.split(":", 3)
    model, category, label = parts[1], parts[2], parts[3]
    uid = call.from_user.id
    all_items = user_filters.get(uid, {}).get("items", [])

    filtered = [i for i in all_items if i["name"].startswith(model)]
    if not filtered:
        await call.answer("Товаров этой модели нет", show_alert=True)
        return

    await _pf_delete_list(call.bot, call.message.chat.id, uid)
    user_filters[uid]["items"] = filtered
    user_filters[uid]["pf_sort"] = "price_asc"
    user_filters[uid]["pf_list_id"] = None

    actual_min = min(i["price"] for i in filtered)
    actual_max = max(i["price"] for i in filtered)
    text = (
        f"🏷 <b>{model}</b>\n"
        f"Найдено: <b>{len(filtered)}</b> шт. · {actual_min:,} — {actual_max:,} ₽"
    )
    await safe_edit(call, text, parse_mode="HTML", reply_markup=kb.price_sort_kb(category, "price_asc", label))
    await _pf_send_list(call.bot, call.message.chat.id, uid, filtered)


@router.message()
async def handle_forwarded(message: Message):
    from bot import ADMIN_IDS
    if message.from_user.id not in ADMIN_IDS:
        return
    if not (message.forward_origin or message.forward_from_chat or message.forward_from):
        return
    text = message.text or message.caption or ""
    if not text:
        await message.answer("❌ Сообщение не содержит текста.")
        return
    series = _detect_series(text)
    if not series:
        await message.answer("⚠️ Серия iPhone не определена в этом сообщении.")
        return

    filtered = _filter_iphone_lines(text)
    marked = _add_markup_to_prices(filtered)

    cache = _load_cache()
    entry = cache.get(series, {"msgs": [], "updated_at": _now_msk()})
    entry["msgs"].append(marked)
    entry["updated_at"] = _now_msk()
    cache[series] = entry
    _save_cache(cache)

    msg_num = len(entry["msgs"])
    line_count = len([l for l in marked.split("\n") if l.strip()])
    await message.answer(
        f"✅ Сообщение {msg_num} сохранено — iPhone {series} ({line_count} строк)\n"
        f"Можешь пересылать ещё сообщения для этой серии."
    )
