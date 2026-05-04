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

# Временный буфер до команды "готово": {series: [msg_text, ...]}
_draft: dict = {}

# Буфер готового прайса ожидающего подтверждения: {series: {"msgs": [...], "updated_at": ...}}
_preview_cache: dict = {}

# Кастомные пометки для серий: {series: str}
_custom_notes: dict = {}

# Буферы для наушников
_hp_draft: dict = {}
_hp_preview_cache: dict = {}
_hp_custom_notes: dict = {}

# Буферы для маков
_mac_draft: dict = {}
_mac_preview_cache: dict = {}
_mac_custom_notes: dict = {}

# Буферы для планшетов
_tab_draft: dict = {}
_tab_preview_cache: dict = {}
_tab_custom_notes: dict = {}


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


# Кириллические буквы, визуально похожие на латинские (встречаются в прайсах)
_CYRILLIC_TO_LATIN = str.maketrans("аАвВеЕкКМоОрРсСТхХ", "aABBEEkKMoOpPcCTxX")


def _normalize(text: str) -> str:
    """Заменяет визуально схожие кириллические буквы на латинские."""
    return text.translate(_CYRILLIC_TO_LATIN)


def _detect_series(text: str) -> list[str]:
    """Возвращает список серий iPhone найденных в тексте.
    Требует явный контекст iPhone: либо 'iPhone 1X', либо строка начинается с серии + модель.
    """
    t = _normalize(text)
    found = []
    for series in ["17", "16", "15", "14", "13", "12"]:
        # Вариант 1: явно написано "iPhone 17 ..."
        explicit = re.search(rf"iPhone\s+{series}\b", t, re.IGNORECASE)
        # Вариант 2: строка начинается с серии и модели (как в прайсе)
        line_start = re.search(
            rf"^\s*{series}\s+(Pro|Plus|Max|Air|mini|\d{{2,4}})\b",
            t, re.IGNORECASE | re.MULTILINE
        )
        if explicit or line_start:
            found.append(series)
    return found


def _split_by_series(text: str, series_list: list[str]) -> dict[str, str]:
    """Разбивает текст на части по сериям. Пояснения копируются в каждую серию."""
    price_lines_by_series: dict[str, list] = {s: [] for s in series_list}
    footnote_lines: list = []
    in_footnote = False

    for line in text.split("\n"):
        if not in_footnote and _is_footnote_line(line):
            in_footnote = True
        if in_footnote:
            footnote_lines.append(line)
            continue
        if not _is_iphone_price_line(line):
            continue
        # Определяем к какой серии относится строка
        matched = False
        line_n = _normalize(line)
        for s in series_list:
            if re.match(rf"^\s*(iPhone\s+)?{s}\s*(Pro|Plus|Max|Air|mini|[еe]\b|\d{{2,4}}\b)", line_n, re.IGNORECASE):
                price_lines_by_series[s].append(line)
                matched = True
                break
        if not matched:
            # Если не определили точно — добавляем во все серии
            for s in series_list:
                price_lines_by_series[s].append(line)

    result = {}
    footnote_text = "\n".join(footnote_lines).strip()
    for s in series_list:
        prices = "\n".join(price_lines_by_series[s]).strip()
        if prices:
            result[s] = prices + ("\n\n" + footnote_text if footnote_text else "")
    return result


_EXCLUDE_LINE_PATTERNS = re.compile(
    r"актив|предактив|распакован|раскрыта\s*упаковка|ASIS|ACTIVE"
    r"|уцен|замена|дисплей|АКБ|батаре|царап|скол|трещ|корпус|ремонт"
    r"|состояние|б/у|БУ\b|used|refurb|витрин|мятая\s*коробка",
    re.IGNORECASE
)


def _is_iphone_price_line(line: str) -> bool:
    """Строка с ценой на iPhone: начинается с номера серии 12-17 или 'iPhone 12-17'."""
    if _EXCLUDE_LINE_PATTERNS.search(line):
        return False
    line_n = _normalize(line)
    has_model_start = bool(re.match(
        r"^\s*(iPhone\s+)?1[2-7]\s*(Pro|Plus|Max|Air|mini|[еe]\b|\d{2,4}\b)",
        line_n, re.IGNORECASE
    ))
    has_price = bool(re.search(r"\d{2,3}[.]\d{3}", line))
    return has_model_start and has_price


def _is_footnote_line(line: str) -> bool:
    """Строки-пояснения в конце сообщения — сохраняем."""
    for pat in _KEEP_FOOTNOTE_PATTERNS:
        if re.search(pat, line):
            return True
    return False


HEADPHONES_CACHE_FILE = os.path.join(os.path.dirname(__file__), "headphones_cache.json")

HP_CATEGORIES = {
    "airpods": "AirPods",
    "airpods_pro": "AirPods Pro",
    "airpods_max": "AirPods Max",
}

# Наценка 1000 для запчастей (отдельные уши, боксы)
HP_PARTS_MARKUP = 1000

_HP_EXCLUDE = re.compile(
    r"актив|предактив|распакован|раскрыта\s*упаковка|ASIS|ACTIVE"
    r"|уцен|замена|АКБ|батаре|царап|скол|трещ|корпус|ремонт"
    r"|состояние|б/у|БУ\b|used|refurb|витрин"
    r"|EarPods",  # EarPods — не AirPods, не нужны
    re.IGNORECASE
)

# Строки с запчастями (ухо/бокс) — наценка 1000р
_HP_PARTS_PATTERN = re.compile(
    r"левое\s*ухо|правое\s*ухо|\bbox\b",
    re.IGNORECASE
)


def _is_hp_parts_line(line: str) -> bool:
    return bool(_HP_PARTS_PATTERN.search(line))


def _is_headphones_price_line(line: str) -> bool:
    if _HP_EXCLUDE.search(line):
        return False
    has_model = bool(re.search(r"AirPods", line, re.IGNORECASE))
    has_price = bool(re.search(r"\d{1,3}[.]\d{3}", line))
    return has_model and has_price


def _classify_hp_line(line: str) -> str | None:
    """Возвращает ключ категории для строки с ценой наушников или None."""
    if not _is_headphones_price_line(line):
        return None
    # Порядок важен: сначала Max и Pro (более специфичные)
    if re.search(r"AirPods\s+Max", line, re.IGNORECASE):
        return "airpods_max"
    if re.search(r"AirPods\s+Pro", line, re.IGNORECASE):
        return "airpods_pro"
    return "airpods"


def _detect_hp_categories(text: str) -> list[str]:
    found = set()
    for line in text.split("\n"):
        cat = _classify_hp_line(line)
        if cat:
            found.add(cat)
    return list(found)


def _split_hp_by_category(text: str) -> dict[str, str]:
    """Разбивает текст на части по категориям наушников."""
    lines_by_cat: dict[str, list] = {"airpods": [], "airpods_pro": [], "airpods_max": []}

    for line in text.split("\n"):
        cat = _classify_hp_line(line)
        if cat:
            lines_by_cat[cat].append(line)

    result = {}
    for cat, lines in lines_by_cat.items():
        if lines:
            result[cat] = "\n".join(lines).strip()
    return result


def _add_markup_to_hp_prices(text: str) -> str:
    price_pattern = re.compile(r"(\d{1,3})[.](\d{3})")
    lines = text.split("\n")
    result = []
    for line in lines:
        if _is_headphones_price_line(line):
            markup = HP_PARTS_MARKUP if _is_hp_parts_line(line) else PRICE_MARKUP
            def replace_price(m, _markup=markup):
                price = int(m.group(1)) * 1000 + int(m.group(2))
                new_price = price + _markup
                return f"{new_price // 1000}.{new_price % 1000:03d}"
            line = price_pattern.sub(replace_price, line)
            line = line.replace("*", "").strip()
        result.append(line)
    return "\n".join(result)


def _filter_headphones_lines_for_cat(text: str, cat: str) -> str:
    """Оставляет только строки нужной категории + пояснения."""
    split = _split_hp_by_category(text)
    return split.get(cat, "")


