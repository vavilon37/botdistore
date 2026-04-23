from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import os

import database as db
import keyboards as kb
import sheets_sync

router = Router()


class Broadcast(StatesGroup):
    waiting_text = State()
    confirm = State()


class AddItem(StatesGroup):
    # iPhone wizard
    iphone_group = State()
    iphone_model = State()
    iphone_storage = State()
    iphone_color = State()
    # common fields
    name = State()
    category = State()
    condition = State()
    buy_price = State()    # цена закупки
    price = State()        # цена продажи
    description = State()
    photo = State()


class MarkSold(StatesGroup):
    waiting_id = State()
    waiting_price = State()


def is_admin(user_id: int) -> bool:
    try:
        from bot import ADMIN_IDS
        return user_id in ADMIN_IDS
    except Exception:
        return False


@router.message(F.text == "➕ Добавить товар")
async def cmd_add_item(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AddItem.name)
    await message.answer("Введите название товара (или выберите категорию — для iPhone удобнее через /add_iphone):")


@router.message(F.text == "➕ Добавить iPhone")
async def cmd_add_iphone(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AddItem.iphone_group)
    await state.update_data(category="Смартфоны")
    await message.answer("Выберите поколение iPhone:", reply_markup=kb.admin_iphone_groups_kb())


# --- iPhone wizard callbacks ---

@router.callback_query(F.data.startswith("aigrp:"))
async def admin_iphone_group(call: CallbackQuery, state: FSMContext):
    group = call.data.split(":", 1)[1]
    await state.update_data(iphone_group=group)
    await state.set_state(AddItem.iphone_model)
    await call.message.edit_text(f"Выберите модель ({group}):", reply_markup=kb.admin_iphone_models_kb(group))


@router.callback_query(F.data == "aiphone:manual")
async def admin_iphone_manual(call: CallbackQuery, state: FSMContext):
    await state.set_state(AddItem.name)
    await call.message.edit_text("Введите название товара вручную:")


@router.callback_query(F.data.startswith("aimodel:"))
async def admin_iphone_model(call: CallbackQuery, state: FSMContext):
    model = call.data.split(":", 1)[1]
    await state.update_data(iphone_model=model)
    await state.set_state(AddItem.iphone_storage)
    await call.message.edit_text(f"Выберите объём памяти для {model}:", reply_markup=kb.admin_iphone_storage_kb(model))


@router.callback_query(F.data.startswith("aistorage:"))
async def admin_iphone_storage(call: CallbackQuery, state: FSMContext):
    _, rest = call.data.split(":", 1)
    model, storage = rest.split("|", 1)
    await state.update_data(iphone_storage=storage)
    await state.set_state(AddItem.iphone_color)
    await call.message.edit_text(f"Выберите цвет для {model} {storage}:", reply_markup=kb.admin_iphone_colors_kb(model, storage))


@router.callback_query(F.data.startswith("aicolor:"))
async def admin_iphone_color(call: CallbackQuery, state: FSMContext):
    _, rest = call.data.split(":", 1)
    parts = rest.split("|", 2)
    model, storage, color = parts[0], parts[1], parts[2]
    auto_name = f"{model} {storage} {color}"
    await state.update_data(
        name=auto_name,
        iphone_color=color,
        category="Смартфоны",
    )
    await state.set_state(AddItem.condition)
    await call.message.edit_text(
        f"✅ Название: <b>{auto_name}</b>\n\nВыберите состояние:",
        parse_mode="HTML",
        reply_markup=kb.admin_conditions_kb()
    )


# --- Общий флоу добавления товара ---

@router.message(AddItem.name)
async def add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AddItem.category)
    await message.answer("Выберите категорию:", reply_markup=kb.admin_categories_kb())


