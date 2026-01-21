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

# Быстрые варианты кнопками (если нет нужного — "Ввести вручную")
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
CB_SET_COUNT = "set_count"

CB_PICK_PREFIX = "pick:"       # pick:{field}:{value}
CB_MANUAL_PREFIX = "manual:"   # manual:{field}
CB_COUNT_PREFIX = "count:"     # count:{n}

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
        # target_count: int (если выбрал кол-во секций заранее)
        db[uid] = {"sections": [], "editing": None, "manual": False, "target_count": 0}

    # на случай старых данных:
    if "manual" not in db[uid]:
        db[uid]["manual"] = False
    if "target_count" not in db[uid]:
        db[uid]["target_count"] = 0
    return db[uid]


# ---------- Keyboards ----------
def main_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("🧱 Задать кол-во секций", callback_data=CB_SET_COUNT)],
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


def count_kb() -> InlineKeyboardMarkup:
    nums = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for n in nums:
        row.append(InlineKeyboardButton(str(n), callback_data=f"{CB_COUNT_PREFIX}{n}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(rows)


# ---------- Helpers ----------
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
    Ниже — пример-заглушка.
    """
    price_per_m2 = 1000.0
    total_m2 = 0.0
    for s in sections:
        m2 = (s.width_mm / 1000.0) * (s.depth_mm / 1000.0) * max(s.levels_count, 0)
        total_m2 += m2
    return total_m2 * price_per_m2


def ask_text(idx: int, field_label: str, action_title: str, total: int) -> str:
    return f"{action_title} секцию {idx+1} из {total}.\n\nВыбери или введи: **{field_label}**"


def current_step(db: Dict[str, Dict], user_id: int) -> Optional[Tuple[int, int, str, str]]:
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


def total_sections(st: Dict[str, Any]) -> int:
    return len(st["sections"])


# ---------- Commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Калькулятор стеллажей.\n\nВыбери действие:", reply_markup=main_menu()
    )


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    st = get_user_state(db, update.effective_user.id)
    st["editing"] = None
    st["manual"] = False
    save_db(db)
    await update.message.reply_text("Выбери действие:", reply_markup=main_menu())


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    st = get_user_state(db, update.effective_user.id)
    st["sections"] = []
    st["editing"] = None
    st["manual"] = False
    st["target_count"] = 0
    save_db(db)
    await update.message.reply_text("✅ Сбросил расчёт. Выбери действие:", reply_markup=main_menu())


# ---------- Menu click handler ----------
async def on_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    db = load_db()
    st = get_user_state(db, q.from_user.id)

    if q.data == CB_MENU:
        st["editing"] = None
        st["manual"] = False
        save_db(db)
        await q.edit_message_text("Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    if q.data == CB_RESET:
        st["sections"] = []
        st["editing"] = None
        st["manual"] = False
        st["target_count"] = 0
        save_db(db)
        await q.edit_message_text("✅ Сбросил расчёт. Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    if q.data == CB_CANCEL:
        st["editing"] = None
        st["manual"] = False
        save_db(db)
        await q.edit_message_text("Ок, отменил. Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    if q.data == CB_SET_COUNT:
        await q.edit_message_text("Сколько секций нужно?", reply_markup=count_kb())
        return ConversationHandler.END

    if q.data == CB_ADD:
        # добавляем ОДНУ секцию и начинаем её заполнять
        st["sections"].append(asdict(Section()))
        idx = len(st["sections"]) - 1
        st["editing"] = {"idx": idx, "field_i": 0}
        st["manual"] = False
        st["target_count"] = 0
        save_db(db)

        field_key, field_label = FIELDS[0]
        await q.edit_message_text(
            ask_text(idx, field_label, "Добавляем", total_sections(st)),
            parse_mode="Markdown",
            reply_markup=input_kb(field_key),
        )
        return ASK_VALUE

    if q.data == CB_LIST:
        cnt = len(st["sections"])
        if cnt == 0:
            await q.edit_message_text("Пока нет секций. Нажми ➕ Добавить секцию.", reply_markup=main_menu())
            return ConversationHandler.END
        await q.edit_message_text("Секции:", reply_markup=list_kb(cnt))
        return ConversationHandler.END

    if q.data.startswith("open:"):
        idx = int(q.data.split(":")[1])
        s = Section(**st["sections"][idx])
        await q.edit_message_text(
            format_section(s, idx),
            parse_mode="Markdown",
            reply_markup=section_actions_kb(idx),
        )
        return ConversationHandler.END

    if q.data.startswith("del:"):
        idx = int(q.data.split(":")[1])
        if 0 <= idx < len(st["sections"]):
            st["sections"].pop(idx)
            save_db(db)
        await q.edit_message_text("Удалено. Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    if q.data.startswith("edit:"):
        idx = int(q.data.split(":")[1])
        st["editing"] = {"idx": idx, "field_i": 0}
        st["manual"] = False
        st["target_count"] = 0
        save_db(db)

        field_key, field_label = FIELDS[0]
        await q.edit_message_text(
            ask_text(idx, field_label, "Редактируем", total_sections(st)),
            parse_mode="Markdown",
            reply_markup=input_kb(field_key),
        )
        return ASK_VALUE

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


# ---------- Choose sections count ----------
async def on_count_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    n = int(q.data.split(":")[1])

    db = load_db()
    st = get_user_state(db, q.from_user.id)

    st["sections"] = [asdict(Section()) for _ in range(n)]
    st["target_count"] = n
    st["editing"] = {"idx": 0, "field_i": 0}
    st["manual"] = False
    save_db(db)

    field_key, field_label = FIELDS[0]
    await q.edit_message_text(
        ask_text(0, field_label, "Заполним", total_sections(st)),
        parse_mode="Markdown",
        reply_markup=input_kb(field_key),
    )
    return ASK_VALUE


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
    total = total_sections(st)

    data = q.data[len(CB_PICK_PREFIX):]  # {field}:{value}
    field_key, value = data.split(":", 1)

    if field_key != key:
        await q.edit_message_text(
            ask_text(idx, label, "Продолжаем", total),
            parse_mode="Markdown",
            reply_markup=input_kb(key),
        )
        return ASK_VALUE

    # запись значения
    if key == "extra_section":
        b = parse_bool_ru(value)
        if b is None:
            await q.edit_message_text("Выбери **да** или **нет**:", reply_markup=input_kb(key), parse_mode="Markdown")
            return ASK_VALUE
        st["sections"][idx][key] = b
    else:
        try:
            st["sections"][idx][key] = int(value)
        except ValueError:
            await q.edit_message_text("Некорректное значение. Выбери снова:", reply_markup=input_kb(key))
            return ASK_VALUE

    st["manual"] = False

    # следующий шаг
    field_i += 1
    if field_i >= len(FIELDS):
        # секция готова -> следующая секция или завершение
        next_idx = idx + 1
        if next_idx < len(st["sections"]):
            st["editing"] = {"idx": next_idx, "field_i": 0}
            st["manual"] = False
            save_db(db)

            first_key, first_label = FIELDS[0]
            await q.edit_message_text(
                f"✅ Секция {idx+1} готова.\n\n"
                + ask_text(next_idx, first_label, "Заполним", total_sections(st)),
                parse_mode="Markdown",
                reply_markup=input_kb(first_key),
            )
            return ASK_VALUE

        st["editing"] = None
        st["manual"] = False
        save_db(db)
        await q.edit_message_text(
            f"✅ Все секции заполнены ({len(st['sections'])}).\n\nТеперь нажми «✅ Применить» в меню.",
            reply_markup=main_menu(),
        )
        return ConversationHandler.END

    st["editing"]["field_i"] = field_i
    save_db(db)

    next_key, next_label = FIELDS[field_i]
    await q.edit_message_text(
        ask_text(idx, next_label, "Продолжаем", total),
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

    field_key = q.data[len(CB_MANUAL_PREFIX):]
    if field_key != key:
        await q.edit_message_text(
            ask_text(idx, label, "Продолжаем", total_sections(st)),
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
    total = total_sections(st)

    if field_i > 0:
        field_i -= 1
    else:
        field_i = 0

    editing["field_i"] = field_i
    st["manual"] = False
    save_db(db)

    key, label = FIELDS[field_i]
    await q.edit_message_text(
        ask_text(idx, label, "Возврат к", total),
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

    step = current_step(db, update.effective_user.id)
    if not step:
        await update.message.reply_text("Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    idx, field_i, key, label = step
    total = total_sections(st)

    # Если manual не включён — просим выбирать кнопками
    if not st.get("manual", False):
        await update.message.reply_text(
            f"Выбери значение кнопкой или нажми «Ввести вручную» для поля: **{label}**",
            parse_mode="Markdown",
            reply_markup=input_kb(key),
        )
        return ASK_VALUE

    raw = update.message.text.strip()

    # Валидация
    if key == "extra_section":
        b = parse_bool_ru(raw)
        if b is None:
            await update.message.reply_text(
                "Введи **да** или **нет**.",
                parse_mode="Markdown",
                reply_markup=manual_only_kb()
            )
            return ASK_VALUE
        st["sections"][idx][key] = b
    else:
        try:
            val = int(raw)
            if val < 0:
                raise ValueError
            st["sections"][idx][key] = val
        except ValueError:
            await update.message.reply_text(
                "Нужно целое число (например: 2000).",
                reply_markup=manual_only_kb()
            )
            return ASK_VALUE

    st["manual"] = False

    # Следующее поле
    field_i += 1
    if field_i >= len(FIELDS):
        next_idx = idx + 1
        if next_idx < len(st["sections"]):
            st["editing"] = {"idx": next_idx, "field_i": 0}
            st["manual"] = False
            save_db(db)

            first_key, first_label = FIELDS[0]
            await update.message.reply_text(
                f"✅ Секция {idx+1} готова.\n\n"
                + ask_text(next_idx, first_label, "Заполним", total_sections(st)),
                parse_mode="Markdown",
                reply_markup=input_kb(first_key),
            )
            return ASK_VALUE

        st["editing"] = None
        st["manual"] = False
        save_db(db)
        await update.message.reply_text(
            f"✅ Все секции заполнены ({len(st['sections'])}).\n\nТеперь нажми «✅ Применить» в меню.",
            reply_markup=main_menu(),
        )
        return ConversationHandler.END

    st["editing"]["field_i"] = field_i
    save_db(db)

    next_key, next_label = FIELDS[field_i]
    await update.message.reply_text(
        ask_text(idx, next_label, "Теперь выбери/введи", total),
        parse_mode="Markdown",
        reply_markup=input_kb(next_key),
    )
    return ASK_VALUE


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()

    # Команды (кнопки-команды в Telegram можно включить через BotFather /setcommands)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(on_count_pick, pattern=r"^count:"),
            CallbackQueryHandler(on_pick, pattern=r"^pick:"),
            CallbackQueryHandler(on_manual, pattern=r"^manual:"),
            CallbackQueryHandler(on_back_field, pattern=r"^back_field$"),
            CallbackQueryHandler(on_menu_click),
        ],
        states={
            ASK_VALUE: [
                CallbackQueryHandler(on_count_pick, pattern=r"^count:"),
                CallbackQueryHandler(on_pick, pattern=r"^pick:"),
                CallbackQueryHandler(on_manual, pattern=r"^manual:"),
                CallbackQueryHandler(on_back_field, pattern=r"^back_field$"),
                CallbackQueryHandler(on_menu_click),
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