def _load_hp_cache() -> dict:
    if os.path.exists(HEADPHONES_CACHE_FILE):
        with open(HEADPHONES_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_hp_cache(cache: dict):
    with open(HEADPHONES_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


MAC_CACHE_FILE = os.path.join(os.path.dirname(__file__), "mac_cache.json")

MAC_CATEGORIES = {
    "macbook_pro": "MacBook Pro",
    "macbook_air": "MacBook Air",
    "imac": "iMac",
    "mac_mini": "Mac Mini",
}

_MAC_EXCLUDE = re.compile(
    r"актив|предактив|распакован|ASIS|ACTIVE|уцен|замена|б/у|БУ\b|used|refurb|витрин",
    re.IGNORECASE
)

# Цена в строках маков: число вида 77.000 / 136.000 / 143000 / 77000
# Может идти после дефиса/тире/пробела или прямо в конце строки
_MAC_PRICE_RE = re.compile(
    r"(?:[-—]\s*|(?<=\s))(\d{2,3})[.](\d{3})\b"   # с точкой: 136.000
    r"|(?:[-—]\s*)(\d{2,3})(\d{3})\b"              # без точки после дефиса: -143000
)


def _extract_mac_price(line: str) -> int | None:
    """Возвращает цену в рублях или None."""
    for m in _MAC_PRICE_RE.finditer(line):
        if m.group(1) is not None:
            return int(m.group(1)) * 1000 + int(m.group(2))
        if m.group(3) is not None:
            return int(m.group(3)) * 1000 + int(m.group(4))
    return None


def _mac_markup(price: int) -> int:
    if price >= 200000:
        return 7000
    if price >= 100000:
        return 4000
    return 2000


# Строка мака должна содержать артикул Apple (буквы+цифры) или явное название модели
_MAC_ARTICLE_RE = re.compile(
    r"MacBook\s+(Pro|Air)|(?<!\w)NEO\b|iMac\b|Mac\s+Mini\b|Mac\s+Studio\b"
    # Артикулы Apple: MW2V3, MGDN4, MDE04, MWUC3, MCX04, MX2F3, Z1AW0000S, MU9E3
    # Обязательно содержат и буквы и цифры (не просто слово типа Grey/Blue)
    r"|(?<![A-Za-z])\[?(?!iPad|iPhone|iPro)[A-Z]{1,2}\d[A-Z0-9]{2,5}\]?\b",
    re.IGNORECASE
)

# Слова которые говорят что строка НЕ цена на мак (гарантия, мышь, клавиатура и т.д.)
_MAC_NOISE_RE = re.compile(
    r"гарантия|гравировка|office|microsoft|magic\s+mouse|magic\s+track|magic\s+keyboard"
    r"|pencil|airtag|apple\s+tv|deppa|кабель|зарядка|magsafe\s+charger|power\s+adapter"
    r"|leather\s+sleeve|\+\d\s*месяц|custom\s+macbook|продолжение"
    r"|mac\s+studio|mac\s+pro\b",  # Mac Studio — не в каталоге
    re.IGNORECASE
)


def _is_mac_price_line(line: str) -> bool:
    if _MAC_EXCLUDE.search(line):
        return False
    if _MAC_NOISE_RE.search(line):
        return False
    price = _extract_mac_price(line)
    if price is None:
        return False
    # Цена должна быть реалистичной для мака (от 50000)
    if price < 50000:
        return False
    return bool(_MAC_ARTICLE_RE.search(line))


_MAC_SECTION_RE = {
    "macbook_pro": re.compile(r"MacBook\s+Pro", re.IGNORECASE),
    "macbook_air": re.compile(r"MacBook\s+Air", re.IGNORECASE),
    "imac": re.compile(r"\biMac\b", re.IGNORECASE),
    "mac_mini": re.compile(r"Mac\s+Mini", re.IGNORECASE),
}


def _classify_mac_line(line: str, section: str | None = None) -> str | None:
    if not _is_mac_price_line(line):
        return None
    # Pro раньше Air — "MacBook Pro" не должен попасть в Air
    if re.search(r"MacBook\s+Pro|\bPro\s+1[46]\b|\bPro\s+14\b", line, re.IGNORECASE):
        return "macbook_pro"
    if re.search(r"MacBook\s+Air|NEO\b", line, re.IGNORECASE):
        return "macbook_air"
    if re.search(r"iMac\b", line, re.IGNORECASE):
        return "imac"
    if re.search(r"Mac\s+Mini\b", line, re.IGNORECASE):
        return "mac_mini"
    # Строка только с артикулом — используем текущую секцию из заголовка
    return section


def _detect_section(line: str) -> str | None:
    """Определяет категорию по заголовку секции (строка без цены)."""
    if _extract_mac_price(line) is not None:
        return None
    for cat, pat in _MAC_SECTION_RE.items():
        if pat.search(line):
            return cat
    return None


def _detect_mac_categories(text: str) -> list[str]:
    found = set()
    section = None
    for line in text.split("\n"):
        s = _detect_section(line)
        if s:
            section = s
        cat = _classify_mac_line(line, section)
        if cat:
            found.add(cat)
    return list(found)


def _split_mac_by_category(text: str) -> dict[str, str]:
    lines_by_cat: dict[str, list] = {k: [] for k in MAC_CATEGORIES}
    section = None

    for line in text.split("\n"):
        s = _detect_section(line)
        if s:
            section = s
        cat = _classify_mac_line(line, section)
        if cat:
            lines_by_cat[cat].append(line)

    result = {}
    for cat, lines in lines_by_cat.items():
        if lines:
            result[cat] = "\n".join(lines).strip()
    return result


def _add_markup_to_mac_prices(text: str) -> str:
    def replace_price(m):
        if m.group(1) is not None:
            price = int(m.group(1)) * 1000 + int(m.group(2))
            new_price = price + _mac_markup(price)
            return m.group(0).replace(
                m.group(1) + "." + m.group(2),
                f"{new_price // 1000}.{new_price % 1000:03d}"
            )
        if m.group(3) is not None:
            price = int(m.group(3)) * 1000 + int(m.group(4))
            new_price = price + _mac_markup(price)
            return m.group(0).replace(
                m.group(3) + m.group(4),
                f"{new_price // 1000}{new_price % 1000:03d}"
            )
        return m.group(0)

    lines = text.split("\n")
    result = []
    for line in lines:
        if _is_mac_price_line(line):
            line = _MAC_PRICE_RE.sub(replace_price, line)
            line = line.replace("*", "").strip()
        result.append(line)
    return "\n".join(result)


def _load_mac_cache() -> dict:
    if os.path.exists(MAC_CACHE_FILE):
        with open(MAC_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_mac_cache(cache: dict):
    with open(MAC_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ─── Планшеты ────────────────────────────────────────────────────────────────

TABLETS_CACHE_FILE = os.path.join(os.path.dirname(__file__), "tablets_cache.json")

TABLET_CATEGORIES = {
    "ipad": "iPad",
    "ipad_pro": "iPad Pro",
    "ipad_air": "iPad Air",
    "ipad_mini": "iPad Mini",
}

_TABLET_EXCLUDE = re.compile(
    r"актив|предактив|распакован|ASIS|ACTIVE|уцен|замена|б/у|БУ\b|used|refurb|витрин"
    r"|вмятина|царап|скол|трещ",
    re.IGNORECASE
)

_TABLET_NOISE_RE = re.compile(
    r"гарантия|гравировка|pencil|magic\s+keyboard|magic\s+mouse|magic\s+track"
    r"|apple\s+tv|кабель|зарядка|airtag|deppa|продолжение|magsafe"
    r"|\+\d\s*месяц",
    re.IGNORECASE
)

_TABLET_PRICE_RE = re.compile(
    r"(?:[-—]\s*|(?<=\s))(\d{2,3})[.](\d{2,3})\b"
    r"|(?:[-—]\s*)(\d{2,3})(\d{3})\b"
)


def _extract_tablet_price(line: str) -> int | None:
    for m in _TABLET_PRICE_RE.finditer(line):
        if m.group(1) is not None:
            frac = m.group(2).ljust(3, "0")
            return int(m.group(1)) * 1000 + int(frac)
        if m.group(3) is not None:
            return int(m.group(3)) * 1000 + int(m.group(4))
    return None


def _tablet_markup(price: int) -> int:
    if price >= 200000:
        return 7000
    if price >= 100000:
        return 4000
    return 2000


_TABLET_SECTION_RE = re.compile(
    r"^\s*(?:iPad\s*Pro|iPro)\b|^\s*iPad\s*Air\b|^\s*iPad\s*[Mm]ini\b|^\s*iPad\b",
    re.IGNORECASE
)


def _detect_tablet_section(line: str) -> str | None:
    """Определяет секцию-заголовок (строка без цены, только название)."""
    if _extract_tablet_price(line) is not None:
        return None
    if re.search(r"iPad\s*Pro|iPro\b", line, re.IGNORECASE):
        return "ipad_pro"
    if re.search(r"iPad\s*Air", line, re.IGNORECASE):
        return "ipad_air"
    if re.search(r"iPad\s*[Mm]ini", line, re.IGNORECASE):
        return "ipad_mini"
    if re.search(r"\biPad\b", line, re.IGNORECASE):
        return "ipad"
    return None


def _is_tablet_price_line(line: str, section: str | None = None) -> bool:
    if _TABLET_EXCLUDE.search(line):
        return False
    if _TABLET_NOISE_RE.search(line):
        return False
    price = _extract_tablet_price(line)
    if price is None or price < 25000:
        return False
    # строка содержит iPad/iPro явно ИЛИ мы внутри секции
    return bool(re.search(r"iPad|iPro\b", line, re.IGNORECASE)) or section is not None


def _classify_tablet_line(line: str, section: str | None = None) -> str | None:
    if not _is_tablet_price_line(line, section):
        return None
    # Явное упоминание в строке имеет приоритет над секцией
    if re.search(r"iPad\s*Pro|iPro\b", line, re.IGNORECASE):
        return "ipad_pro"
    if re.search(r"iPad\s*Air", line, re.IGNORECASE):
        return "ipad_air"
    if re.search(r"iPad\s*[Mm]ini", line, re.IGNORECASE):
        return "ipad_mini"
    if re.search(r"\biPad\b", line, re.IGNORECASE):
        return "ipad"
    # Используем контекст секции
    return section


def _detect_tablet_categories(text: str) -> list[str]:
    found = set()
    section = None
    for line in text.split("\n"):
        s = _detect_tablet_section(line)
        if s:
            section = s
        cat = _classify_tablet_line(line, section)
        if cat:
            found.add(cat)
    return list(found)


def _split_tablet_by_category(text: str) -> dict[str, str]:
    lines_by_cat: dict[str, list] = {k: [] for k in TABLET_CATEGORIES}
    section = None
    for line in text.split("\n"):
        s = _detect_tablet_section(line)
        if s:
            section = s
        cat = _classify_tablet_line(line, section)
        if cat:
            lines_by_cat[cat].append(line)
    result = {}
    for cat, lines in lines_by_cat.items():
        if lines:
            result[cat] = "\n".join(lines).strip()
    return result


def _add_markup_to_tablet_prices(text: str) -> str:
    def replace_price(m):
        if m.group(1) is not None:
            frac = m.group(2).ljust(3, "0")
            price = int(m.group(1)) * 1000 + int(frac)
            new_price = price + _tablet_markup(price)
            return m.group(0).replace(
                m.group(1) + "." + m.group(2),
                f"{new_price // 1000}.{new_price % 1000:03d}"
            )
        if m.group(3) is not None:
            price = int(m.group(3)) * 1000 + int(m.group(4))
            new_price = price + _tablet_markup(price)
            return m.group(0).replace(
                m.group(3) + m.group(4),
                f"{new_price // 1000}{new_price % 1000:03d}"
            )
        return m.group(0)

    lines = text.split("\n")
    result = []
    section = None
    for line in lines:
        s = _detect_tablet_section(line)
        if s:
            section = s
        if _is_tablet_price_line(line, section):
            line = _TABLET_PRICE_RE.sub(replace_price, line)
            line = line.replace("*", "").strip()
        result.append(line)
    return "\n".join(result)


def _load_tablets_cache() -> dict:
    if os.path.exists(TABLETS_CACHE_FILE):
        with open(TABLETS_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_tablets_cache(cache: dict):
    with open(TABLETS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════
#  SAMSUNG
# ══════════════════════════════════════════════════════

SAMSUNG_CACHE_FILE = os.path.join(os.path.dirname(__file__), "samsung_cache.json")
SAMSUNG_MARKUP = 2000

SAMSUNG_CATEGORIES = {
    "galaxy_s": "Galaxy S",
    "galaxy_a": "Galaxy A",
    "galaxy_z": "Galaxy Z",
}

_SAMSUNG_EXCLUDE = re.compile(
    r"актив|предактив|распакован|распак|раскрыта\s*упаковка|ASIS|ACTIVE"
    r"|уцен|замена|АКБ|батаре|царап|скол|трещ|корпус|ремонт"
    r"|состояние|б/у|БУ\b|used|refurb|витрин",
    re.IGNORECASE
)

# Цены в посте бывают двух форматов:
#   с точкой:  72.000  /  48.000  /  69.900
#   без точки: 7000  /  34200  /  113500
_SAMSUNG_PRICE_DOT_RE = re.compile(r"(\d{1,3})[.](\d{3})")
_SAMSUNG_PRICE_PLAIN_RE = re.compile(r"\b(\d{4,6})\b")

# Строки с моделями Samsung — без слова "Galaxy", как в реальном прайсе:
#   A06, A56, S24, S25 FE, S25+, S25 Ultra, Z Fold 6, Z Fold7
_SAMSUNG_MODEL_RE = re.compile(
    r"(?<![A-Za-z\d])"          # не внутри слова
    r"(?:"
    r"[AaАа]\d{2}\b"            # A06, A17, A36, A56, A57 и т.д.
    r"|S\d{2,3}(?:\s*(?:FE|Plus|\+|Ultra))?\b"  # S24, S25, S25 FE, S25+, S25 Ultra, S26 и т.д.
    r"|Z\s+Fold\s*\d+"          # Z Fold 6, Z Fold7
    r")"
)

# Минимальная цена Samsung (убираем мусор типа "4/64" или "8/128")
_SAMSUNG_PRICE_MIN = 5000


def _extract_samsung_price(line: str) -> int | None:
    """Извлекает цену из строки Samsung. Возвращает None если цены нет."""
    # Сначала ищем формат с точкой (72.000)
    for m in _SAMSUNG_PRICE_DOT_RE.finditer(line):
        price = int(m.group(1)) * 1000 + int(m.group(2))
        if price >= _SAMSUNG_PRICE_MIN:
            return price
    # Потом без точки (7000, 34200)
    for m in _SAMSUNG_PRICE_PLAIN_RE.finditer(line):
        price = int(m.group(1))
        if price >= _SAMSUNG_PRICE_MIN:
            return price
    return None


def _is_samsung_price_line(line: str) -> bool:
    if _SAMSUNG_EXCLUDE.search(line):
        return False
    if not _SAMSUNG_MODEL_RE.search(line):
        return False
    return _extract_samsung_price(line) is not None


def _classify_samsung_line(line: str) -> str | None:
    if not _is_samsung_price_line(line):
        return None
    # Z Fold — проверяем первым (более специфично)
    if re.search(r"\bZ\s+Fold", line, re.IGNORECASE):
        return "galaxy_z"
    # S-серия: S24, S25, S25 FE, S25+, S25 Ultra, S26 и т.д.
    if re.search(r"\bS\d{2,3}\b", line):
        return "galaxy_s"
    # A-серия: A06, A17, A36 и т.д.
    if re.search(r"\b[AaАа]\d{2}\b", line):
        return "galaxy_a"
    return None


def _detect_samsung_categories(text: str) -> list[str]:
    found = set()
    for line in text.split("\n"):
        cat = _classify_samsung_line(line)
        if cat:
            found.add(cat)
    return list(found)


def _split_samsung_by_category(text: str) -> dict[str, str]:
    lines_by_cat: dict[str, list] = {k: [] for k in SAMSUNG_CATEGORIES}
    for line in text.split("\n"):
        cat = _classify_samsung_line(line)
        if cat:
            lines_by_cat[cat].append(line)
    result = {}
    for cat, lines in lines_by_cat.items():
        if lines:
            result[cat] = "\n".join(lines).strip()
    return result


def _add_markup_to_samsung_prices(text: str) -> str:
    lines = text.split("\n")
    result = []
    for line in lines:
        if not _is_samsung_price_line(line):
            result.append(line)
            continue
        # Заменяем цену с точкой (72.000 → 74.000)
        def replace_dot(m):
            price = int(m.group(1)) * 1000 + int(m.group(2))
            if price < _SAMSUNG_PRICE_MIN:
                return m.group(0)
            new_price = price + SAMSUNG_MARKUP
            return f"{new_price // 1000}.{new_price % 1000:03d}"
        new_line = _SAMSUNG_PRICE_DOT_RE.sub(replace_dot, line)
        # Если цена была без точки — ищем и заменяем
        if new_line == line:
            def replace_plain(m):
                price = int(m.group(1))
                if price < _SAMSUNG_PRICE_MIN:
                    return m.group(0)
                new_price = price + SAMSUNG_MARKUP
                return str(new_price)
            new_line = _SAMSUNG_PRICE_PLAIN_RE.sub(replace_plain, line)
        new_line = new_line.replace("*", "").strip()
        result.append(new_line)
    return "\n".join(result)


def _load_samsung_cache() -> dict:
    if os.path.exists(SAMSUNG_CACHE_FILE):
        with open(SAMSUNG_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_samsung_cache(cache: dict):
    with open(SAMSUNG_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════
#  PIXEL / ONEPLUS / NOTHING
# ══════════════════════════════════════════════════════

PIXEL_CACHE_FILE = os.path.join(os.path.dirname(__file__), "pixel_cache.json")
PIXEL_MARKUP = 2000

PIXEL_CATEGORIES = {
    "pixel": "Google Pixel",
    "oneplus": "OnePlus / Nothing",
}

_PIXEL_EXCLUDE = re.compile(
    r"актив|предактив|распакован|ASIS|ACTIVE|уцен|замена|б/у|БУ\b|used|refurb|витрин"
    r"|мятый\s*угол|мятая\s*коробка|царап|скол|трещ",
    re.IGNORECASE
)

# Цена: число после дефиса/тире (53000, 57 000) или с точкой (43.500), или в конце строки
_PIXEL_PRICE_RE = re.compile(
    r"[-—]\s*(\d{2,3})[.](\d{3})\b"   # -43.500
    r"|[-—]\s*(\d{4,6})\b"            # -53000
    r"|(?<!\d)(\d{2,3})[.](\d{3})\b"  # 43.500 без дефиса
    r"|(?<!\d)(\d{4,6})\b(?!\s*[Gg][Bb]|[Тт][Бб]|\s*[Гг][Бб]|\s*/)"  # 46500 без дефиса, не RAM/storage
)

_PIXEL_MODEL_RE = re.compile(
    r"\bPixel\s+\d|Google\s+Pixel|OnePlus\s+\d|OnePlus\s+Nord|Nothing\s+Phone",
    re.IGNORECASE
)

_PIXEL_PRICE_MIN = 20000


def _extract_pixel_price(line: str) -> int | None:
    for m in _PIXEL_PRICE_RE.finditer(line):
        if m.group(1) is not None:
            price = int(m.group(1)) * 1000 + int(m.group(2))
        elif m.group(3) is not None:
            price = int(m.group(3))
        elif m.group(4) is not None:
            price = int(m.group(4)) * 1000 + int(m.group(5))
        elif m.group(6) is not None:
            price = int(m.group(6))
        else:
            continue
        if price >= _PIXEL_PRICE_MIN:
            return price
    return None


def _is_pixel_price_line(line: str) -> bool:
    if _PIXEL_EXCLUDE.search(line):
        return False
    if not _PIXEL_MODEL_RE.search(line):
        return False
    return _extract_pixel_price(line) is not None


def _classify_pixel_line(line: str) -> str | None:
    if not _is_pixel_price_line(line):
        return None
    if re.search(r"\bPixel\b|Google\s+Pixel", line, re.IGNORECASE):
        return "pixel"
    return "oneplus"


def _detect_pixel_categories(text: str) -> list[str]:
    found = set()
    for line in text.split("\n"):
        cat = _classify_pixel_line(line)
        if cat:
            found.add(cat)
    return list(found)


def _split_pixel_by_category(text: str) -> dict[str, str]:
    lines_by_cat: dict[str, list] = {k: [] for k in PIXEL_CATEGORIES}
    for line in text.split("\n"):
        cat = _classify_pixel_line(line)
        if cat:
            lines_by_cat[cat].append(line)
    result = {}
    for cat, lines in lines_by_cat.items():
        if lines:
            result[cat] = "\n".join(lines).strip()
    return result


def _add_markup_to_pixel_prices(text: str) -> str:
    lines = text.split("\n")
    result = []
    for line in lines:
        if not _is_pixel_price_line(line):
            result.append(line)
            continue

        def _replace(m):
            if m.group(1) is not None:
                price = int(m.group(1)) * 1000 + int(m.group(2))
                new_price = price + PIXEL_MARKUP
                return m.group(0).replace(
                    m.group(1) + "." + m.group(2),
                    f"{new_price // 1000}.{new_price % 1000:03d}"
                )
            elif m.group(3) is not None:
                price = int(m.group(3))
                new_price = price + PIXEL_MARKUP
                return m.group(0).replace(m.group(3), str(new_price))
            elif m.group(4) is not None:
                price = int(m.group(4)) * 1000 + int(m.group(5))
                new_price = price + PIXEL_MARKUP
                return m.group(0).replace(
                    m.group(4) + "." + m.group(5),
                    f"{new_price // 1000}.{new_price % 1000:03d}"
                )
            elif m.group(6) is not None:
                price = int(m.group(6))
                new_price = price + PIXEL_MARKUP
                return m.group(0).replace(m.group(6), str(new_price))
            return m.group(0)

        line = _PIXEL_PRICE_RE.sub(_replace, line)
        line = line.replace("*", "").strip()
        result.append(line)
    return "\n".join(result)


def _load_pixel_cache() -> dict:
    if os.path.exists(PIXEL_CACHE_FILE):
        with open(PIXEL_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_pixel_cache(cache: dict):
    with open(PIXEL_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


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
    price_pattern = re.compile(r"(\d{2,3})[.](\d{3})")
    lines = text.split("\n")
    result = []
    for line in lines:
        if _is_iphone_price_line(line):
            def replace_price(m):
                price = int(m.group(1)) * 1000 + int(m.group(2))
                new_price = price + PRICE_MARKUP
                return f"{new_price // 1000}.{new_price % 1000:03d}"
            line = price_pattern.sub(replace_price, line)
            line = line.replace("*", "").strip()
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


class PriceNotesState(StatesGroup):
    waiting_notes = State()


class HpPriceNotesState(StatesGroup):
    waiting_notes = State()


class MacPriceNotesState(StatesGroup):
    waiting_notes = State()


class TabPriceNotesState(StatesGroup):
    waiting_notes = State()


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


@router.callback_query(F.data == "new_type:samsung")
async def cb_new_type_samsung(call: CallbackQuery):
    from bot import ADMIN_IDS
    cache = _load_samsung_cache()
    is_admin = call.from_user.id in ADMIN_IDS

    if not cache:
        if is_admin:
            await call.answer("⚠️ Цены Samsung не загружены. Перешлите сообщение из канала поставщика.", show_alert=True)
        else:
            await call.answer("Цены временно недоступны. Напишите администратору @idistoreman", show_alert=True)
        return

    lines = ["📱 <b>Samsung — актуальные цены</b>\n"]
    for cat_key, cat_name in SAMSUNG_CATEGORIES.items():
        entry = cache.get(cat_key)
        if not entry:
            continue
        updated = entry.get("updated_at", "—")
        msgs = entry.get("msgs") or ([entry["text"]] if entry.get("text") else [])
        if not msgs:
            continue
        lines.append(f"<b>{cat_name}</b>  🕐 {updated}\n{msgs[0]}\n")

    if len(lines) == 1:
        if is_admin:
            await call.answer("⚠️ Цены Samsung не загружены.", show_alert=True)
        else:
            await call.answer("Цены временно недоступны. Напишите администратору @idistoreman", show_alert=True)
        return

    disclaimer = "⚠️ Цены актуальны на момент последнего обновления. Для уточнения пишите @idistoreman\n\n"
    full_text = disclaimer + "\n".join(lines)

    # Разбиваем на части если текст слишком длинный
    if len(full_text) <= 4096:
        await call.message.edit_text(full_text, parse_mode="HTML", reply_markup=kb.samsung_back_kb())
    else:
        chunks = []
        current = disclaimer
        for chunk in lines[1:]:
            if len(current) + len(chunk) > 4000:
                chunks.append(current)
                current = chunk
            else:
                current += "\n" + chunk
        if current:
            chunks.append(current)
        await call.message.edit_text(chunks[0], parse_mode="HTML", reply_markup=kb.samsung_back_kb())
        for chunk in chunks[1:]:
            await call.message.answer(chunk, parse_mode="HTML", reply_markup=kb.samsung_back_kb())


@router.callback_query(F.data == "new_type:pixel")
async def cb_new_type_pixel(call: CallbackQuery):
    from bot import ADMIN_IDS
    cache = _load_pixel_cache()
    is_admin = call.from_user.id in ADMIN_IDS

    if not cache:
        if is_admin:
            await call.answer("⚠️ Цены Pixel/OnePlus не загружены. Перешлите сообщение из канала поставщика.", show_alert=True)
        else:
            await call.answer("Цены временно недоступны. Напишите администратору @idistoreman", show_alert=True)
        return

    lines = ["📱 <b>Pixel / OnePlus — актуальные цены</b>\n"]
    for cat_key, cat_name in PIXEL_CATEGORIES.items():
        entry = cache.get(cat_key)
        if not entry:
            continue
        updated = entry.get("updated_at", "—")
        msgs = entry.get("msgs") or ([entry["text"]] if entry.get("text") else [])
        if not msgs:
            continue
        lines.append(f"<b>{cat_name}</b>  🕐 {updated}\n{msgs[0]}\n")

    if len(lines) == 1:
        if is_admin:
            await call.answer("⚠️ Цены Pixel/OnePlus не загружены.", show_alert=True)
        else:
            await call.answer("Цены временно недоступны. Напишите администратору @idistoreman", show_alert=True)
        return

    disclaimer = "⚠️ Цены актуальны на момент последнего обновления. Для уточнения пишите @idistoreman\n\n"
    full_text = disclaimer + "\n".join(lines)

    if len(full_text) <= 4096:
        await call.message.edit_text(full_text, parse_mode="HTML", reply_markup=kb.pixel_back_kb())
    else:
        chunks = []
        current = disclaimer
        for chunk in lines[1:]:
            if len(current) + len(chunk) > 4000:
                chunks.append(current)
                current = chunk
            else:
                current += "\n" + chunk
        if current:
            chunks.append(current)
        await call.message.edit_text(chunks[0], parse_mode="HTML", reply_markup=kb.pixel_back_kb())
        for chunk in chunks[1:]:
            await call.message.answer(chunk, parse_mode="HTML", reply_markup=kb.pixel_back_kb())


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
    msgs = entry.get("msgs") or ([entry["text"]] if entry.get("text") else None)
    if not msgs:
        if is_admin:
            await call.answer(f"⚠️ Цены iPhone {series} не загружены. Перешлите сообщение из канала поставщика.", show_alert=True)
        else:
            await call.answer("Цены временно недоступны. Напишите администратору @idistoreman", show_alert=True)
        return
    # Собираем блоки цен и дедуплицируем строки пояснений
    price_blocks = []
    seen_footnote_lines = []
    for msg_text in msgs:
        price_text, footnote_text = _split_prices_and_footnotes(msg_text)
        if price_text.strip():
            price_blocks.append(price_text.strip())
        for line in footnote_text.split("\n"):
            if line not in seen_footnote_lines:
                seen_footnote_lines.append(line)

    footnote_combined = "\n".join(seen_footnote_lines).strip()

    # Отправляем все блоки цен подряд
    for i, block in enumerate(price_blocks):
        body = (disclaimer if i == 0 else "") + block
        is_last_block = (i == len(price_blocks) - 1)
        if i == 0 and is_last_block and not footnote_combined:
            await call.message.edit_text(body, parse_mode="HTML", reply_markup=kb.new_series_back_kb())
        elif i == 0:
            await call.message.edit_text(body, parse_mode="HTML")
        elif is_last_block and not footnote_combined:
            await call.message.answer(body, parse_mode="HTML", reply_markup=kb.new_series_back_kb())
        else:
            await call.message.answer(body, parse_mode="HTML")

    # Одно сообщение с пояснениями + кнопка назад
    if footnote_combined:
        await call.message.answer(footnote_combined, parse_mode="HTML", reply_markup=kb.new_series_back_kb())
    elif not price_blocks:
        await call.message.answer("◀️", reply_markup=kb.new_series_back_kb())



@router.message(F.text == "♻️ Смартфоны Б/У")
async def cmd_smartphones_bu(message: Message):
    uid = message.from_user.id
    user_filters.setdefault(uid, {})["phone_cond"] = "used"
    await message.answer("Б/У смартфоны — выберите раздел:", reply_markup=kb.smartphones_kb("used"))



@router.message(F.text == "🎧 Наушники")
async def cmd_headphones(message: Message):
    await message.answer(
        "🎧 Наушники Apple — выберите категорию:",
        reply_markup=kb.headphones_type_kb()
    )


@router.callback_query(F.data.startswith("hp_cat:"))
async def cb_hp_category(call: CallbackQuery):
    from bot import ADMIN_IDS
    cat_key = call.data.split(":")[1]
    if cat_key == "back":
        await call.message.edit_text(
            "🎧 Наушники Apple — выберите категорию:",
            reply_markup=kb.headphones_type_kb()
        )
        return

    cat_name = HP_CATEGORIES.get(cat_key, cat_key)
    cache = _load_hp_cache()
    entry = cache.get(cat_key)
    is_admin = call.from_user.id in ADMIN_IDS

    if not entry:
        if is_admin:
            await call.answer(
                f"⚠️ Цены {cat_name} не загружены. Перешлите сообщение из канала поставщика.",
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
        f"🎧 <b>{cat_name}</b>  🕐 {updated}\n\n"
    )
    msgs = entry.get("msgs") or ([entry["text"]] if entry.get("text") else None)
    if not msgs:
        if is_admin:
            await call.answer(f"⚠️ Цены {cat_name} не загружены.", show_alert=True)
        else:
            await call.answer("Цены временно недоступны. Напишите администратору @idistoreman", show_alert=True)
        return

    price_blocks = []
    seen_footnote_lines = []
    for msg_text in msgs:
        price_text, footnote_text = _split_prices_and_footnotes(msg_text)
        if price_text.strip():
            price_blocks.append(price_text.strip())
        for line in footnote_text.split("\n"):
            if line not in seen_footnote_lines:
                seen_footnote_lines.append(line)

    footnote_combined = "\n".join(seen_footnote_lines).strip()

    for i, block in enumerate(price_blocks):
        body = (disclaimer if i == 0 else "") + block
        is_last_block = (i == len(price_blocks) - 1)
        if i == 0 and is_last_block and not footnote_combined:
            await call.message.edit_text(body, parse_mode="HTML", reply_markup=kb.hp_back_kb())
        elif i == 0:
            await call.message.edit_text(body, parse_mode="HTML")
        elif is_last_block and not footnote_combined:
            await call.message.answer(body, parse_mode="HTML", reply_markup=kb.hp_back_kb())
        else:
            await call.message.answer(body, parse_mode="HTML")

    if footnote_combined:
        await call.message.answer(footnote_combined, parse_mode="HTML", reply_markup=kb.hp_back_kb())
    elif not price_blocks:
        await call.message.answer("◀️", reply_markup=kb.hp_back_kb())


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
        "📱 <b>Смартфоны New</b> — новые смартфоны\n"
        "🎧 <b>Наушники</b> — AirPods и другие\n"
        "🔌 <b>Аксессуары</b> — кабели, чехлы, зарядки\n"
        "📟 <b>Планшеты</b> — iPad (Air, Pro, Mini) и другие\n"
        "🖥 <b>Маки</b> — MacBook Air/Pro, iMac, Mac Mini\n"
        "🍎 <b>Рынок б/у iPhone</b> — все айфоны в наличии вперемешку\n"
        "💰 <b>Выкуп</b> — оценка и выкуп вашего устройства\n"
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


async def _send_hp_preview(message: Message):
    """Отправляет превью прайса наушников из _hp_preview_cache с кнопками."""
    await message.answer("👁 <b>Превью наушников — так увидит пользователь:</b>", parse_mode="HTML")
    for cat_key, entry in _hp_preview_cache.items():
        cat_name = HP_CATEGORIES[cat_key]
        msgs = entry["msgs"]
        updated = entry["updated_at"]
        disclaimer = (
            f"⚠️ Цены актуальны на момент последнего обновления. "
            f"Для уточнения пишите @idistoreman\n\n"
            f"🎧 <b>{cat_name}</b>  🕐 {updated}\n\n"
        )
        price_blocks = []
        seen_footnote_lines = []
        for msg_text in msgs:
            price_text, footnote_text = _split_prices_and_footnotes(msg_text)
            if price_text.strip():
                price_blocks.append(price_text.strip())
            for line in footnote_text.split("\n"):
                if line not in seen_footnote_lines:
                    seen_footnote_lines.append(line)

        if cat_key in _hp_custom_notes:
            footnote_combined = _hp_custom_notes[cat_key].strip()
        else:
            footnote_combined = "\n".join(seen_footnote_lines).strip()

        for i, block in enumerate(price_blocks):
            body = (disclaimer if i == 0 else "") + block
            await message.answer(body, parse_mode="HTML")

        if footnote_combined:
            await message.answer(footnote_combined, parse_mode="HTML")

    await message.answer(
        "Как выглядит? Сохранить или изменить пометки?",
        reply_markup=kb.hp_preview_kb()
    )


async def _send_preview(message: Message):
    """Отправляет превью прайса из _preview_cache с кнопками."""
    await message.answer("👁 <b>Превью — так увидит пользователь:</b>", parse_mode="HTML")
    for series, entry in _preview_cache.items():
        msgs = entry["msgs"]
        updated = entry["updated_at"]
        disclaimer = (
            f"⚠️ Цены актуальны на момент последнего обновления. "
            f"Для уточнения пишите @idistoreman\n\n"
            f"📱 <b>iPhone {series}</b>  🕐 {updated}\n\n"
        )
        price_blocks = []
        seen_footnote_lines = []
        for msg_text in msgs:
            price_text, footnote_text = _split_prices_and_footnotes(msg_text)
            if price_text.strip():
                price_blocks.append(price_text.strip())
            for line in footnote_text.split("\n"):
                if line not in seen_footnote_lines:
                    seen_footnote_lines.append(line)

        # Кастомные пометки заменяют стандартные если заданы
        if series in _custom_notes:
            footnote_combined = _custom_notes[series].strip()
        else:
            footnote_combined = "\n".join(seen_footnote_lines).strip()

        for i, block in enumerate(price_blocks):
            body = (disclaimer if i == 0 else "") + block
            await message.answer(body, parse_mode="HTML")

        if footnote_combined:
            await message.answer(footnote_combined, parse_mode="HTML")

    await message.answer(
        "Как выглядит? Сохранить или изменить пометки?",
        reply_markup=kb.preview_kb()
    )


@router.message(F.text.lower() == "готово наушники")
async def handle_hp_done(message: Message, state: FSMContext):
    from bot import ADMIN_IDS
    if message.from_user.id not in ADMIN_IDS:
        return
    if not _hp_draft:
        await message.answer("Нет накопленных сообщений для наушников.")
        return
    _hp_preview_cache.clear()
    _hp_custom_notes.clear()
    for cat_key, parts in _hp_draft.items():
        # Данные уже разбиты по категориям при пересылке — только добавляем наценку
        msgs = [_add_markup_to_hp_prices(p) for p in parts]
        msgs = [m for m in msgs if m.strip()]
        _hp_preview_cache[cat_key] = {"msgs": msgs, "updated_at": _now_msk()}
    _hp_draft.clear()
    await _send_hp_preview(message)


@router.callback_query(F.data == "hp_preview:save")
async def cb_hp_preview_save(call: CallbackQuery):
    from bot import ADMIN_IDS
    if call.from_user.id not in ADMIN_IDS:
        return
    if not _hp_preview_cache:
        await call.answer("Нет данных для сохранения.", show_alert=True)
        return
    cache = _load_hp_cache()
    saved = []
    for cat_key, entry in _hp_preview_cache.items():
        msgs = entry["msgs"]
        if cat_key in _hp_custom_notes:
            clean_msgs = []
            for m in msgs:
                price_text, _ = _split_prices_and_footnotes(m)
                clean_msgs.append(price_text.strip())
            if clean_msgs:
                clean_msgs[-1] = clean_msgs[-1] + "\n\n" + _hp_custom_notes[cat_key]
            msgs = clean_msgs
        cache[cat_key] = {"msgs": msgs, "updated_at": entry["updated_at"]}
        saved.append(HP_CATEGORIES[cat_key])
    _save_hp_cache(cache)
    _hp_preview_cache.clear()
    _hp_custom_notes.clear()
    await call.message.edit_reply_markup()
    await call.message.answer("✅ Сохранено: " + ", ".join(saved))


@router.callback_query(F.data == "hp_preview:edit_notes")
async def cb_hp_preview_edit_notes(call: CallbackQuery, state: FSMContext):
    from bot import ADMIN_IDS
    if call.from_user.id not in ADMIN_IDS:
        return
    cats_str = ", ".join(HP_CATEGORIES[c] for c in _hp_preview_cache)
    await call.message.edit_reply_markup()
    await call.message.answer(
        f"✏️ Пришли новый текст пометок для <b>{cats_str}</b>.\n"
        f"Он заменит стандартные пояснения во всех категориях превью.",
        parse_mode="HTML"
    )
    await state.set_state(HpPriceNotesState.waiting_notes)


@router.message(HpPriceNotesState.waiting_notes)
async def handle_hp_new_notes(message: Message, state: FSMContext):
    from bot import ADMIN_IDS
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    new_notes = message.text or ""
    for cat_key in _hp_preview_cache:
        _hp_custom_notes[cat_key] = new_notes
    await _send_hp_preview(message)


@router.message(F.text.lower() == "готово")
async def handle_done(message: Message, state: FSMContext):
    from bot import ADMIN_IDS
    if message.from_user.id not in ADMIN_IDS:
        return
    if not _draft:
        await message.answer("Нет накопленных сообщений.")
        return
    _preview_cache.clear()
    _custom_notes.clear()
    for series, parts in _draft.items():
        msgs = [_add_markup_to_prices(_filter_iphone_lines(p)) for p in parts]
        msgs = [m for m in msgs if m.strip()]
        _preview_cache[series] = {"msgs": msgs, "updated_at": _now_msk()}
    _draft.clear()
    await _send_preview(message)


@router.callback_query(F.data == "preview:save")
async def cb_preview_save(call: CallbackQuery):
    from bot import ADMIN_IDS
    if call.from_user.id not in ADMIN_IDS:
        return
    if not _preview_cache:
        await call.answer("Нет данных для сохранения.", show_alert=True)
        return
    cache = _load_cache()
    saved = []
    for series, entry in _preview_cache.items():
        msgs = entry["msgs"]
        # Применяем кастомные пометки если есть
        if series in _custom_notes:
            # Убираем старые пояснения из каждого msg и добавляем кастомные к последнему
            clean_msgs = []
            for m in msgs:
                price_text, _ = _split_prices_and_footnotes(m)
                clean_msgs.append(price_text.strip())
            if clean_msgs:
                clean_msgs[-1] = clean_msgs[-1] + "\n\n" + _custom_notes[series]
            msgs = clean_msgs
        cache[series] = {"msgs": msgs, "updated_at": entry["updated_at"]}
        saved.append(f"iPhone {series}")
    _save_cache(cache)
    _preview_cache.clear()
    _custom_notes.clear()
    await call.message.edit_reply_markup()
    await call.message.answer("✅ Сохранено: " + ", ".join(saved))


@router.callback_query(F.data == "preview:edit_notes")
async def cb_preview_edit_notes(call: CallbackQuery, state: FSMContext):
    from bot import ADMIN_IDS
    if call.from_user.id not in ADMIN_IDS:
        return
    series_str = ", ".join(f"iPhone {s}" for s in _preview_cache)
    await call.message.edit_reply_markup()
    await call.message.answer(
        f"✏️ Пришли новый текст пометок для <b>{series_str}</b>.\n"
        f"Он заменит стандартные пояснения во всех сериях превью.",
        parse_mode="HTML"
    )
    await state.set_state(PriceNotesState.waiting_notes)


@router.message(PriceNotesState.waiting_notes)
async def handle_new_notes(message: Message, state: FSMContext):
    from bot import ADMIN_IDS
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    new_notes = message.text or ""
    for series in _preview_cache:
        _custom_notes[series] = new_notes
    await _send_preview(message)


async def _send_mac_preview(message: Message):
    await message.answer("👁 <b>Превью маков — так увидит пользователь:</b>", parse_mode="HTML")
    for cat_key, entry in _mac_preview_cache.items():
        cat_name = MAC_CATEGORIES[cat_key]
        msgs = entry["msgs"]
        updated = entry["updated_at"]
        disclaimer = (
            f"⚠️ Цены актуальны на момент последнего обновления. "
            f"Для уточнения пишите @idistoreman\n\n"
            f"💻 <b>{cat_name}</b>  🕐 {updated}\n\n"
        )
        price_blocks = []
        seen_footnote_lines = []
        for msg_text in msgs:
            price_text, footnote_text = _split_prices_and_footnotes(msg_text)
            if price_text.strip():
                price_blocks.append(price_text.strip())
            for line in footnote_text.split("\n"):
                if line not in seen_footnote_lines:
                    seen_footnote_lines.append(line)

        if cat_key in _mac_custom_notes:
            footnote_combined = _mac_custom_notes[cat_key].strip()
        else:
            footnote_combined = "\n".join(seen_footnote_lines).strip()

        for i, block in enumerate(price_blocks):
            body = (disclaimer if i == 0 else "") + block
            await message.answer(body, parse_mode="HTML")
        if footnote_combined:
            await message.answer(footnote_combined, parse_mode="HTML")

    await message.answer(
        "Как выглядит? Сохранить или изменить пометки?",
        reply_markup=kb.mac_preview_kb()
    )


@router.message(F.text.lower() == "готово маки")
async def handle_mac_done(message: Message, state: FSMContext):
    from bot import ADMIN_IDS
    if message.from_user.id not in ADMIN_IDS:
        return
    if not _mac_draft:
        await message.answer("Нет накопленных сообщений для маков.")
        return
    _mac_preview_cache.clear()
    _mac_custom_notes.clear()
    for cat_key, parts in _mac_draft.items():
        msgs = [_add_markup_to_mac_prices(p) for p in parts]
        msgs = [m for m in msgs if m.strip()]
        _mac_preview_cache[cat_key] = {"msgs": msgs, "updated_at": _now_msk()}
    _mac_draft.clear()
    await _send_mac_preview(message)


@router.callback_query(F.data == "mac_preview:save")
async def cb_mac_preview_save(call: CallbackQuery):
    from bot import ADMIN_IDS
    if call.from_user.id not in ADMIN_IDS:
        return
    if not _mac_preview_cache:
        await call.answer("Нет данных для сохранения.", show_alert=True)
        return
    cache = _load_mac_cache()
    saved = []
    for cat_key, entry in _mac_preview_cache.items():
        msgs = entry["msgs"]
        if cat_key in _mac_custom_notes:
            clean_msgs = []
            for m in msgs:
                price_text, _ = _split_prices_and_footnotes(m)
                clean_msgs.append(price_text.strip())
            if clean_msgs:
                clean_msgs[-1] = clean_msgs[-1] + "\n\n" + _mac_custom_notes[cat_key]
            msgs = clean_msgs
        cache[cat_key] = {"msgs": msgs, "updated_at": entry["updated_at"]}
        saved.append(MAC_CATEGORIES[cat_key])
    _save_mac_cache(cache)
    _mac_preview_cache.clear()
    _mac_custom_notes.clear()
    await call.message.edit_reply_markup()
    await call.message.answer("✅ Сохранено: " + ", ".join(saved))


@router.callback_query(F.data == "mac_preview:edit_notes")
async def cb_mac_preview_edit_notes(call: CallbackQuery, state: FSMContext):
    from bot import ADMIN_IDS
    if call.from_user.id not in ADMIN_IDS:
        return
    cats_str = ", ".join(MAC_CATEGORIES[c] for c in _mac_preview_cache)
    await call.message.edit_reply_markup()
    await call.message.answer(
        f"✏️ Пришли новый текст пометок для <b>{cats_str}</b>.\n"
        f"Он заменит стандартные пояснения во всех категориях превью.",
        parse_mode="HTML"
    )
    await state.set_state(MacPriceNotesState.waiting_notes)


@router.message(MacPriceNotesState.waiting_notes)
async def handle_mac_new_notes(message: Message, state: FSMContext):
    from bot import ADMIN_IDS
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    for cat_key in _mac_preview_cache:
        _mac_custom_notes[cat_key] = message.text or ""
    await _send_mac_preview(message)


@router.message(F.text == "📟 Планшеты")
async def cmd_tablets_menu(message: Message):
    await message.answer("📟 Планшеты — выберите бренд:", reply_markup=kb.tablets_brand_kb())


@router.callback_query(F.data.startswith("tab_brand:"))
async def cb_tablet_brand(call: CallbackQuery):
    brand = call.data.split(":")[1]
    if brand == "back":
        await call.message.edit_text("📟 Планшеты — выберите бренд:", reply_markup=kb.tablets_brand_kb())
        return
    if brand == "apple":
        await call.message.edit_text("🍎 Apple планшеты — выберите категорию:", reply_markup=kb.tablets_apple_kb())
    else:
        await call.answer("Раздел в разработке", show_alert=True)


@router.callback_query(F.data.startswith("tab_cat:"))
async def cb_tablet_category(call: CallbackQuery):
    from bot import ADMIN_IDS
    cat_key = call.data.split(":")[1]
    if cat_key == "back":
        await call.message.edit_text("🍎 Apple планшеты — выберите категорию:", reply_markup=kb.tablets_apple_kb())
        return

    cat_name = TABLET_CATEGORIES.get(cat_key, cat_key)
    cache = _load_tablets_cache()
    entry = cache.get(cat_key)
    is_admin = call.from_user.id in ADMIN_IDS

    if not entry:
        if is_admin:
            await call.answer(
                f"⚠️ Цены {cat_name} не загружены. Перешлите сообщение из канала поставщика.",
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
        f"📟 <b>{cat_name}</b>  🕐 {updated}\n\n"
    )
    msgs = entry.get("msgs") or ([entry["text"]] if entry.get("text") else None)
    if not msgs:
        await call.answer("Цены временно недоступны. Напишите администратору @idistoreman", show_alert=True)
        return

    price_blocks = []
    seen_footnote_lines = []
    for msg_text in msgs:
        price_text, footnote_text = _split_prices_and_footnotes(msg_text)
        if price_text.strip():
            price_blocks.append(price_text.strip())
        for line in footnote_text.split("\n"):
            if line not in seen_footnote_lines:
                seen_footnote_lines.append(line)

    footnote_combined = "\n".join(seen_footnote_lines).strip()

    for i, block in enumerate(price_blocks):
        body = (disclaimer if i == 0 else "") + block
        is_last_block = (i == len(price_blocks) - 1)
        if i == 0 and is_last_block and not footnote_combined:
            await call.message.edit_text(body, parse_mode="HTML", reply_markup=kb.tablet_back_kb())
        elif i == 0:
            await call.message.edit_text(body, parse_mode="HTML")
        elif is_last_block and not footnote_combined:
            await call.message.answer(body, parse_mode="HTML", reply_markup=kb.tablet_back_kb())
        else:
            await call.message.answer(body, parse_mode="HTML")

    if footnote_combined:
        await call.message.answer(footnote_combined, parse_mode="HTML", reply_markup=kb.tablet_back_kb())
    elif not price_blocks:
        await call.message.answer("◀️", reply_markup=kb.tablet_back_kb())


async def _send_tab_preview(message: Message):
    await message.answer("👁 <b>Превью планшетов — так увидит пользователь:</b>", parse_mode="HTML")
    for cat_key, entry in _tab_preview_cache.items():
        cat_name = TABLET_CATEGORIES[cat_key]
        msgs = entry["msgs"]
        updated = entry["updated_at"]
        disclaimer = (
            f"⚠️ Цены актуальны на момент последнего обновления. "
            f"Для уточнения пишите @idistoreman\n\n"
            f"📟 <b>{cat_name}</b>  🕐 {updated}\n\n"
        )
        price_blocks = []
        seen_footnote_lines = []
        for msg_text in msgs:
            price_text, footnote_text = _split_prices_and_footnotes(msg_text)
            if price_text.strip():
                price_blocks.append(price_text.strip())
            for line in footnote_text.split("\n"):
                if line not in seen_footnote_lines:
                    seen_footnote_lines.append(line)

        if cat_key in _tab_custom_notes:
            footnote_combined = _tab_custom_notes[cat_key].strip()
        else:
            footnote_combined = "\n".join(seen_footnote_lines).strip()

        for i, block in enumerate(price_blocks):
            body = (disclaimer if i == 0 else "") + block
            await message.answer(body, parse_mode="HTML")
        if footnote_combined:
            await message.answer(footnote_combined, parse_mode="HTML")

    await message.answer(
        "Как выглядит? Сохранить или изменить пометки?",
        reply_markup=kb.tablet_preview_kb()
    )


@router.message(F.text.lower() == "готово планшеты")
async def handle_tab_done(message: Message, state: FSMContext):
    from bot import ADMIN_IDS
    if message.from_user.id not in ADMIN_IDS:
        return
    if not _tab_draft:
        await message.answer("Нет накопленных сообщений для планшетов.")
        return
    _tab_preview_cache.clear()
    _tab_custom_notes.clear()
    for cat_key, parts in _tab_draft.items():
        msgs = [_add_markup_to_tablet_prices(p) for p in parts]
        msgs = [m for m in msgs if m.strip()]
        _tab_preview_cache[cat_key] = {"msgs": msgs, "updated_at": _now_msk()}
    _tab_draft.clear()
    await _send_tab_preview(message)


@router.callback_query(F.data.startswith("tab_preview:"))
async def cb_tab_preview(call: CallbackQuery, state: FSMContext):
    from bot import ADMIN_IDS
    if call.from_user.id not in ADMIN_IDS:
        return
    action = call.data.split(":")[1]
    if action == "save":
        cache = _load_tablets_cache()
        for cat_key, entry in _tab_preview_cache.items():
            if cat_key in _tab_custom_notes:
                for i, msg_text in enumerate(entry["msgs"]):
                    entry["msgs"][i] = msg_text + "\n\n" + _tab_custom_notes[cat_key]
            cache[cat_key] = entry
        _save_tablets_cache(cache)
        _tab_preview_cache.clear()
        _tab_custom_notes.clear()
        await call.message.answer("✅ Цены на планшеты сохранены!")
    elif action == "edit_notes":
        cats_str = ", ".join(TABLET_CATEGORIES[c] for c in _tab_preview_cache)
        await call.message.answer(
            f"✏️ Пришли новый текст пометок для <b>{cats_str}</b>.\n"
            f"Он заменит стандартные пояснения во всех категориях превью.",
            parse_mode="HTML"
        )
        await state.set_state(TabPriceNotesState.waiting_notes)


@router.message(TabPriceNotesState.waiting_notes)
async def handle_tab_new_notes(message: Message, state: FSMContext):
    from bot import ADMIN_IDS
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    for cat_key in _tab_preview_cache:
        _tab_custom_notes[cat_key] = message.text or ""
    await _send_tab_preview(message)


@router.message(F.text == "🖥 Маки")
async def cmd_macs_menu(message: Message):
    await message.answer("🖥 Маки — выберите категорию:", reply_markup=kb.macs_type_kb())


@router.callback_query(F.data.startswith("mac_cat:"))
async def cb_mac_category(call: CallbackQuery):
    from bot import ADMIN_IDS
    cat_key = call.data.split(":")[1]
    if cat_key == "back":
        await call.message.edit_text("🖥 Маки — выберите категорию:", reply_markup=kb.macs_type_kb())
        return

    cat_name = MAC_CATEGORIES.get(cat_key, cat_key)
    cache = _load_mac_cache()
    entry = cache.get(cat_key)
    is_admin = call.from_user.id in ADMIN_IDS

    if not entry:
        if is_admin:
            await call.answer(
                f"⚠️ Цены {cat_name} не загружены. Перешлите сообщение из канала поставщика.",
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
        f"💻 <b>{cat_name}</b>  🕐 {updated}\n\n"
    )
    msgs = entry.get("msgs") or ([entry["text"]] if entry.get("text") else None)
    if not msgs:
        await call.answer("Цены временно недоступны. Напишите администратору @idistoreman", show_alert=True)
        return

    price_blocks = []
    seen_footnote_lines = []
    for msg_text in msgs:
        price_text, footnote_text = _split_prices_and_footnotes(msg_text)
        if price_text.strip():
            price_blocks.append(price_text.strip())
        for line in footnote_text.split("\n"):
            if line not in seen_footnote_lines:
                seen_footnote_lines.append(line)

    footnote_combined = "\n".join(seen_footnote_lines).strip()

    for i, block in enumerate(price_blocks):
        body = (disclaimer if i == 0 else "") + block
        is_last_block = (i == len(price_blocks) - 1)
        if i == 0 and is_last_block and not footnote_combined:
            await call.message.edit_text(body, parse_mode="HTML", reply_markup=kb.mac_back_kb())
        elif i == 0:
            await call.message.edit_text(body, parse_mode="HTML")
        elif is_last_block and not footnote_combined:
            await call.message.answer(body, parse_mode="HTML", reply_markup=kb.mac_back_kb())
        else:
            await call.message.answer(body, parse_mode="HTML")

    if footnote_combined:
        await call.message.answer(footnote_combined, parse_mode="HTML", reply_markup=kb.mac_back_kb())
    elif not price_blocks:
        await call.message.answer("◀️", reply_markup=kb.mac_back_kb())


async def process_price_text(bot, admin_ids: set, text: str, silent: bool = False):
    """Парсит текст поста и автоматически сохраняет цены в кэш."""
    series_list = _detect_series(text)
    mac_cats = [] if series_list else _detect_mac_categories(text)
    hp_cats = [] if (series_list or mac_cats) else _detect_hp_categories(text)
    tab_cats = [] if (series_list or mac_cats or hp_cats) else _detect_tablet_categories(text)

    if series_list:
        split = _split_by_series(text, series_list) if len(series_list) > 1 else {series_list[0]: text}
        cache = _load_cache()
        for s, s_text in split.items():
            new_msgs = [_add_markup_to_prices(_filter_iphone_lines(s_text))]
            new_msgs = [m for m in new_msgs if m.strip()]
            if not new_msgs:
                continue
            existing = cache.get(s, {})
            old_msgs = existing.get("msgs", [])
            cache[s] = {"msgs": old_msgs + new_msgs, "updated_at": _now_msk()}
        _save_cache(cache)
        if not silent:
            names = ", ".join(f"iPhone {s}" for s in split)
            for aid in admin_ids:
                await bot.send_message(aid, f"✅ Авто: обновлены цены — {names}")

    elif mac_cats:
        split = _split_mac_by_category(text)
        cache = _load_mac_cache()
        for cat, cat_text in split.items():
            if cat_text.strip():
                msgs = [_add_markup_to_mac_prices(cat_text)]
                msgs = [m for m in msgs if m.strip()]
                cache[cat] = {"msgs": msgs, "updated_at": _now_msk()}
        _save_mac_cache(cache)
        if not silent:
            names = ", ".join(MAC_CATEGORIES[c] for c in split if split[c].strip())
            for aid in admin_ids:
                await bot.send_message(aid, f"✅ Авто: обновлены цены — {names}")

    elif hp_cats:
        split = _split_hp_by_category(text)
        cache = _load_hp_cache()
        for cat, cat_text in split.items():
            if cat_text.strip():
                msgs = [_add_markup_to_hp_prices(cat_text)]
                msgs = [m for m in msgs if m.strip()]
                cache[cat] = {"msgs": msgs, "updated_at": _now_msk()}
        _save_hp_cache(cache)
        if not silent:
            names = ", ".join(HP_CATEGORIES[c] for c in split if split[c].strip())
            for aid in admin_ids:
                await bot.send_message(aid, f"✅ Авто: обновлены цены — {names}")

    elif tab_cats:
        split = _split_tablet_by_category(text)
        cache = _load_tablets_cache()
        for cat, cat_text in split.items():
            if cat_text.strip():
                msgs = [_add_markup_to_tablet_prices(cat_text)]
                msgs = [m for m in msgs if m.strip()]
                cache[cat] = {"msgs": msgs, "updated_at": _now_msk()}
        _save_tablets_cache(cache)
        if not silent:
            names = ", ".join(TABLET_CATEGORIES[c] for c in split if split[c].strip())
            for aid in admin_ids:
                await bot.send_message(aid, f"✅ Авто: обновлены цены — {names}")

    elif _detect_samsung_categories(text):
        split = _split_samsung_by_category(text)
        cache = _load_samsung_cache()
        for cat, cat_text in split.items():
            if cat_text.strip():
                msgs = [_add_markup_to_samsung_prices(cat_text)]
                msgs = [m for m in msgs if m.strip()]
                cache[cat] = {"msgs": msgs, "updated_at": _now_msk()}
        _save_samsung_cache(cache)
        if not silent:
            names = ", ".join(SAMSUNG_CATEGORIES[c] for c in split if split[c].strip())
            for aid in admin_ids:
                await bot.send_message(aid, f"✅ Авто: обновлены цены — {names}")

    elif _detect_pixel_categories(text):
        split = _split_pixel_by_category(text)
        cache = _load_pixel_cache()
        for cat, cat_text in split.items():
            if cat_text.strip():
                msgs = [_add_markup_to_pixel_prices(cat_text)]
                msgs = [m for m in msgs if m.strip()]
                cache[cat] = {"msgs": msgs, "updated_at": _now_msk()}
        _save_pixel_cache(cache)
        if not silent:
            names = ", ".join(PIXEL_CATEGORIES[c] for c in split if split[c].strip())
            for aid in admin_ids:
                await bot.send_message(aid, f"✅ Авто: обновлены цены — {names}")

    else:
        if not silent:
            preview = text[:200].replace("<", "&lt;")
            for aid in admin_ids:
                await bot.send_message(
                    aid,
                    f"⚠️ Авто: тип товара не определён.\n<code>{preview}</code>",
                    parse_mode="HTML"
                )


# ══════════════════════════════════════════════════════
#  ВЫКУП УСТРОЙСТВ
# ══════════════════════════════════════════════════════

BUYOUT_KIT_OPTIONS = ["Коробка", "Шнур", "Блок", "Чехол"]


class BuyoutState(StatesGroup):
    model = State()
    color = State()
    storage = State()
    photos = State()
    condition = State()
    screen = State()
    body = State()
    battery = State()
    kit = State()
    price = State()
    comment = State()


@router.message(F.text == "💰 Выкуп")
async def cmd_buyout(message: Message, state: FSMContext):
    await message.answer(
        "📋 <b>Выкуп устройств</b>\n\n"
        "Для оценки вашего iPhone нам понадобится:\n\n"
        "1️⃣ Модель\n"
        "2️⃣ Цвет\n"
        "3️⃣ Объём памяти\n"
        "4️⃣ Фото телефона (минимум 3)\n"
        "5️⃣ Общее состояние — по шкале от 1 до 10\n"
        "6️⃣ Состояние экрана — по шкале от 1 до 10\n"
        "7️⃣ Состояние корпуса — по шкале от 1 до 10\n"
        "8️⃣ Состояние АКБ — в процентах\n"
        "9️⃣ Комплектация\n"
        "🔟 Желаемая цена\n"
        "💬 Комментарий (необязательно)\n\n"
        "Если есть вопросы — пишите @idistoreman",
        parse_mode="HTML",
        reply_markup=kb.buyout_start_kb()
    )


@router.callback_query(F.data == "buyout:start")
async def buyout_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(BuyoutState.model)
    await call.message.edit_text(
        "📱 <b>Шаг 1 из 11 — Модель</b>\n\n"
        "Напишите модель вашего iPhone (например: <code>iPhone 13 Pro</code>):",
        parse_mode="HTML"
    )


@router.message(BuyoutState.model)
async def buyout_model(message: Message, state: FSMContext):
    await state.update_data(model=(message.text or "").strip())
    await state.set_state(BuyoutState.color)
    await message.answer(
        "🎨 <b>Шаг 2 из 11 — Цвет</b>\n\n"
        "Выберите цвет вашего устройства:",
        parse_mode="HTML",
        reply_markup=kb.buyout_color_kb()
    )


@router.callback_query(F.data.startswith("buyout_color:"), BuyoutState.color)
async def buyout_color(call: CallbackQuery, state: FSMContext):
    color = call.data.split(":", 1)[1]
    await state.update_data(color=color)
    await state.set_state(BuyoutState.storage)
    await call.message.edit_text(
        "💾 <b>Шаг 3 из 11 — Объём памяти</b>\n\n"
        "Выберите объём памяти устройства:",
        parse_mode="HTML",
        reply_markup=kb.buyout_storage_kb()
    )


@router.callback_query(F.data.startswith("buyout_storage:"), BuyoutState.storage)
async def buyout_storage(call: CallbackQuery, state: FSMContext):
    storage = call.data.split(":", 1)[1]
    await state.update_data(storage=storage)
    await state.set_state(BuyoutState.photos)
    await state.update_data(photos=[])
    await call.message.edit_text(
        "📸 <b>Шаг 4 из 11 — Фото</b>\n\n"
        "⚠️ <b>Важно:</b> отправьте минимум <b>3 фотографии</b> с разных ракурсов.\n\n"
        "Обязательно сфотографируйте:\n"
        "• Экран (включённый)\n"
        "• Заднюю крышку\n"
        "• Боковые грани\n\n"
        "Максимум 10 фото. Когда закончите — нажмите <b>Готово</b>.",
        parse_mode="HTML",
        reply_markup=kb.buyout_photos_done_kb()
    )


@router.message(BuyoutState.photos, F.photo)
async def buyout_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    if len(photos) >= 10:
        await message.answer("Максимум 10 фото. Нажмите <b>Готово</b>.", parse_mode="HTML")
        return
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    count = len(photos)
    note = " (минимум 3)" if count < 3 else ""
    await message.answer(
        f"✅ Фото {count} добавлено{note}. Отправьте ещё или нажмите <b>Готово</b>.",
        parse_mode="HTML",
        reply_markup=kb.buyout_photos_done_kb()
    )


@router.callback_query(F.data == "buyout:photos_done", BuyoutState.photos)
async def buyout_photos_done(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    if len(photos) < 3:
        await call.answer(f"Добавьте минимум 3 фото! Сейчас: {len(photos)}", show_alert=True)
        return
    await state.set_state(BuyoutState.condition)
    await call.message.edit_text(
        "📊 <b>Шаг 5 из 11 — Общее состояние</b>\n\n"
        "Оцените общее состояние телефона по шкале от <b>1</b> до <b>10</b>:",
        parse_mode="HTML",
        reply_markup=kb.buyout_score_kb("buyout_cond")
    )


@router.callback_query(F.data.startswith("buyout_cond:"), BuyoutState.condition)
async def buyout_condition(call: CallbackQuery, state: FSMContext):
    score = call.data.split(":")[1]
    await state.update_data(condition=score)
    await state.set_state(BuyoutState.screen)
    await call.message.edit_text(
        "🖥 <b>Шаг 6 из 11 — Состояние экрана</b>\n\n"
        "Оцените состояние экрана по шкале от <b>1</b> до <b>10</b>:",
        parse_mode="HTML",
        reply_markup=kb.buyout_score_kb("buyout_screen")
    )


@router.callback_query(F.data.startswith("buyout_screen:"), BuyoutState.screen)
async def buyout_screen(call: CallbackQuery, state: FSMContext):
    score = call.data.split(":")[1]
    await state.update_data(screen=score)
    await state.set_state(BuyoutState.body)
    await call.message.edit_text(
        "📦 <b>Шаг 7 из 11 — Состояние корпуса</b>\n\n"
        "Оцените состояние корпуса по шкале от <b>1</b> до <b>10</b>:",
        parse_mode="HTML",
        reply_markup=kb.buyout_score_kb("buyout_body")
    )


@router.callback_query(F.data.startswith("buyout_body:"), BuyoutState.body)
async def buyout_body(call: CallbackQuery, state: FSMContext):
    score = call.data.split(":")[1]
    await state.update_data(body=score)
    await state.set_state(BuyoutState.battery)
    await call.message.edit_text(
        "🔋 <b>Шаг 8 из 11 — Состояние АКБ</b>\n\n"
        "Введите уровень заряда аккумулятора в <b>процентах</b> (например: <code>87</code>):",
        parse_mode="HTML"
    )


@router.message(BuyoutState.battery)
async def buyout_battery(message: Message, state: FSMContext):
    txt = (message.text or "").strip().replace("%", "")
    if not txt.isdigit() or not (1 <= int(txt) <= 100):
        await message.answer("Введите число от 1 до 100 (например: 87).")
        return
    await state.update_data(battery=txt)
    await state.set_state(BuyoutState.kit)
    await state.update_data(kit=[])
    await message.answer(
        "🎁 <b>Шаг 9 из 11 — Комплектация</b>\n\n"
        "Отметьте что есть в комплекте, затем нажмите <b>Готово</b>:",
        parse_mode="HTML",
        reply_markup=kb.buyout_kit_kb([])
    )


@router.callback_query(F.data.startswith("buyout_kit:"), BuyoutState.kit)
async def buyout_kit_toggle(call: CallbackQuery, state: FSMContext):
    item = call.data.split(":", 1)[1]
    if item == "done":
        data = await state.get_data()
        await state.set_state(BuyoutState.price)
        kit_text = ", ".join(data.get("kit", [])) or "Только телефон"
        await call.message.edit_text(
            f"✅ Комплект: {kit_text}\n\n"
            "💰 <b>Шаг 10 из 11 — Желаемая цена</b>\n\n"
            "Введите желаемую цену в рублях (только цифры):",
            parse_mode="HTML"
        )
        return
    data = await state.get_data()
    kit = data.get("kit", [])
    if item in kit:
        kit.remove(item)
    else:
        kit.append(item)
    await state.update_data(kit=kit)
    await call.message.edit_reply_markup(reply_markup=kb.buyout_kit_kb(kit))


@router.message(BuyoutState.price)
async def buyout_price(message: Message, state: FSMContext):
    txt = (message.text or "").strip().replace(" ", "").replace(",", "")
    if not txt.isdigit():
        await message.answer("Введите только цифры (например: 35000).")
        return
    await state.update_data(price=txt)
    await state.set_state(BuyoutState.comment)
    await message.answer(
        "💬 <b>Шаг 11 из 11 — Комментарий</b>\n\n"
        "Напишите любые дополнительные детали о состоянии устройства,\n"
        "или нажмите <b>Пропустить</b>:",
        parse_mode="HTML",
        reply_markup=kb.buyout_skip_comment_kb()
    )


async def _send_buyout(bot, admin_ids: set, from_user, chat_id: int, data: dict, comment: str | None):
    kit_text = ", ".join(data.get("kit", [])) or "Только телефон"
    admin_text = (
        f"📥 <b>Новая заявка на выкуп!</b>\n\n"
        f"👤 Пользователь: {from_user.full_name}"
        + (f" (@{from_user.username})" if from_user.username else f" (id: {from_user.id})")
        + f"\n\n"
        f"📱 Модель: <b>{data.get('model')}</b>\n"
        f"🎨 Цвет: <b>{data.get('color')}</b>\n"
        f"💾 Память: <b>{data.get('storage')}</b>\n\n"
        f"📊 Общее состояние: <b>{data.get('condition')}/10</b>\n"
        f"🖥 Экран: <b>{data.get('screen')}/10</b>\n"
        f"📦 Корпус: <b>{data.get('body')}/10</b>\n"
        f"🔋 АКБ: <b>{data.get('battery')}%</b>\n"
        f"🎁 Комплект: <b>{kit_text}</b>\n"
        f"💰 Желаемая цена: <b>{int(data.get('price', 0)):,} ₽</b>"
        + (f"\n💬 Комментарий: {comment}" if comment else "")
    )
    photos = data.get("photos", [])
    for aid in admin_ids:
        try:
            if len(photos) == 1:
                await bot.send_photo(aid, photos[0], caption=admin_text, parse_mode="HTML")
            elif len(photos) > 1:
                from aiogram.types import InputMediaPhoto
                media = [InputMediaPhoto(media=photos[0], caption=admin_text, parse_mode="HTML")]
                media += [InputMediaPhoto(media=p) for p in photos[1:]]
                await bot.send_media_group(aid, media)
            else:
                await bot.send_message(aid, admin_text, parse_mode="HTML")
        except Exception:
            pass


@router.message(BuyoutState.comment)
async def buyout_comment(message: Message, state: FSMContext):
    comment = (message.text or "").strip()
    data = await state.get_data()
    await state.clear()
    await message.answer(
        "✅ <b>Заявка на выкуп отправлена!</b>\n\n"
        "Мы рассмотрим вашу заявку и свяжемся с вами в ближайшее время.\n\n"
        "Если есть вопросы — пишите @idistoreman",
        parse_mode="HTML",
        reply_markup=kb.main_menu()
    )
    from bot import ADMIN_IDS
    await _send_buyout(message.bot, ADMIN_IDS, message.from_user, message.chat.id, data, comment or None)


@router.callback_query(F.data == "buyout:skip_comment", BuyoutState.comment)
async def buyout_skip_comment(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        "✅ <b>Заявка на выкуп отправлена!</b>\n\n"
        "Мы рассмотрим вашу заявку и свяжемся с вами в ближайшее время.\n\n"
        "Если есть вопросы — пишите @idistoreman",
        parse_mode="HTML",
        reply_markup=kb.main_menu()
    )
    from bot import ADMIN_IDS
    await _send_buyout(call.bot, ADMIN_IDS, call.from_user, call.message.chat.id, data, None)


@router.message()
async def handle_forwarded(message: Message):
    from bot import ADMIN_IDS, SOURCE_BOT_ID
    uid = message.from_user.id if message.from_user else None

    is_from_source_bot = SOURCE_BOT_ID and uid == SOURCE_BOT_ID
    is_admin_forward = (
        uid in ADMIN_IDS
        and (message.forward_origin or message.forward_from_chat or message.forward_from)
    )
    if not (is_admin_forward or is_from_source_bot):
        return

    text = message.text or message.caption or ""
    if not text:
        await message.answer("❌ Сообщение не содержит текста.")
        return

    # Приоритет: iPhone > Mac > AirPods > Планшеты
    series_list = _detect_series(text)
    mac_cats = [] if series_list else _detect_mac_categories(text)
    hp_cats = [] if (series_list or mac_cats) else _detect_hp_categories(text)
    tab_cats = [] if (series_list or mac_cats or hp_cats) else _detect_tablet_categories(text)

    if series_list:
        # iPhone
        if len(series_list) == 1:
            s = series_list[0]
            if s not in _draft:
                _draft[s] = []
            _draft[s].append(text)
            part_num = len(_draft[s])
            await message.answer(
                f"✅ Часть {part_num} принята — iPhone {s}\n"
                f"Пересылай ещё или напиши <b>готово</b> чтобы сохранить.",
                parse_mode="HTML"
            )
        else:
            split = _split_by_series(text, series_list)
            for s, s_text in split.items():
                if s not in _draft:
                    _draft[s] = []
                _draft[s].append(s_text)
            names = ", ".join(f"iPhone {s}" for s in split)
            await message.answer(
                f"✅ Разбито на серии: {names}\n"
                f"Пересылай ещё или напиши <b>готово</b> чтобы сохранить.",
                parse_mode="HTML"
            )
    elif mac_cats:
        # Маки — разбиваем по категориям сразу
        split = _split_mac_by_category(text)
        for cat, cat_text in split.items():
            if cat_text.strip():
                if cat not in _mac_draft:
                    _mac_draft[cat] = []
                _mac_draft[cat].append(cat_text)
        names = ", ".join(MAC_CATEGORIES[c] for c in split if split[c].strip())
        total_parts = max((len(_mac_draft[c]) for c in split if _mac_draft.get(c)), default=1)
        await message.answer(
            f"✅ Часть {total_parts} принята — {names}\n"
            f"Пересылай ещё или напиши <b>готово маки</b> чтобы сохранить.",
            parse_mode="HTML"
        )
    elif hp_cats:
        # Наушники — разбиваем по категориям сразу
        split = _split_hp_by_category(text)
        for cat, cat_text in split.items():
            if cat_text.strip():
                if cat not in _hp_draft:
                    _hp_draft[cat] = []
                _hp_draft[cat].append(cat_text)
        names = ", ".join(HP_CATEGORIES[c] for c in split if split[c].strip())
        total_parts = max((len(_hp_draft[c]) for c in split if _hp_draft.get(c)), default=1)
        await message.answer(
            f"✅ Часть {total_parts} принята — {names}\n"
            f"Пересылай ещё или напиши <b>готово наушники</b> чтобы сохранить.",
            parse_mode="HTML"
        )
    elif tab_cats:
        # Планшеты — разбиваем по категориям сразу
        split = _split_tablet_by_category(text)
        for cat, cat_text in split.items():
            if cat_text.strip():
                if cat not in _tab_draft:
                    _tab_draft[cat] = []
                _tab_draft[cat].append(cat_text)
        names = ", ".join(TABLET_CATEGORIES[c] for c in split if split[c].strip())
        total_parts = max((len(_tab_draft[c]) for c in split if _tab_draft.get(c)), default=1)
        await message.answer(
            f"✅ Часть {total_parts} принята — {names}\n"
            f"Пересылай ещё или напиши <b>готово планшеты</b> чтобы сохранить.",
            parse_mode="HTML"
        )
    else:
        preview = text[:300].replace("<", "&lt;")
        await message.answer(
            f"⚠️ Тип товара не определён.\n\n"
            f"<b>Начало полученного текста:</b>\n<code>{preview}</code>",
            parse_mode="HTML"
        )