@router.callback_query(F.data.startswith("acat:"))
async def add_category(call: CallbackQuery, state: FSMContext):
    category = call.data.split(":", 1)[1]
    current_state = await state.get_state()

    # Если нажали "Смартфоны" во время wizard iPhone — возврат к выбору группы
    if category == "Смартфоны" and current_state == AddItem.iphone_model:
        await state.set_state(AddItem.iphone_group)
        await call.message.edit_text("Выберите поколение iPhone:", reply_markup=kb.admin_iphone_groups_kb())
        return

    await state.update_data(category=category)

    # Если это Смартфоны и мы в обычном флоу — предложить iPhone wizard
    if category == "Смартфоны":
        await state.set_state(AddItem.iphone_group)
        await call.message.edit_text(
            "Выберите поколение iPhone или введите вручную:",
            reply_markup=kb.admin_iphone_groups_kb()
        )
        return

    await state.set_state(AddItem.condition)
    await call.message.edit_text("Выберите состояние:", reply_markup=kb.admin_conditions_kb())


@router.callback_query(F.data.startswith("acond:"))
async def add_condition(call: CallbackQuery, state: FSMContext):
    condition = call.data.split(":", 1)[1]
    await state.update_data(condition=condition)
    await state.set_state(AddItem.buy_price)
    await call.message.edit_text(
        "💰 Введите <b>цену закупки</b> в рублях (только цифры).\n"
        "Если не хотите указывать — отправьте <b>0</b>.",
        parse_mode="HTML"
    )


@router.message(AddItem.buy_price)
async def add_buy_price(message: Message, state: FSMContext):
    txt = (message.text or "").strip().replace(" ", "")
    if not txt.isdigit():
        await message.answer("Пожалуйста, введите только число (или 0 чтобы пропустить).")
        return
    await state.update_data(buy_price=int(txt))
    await state.set_state(AddItem.price)
    await message.answer("💵 Введите <b>цену продажи</b> в рублях:", parse_mode="HTML")


@router.message(AddItem.price)
async def add_price(message: Message, state: FSMContext):
    txt = (message.text or "").strip().replace(" ", "")
    if not txt.isdigit():
        await message.answer("Пожалуйста, введите только число.")
        return
    await state.update_data(price=int(txt))
    await state.set_state(AddItem.description)
    await message.answer("Введите описание товара (или отправьте '-' чтобы пропустить):")


@router.message(AddItem.description)
async def add_description(message: Message, state: FSMContext):
    desc = message.text if message.text != "-" else ""
    await state.update_data(description=desc, photos=[])
    await state.set_state(AddItem.photo)
    await message.answer(
        "Отправьте фото товара.\n"
        "Можно отправить несколько по одному.\n"
        "Когда закончите — напишите <b>готово</b>.\n"
        "Чтобы пропустить фото — напишите <b>-</b>",
        parse_mode="HTML"
    )


@router.message(AddItem.photo, F.photo)
async def add_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    data = await state.get_data()
    photos = data.get("photos", [])
    photos.append(photo_id)
    await state.update_data(photos=photos)
    await message.answer(f"📸 Фото {len(photos)} добавлено. Отправьте ещё или напишите <b>готово</b>.", parse_mode="HTML")


@router.message(AddItem.photo, F.text.lower() == "готово")
async def finish_photos(message: Message, state: FSMContext):
    await _finish_add(message, state)


@router.message(AddItem.photo, F.text == "-")
async def skip_photo(message: Message, state: FSMContext):
    await _finish_add(message, state)


