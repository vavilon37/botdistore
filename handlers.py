from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InputMediaPhoto
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import database as db
import keyboards as kb

router = Router()

# Store filter state per user in memory
user_filters: dict = {}


class SearchState(StatesGroup):
    waiting_query = State()


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
    text += "\n\n✍️ Написать: @distore_original"
    return text


MENU_CATEGORY_MAP = {
    "📱 Смартфоны": "Смартфоны",
    "💻 Ноутбуки": "Ноутбуки",
    "🎧 Наушники": "Наушники",
    "🔌 Аксессуары": "Аксессуары",
    "📟 Планшеты": "Планшеты",
    "📦 Другое": "Другое",
}


@router.message(F.text.in_(MENU_CATEGORY_MAP))
async def cmd_menu_category(message: Message):
    category = MENU_CATEGORY_MAP[message.text]
    if category == "Смартфоны":
        await message.answer("Выберите состояние:", reply_markup=kb.smartphones_condition_kb())
        return

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
            "Ничего не найдено. Попробуйте другой запрос.\n\nНе нашли что искали? Пишите: @distore_original",
            reply_markup=kb.main_menu()
        )
        return

    uid = message.from_user.id
    user_filters[uid] = {"items": [dict(i) for i in items], "page": 0}
    await message.answer(
        f"Найдено {len(items)} товаров:",
        reply_markup=kb.items_list_kb(user_filters[uid]["items"])
    )


@router.message(F.text == "🍎 Весь прайс iPhone")
async def cmd_iphone_pricelist(message: Message):
    items = await db.search_items("iPhone")
    if not items:
        await message.answer("Товаров iPhone не найдено.", reply_markup=kb.main_menu())
        return
    uid = message.from_user.id
    user_filters[uid] = {"items": [dict(i) for i in items], "page": 0}
    await message.answer(
        f"🍎 <b>Прайс лист iPhone</b> — {len(items)} шт.:",
        parse_mode="HTML",
        reply_markup=kb.items_list_kb(user_filters[uid]["items"])
    )


@router.message(F.text == "🔧 Сервис")
async def cmd_service(message: Message):
    await message.answer("По поводу ремонта пишите: @distore_original")


@router.message(F.text == "ℹ️ О боте")
async def cmd_about(message: Message):
    await message.answer(
        "🛍 <b>Магазин техники</b>\n\n"
        "Здесь вы найдёте б/у и новую технику по выгодным ценам.\n\n"
        "📱 Смартфоны, 💻 Ноутбуки, 🎮 Консоли и другие категории — прямо в меню\n"
        "🔍 Поиск по названию — найти конкретный товар\n"
        "🍎 Весь прайс iPhone — все айфоны в наличии",
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

    text = format_item(item)
    markup = kb.phone_card_kb(index, len(items), item["id"], is_admin)
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
    text = format_item(item)
    markup = kb.phone_card_kb(index, len(items), item["id"], is_admin)
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
    await call.message.edit_text("Выберите состояние:", reply_markup=kb.smartphones_condition_kb())


@router.callback_query(F.data.startswith("scond:"))
async def cb_smartphones_cond(call: CallbackQuery):
    cond = call.data.split(":", 1)[1]
    label = COND_LABEL.get(cond, "")
    await call.message.edit_text(f"{label} смартфоны — выберите раздел:", reply_markup=kb.smartphones_kb(cond))


@router.callback_query(F.data.startswith("scat:iphone"))
async def cb_iphone_groups(call: CallbackQuery):
    cond = call.data.split("|", 1)[1] if "|" in call.data else "all"
    user_filters[call.from_user.id] = user_filters.get(call.from_user.id, {})
    user_filters[call.from_user.id]["phone_cond"] = cond
    await call.message.edit_text("Выберите поколение iPhone:", reply_markup=kb.iphone_groups_kb(cond))


@router.callback_query(F.data.startswith("igrp:"))
async def cb_iphone_group(call: CallbackQuery):
    group = call.data.split(":", 1)[1]
    uid = call.from_user.id
    user_filters.setdefault(uid, {})["iphone_group"] = group
    user_filters[uid].pop("iphone_model", None)
    user_filters[uid].pop("iphone_storage", None)
    cond = user_filters[uid].get("phone_cond", "all")
    await call.message.edit_text(f"Выберите модель ({group}):", reply_markup=kb.iphone_models_kb(group, cond))


@router.callback_query(F.data.startswith("igrp_back:"))
async def cb_igrp_back(call: CallbackQuery):
    cond = call.data.split(":", 1)[1]
    await call.message.edit_text("Выберите поколение iPhone:", reply_markup=kb.iphone_groups_kb(cond))


@router.callback_query(F.data.startswith("imodel:"))
async def cb_iphone_model(call: CallbackQuery):
    model = call.data.split(":", 1)[1]
    uid = call.from_user.id
    user_filters.setdefault(uid, {})["iphone_model"] = model
    user_filters[uid].pop("iphone_storage", None)
    cond = user_filters[uid].get("phone_cond", "all")
    await call.message.edit_text(f"Выберите память для {model}:", reply_markup=kb.iphone_storage_kb(model, cond))


@router.callback_query(F.data.startswith("istorage:"))
async def cb_iphone_storage(call: CallbackQuery):
    _, rest = call.data.split(":", 1)
    model, storage = rest.split("|", 1)
    uid = call.from_user.id
    user_filters.setdefault(uid, {})["iphone_storage"] = storage
    cond = user_filters[uid].get("phone_cond", "all")
    await call.message.edit_text(f"Выберите цвет {model} {storage}:", reply_markup=kb.iphone_colors_kb(model, storage, cond))


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
        await call.message.edit_text(
            "Товаров не найдено.\n\nНе нашли что искали? Пишите: @distore_original",
            reply_markup=kb.main_menu_inline()
        )
        return

    all_items = [dict(i) for i in all_items]
    items = _filter_by_cond(all_items, cond)

    if not items:
        await call.message.edit_text(
            f"Нет товаров в категории «{COND_LABEL[cond]}».\n\nНе нашли что искали? Пишите: @distore_original",
            reply_markup=kb.main_menu_inline()
        )
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
    await call.message.edit_text("Выберите категорию:", reply_markup=kb.categories_kb())


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
    await call.message.edit_text(
        f"{'Все товары' if category == 'all' else category} — {len(items)} шт.:",
        reply_markup=kb.items_list_kb(user_filters[uid]["items"])
    )


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
    await call.message.edit_text(
        f"Найдено {len(items)} товаров:",
        reply_markup=kb.items_list_kb(user_filters[uid]["items"])
    )


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
    await call.message.edit_reply_markup(reply_markup=kb.items_list_kb(data["items"], page))


@router.callback_query(F.data == "back:catalog")
async def cb_back_catalog(call: CallbackQuery):
    uid = call.from_user.id
    data = user_filters.get(uid)
    page = data.get("page", 0) if data else 0
    items = data.get("items", []) if data else []

    if not items:
        await call.message.edit_text("Каталог пуст.", reply_markup=kb.categories_kb())
        return

    await call.message.edit_text(
        f"Найдено {len(items)} товаров:",
        reply_markup=kb.items_list_kb(items, page)
    )


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
        await call.message.edit_text(
            "Товаров не найдено.\n\nНе нашли что искали? Пишите: @distore_original",
            reply_markup=kb.main_menu_inline()
        )
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
    await call.answer("Товар удалён ✅")
    await call.message.delete()
