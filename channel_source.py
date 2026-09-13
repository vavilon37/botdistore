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

# Насколько далеко искать подпись альбома от указанного сообщения
ALBUM_SPAN = 10


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


async def _caption_of_album(client, channel: str, msg_id: int, group_id: int) -> str:
    """Подпись альбома. В группе фото текст лежит ровно на одном сообщении,
    у остальных поле пустое — ищем его среди соседей по grouped_id.

    Сверка идёт по самому grouped_id, а не по близости номеров: подпись
    соседнего, но чужого поста подставить нельзя.
    """
    span = range(max(msg_id - ALBUM_SPAN, 1), msg_id + ALBUM_SPAN + 1)
    try:
        neighbours = await client.get_messages(channel, ids=list(span))
    except Exception as e:
        logger.error("Не удалось дочитать альбом %s/%s: %s", channel, msg_id, e)
        return ""

    for n in neighbours:
        if n is None or getattr(n, "grouped_id", None) != group_id:
            continue
        text = (getattr(n, "message", "") or "").strip()
        if text:
            logger.info(
                "%s/%s: альбом, подпись взята из сообщения %s", channel, msg_id, n.id
            )
            return text
    return ""


async def fetch_texts(post_paths: list[str]) -> dict[str, str]:
    """Тексты постов по списку «канал/id». Пропущенные просто отсутствуют."""
    client = await get_client()

    by_channel: dict[str, list[int]] = {}
    for path in post_paths:
        channel, msg_id = path.split("/")
        by_channel.setdefault(channel, []).append(int(msg_id))

    out: dict[str, str] = {}
    # Если несколько запрошенных ID оказались членами одного альбома, подпись
    # у них общая. Отдаём её один раз, иначе одни и те же цены лягут в кэш
    # столько раз, сколько членов альбома попало в список.
    seen_albums: set[tuple[str, int]] = set()

    for channel, ids in by_channel.items():
        try:
            messages = await client.get_messages(channel, ids=ids)
        except Exception as e:
            logger.error("Не удалось прочитать %s: %s", channel, e)
            continue

        for msg_id, msg in zip(ids, messages):
            if msg is None:
                logger.warning("%s/%s: сообщение удалено или недоступно", channel, msg_id)
                continue

            text = (getattr(msg, "message", "") or "").strip()
            if text:
                out[f"{channel}/{msg_id}"] = text
                continue

            group_id = getattr(msg, "grouped_id", None)
            if not group_id:
                logger.warning("%s/%s: пост без текста", channel, msg_id)
                continue

            if (channel, group_id) in seen_albums:
                logger.info(
                    "%s/%s: тот же альбом, что и предыдущий пост — пропускаем",
                    channel, msg_id,
                )
                continue

            text = await _caption_of_album(client, channel, msg_id, group_id)
            if text:
                seen_albums.add((channel, group_id))
                out[f"{channel}/{msg_id}"] = text
            else:
                logger.warning(
                    "%s/%s: альбом без подписи (группа %s)", channel, msg_id, group_id
                )

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
