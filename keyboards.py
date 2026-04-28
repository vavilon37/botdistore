from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from iphone_data import IPHONE_GROUPS, IPHONE_MODELS

CATEGORIES = ["Смартфоны", "Ноутбуки", "Планшеты", "Наушники", "Аксессуары", "Другое"]
CONDITIONS = ["Новое", "Как новое", "Хорошее", "Удовлетворительное"]


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📱 Смартфоны New"), KeyboardButton(text="♻️ Смартфоны Б/У")],
        [KeyboardButton(text="🎧 Наушники"), KeyboardButton(text="🔌 Аксессуары")],
        [KeyboardButton(text="📟 Планшеты"), KeyboardButton(text="🖥 Маки")],
        [KeyboardButton(text="🔍 Поиск по названию"), KeyboardButton(text="💰 Фильтр")],
        [KeyboardButton(text="🍎 Рынок б/у iPhone")],
        [KeyboardButton(text="❤️ Избранное")],
        [KeyboardButton(text="🔧 Сервис"), KeyboardButton(text="ℹ️ О боте")],
    ], resize_keyboard=True)


def main_menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="back:main")]
    ])


def smartphones_condition_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Новые", callback_data="scond:new")],
        [InlineKeyboardButton(text="♻️ Б/У", callback_data="scond:used")],
        [InlineKeyboardButton(text="🔄 Все", callback_data="scond:all")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back:categories")],
    ])


def smartphones_kb(cond: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍎 iPhone", callback_data=f"scat:iphone|{cond}")],
        [InlineKeyboardButton(text="📱 Другие смартфоны", callback_data=f"scat:other|{cond}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="cat:Смартфоны")],
    ])


def _search_btn(label: str) -> list:
    return [InlineKeyboardButton(text=f"🔍 Показать: {label}", callback_data="isearch:now")]


def iphone_groups_kb(cond: str) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=g, callback_data=f"igrp:{g}")] for g in IPHONE_GROUPS]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"scond:{cond}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def iphone_models_kb(group: str, cond: str = "all") -> InlineKeyboardMarkup:
    models = IPHONE_GROUPS.get(group, [])
    buttons = [[InlineKeyboardButton(text=m, callback_data=f"imodel:{m}")] for m in models]
    cond_label = {"new": "🆕", "used": "♻️", "all": "🔄"}.get(cond, "")
    buttons.append(_search_btn(f"{cond_label} {group}".strip()))
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"igrp_back:{cond}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def iphone_storage_kb(model: str, cond: str = "all") -> InlineKeyboardMarkup:
    storage_list = IPHONE_MODELS.get(model, {}).get("storage", [])
    buttons = [[InlineKeyboardButton(text=s, callback_data=f"istorage:{model}|{s}")] for s in storage_list]
    group = next((g for g, ms in IPHONE_GROUPS.items() if model in ms), None)
    cond_label = {"new": "🆕", "used": "♻️", "all": "🔄"}.get(cond, "")
    buttons.append(_search_btn(f"{cond_label} {model}".strip()))
    back_cb = f"igrp:{group}" if group else "scat:iphone"
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def iphone_colors_kb(model: str, storage: str, cond: str = "all") -> InlineKeyboardMarkup:
    colors = IPHONE_MODELS.get(model, {}).get("colors", [])
    buttons = [[InlineKeyboardButton(text=c, callback_data=f"icolor:{model}|{storage}|{c}")] for c in colors]
    cond_label = {"new": "🆕", "used": "♻️", "all": "🔄"}.get(cond, "")
    buttons.append(_search_btn(f"{cond_label} {model} {storage}".strip()))
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"imodel:{model}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def categories_kb(back=False) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=cat, callback_data=f"cat:{cat}")] for cat in CATEGORIES]
    buttons.append([InlineKeyboardButton(text="🔄 Все товары", callback_data="cat:all")])
    if back:
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def conditions_kb() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=c, callback_data=f"cond:{c}")] for c in CONDITIONS]
    buttons.append([InlineKeyboardButton(text="🔄 Любое состояние", callback_data="cond:all")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back:filters")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def item_kb(item_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text="◀️ Назад к списку", callback_data="back:catalog")]]
    if is_admin:
        buttons.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{item_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def phone_card_kb(index: int, total: int, item_id: int, is_admin: bool = False,
                  is_fav: bool = False, views: int = 0, item_name: str = "", is_sub: bool = False) -> InlineKeyboardMarkup:
    nav = []
    if index > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"pcard:{index - 1}"))
    if index < total - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"pcard:{index + 1}"))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text=f"{index + 1} / {total}", callback_data="noop")])
    fav_text = "❤️ В избранном" if is_fav else "🤍 В избранное"
    buttons.append([InlineKeyboardButton(text=fav_text, callback_data=f"fav:{item_id}")])
    if views:
        buttons.append([InlineKeyboardButton(text=f"👁 {views} просмотров", callback_data="noop")])
    buttons.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="back:main")])
    if is_admin:
        buttons.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{item_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def items_list_kb(items: list, page: int = 0, per_page: int = 5) -> InlineKeyboardMarkup:
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    buttons = []
    for item in page_items:
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} — {item['price']} ₽",
            callback_data=f"item:{item['id']}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"page:{page-1}"))
    if end < len(items):
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"page:{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="back:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Добавить iPhone"), KeyboardButton(text="➕ Добавить товар")],
        [KeyboardButton(text="✅ Продал товар"), KeyboardButton(text="📊 Синк в Sheets")],
        [KeyboardButton(text="📱 Смартфоны New"), KeyboardButton(text="🔍 Поиск по названию")],
        [KeyboardButton(text="📢 Рассылка")],
        [KeyboardButton(text="◀️ Выйти из админки")],
    ], resize_keyboard=True)


