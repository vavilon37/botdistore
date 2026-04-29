import asyncio
import json
import logging
import os
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MONITOR_STATE_FILE = os.path.join(os.path.dirname(__file__), "monitor_state.json")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))

TRACKED_POSTS = [
    # Айфоны
    "BigSaleApple/12854",
    "BigSaleApple/12472",
    "BigSaleApple/12471",
    "BigSaleApple/12470",
    "BigSaleApple/12468",
    "BigSaleApple/12466",
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
        with open(MONITOR_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
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


async def _fetch_post_text(session: aiohttp.ClientSession, post_path: str) -> str | None:
    channel, post_id_str = post_path.split("/")
    post_id = int(post_id_str)
    url = f"https://t.me/s/{channel}?before={post_id + 1}"
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
            soup = BeautifulSoup(html, "lxml")
            post_wrap = soup.find("div", attrs={"data-post": f"{channel}/{post_id_str}"})
            if not post_wrap:
                return None
            text_elem = post_wrap.find("div", class_="tgme_widget_message_text")
            if text_elem:
                return _html_to_text(text_elem).strip()
            return None
    except Exception as e:
        logger.error(f"Ошибка получения {post_path}: {e}")
        return None


async def check_and_process(bot, admin_ids: set, process_text_fn, force: bool = False):
    """
    Проверяет посты на изменения. Для каждого изменённого поста
    вызывает process_text_fn(text) — функцию из handlers.py.
    force=True — обработать все посты принудительно, даже если не изменились.
    """
    state = _load_state()
    changed = 0

    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_post_text(session, p) for p in TRACKED_POSTS]
        results = await asyncio.gather(*tasks)

    for post_path, text in zip(TRACKED_POSTS, results):
        if text is None:
            continue
        prev = state.get(post_path)

        if not force:
            if prev == text:
                continue
            changed += 1
            state[post_path] = text
            # Первый запуск — просто сохраняем, не обрабатываем
            if prev is None:
                continue
        else:
            state[post_path] = text
            changed += 1

        logger.info(f"{'[force] ' if force else ''}Обрабатываем пост {post_path}...")
        try:
            await process_text_fn(bot, admin_ids, text)
        except Exception as e:
            logger.error(f"Ошибка обработки {post_path}: {e}")
            for aid in admin_ids:
                try:
                    await bot.send_message(aid, f"⚠️ Ошибка обработки поста {post_path}: {e}")
                except Exception:
                    pass

    _save_state(state)
    if changed:
        logger.info(f"Монитор: {'принудительно ' if force else ''}обработано {changed} постов")
