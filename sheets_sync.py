"""
Синхронизация с Google Sheets через Apps Script Web App.

Каждое добавление товара / продажа / удаление в боте отправляет POST
в Apps Script, который пишет строку в лист «📦 Склад».

В .env должны быть:
    SHEETS_WEBAPP_URL=https://script.google.com/macros/s/.../exec
    SHEETS_TOKEN=<тот же что в apps_script.gs>

Если переменные не заданы — синхронизация просто отключена (бот работает как раньше).
"""
import os
import json
import asyncio
import logging
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

log = logging.getLogger("sheets_sync")

WEBAPP_URL = os.getenv("SHEETS_WEBAPP_URL", "").strip()
TOKEN      = os.getenv("SHEETS_TOKEN", "").strip()
TIMEOUT    = 15  # секунд


def is_enabled() -> bool:
    return bool(WEBAPP_URL and TOKEN)


# ─── Фильтр: в Sheets попадают ТОЛЬКО Б/У iPhone ──────────
# Новые iPhone (condition == "Новый") и любые аксессуары/прочая техника
# учитываются в SQLite, но в Google Sheets не выгружаются.
_NEW_CONDITIONS = {"новый", "новое", "new"}

def _is_used_iphone(model: str, condition: str) -> bool:
    m = (model or "").lower()
    c = (condition or "").strip().lower()
    if "iphone" not in m:
        return False
    if c in _NEW_CONDITIONS:
        return False
    return True


def _post_sync(payload: dict) -> dict:
    """Блокирующий POST. Запускаем в executor чтобы не тормозить aiogram."""
    body = json.dumps({"token": TOKEN, **payload}).encode("utf-8")
    req = Request(
        WEBAPP_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"ok": False, "error": "Bad JSON: " + raw[:200]}
    except HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
    except URLError as e:
        return {"ok": False, "error": f"URL error: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _post(payload: dict) -> dict:
    if not is_enabled():
        return {"ok": False, "error": "sheets_sync disabled (no env vars)"}
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _post_sync, payload)


# ─── Публичные операции ───────────────────────────────────

async def add_item(item_id: int, *, category: str, model: str,
                   storage: str = "", color: str = "",
                   condition: str = "", buy_price: int = 0,
                   sell_price: int = 0, note: str = "",
                   created_at: str = None) -> dict:
    """Добавить или обновить товар в листе 📦 Склад.

    Только Б/У iPhone — всё остальное (новые iPhone, аксессуары) пропускается.
    """
    if not is_enabled():
        return {"ok": False, "error": "disabled"}
    if not _is_used_iphone(model, condition):
        return {"ok": True, "skipped": "не Б/У iPhone — фильтр Sheets"}
    return await _post({
        "op": "add",
        "id": int(item_id),
        "category": category or "",
        "model": model or "",
        "storage": storage or "",
        "color": color or "",
        "condition": condition or "",
        "buy_price": int(buy_price or 0),
        "sell_price": int(sell_price or 0),
        "note": note or "",
        "created_at": created_at,
    })


async def mark_sold(item_id: int, *, sell_price: int = None,
                    sold_at: str = None, note: str = "") -> dict:
    if not is_enabled():
        return {"ok": False, "error": "disabled"}
    payload = {"op": "sold", "id": int(item_id)}
    if sell_price is not None:
        payload["sell_price"] = int(sell_price)
    if sold_at:
        payload["sold_at"] = sold_at
    if note:
        payload["note"] = note
    return await _post(payload)


async def mark_deleted(item_id: int) -> dict:
    if not is_enabled():
        return {"ok": False, "error": "disabled"}
    return await _post({"op": "delete", "id": int(item_id)})


async def update_item(item_id: int, **fields) -> dict:
    if not is_enabled():
        return {"ok": False, "error": "disabled"}
    payload = {"op": "update", "id": int(item_id)}
    for k in ("sell_price", "buy_price", "model", "color",
              "storage", "condition", "note", "status"):
        if k in fields and fields[k] is not None:
            payload[k] = fields[k]
    return await _post(payload)


async def ping() -> dict:
    return await _post({"op": "ping"})


# ─── Безопасный вызов из хендлеров (не падает если sheets недоступен) ───
async def safe(coro) -> None:
    try:
        res = await coro
        if not res.get("ok"):
            log.warning("sheets_sync error: %s", res.get("error"))
    except Exception as e:  # noqa
        log.exception("sheets_sync exception: %s", e)