def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast:confirm"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast:cancel")],
    ])


FILTER_CATEGORIES = [
    ("📱 Смартфоны", "Смартфоны"),
    ("📟 Планшеты", "Планшеты"),
    ("🖥 Маки", "Ноутбуки"),
    ("🎧 Наушники", "Наушники"),
    ("🔌 Аксессуары", "Аксессуары"),
    ("🔄 Все категории", "all"),
]

PRICE_RANGES = [
    ("до 5 000 ₽", 0, 5000),
    ("5 000 — 10 000 ₽", 5000, 10000),
    ("10 000 — 20 000 ₽", 10000, 20000),
    ("20 000 — 35 000 ₽", 20000, 35000),
    ("35 000 — 55 000 ₽", 35000, 55000),
    ("55 000 — 80 000 ₽", 55000, 80000),
    ("от 80 000 ₽", 80000, 0),
]


def price_filter_category_kb() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=label, callback_data=f"pf_cat:{cat}")]
               for label, cat in FILTER_CATEGORIES]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def price_filter_kb(category: str = "all") -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=label, callback_data=f"price:{mn}:{mx}:{category}")]
               for label, mn, mx in PRICE_RANGES]
    buttons.append([InlineKeyboardButton(text="✏️ Ввести свой диапазон", callback_data=f"price:custom:{category}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="pf_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def price_sort_kb(category: str, sort: str, label: str) -> InlineKeyboardMarkup:
    sorts = [
        ("💰 Дешевле", "price_asc"),
        ("💎 Дороже", "price_desc"),
    ]
    sort_buttons = []
    for s_label, s_key in sorts:
        text = f"✅ {s_label}" if sort == s_key else s_label
        sort_buttons.append(InlineKeyboardButton(text=text, callback_data=f"psort:{s_key}:{category}:{label}"))
    buttons = [sort_buttons]
    buttons.append([InlineKeyboardButton(text="🏷 По модели", callback_data=f"pmodel_list:{category}:{label}")])
    buttons.append([InlineKeyboardButton(text="🔄 Фильтр", callback_data=f"pf_cat:{category}" if category != "all" else "pf_back")])
    buttons.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="back:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def price_model_list_kb(models: list, category: str, label: str) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=m, callback_data=f"pmodel:{m}:{category}:{label}")] for m in models]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"pmodel_back:{category}:{label}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def subscriptions_kb(subs: list[str]) -> InlineKeyboardMarkup:
    buttons = []
    for model in subs:
        buttons.append([InlineKeyboardButton(text=f"❌ {model}", callback_data=f"unsub:{model}")])
    buttons.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="back:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def new_smartphones_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍎 iPhone", callback_data="new_type:iphone")],
        [InlineKeyboardButton(text="📱 Другие", callback_data="new_type:other")],
    ])


def iphone_series_kb() -> InlineKeyboardMarkup:
    series = ["17", "16", "15", "14", "13", "12"]
    buttons = [[InlineKeyboardButton(text=s, callback_data=f"new_series:{s}")] for s in series]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="new_type:back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def new_series_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к сериям", callback_data="new_type:iphone")],
    ])


def preview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data="preview:save")],
        [InlineKeyboardButton(text="✏️ Изменить пометки", callback_data="preview:edit_notes")],
    ])


def headphones_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎵 AirPods", callback_data="hp_cat:airpods")],
        [InlineKeyboardButton(text="🎧 AirPods Pro", callback_data="hp_cat:airpods_pro")],
        [InlineKeyboardButton(text="🎼 AirPods Max", callback_data="hp_cat:airpods_max")],
    ])


def hp_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к наушникам", callback_data="hp_cat:back")],
    ])


def hp_preview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data="hp_preview:save")],
        [InlineKeyboardButton(text="✏️ Изменить пометки", callback_data="hp_preview:edit_notes")],
    ])


def admin_categories_kb() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=cat, callback_data=f"acat:{cat}")] for cat in CATEGORIES]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_conditions_kb() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=c, callback_data=f"acond:{c}")] for c in CONDITIONS]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- Admin iPhone wizard keyboards ---

def admin_iphone_groups_kb() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=g, callback_data=f"aigrp:{g}")] for g in IPHONE_GROUPS]
    buttons.append([InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="aiphone:manual")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_iphone_models_kb(group: str) -> InlineKeyboardMarkup:
    models = IPHONE_GROUPS.get(group, [])
    buttons = [[InlineKeyboardButton(text=m, callback_data=f"aimodel:{m}")] for m in models]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="acat:Смартфоны")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_iphone_storage_kb(model: str) -> InlineKeyboardMarkup:
    storage_list = IPHONE_MODELS.get(model, {}).get("storage", [])
    buttons = [[InlineKeyboardButton(text=s, callback_data=f"aistorage:{model}|{s}")] for s in storage_list]
    group = next((g for g, ms in IPHONE_GROUPS.items() if model in ms), None)
    back_cb = f"aigrp:{group}" if group else "acat:Смартфоны"
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_iphone_colors_kb(model: str, storage: str) -> InlineKeyboardMarkup:
    colors = IPHONE_MODELS.get(model, {}).get("colors", [])
    buttons = [[InlineKeyboardButton(text=c, callback_data=f"aicolor:{model}|{storage}|{c}")] for c in colors]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"aimodel:{model}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