async def _finish_add(message: Message, state: FSMContext):
    data = await state.get_data()
    photos: list = data.get("photos", [])
    await state.clear()

    first_photo = photos[0] if photos else ""
    buy_price = int(data.get("buy_price", 0) or 0)
    item_id = await db.add_item(
        name=data["name"],
        category=data["category"],
        condition=data["condition"],
        price=data["price"],
        description=data.get("description", ""),
        photo_id=first_photo,
        buy_price=buy_price,
        model=data.get("iphone_model", ""),
        storage=data.get("iphone_storage", ""),
        color=data.get("iphone_color", ""),
    )

    if photos:
        await db.add_item_photos(item_id, photos)

    # ─── Пишем в Google Sheets (📦 Склад) ───
    margin_str = ""
    if buy_price > 0:
        margin = data["price"] - buy_price
        margin_pct = margin / buy_price * 100 if buy_price else 0
        margin_str = f"\nНаценка: {margin:,} ₽ ({margin_pct:.1f}%)"

    sync_status = ""
    if sheets_sync.is_enabled():
        result = await sheets_sync.add_item(
            item_id,
            category=data["category"],
            model=data.get("iphone_model") or data["name"],
            storage=data.get("iphone_storage", ""),
            color=data.get("iphone_color", ""),
            condition=data["condition"],
            buy_price=buy_price,
            sell_price=data["price"],
            note=data.get("description", ""),
        )
        sync_status = "\n📊 Записано в Google Sheets" if result.get("ok") else \
                      f"\n⚠️ Sheets: {result.get('error','?')}"

    await message.answer(
        f"✅ Товар добавлен!\n"
        f"ID: <code>{item_id}</code>\n"
        f"<b>{data['name']}</b>\n"
        f"Закупка: {buy_price:,} ₽\n"
        f"Продажа: {data['price']:,} ₽"
        f"{margin_str}\n"
        f"Категория: {data['category']}\n"
        f"Состояние: {data['condition']}\n"
        f"Фото: {len(photos)} шт."
        f"{sync_status}",
        parse_mode="HTML",
        reply_markup=kb.admin_menu()
    )

    # Уведомляем подписчиков
    name = data["name"]
    subscribers = set()
    words = name.split()
    for i in range(len(words)):
        for j in range(i + 1, len(words) + 1):
            phrase = " ".join(words[i:j])
            for uid in await db.get_subscribers(phrase):
                subscribers.add(uid)

    if subscribers:
        notify_text = (
            f"🔔 Появился новый товар по вашей подписке!\n\n"
            f"<b>{name}</b> — {data['price']:,} ₽\n"
            f"Состояние: {data['condition']}\n\n"
            f"✍️ Написать: @idistoreman"
        )
        bot: Bot = message.bot
        for uid in subscribers:
            try:
                await bot.send_message(uid, notify_text, parse_mode="HTML")
            except Exception:
                pass


@router.message(F.text == "📢 Рассылка")
async def cmd_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    total = await db.count_users()
    await state.set_state(Broadcast.waiting_text)
    await message.answer(
        f"Введите текст рассылки.\n"
        f"Получателей: <b>{total}</b> чел.\n\n"
        f"Поддерживается HTML-форматирование: <b>жирный</b>, <i>курсив</i>, ссылки.\n"
        f"Для отмены напишите /cancel",
        parse_mode="HTML"
    )


