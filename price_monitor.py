import asyncio
import json
import logging
import os
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

import channel_source

logger = logging.getLogger(__name__)

DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(__file__))
os.makedirs(DATA_DIR, exist_ok=True)

MONITOR_STATE_FILE = os.path.join(DATA_DIR, "monitor_state.json")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))

TRACKED_POSTS = [
    # Айфоны — по одному посту на раздел прайса
    "BigSaleApple/12966",   # 17 / 17 Air
    "BigSaleApple/12968",   # 17 Pro / Pro Max
    "BigSaleApple/12965",   # 16
    "BigSaleApple/12964",   # 13-15
    # Маки
    "BigSaleApple/12463",
    "BigSaleApple/12464",
    "BigSaleApple/12459",
    "BigSaleApple/12460",
    "BigSaleApple/12455",
    "BigSaleApple/12456",
    # Айпады
    "BigSaleApple/12328",
    "BigSaleApple/12304",
    "BigSaleApple/12255",
    "BigSaleApple/12266",
    # Эирподы
    "BigSaleApple/12252",
    # Samsung
    "BigSaleApple/11198",
    # Pixel / OnePlus
    "BigSaleApple/11196",
    # Apple Watch
    "BigSaleApple/12119",
    "BigSaleApple/12149",
    "BigSaleApple/12150",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _load_state() -> dict:
    if Path(MONITOR_STATE_FILE).exists():
        try:
            with open(MONITOR_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Битый monitor_state.json ({e}), начинаем с чистого")
    return {}


def _save_state(state: dict):
    with open(MONITOR_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _extract_emoji(node) -> str:
    b = node.find("b")
    return b.get_text() if b else node.get_text()


def _html_to_text(elem) -> str:
    result = []
    for node in elem.children:
        if isinstance(node, str):
            result.append(node)
        elif node.name == "br":
            result.append("\n")
        elif node.name == "tg-emoji":
            result.append(_extract_emoji(node))
        elif node.name == "i" and "emoji" in (node.get("class") or []):
            result.append(_extract_emoji(node))
        else:
            result.append(_html_to_text(node))
    return "".join(result)


async def _get(session: aiohttp.ClientSession, url: str) -> str | None:
    async with session.get(
        url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)
    ) as resp:
        if resp.status != 200:
            logger.warning(f"{url} -> HTTP {resp.status}")
            return None
        body = await resp.text()
        if not body.strip():
            logger.warning(f"{url} -> HTTP 200, но тело пустое")
            return None
        return body


async def _fetch_via_embed(
    session: aiohttp.ClientSession, channel: str, post_id: str
) -> str | None:
    """Одиночный виджет поста. В отличие от ленты понимает альбомы:
    пост с несколькими фото отдаётся по своему прямому ID."""
    html = await _get(session, f"https://t.me/{channel}/{post_id}?embed=1")
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    text_elem = soup.find("div", class_="tgme_widget_message_text")
    if not text_elem:
        has_widget = bool(soup.find("div", class_="tgme_widget_message"))
        logger.warning(
            f"{channel}/{post_id}: embed без текста "
            f"(карточка {'есть' if has_widget else 'отсутствует'}, "
            f"{len(html)} байт)"
        )
        return None
    return _html_to_text(text_elem).strip()


async def _fetch_via_feed(
    session: aiohttp.ClientSession, channel: str, post_id: str
) -> str | None:
    """Запасной путь — лента канала. Ищет пост по точному data-post."""
    html = await _get(session, f"https://t.me/s/{channel}?before={int(post_id) + 1}")
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    post_wrap = soup.find("div", attrs={"data-post": f"{channel}/{post_id}"})
    if post_wrap:
        text_elem = post_wrap.find("div", class_="tgme_widget_message_text")
        if text_elem:
            return _html_to_text(text_elem).strip()
        logger.warning(f"{channel}/{post_id}: карточка в ленте есть, текста внутри нет")

    # Раньше здесь был подбор ближайшей карточки для альбомов. Убрано:
    # при защищённом канале он молча подставлял текст чужого поста.
    ids = sorted(
        d.get("data-post", "").split("/")[-1]
        for d in soup.find_all("div", attrs={"data-post": True})
    )
    logger.warning(
        f"{channel}/{post_id}: текста нет в ленте. Доступны: {ids[-8:]}"
    )
    return None


async def _fetch_post_text(session: aiohttp.ClientSession, post_path: str) -> str | None:
    channel, post_id = post_path.split("/")
    for name, fetcher in (("embed", _fetch_via_embed), ("лента", _fetch_via_feed)):
        try:
            text = await fetcher(session, channel, post_id)
        except Exception as e:
            logger.error(f"{post_path}: способ «{name}» упал — {e}")
            continue
        if text:
            logger.debug(f"{post_path}: получен через «{name}», {len(text)} символов")
            return text
    logger.warning(f"{post_path}: текст не удалось получить ни одним способом")
    return None


async def check_and_process(bot, admin_ids: set, process_text_fn, force: bool = False) -> dict:
    """
    Проверяет посты на изменения. Для каждого изменённого поста
    вызывает process_text_fn(text) — функцию из handlers.py.
    force=True — обработать все посты принудительно, даже если не изменились.
    Возвращает dict с итогами: fetched, processed, errors, failed_fetch.
    """
    state = _load_state()
    stats = {"fetched": 0, "processed": 0, "errors": [], "failed_fetch": [], "restored": []}

    backup = {}
    if force:
        # Кэши обнуляются перед прогоном, иначе iPhone-ветка накапливает дубли.
        # Но сначала снимаем копию: если парсинг ничего не даст (сменился формат
        # прайса или пост удалён), старые цены вернём вместо пустого раздела.
        from handlers import (
            _load_cache, _load_mac_cache, _load_hp_cache,
            _load_tablets_cache, _load_samsung_cache, _load_pixel_cache,
            _load_watch_cache,
            _save_cache, _save_mac_cache, _save_hp_cache,
            _save_tablets_cache, _save_samsung_cache, _save_pixel_cache,
            _save_watch_cache,
        )
        backup = {
            "iPhone":   (_load_cache(), _load_cache, _save_cache),
            "Mac":      (_load_mac_cache(), _load_mac_cache, _save_mac_cache),
            "Наушники": (_load_hp_cache(), _load_hp_cache, _save_hp_cache),
            "Планшеты": (_load_tablets_cache(), _load_tablets_cache, _save_tablets_cache),
            "Samsung":  (_load_samsung_cache(), _load_samsung_cache, _save_samsung_cache),
            "Pixel":    (_load_pixel_cache(), _load_pixel_cache, _save_pixel_cache),
            "Watch":    (_load_watch_cache(), _load_watch_cache, _save_watch_cache),
        }
        for snapshot, _, save_fn in backup.values():
            save_fn({})

    if channel_source.is_configured():
        try:
            texts = await channel_source.fetch_texts(TRACKED_POSTS)
            results = [texts.get(p) for p in TRACKED_POSTS]
        except Exception as e:
            logger.error(f"Клиент канала недоступен ({e}), пробуем веб-парсер")
            results = None
    else:
        results = None

    if results is None:
        async with aiohttp.ClientSession() as session:
            tasks = [_fetch_post_text(session, p) for p in TRACKED_POSTS]
            results = await asyncio.gather(*tasks)

    for post_path, text in zip(TRACKED_POSTS, results):
        if text is None:
            stats["failed_fetch"].append(post_path)
            continue

        stats["fetched"] += 1
        prev = state.get(post_path)

        if not force:
            if prev == text:
                continue
            state[post_path] = text
            # Первый запуск — просто сохраняем, не обрабатываем
            if prev is None:
                continue
        else:
            state[post_path] = text

        logger.info(f"{'[force] ' if force else ''}Обрабатываем пост {post_path}...")
        try:
            await process_text_fn(bot, admin_ids, text)
            stats["processed"] += 1
        except Exception as e:
            logger.error(f"Ошибка обработки {post_path}: {e}")
            stats["errors"].append(f"{post_path}: {e}")

    # Категории, которые после прогона остались пустыми, но раньше данные имели,
    # — это молчаливый сбой парсинга. Возвращаем старые цены и сообщаем админу.
    for name, (snapshot, load_fn, save_fn) in backup.items():
        if snapshot and not load_fn():
            save_fn(snapshot)
            stats["restored"].append(name)
            logger.warning(
                f"{name}: парсинг не дал ни одной позиции, откатили кэш на прошлую версию"
            )

    _save_state(state)
    logger.info(
        f"Монитор: получено {stats['fetched']}, обработано {stats['processed']}, "
        f"ошибок {len(stats['errors'])}, недоступно {len(stats['failed_fetch'])}, "
        f"откатов {len(stats['restored'])}"
    )
    return stats
