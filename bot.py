import json
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters
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

# Варианты для быстрых кнопок (если нет нужного — жми "Ввести вручную")
OPTIONS = {
    "height_mm": [1600, 1850, 2200, 2350, 2500, 2550, 2750, 3000, 3100],
    "width_mm": [700, 1000, 1200, 1500],
    "depth_mm": [300, 400, 500, 600, 700, 800],
    "load_per_shelf_kg": [100, 150, 200],
    "max_total_load_kg": [500, 750, 1000],
    "levels_count": [2, 3, 4, 5, 6, 7, 8, 9],
    "extra_section": ["да", "нет"],
}

# Callback data
CB_ADD = "add"
CB_LIST = "list"
CB_APPLY = "apply"
CB_MENU = "menu"
CB_RESET = "reset_all"
CB_BACK = "back_field"
CB_CANCEL = "cancel_edit"

# Для pick/manual
CB_PICK_PREFIX = "pick:"      # pick:{field}:{value}
CB_MANUAL_PREFIX = "manual:"  # manual:{field}

# Состояния диалога
ASK_VALUE = 1


@dataclass
class Section:
    height_mm: int = 0
    width_mm: int = 0
    depth_mm: int = 0
    load_per_shelf_kg: int = 0
    max_total_load_kg: int = 0
    levels_count: int = 0
    extra_section: bool = False


def load_db() -> Dict[str, Dict]:
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(db: Dict[str, Dict]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def get_user_state(db: Dict[str, Dict], user_id: int) -> Dict[str, Any]:
    uid = str(user_id)
    if uid not in db:
        # editing: {"idx": int, "field_i": int}
        # manual: bool (ожидаем ручной ввод текстом)
        db[uid] = {"sections": [], "editing": None, "manual": False}
    # гарантируем ключи на старых данных
    if "manual" not in db[uid]:
        db[uid]["manual"] = False
    return db[uid]


def main_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("➕ Добавить секцию", callback_data=CB_ADD)],
        [InlineKeyboardButton("📋 Секции", callback_data=CB_LIST)],
        [InlineKeyboardButton("✅ Применить", callback_data=CB_APPLY)],
    ]
    return InlineKeyboardMarkup(kb)


def section_actions_kb(idx: int) -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit:{idx}"),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{idx}"),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data=CB_MENU)],
    ]
    return InlineKeyboardMarkup(kb)


def list_kb(sections_count: int) -> InlineKeyboardMarkup:
    kb = []
    for i in range(sections_count):
        kb.append([InlineKeyboardButton(f"Секция {i+1}", callback_data=f"open:{i}")])
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(kb)


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
    """
    TODO: сюда вставим твою реальную формулу.
    Ниже — пример-заглушка: считаем "площадь полок" (ширина*глубина*уровни) в м²
    и умножаем на условную цену 1000 руб/м².
    """
    price_per_m2 = 1000.0  # заменишь на свои правила/прайс
    total_m2 = 0.0
    for s in sections:
        m2 = (s.width_mm / 1000.0) * (s.depth_mm / 1000.0) * max(s.levels_count, 0)
        total_m2 += m2
    return total_m2 * price_per_m2


# ---------- UI for step input (pick/manual/back/cancel) ----------
def input_kb(field_key: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    opts = OPTIONS.get(field_key, [])

    # варианты (2 колонки)
    row: List[InlineKeyboardButton] = []
    for v in opts:
        row.append(InlineKeyboardButton(str(v), callback_data=f"{CB_PICK_PREFIX}{field_key}:{v}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("✍️ Ввести вручную", callback_data=f"{CB_MANUAL_PREFIX}{field_key}")])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data=CB_BACK),
        InlineKeyboardButton("⛔️ Отмена", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def finish_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Новый расчёт", callback_data=CB_RESET)],
        [InlineKeyboardButton("⬅️ В меню", callback_data=CB_MENU)],
    ])


def manual_only_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⬅️ Назад", callback_data=CB_BACK),
            InlineKeyboardButton("⛔️ Отмена", callback_data=CB_CANCEL),
        ]
    ])


def ask_text(idx: int, field_label: str, action_title: str) -> str:
    return f"{action_title} секцию {idx+1}.\n\nВыбери или введи: **{field_label}**"


def current_step(db: Dict[str, Dict], user_id: int) -> Optional[Tuple[int, int, str, str]]:
    """Возвращает (idx, field_i, key, label) или None."""
    st = get_user_state(db, user_id)
    editing = st.get("editing")
    if not editing:
        return None
    idx = editing["idx"]
    field_i = editing["field_i"]
    if idx >= len(st["sections"]) or field_i < 0 or field_i >= len(FIELDS):
        return None
    key, label = FIELDS[field_i]
    return idx, field_i, key, label


# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Калькулятор стеллажей.\n\nВыбери действие:", reply_markup=main_menu()
    )


