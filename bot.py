import json
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

DATA_FILE = "data.json"

FIELDS: List[Tuple[str, str]] = [
    ("height_mm", "Высота, мм"),
    ("width_mm", "Ширина, мм"),
    ("depth_mm", "Глубина, мм"),
    ("load_per_shelf_kg", "Нагрузка на полку, кг"),
    ("max_total_load_kg", "Макс. общая нагрузка, кг"),
    ("levels_count", "Кол-во уровней"),
    ("extra_section", "Доп секция (да/нет)"),
]

# Состояния диалога
ASK_VALUE = 1


# Предложенные варианты (можешь поменять на свои любые)
PRESETS: Dict[str, List[Any]] = {
    "height_mm": [1200, 1500, 1800, 2000, 2200, 2400],
    "width_mm": [600, 800, 1000, 1200, 1500],
    "depth_mm": [300, 400, 500, 600, 700],
    "load_per_shelf_kg": [80, 120, 150, 200, 250],
    "max_total_load_kg": [300, 500, 800, 1000, 1200],
    "levels_count": [3, 4, 5, 6, 7],
    "extra_section": ["да", "нет"],
}


@dataclass
class Section:
    height_mm: int = 0
    width_mm: int = 0
    depth_mm: int = 0
    load_per_shelf_kg: int = 0
    max_total_load_kg: int = 0
    levels_count: int = 0
    extra_section: bool = False


# -------------------- DB --------------------

def load_db() -> Dict[str, Dict]:
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(db: Dict[str, Dict]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def get_user_state(db: Dict[str, Dict], user_id: int) -> Dict:
    uid = str(user_id)
    if uid not in db:
        db[uid] = {
            "sections": [],
            # editing: {"idx": int, "field_i": int, "custom": bool}
            "editing": None
        }
    return db[uid]


# -------------------- UI --------------------

def main_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("➕ Добавить секцию", callback_data="add")],
        [InlineKeyboardButton("📋 Секции", callback_data="list")],
        [InlineKeyboardButton("✅ Применить", callback_data="apply")],
        [InlineKeyboardButton("🔄 Сброс", callback_data="reset_all")],
    ]
    return InlineKeyboardMarkup(kb)


def section_actions_kb(idx: int) -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit:{idx}"),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{idx}"),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data="list")],
        [InlineKeyboardButton("🏠 Меню", callback_data="menu")],
    ]
    return InlineKeyboardMarkup(kb)


def list_kb(sections_count: int) -> InlineKeyboardMarkup:
    kb = []
    for i in range(sections_count):
        kb.append([InlineKeyboardButton(f"Секция {i+1}", callback_data=f"open:{i}")])
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu")])
    kb.append([InlineKeyboardButton("🔄 Сброс", callback_data="reset_all")])
    return InlineKeyboardMarkup(kb)


def nav_kb() -> List[List[InlineKeyboardButton]]:
    return [
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="step_back"),
            InlineKeyboardButton("🏠 Меню", callback_data="menu"),
        ],
        [InlineKeyboardButton("🔄 Сброс", callback_data="reset_all")],
    ]


def chunk_buttons(values: List[Any], per_row: int = 3) -> List[List[InlineKeyboardButton]]:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for v in values:
        row.append(InlineKeyboardButton(str(v), callback_data=f"pick:{v}"))
        if len(row) >= per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def ask_field_kb(field_key: str, custom_mode: bool = False) -> InlineKeyboardMarkup:
    # Если custom_mode=True — показываем только навигацию (ждём ввод вручную)
    if custom_mode:
        return InlineKeyboardMarkup(nav_kb())

    values = PRESETS.get(field_key, [])
    rows: List[List[InlineKeyboardButton]] = []

    # Для "да/нет" сделаем в одну строку
    if field_key == "extra_section":
        rows.append([
            InlineKeyboardButton("✅ Да", callback_data="pick:да"),
            InlineKeyboardButton("❌ Нет", callback_data="pick:нет"),
        ])
    else:
        rows.extend(chunk_buttons(values, per_row=3))

    rows.append([InlineKeyboardButton("⌨️ Ввести своё", callback_data="custom")])
    rows.extend(nav_kb())
    return InlineKeyboardMarkup(rows)


