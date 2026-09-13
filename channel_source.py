"""Чтение постов канала через пользовательский клиент Telegram.

Нужен потому, что в канале поставщика включена защита контента: веб-превью
(t.me/s/… и ?embed=1) отдаёт страницу без текста, а Bot API не даёт читать
чужой канал без прав администратора в нём.

Работает только на чтение: забирает тексты нужных постов и слушает новые
и отредактированные. Никаких отправок, вступлений и прочей активности.

Настраивается тремя переменными окружения. Если хоть одной нет — модуль
выключен, и всё продолжает работать по-старому через веб-парсер.

    TG_API_ID    — с my.telegram.org
    TG_API_HASH  — оттуда же
    TG_SESSION   — строка сессии, см. make_session.py

TG_SESSION равнозначна доступу к аккаунту. Только переменные окружения,
никогда не в репозиторий.
"""
import logging
import os

logger = logging.getLogger(__name__)

API_ID = os.getenv("TG_API_ID", "").strip()
API_HASH = os.getenv("TG_API_HASH", "").strip()
SESSION = os.getenv("TG_SESSION", "").strip()

_client = None


def is_configured() -> bool:
    return bool(API_ID and API_HASH and SESSION)


async def get_client():
    """Подключается при первом обращении и держит соединение дальше."""
    global _client
    if _client is not None:
        return _client

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(SESSION), int(API_ID), API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        raise RuntimeError(
            "TG_SESSION недействительна или отозвана. "
            "Сгенерируйте заново: python make_session.py"
        )

    me = await client.get_me()
    logger.info("Клиент канала подключён как @%s", me.username or me.id)
    _client = client
    return _client


async def fetch_texts(post_paths: list[str]) -> dict[str, str]:
    """Тексты постов по списку «канал/id». Пропущенные просто отсутствуют."""
    client = await get_client()

    by_channel: dict[str, list[int]] = {}
    for path in post_paths:
        channel, msg_id = path.split("/")
        by_channel.setdefault(channel, []).append(int(msg_id))

    out: dict[str, str] = {}
    for channel, ids in by_channel.items():
        try:
            messages = await client.get_messages(channel, ids=ids)
        except Exception as e:
            logger.error("Не удалось прочитать %s: %s", channel, e)
            continue
        for msg_id, msg in zip(ids, messages):
            text = (getattr(msg, "message", "") or "").strip() if msg else ""
            if text:
                out[f"{channel}/{msg_id}"] = text
            else:
                logger.warning("%s/%s: пост пуст или удалён", channel, msg_id)

    logger.info("Прочитано постов: %d из %d", len(out), len(post_paths))
    return out


async def listen(channels: list[str], on_text) -> None:
    """Слушает новые и отредактированные посты. Не возвращает управление."""
    from telethon import events

    client = await get_client()

    async def handler(event):
        text = (event.message.message or "").strip()
        if not text:
            return
        kind = "изменён" if isinstance(event, events.MessageEdited.Event) else "новый"
        logger.info("Пост %s в канале (%s), %d символов", event.message.id, kind, len(text))
        try:
            await on_text(text)
        except Exception:
            logger.exception("Ошибка обработки поста %s", event.message.id)

    client.add_event_handler(handler, events.NewMessage(chats=channels))
    client.add_event_handler(handler, events.MessageEdited(chats=channels))

    logger.info("Слушаем каналы: %s", ", ".join(channels))
    await client.run_until_disconnected()