async def on_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    db = load_db()
    st = get_user_state(db, q.from_user.id)

    # Меню
    if q.data == CB_MENU:
        st["editing"] = None
        st["manual"] = False
        save_db(db)
        await q.edit_message_text("Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    # Новый расчёт: очищаем всё
    if q.data == CB_RESET:
        st["sections"] = []
        st["editing"] = None
        st["manual"] = False
        save_db(db)
        await q.edit_message_text("✅ Сбросил расчёт. Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    # Отмена редактирования (во время ввода)
    if q.data == CB_CANCEL:
        st["editing"] = None
        st["manual"] = False
        save_db(db)
        await q.edit_message_text("Ок, отменил. Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    # Добавить секцию
    if q.data == CB_ADD:
        st["sections"].append(asdict(Section()))
        idx = len(st["sections"]) - 1
        st["editing"] = {"idx": idx, "field_i": 0}
        st["manual"] = False
        save_db(db)

        field_key, field_label = FIELDS[0]
        await q.edit_message_text(
            ask_text(idx, field_label, "Добавляем"),
            parse_mode="Markdown",
            reply_markup=input_kb(field_key),
        )
        return ASK_VALUE

    # Список секций
    if q.data == CB_LIST:
        cnt = len(st["sections"])
        if cnt == 0:
            await q.edit_message_text("Пока нет секций. Нажми ➕ Добавить секцию.", reply_markup=main_menu())
            return ConversationHandler.END
        await q.edit_message_text("Секции:", reply_markup=list_kb(cnt))
        return ConversationHandler.END

    # Открыть секцию
    if q.data.startswith("open:"):
        idx = int(q.data.split(":")[1])
        s = Section(**st["sections"][idx])
        await q.edit_message_text(
            format_section(s, idx),
            parse_mode="Markdown",
            reply_markup=section_actions_kb(idx),
        )
        return ConversationHandler.END

    # Удалить секцию
    if q.data.startswith("del:"):
        idx = int(q.data.split(":")[1])
        if 0 <= idx < len(st["sections"]):
            st["sections"].pop(idx)
            save_db(db)
        await q.edit_message_text("Удалено. Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    # Редактировать секцию (с нуля по полям)
    if q.data.startswith("edit:"):
        idx = int(q.data.split(":")[1])
        st["editing"] = {"idx": idx, "field_i": 0}
        st["manual"] = False
        save_db(db)

        field_key, field_label = FIELDS[0]
        await q.edit_message_text(
            ask_text(idx, field_label, "Редактируем"),
            parse_mode="Markdown",
            reply_markup=input_kb(field_key),
        )
        return ASK_VALUE

    # Применить (итог)
    if q.data == CB_APPLY:
        sections = [Section(**x) for x in st["sections"]]
        if not sections:
            await q.edit_message_text("Секций нет. Добавь хотя бы одну.", reply_markup=main_menu())
            return ConversationHandler.END

        total = calc_price(sections)
        text = "✅ **Итог**\n\n"
        for i, s in enumerate(sections):
            text += format_section(s, i) + "\n\n"
        text += f"**Итого (пример): {total:,.2f} руб**\n\n(Формулу расчёта цены настроим под твой прайс.)"

        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=finish_kb())
        return ConversationHandler.END

    await q.edit_message_text("Не понял команду. Выбери действие:", reply_markup=main_menu())
    return ConversationHandler.END


# ---------- Pick value button ----------
async def on_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    db = load_db()
    st = get_user_state(db, q.from_user.id)
    step = current_step(db, q.from_user.id)
    if not step:
        await q.edit_message_text("Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    idx, field_i, key, label = step

    # pick:{field}:{value}
    data = q.data[len(CB_PICK_PREFIX):]
    field_key, value = data.split(":", 1)

    # если кнопка не для текущего поля — покажем текущую клавиатуру
    if field_key != key:
        await q.edit_message_text(
            ask_text(idx, label, "Продолжаем"),
            parse_mode="Markdown",
            reply_markup=input_kb(key),
        )
        return ASK_VALUE

    # записываем значение
    if key == "extra_section":
        b = parse_bool_ru(value)
        if b is None:
            await q.edit_message_text("Выбери **да** или **нет**:", reply_markup=input_kb(key))
            return ASK_VALUE
        st["sections"][idx][key] = b
    else:
        try:
            st["sections"][idx][key] = int(value)
        except ValueError:
            await q.edit_message_text("Некорректное значение. Выбери снова:", reply_markup=input_kb(key))
            return ASK_VALUE

    # следующий шаг
    st["manual"] = False
    field_i += 1
    if field_i >= len(FIELDS):
        st["editing"] = None
        save_db(db)
        s = Section(**st["sections"][idx])
        await q.edit_message_text(
            "Готово ✅\n\n" + format_section(s, idx),
            parse_mode="Markdown",
            reply_markup=main_menu()
        )
        return ConversationHandler.END

    st["editing"]["field_i"] = field_i
    save_db(db)

    next_key, next_label = FIELDS[field_i]
    await q.edit_message_text(
        ask_text(idx, next_label, "Продолжаем"),
        parse_mode="Markdown",
        reply_markup=input_kb(next_key),
    )
    return ASK_VALUE


# ---------- Switch to manual input ----------
async def on_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    db = load_db()
    st = get_user_state(db, q.from_user.id)
    step = current_step(db, q.from_user.id)
    if not step:
        await q.edit_message_text("Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    idx, field_i, key, label = step

    # manual:{field}
    field_key = q.data[len(CB_MANUAL_PREFIX):]
    if field_key != key:
        await q.edit_message_text(
            ask_text(idx, label, "Продолжаем"),
            parse_mode="Markdown",
            reply_markup=input_kb(key),
        )
        return ASK_VALUE

    st["manual"] = True
    save_db(db)
    await q.edit_message_text(
        f"Ок. Введи вручную: **{label}**",
        parse_mode="Markdown",
        reply_markup=manual_only_kb(),
    )
    return ASK_VALUE


# ---------- Back one field ----------
async def on_back_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    db = load_db()
    st = get_user_state(db, q.from_user.id)
    editing = st.get("editing")
    if not editing:
        await q.edit_message_text("Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    idx = editing["idx"]
    field_i = editing["field_i"]

    # назад на поле
    if field_i > 0:
        field_i -= 1
    else:
        # если это первое поле — остаёмся на первом
        field_i = 0

    editing["field_i"] = field_i
    st["manual"] = False
    save_db(db)

    key, label = FIELDS[field_i]
    await q.edit_message_text(
        ask_text(idx, label, "Возврат к"),
        parse_mode="Markdown",
        reply_markup=input_kb(key),
    )
    return ASK_VALUE


# ---------- Manual text input ----------
async def on_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    st = get_user_state(db, update.effective_user.id)

    editing = st.get("editing")
    if not editing:
        await update.message.reply_text("Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    # Если manual не включён — просим выбрать кнопками
    if not st.get("manual", False):
        idx, field_i, key, label = current_step(db, update.effective_user.id) or (None, None, None, None)
        if key is None:
            await update.message.reply_text("Выбери действие:", reply_markup=main_menu())
            return ConversationHandler.END
        await update.message.reply_text(
            f"Выбери значение кнопкой или нажми «Ввести вручную» для поля: **{label}**",
            parse_mode="Markdown",
            reply_markup=input_kb(key),
        )
        return ASK_VALUE

    idx = editing["idx"]
    field_i = editing["field_i"]
    if idx >= len(st["sections"]):
        st["editing"] = None
        st["manual"] = False
        save_db(db)
        await update.message.reply_text("Секция не найдена. Меню:", reply_markup=main_menu())
        return ConversationHandler.END

    key, label = FIELDS[field_i]
    raw = update.message.text.strip()

    # Валидация
    if key == "extra_section":
        b = parse_bool_ru(raw)
        if b is None:
            await update.message.reply_text("Введи **да** или **нет**.", parse_mode="Markdown", reply_markup=manual_only_kb())
            return ASK_VALUE
        st["sections"][idx][key] = b
    else:
        try:
            val = int(raw)
            if val < 0:
                raise ValueError
            st["sections"][idx][key] = val
        except ValueError:
            await update.message.reply_text("Нужно целое число (например: 2000).", reply_markup=manual_only_kb())
            return ASK_VALUE

    # Ручной ввод закончили — возвращаемся к кнопкам дальше
    st["manual"] = False

    # Следующее поле
    field_i += 1
    if field_i >= len(FIELDS):
        st["editing"] = None
        save_db(db)
        s = Section(**st["sections"][idx])
        await update.message.reply_text(
            "Готово ✅\n\n" + format_section(s, idx),
            parse_mode="Markdown",
            reply_markup=main_menu()
        )
        return ConversationHandler.END

    st["editing"]["field_i"] = field_i
    save_db(db)

    next_key, next_label = FIELDS[field_i]
    await update.message.reply_text(
        ask_text(idx, next_label, "Теперь выбери/введи"),
        parse_mode="Markdown",
        reply_markup=input_kb(next_key),
    )
    return ASK_VALUE


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            # Важно: сначала узкие обработчики, потом общий menu
            CallbackQueryHandler(on_pick, pattern=r"^pick:"),
            CallbackQueryHandler(on_manual, pattern=r"^manual:"),
            CallbackQueryHandler(on_back_field, pattern=r"^back_field$"),
            CallbackQueryHandler(on_menu_click),
        ],
        states={
            ASK_VALUE: [
                CallbackQueryHandler(on_pick, pattern=r"^pick:"),
                CallbackQueryHandler(on_manual, pattern=r"^manual:"),
                CallbackQueryHandler(on_back_field, pattern=r"^back_field$"),
                CallbackQueryHandler(on_menu_click),  # для cancel/menu и т.п.
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_value),
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