async def safe_edit(q, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None, parse_mode: Optional[str] = None):
    try:
        await q.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        # Частая ошибка: "Message is not modified"
        if "Message is not modified" in str(e):
            return
        raise


# -------------------- Helpers --------------------

def format_section(s: Section, idx: int) -> str:
    return (
        f"**Секция {idx+1}**\n"
        f"Высота: {s.height_mm} мм\n"
        f"Ширина: {s.width_mm} мм\n"
        f"Глубина: {s.depth_mm} мм\n"
        f"Нагрузка на полку: {s.load_per_shelf_kg} кг\n"
        f"Макс. общая нагрузка: {s.max_total_load_kg} кг\n"
        f"Кол-во уровней: {s.levels_count}\n"
        f"Доп секция: {'да' if s.extra_section else 'нет'}"
    )


def parse_bool_ru(text: str) -> Optional[bool]:
    t = text.strip().lower()
    if t in ("да", "д", "yes", "y", "1", "true"):
        return True
    if t in ("нет", "н", "no", "n", "0", "false"):
        return False
    return None


def calc_price(sections: List[Section]) -> float:
    # Заглушка — заменишь на свою формулу
    price_per_m2 = 1000.0
    total_m2 = 0.0
    for s in sections:
        m2 = (s.width_mm / 1000.0) * (s.depth_mm / 1000.0) * max(s.levels_count, 0)
        total_m2 += m2
    return total_m2 * price_per_m2


def current_field(editing: Dict) -> Tuple[str, str]:
    field_i = editing["field_i"]
    return FIELDS[field_i]


def ensure_editing_exists(st: Dict) -> Optional[Dict]:
    ed = st.get("editing")
    if not ed:
        return None
    return ed


def reset_user(st: Dict):
    st["sections"] = []
    st["editing"] = None


def start_editing(st: Dict, idx: int, field_i: int = 0):
    st["editing"] = {"idx": idx, "field_i": field_i, "custom": False}


def set_custom_mode(st: Dict, enabled: bool):
    if st.get("editing"):
        st["editing"]["custom"] = enabled


def validate_and_set_value(st: Dict, idx: int, key: str, raw: str) -> Tuple[bool, str]:
    """
    Возвращает (ok, error_message)
    """
    raw = raw.strip()

    if key == "extra_section":
        b = parse_bool_ru(raw)
        if b is None:
            return False, "Введи **да** или **нет**."
        st["sections"][idx][key] = b
        return True, ""

    try:
        val = int(raw)
        if val < 0:
            raise ValueError
        st["sections"][idx][key] = val
        return True, ""
    except ValueError:
        return False, "Нужно целое число (например: 2000)."


async def prompt_current_field_text(idx: int, field_label: str, action_title: str) -> str:
    return f"{action_title} секцию {idx+1}.\n\nВведи: **{field_label}**"


async def send_next_prompt_text(update_or_q, text: str, markup: InlineKeyboardMarkup, edit: bool):
    if edit:
        q = update_or_q
        await safe_edit(q, text, reply_markup=markup, parse_mode="Markdown")
    else:
        upd = update_or_q
        await upd.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


def action_title_for_mode(is_edit: bool) -> str:
    return "Редактируем" if is_edit else "Добавляем"


# -------------------- Handlers --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /start всегда показывает меню (не ломая секции)
    await update.message.reply_text(
        "Калькулятор стеллажей.\n\nВыбери действие:",
        reply_markup=main_menu()
    )