@router.message(Broadcast.waiting_text)
async def broadcast_text(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Рассылка отменена.", reply_markup=kb.admin_menu())
        return
    await state.update_data(text=message.text)
    await state.set_state(Broadcast.confirm)
    await message.answer(
        f"Предпросмотр рассылки:\n\n{message.text}\n\n"
        f"Отправить всем пользователям?",
        parse_mode="HTML",
        reply_markup=kb.broadcast_confirm_kb()
    )


@router.callback_query(F.data == "broadcast:confirm")
async def broadcast_confirm(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    text = data.get("text", "")
    user_ids = await db.get_all_user_ids()

    await call.message.edit_text(f"⏳ Отправляю {len(user_ids)} пользователям...")

    sent = 0
    failed = 0
    bot: Bot = call.bot
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1

    await call.message.answer(
        f"✅ Рассылка завершена!\n"
        f"Отправлено: {sent}\n"
        f"Не доставлено: {failed}",
        reply_markup=kb.admin_menu()
    )


@router.callback_query(F.data == "broadcast:cancel")
async def broadcast_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Рассылка отменена.")
    await call.message.answer("Главное меню:", reply_markup=kb.admin_menu())


@router.message(F.text == "◀️ Выйти из админки")
async def exit_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Вы вышли из режима администратора.", reply_markup=kb.main_menu())


@router.message(F.text == "/admin")
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await message.answer("Режим администратора активирован.", reply_markup=kb.admin_menu())


# ─── Помечаем товар проданным ──────────────────────────────

@router.message(F.text == "✅ Продал товар")
async def cmd_sold_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(MarkSold.waiting_id)
    await message.answer(
        "Введите <b>ID</b> проданного товара (число).\n"
        "ID показывается при добавлении и в карточке товара.\n\n"
        "Для отмены — /cancel",
        parse_mode="HTML"
    )


@router.message(MarkSold.waiting_id)
async def sold_id(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=kb.admin_menu()); return
    if not txt.isdigit():
        await message.answer("Нужно число (ID товара)."); return

    item_id = int(txt)
    item = await db.get_item(item_id)
    if not item:
        await state.clear()
        await message.answer("Товар с таким ID не найден.", reply_markup=kb.admin_menu()); return

    await state.update_data(item_id=item_id, current_price=item["price"])
    await state.set_state(MarkSold.waiting_price)
    await message.answer(
        f"<b>{item['name']}</b>\n"
        f"Текущая цена в боте: {item['price']:,} ₽\n\n"
        f"Введите <b>финальную цену продажи</b> в рублях.\n"
        f"Если продали по той же цене — отправьте <b>=</b>\n"
        f"Для отмены — /cancel",
        parse_mode="HTML"
    )


@router.message(MarkSold.waiting_price)
async def sold_price(message: Message, state: FSMContext):
    txt = (message.text or "").strip().replace(" ", "")
    if txt == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=kb.admin_menu()); return

    data = await state.get_data()
    item_id = data["item_id"]
    if txt == "=":
        sell_price = data["current_price"]
    elif txt.isdigit():
        sell_price = int(txt)
    else:
        await message.answer("Нужно число или =")
        return

    await state.clear()

    # Локальная БД
    await db.mark_sold(item_id, sell_price=sell_price)

    # Google Sheets
    sync_status = ""
    if sheets_sync.is_enabled():
        res = await sheets_sync.mark_sold(item_id, sell_price=sell_price)
        sync_status = "📊 Записано в Google Sheets" if res.get("ok") else \
                      f"⚠️ Sheets: {res.get('error','?')}"

    await message.answer(
        f"✅ Товар <code>{item_id}</code> помечен проданным за <b>{sell_price:,} ₽</b>\n"
        f"{sync_status}",
        parse_mode="HTML",
        reply_markup=kb.admin_menu()
    )


# ─── Массовая выгрузка всех товаров в Sheets (одноразово) ───

@router.message(F.text == "📊 Синк в Sheets")
async def cmd_sync_all(message: Message):
    if not is_admin(message.from_user.id):
        return
    if not sheets_sync.is_enabled():
        await message.answer(
            "❌ Синхронизация выключена.\n\n"
            "Добавьте в .env:\n"
            "<code>SHEETS_WEBAPP_URL=...</code>\n"
            "<code>SHEETS_TOKEN=...</code>",
            parse_mode="HTML"
        )
        return

    # Сначала — пинг
    pong = await sheets_sync.ping()
    if not pong.get("ok"):
        await message.answer(f"❌ Не удалось достучаться до Apps Script:\n<code>{pong.get('error')}</code>",
                             parse_mode="HTML")
        return

    await message.answer("⏳ Выгружаю все товары в Google Sheets...")

    items = await db.get_items()  # только активные
    sent = 0
    failed = 0
    for item in items:
        try:
            res = await sheets_sync.add_item(
                item["id"],
                category=item["category"],
                model=item["model"] if "model" in item.keys() and item["model"] else item["name"],
                storage=(item["storage"] if "storage" in item.keys() else "") or "",
                color=(item["color"] if "color" in item.keys() else "") or "",
                condition=item["condition"],
                buy_price=int(item["buy_price"] or 0) if "buy_price" in item.keys() else 0,
                sell_price=int(item["price"]),
                note=item["description"] or "",
            )
            if res.get("ok"):
                sent += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    await message.answer(
        f"✅ Готово.\n"
        f"Загружено: <b>{sent}</b>\n"
        f"Ошибок: <b>{failed}</b>",
        parse_mode="HTML",
        reply_markup=kb.admin_menu()
    )


@router.message(F.text == "/export")
async def cmd_export(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    db_path = db.DB_PATH
    if not os.path.exists(db_path):
        await message.answer("❌ Файл базы данных не найден.")
        return

    total = await db.count_active_items()
    users = await db.count_users()

    await message.answer_document(
        FSInputFile(db_path, filename="shop.db"),
        caption=(
            f"📦 <b>Экспорт базы данных</b>\n\n"
            f"🛍 Товаров в наличии: <b>{total}</b>\n"
            f"👤 Пользователей: <b>{users}</b>\n\n"
            f"Сохраните файл <code>shop.db</code> и замените им локальную копию."
        ),
        parse_mode="HTML"
    )