async def on_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    db = load_db()
    st = get_user_state(db, q.from_user.id)

    # --- Глобальные кнопки ---
    if q.data == "menu":
        st["editing"] = None
        save_db(db)
        await safe_edit(q, "Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    if q.data == "reset_all":
        reset_user(st)
        save_db(db)
        await safe_edit(q, "Сброшено ✅\n\nВыбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    # --- Навигация во время ввода ---
    if q.data == "step_back":
        ed = ensure_editing_exists(st)
        if not ed:
            await safe_edit(q, "Выбери действие:", reply_markup=main_menu())
            return ConversationHandler.END

        # если были в custom-режиме — просто выходим из него и снова показываем варианты
        if ed.get("custom"):
            set_custom_mode(st, False)
            key, label = current_field(ed)
            save_db(db)
            await safe_edit(
                q,
                f"Ок. Выбери значение для **{label}** или введи своё:",
                reply_markup=ask_field_kb(key, custom_mode=False),
                parse_mode="Markdown",
            )
            return ASK_VALUE

        # иначе — реально откатываем шаг назад
        if ed["field_i"] > 0:
            ed["field_i"] -= 1
        set_custom_mode(st, False)
        key, label = current_field(ed)
        save_db(db)
        await safe_edit(
            q,
            f"Назад.\n\nВыбери значение для **{label}** или введи своё:",
            reply_markup=ask_field_kb(key, custom_mode=False),
            parse_mode="Markdown",
        )
        return ASK_VALUE

    if q.data == "custom":
        ed = ensure_editing_exists(st)
        if not ed:
            await safe_edit(q, "Выбери действие:", reply_markup=main_menu())
            return ConversationHandler.END
        set_custom_mode(st, True)
        key, label = current_field(ed)
        save_db(db)
        await safe_edit(
            q,
            f"Введи значение вручную для **{label}**:",
            reply_markup=ask_field_kb(key, custom_mode=True),
            parse_mode="Markdown",
        )
        return ASK_VALUE

    if q.data.startswith("pick:"):
        ed = ensure_editing_exists(st)
        if not ed:
            await safe_edit(q, "Выбери действие:", reply_markup=main_menu())
            return ConversationHandler.END

        idx = ed["idx"]
        if idx >= len(st["sections"]):
            st["editing"] = None
            save_db(db)
            await safe_edit(q, "Секция не найдена. Меню:", reply_markup=main_menu())
            return ConversationHandler.END

        raw = q.data.split("pick:", 1)[1]
        key, label = current_field(ed)

        ok, err = validate_and_set_value(st, idx, key, raw)
        if not ok:
            save_db(db)
            await safe_edit(
                q,
                err + f"\n\nВыбери значение для **{label}** или введи своё:",
                reply_markup=ask_field_kb(key, custom_mode=False),
                parse_mode="Markdown",
            )
            return ASK_VALUE

        # принято
        set_custom_mode(st, False)
        ed["field_i"] += 1

        # закончились поля
        if ed["field_i"] >= len(FIELDS):
            st["editing"] = None
            save_db(db)
            s = Section(**st["sections"][idx])
            await safe_edit(
                q,
                "Готово ✅\n\n" + format_section(s, idx) + "\n\nВыбери действие:",
                reply_markup=main_menu(),
                parse_mode="Markdown",
            )
            return ConversationHandler.END

        # следующий вопрос
        next_key, next_label = current_field(ed)
        save_db(db)
        await safe_edit(
            q,
            f"Теперь выбери значение для **{next_label}** или введи своё:",
            reply_markup=ask_field_kb(next_key, custom_mode=False),
            parse_mode="Markdown",
        )
        return ASK_VALUE

    # --- Основные действия ---
    if q.data == "add":
        st["sections"].append(asdict(Section()))
        idx = len(st["sections"]) - 1
        start_editing(st, idx, field_i=0)
        save_db(db)

        key, label = current_field(st["editing"])
        text = f"Добавляем секцию {idx+1}.\n\nВыбери значение для **{label}** или введи своё:"
        await safe_edit(q, text, reply_markup=ask_field_kb(key, custom_mode=False), parse_mode="Markdown")
        return ASK_VALUE

    if q.data == "list":
        cnt = len(st["sections"])
        if cnt == 0:
            await safe_edit(q, "Пока нет секций. Нажми ➕ Добавить секцию.", reply_markup=main_menu())
            return ConversationHandler.END
        await safe_edit(q, "Секции:", reply_markup=list_kb(cnt))
        return ConversationHandler.END

    if q.data.startswith("open:"):
        idx = int(q.data.split(":")[1])
        s = Section(**st["sections"][idx])
        await safe_edit(
            q,
            format_section(s, idx),
            reply_markup=section_actions_kb(idx),
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    if q.data.startswith("del:"):
        idx = int(q.data.split(":")[1])
        if 0 <= idx < len(st["sections"]):
            st["sections"].pop(idx)
            save_db(db)
        await safe_edit(q, "Удалено. Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    if q.data.startswith("edit:"):
        idx = int(q.data.split(":")[1])
        start_editing(st, idx, field_i=0)
        save_db(db)

        key, label = current_field(st["editing"])
        text = f"Редактируем секцию {idx+1}.\n\nВыбери значение для **{label}** или введи своё:"
        await safe_edit(q, text, reply_markup=ask_field_kb(key, custom_mode=False), parse_mode="Markdown")
        return ASK_VALUE

    if q.data == "apply":
        sections = [Section(**x) for x in st["sections"]]
        if not sections:
            await safe_edit(q, "Секций нет. Добавь хотя бы одну.", reply_markup=main_menu())
            return ConversationHandler.END

        total = calc_price(sections)
        text = "✅ **Итог**\n\n"
        for i, s in enumerate(sections):
            text += format_section(s, i) + "\n\n"
        text += f"**Итого (пример): {total:,.2f} руб**\n\n"
        text += "Хочешь новый расчёт?"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Новый расчёт", callback_data="reset_all")],
            [InlineKeyboardButton("🏠 Меню", callback_data="menu")],
        ])
        await safe_edit(q, text, parse_mode="Markdown", reply_markup=kb)
        return ConversationHandler.END

    await safe_edit(q, "Не понял команду. Выбери действие:", reply_markup=main_menu())
    return ConversationHandler.END


async def on_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Сюда попадаем, когда пользователь вводит число/текст вручную.
    """
    db = load_db()
    st = get_user_state(db, update.effective_user.id)
    ed = ensure_editing_exists(st)

    if not ed:
        await update.message.reply_text("Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    idx = ed["idx"]
    if idx >= len(st["sections"]):
        st["editing"] = None
        save_db(db)
        await update.message.reply_text("Секция не найдена. Меню:", reply_markup=main_menu())
        return ConversationHandler.END

    key, label = current_field(ed)
    raw = update.message.text.strip()

    ok, err = validate_and_set_value(st, idx, key, raw)
    if not ok:
        await update.message.reply_text(
            err + f"\n\nВведи ещё раз **{label}** (или нажми Меню/Назад/Сброс):",
            parse_mode="Markdown",
            reply_markup=ask_field_kb(key, custom_mode=True),  # раз уж вручную — оставим custom кб
        )
        return ASK_VALUE

    # принято — выходим из custom режима и идём дальше
    set_custom_mode(st, False)
    ed["field_i"] += 1

    if ed["field_i"] >= len(FIELDS):
        st["editing"] = None
        save_db(db)
        s = Section(**st["sections"][idx])
        await update.message.reply_text(
            "Готово ✅\n\n" + format_section(s, idx) + "\n\nВыбери действие:",
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )
        return ConversationHandler.END

    save_db(db)
    next_key, next_label = current_field(ed)
    await update.message.reply_text(
        f"Теперь выбери значение для **{next_label}** или введи своё:",
        parse_mode="Markdown",
        reply_markup=ask_field_kb(next_key, custom_mode=False),
    )
    return ASK_VALUE


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(on_menu_click),
        ],
        states={
            ASK_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_value),
                CallbackQueryHandler(on_menu_click),  # чтобы кнопки работали и в ASK_VALUE
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    return app


if __name__ == "__main__":
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("Set BOT_TOKEN env var")
    build_app(token).run_polling()
