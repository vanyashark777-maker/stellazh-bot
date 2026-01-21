import json
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any

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

FIELDS = [
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


def get_user_state(db: Dict[str, Dict], user_id: int) -> Dict:
    uid = str(user_id)
    if uid not in db:
        db[uid] = {"sections": [], "editing": None}  # editing: {"idx": int, "field_i": int}
    return db[uid]


def main_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("➕ Добавить секцию", callback_data="add")],
        [InlineKeyboardButton("📋 Секции", callback_data="list")],
        [InlineKeyboardButton("✅ Применить", callback_data="apply")],
        [InlineKeyboardButton("🔄 Сброс", callback_data="reset")],
    ]
    return InlineKeyboardMarkup(kb)


def input_menu_kb() -> InlineKeyboardMarkup:
    # Показываем при каждом вопросе ввода значения
    kb = [
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="step_back"),
            InlineKeyboardButton("🏠 Меню", callback_data="menu"),
        ],
        [InlineKeyboardButton("🔄 Сброс", callback_data="reset")],
    ]
    return InlineKeyboardMarkup(kb)


def section_actions_kb(idx: int) -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit:{idx}"),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{idx}"),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data="menu")],
    ]
    return InlineKeyboardMarkup(kb)


def list_kb(sections_count: int) -> InlineKeyboardMarkup:
    kb = []
    for i in range(sections_count):
        kb.append([InlineKeyboardButton(f"Секция {i+1}", callback_data=f"open:{i}")])
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu")])
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
    Заглушка-формула: площадь полок (ширина*глубина*уровни) в м² * 1000 руб/м².
    """
    price_per_m2 = 1000.0
    total_m2 = 0.0
    for s in sections:
        m2 = (s.width_mm / 1000.0) * (s.depth_mm / 1000.0) * max(s.levels_count, 0)
        total_m2 += m2
    return total_m2 * price_per_m2


async def safe_edit(
    q,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = None,
) -> None:
    """
    Защита от ошибки:
    telegram.error.BadRequest: Message is not modified
    """
    try:
        await q.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            # Ничего страшного — просто игнорируем
            return
        raise


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /start всегда возвращает в меню и сбрасывает текущий ввод (чтобы не путаться)
    db = load_db()
    st = get_user_state(db, update.effective_user.id)
    st["editing"] = None
    save_db(db)

    await update.message.reply_text(
        "Калькулятор стеллажей.\n\nВыбери действие:",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


async def on_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    db = load_db()
    st = get_user_state(db, q.from_user.id)

    # --- Общие кнопки ---
    if q.data == "menu":
        st["editing"] = None
        save_db(db)
        await safe_edit(q, "Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    if q.data == "reset":
        st["sections"] = []
        st["editing"] = None
        save_db(db)
        await safe_edit(q, "Сбросил расчёт ✅\nВыбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    if q.data == "step_back":
        editing = st.get("editing")
        if not editing:
            await safe_edit(q, "Выбери действие:", reply_markup=main_menu())
            return ConversationHandler.END

        # откат на предыдущее поле
        if editing["field_i"] > 0:
            editing["field_i"] -= 1
            save_db(db)

        idx = editing["idx"]
        if idx >= len(st["sections"]):
            st["editing"] = None
            save_db(db)
            await safe_edit(q, "Секция не найдена. Меню:", reply_markup=main_menu())
            return ConversationHandler.END

        _, field_label = FIELDS[editing["field_i"]]
        await safe_edit(
            q,
            f"Ок, шаг назад.\n\nВведи снова: **{field_label}**",
            parse_mode="Markdown",
            reply_markup=input_menu_kb(),
        )
        return ASK_VALUE

    # --- Меню ---
    if q.data == "add":
        st["sections"].append(asdict(Section()))
        idx = len(st["sections"]) - 1
        st["editing"] = {"idx": idx, "field_i": 0}
        save_db(db)

        _, field_label = FIELDS[0]
        await safe_edit(
            q,
            f"Добавляем секцию {idx+1}.\n\nВведи: **{field_label}**",
            parse_mode="Markdown",
            reply_markup=input_menu_kb(),
        )
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
        if not (0 <= idx < len(st["sections"])):
            await safe_edit(q, "Секция не найдена.", reply_markup=main_menu())
            return ConversationHandler.END
        s = Section(**st["sections"][idx])
        await safe_edit(
            q,
            format_section(s, idx),
            parse_mode="Markdown",
            reply_markup=section_actions_kb(idx),
        )
        return ConversationHandler.END

    if q.data.startswith("del:"):
        idx = int(q.data.split(":")[1])
        if 0 <= idx < len(st["sections"]):
            st["sections"].pop(idx)
            st["editing"] = None
            save_db(db)
        await safe_edit(q, "Удалено. Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    if q.data.startswith("edit:"):
        idx = int(q.data.split(":")[1])
        if not (0 <= idx < len(st["sections"])):
            await safe_edit(q, "Секция не найдена.", reply_markup=main_menu())
            return ConversationHandler.END
        st["editing"] = {"idx": idx, "field_i": 0}
        save_db(db)

        _, field_label = FIELDS[0]
        await safe_edit(
            q,
            f"Редактируем секцию {idx+1}.\n\nВведи: **{field_label}**",
            parse_mode="Markdown",
            reply_markup=input_menu_kb(),
        )
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
        text += f"**Итого (пример): {total:,.2f} руб**\n\n(Формулу расчёта цены настроим под твой прайс.)"

        await safe_edit(q, text, parse_mode="Markdown", reply_markup=main_menu())
        return ConversationHandler.END

    await safe_edit(q, "Не понял команду. Выбери действие:", reply_markup=main_menu())
    return ConversationHandler.END


async def on_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    st = get_user_state(db, update.effective_user.id)
    editing = st.get("editing")

    if not editing:
        await update.message.reply_text("Выбери действие:", reply_markup=main_menu())
        return ConversationHandler.END

    idx = editing["idx"]
    field_i = editing["field_i"]

    if idx >= len(st["sections"]):
        st["editing"] = None
        save_db(db)
        await update.message.reply_text("Секция не найдена. Меню:", reply_markup=main_menu())
        return ConversationHandler.END

    key, label = FIELDS[field_i]
    raw = update.message.text.strip()

    # Валидация
    if key == "extra_section":
        b = parse_bool_ru(raw)
        if b is None:
            await update.message.reply_text("Введи **да** или **нет**.", parse_mode="Markdown", reply_markup=input_menu_kb())
            return ASK_VALUE
        st["sections"][idx][key] = b
    else:
        try:
            val = int(raw)
            if val < 0:
                raise ValueError
            st["sections"][idx][key] = val
        except ValueError:
            await update.message.reply_text("Нужно целое число (например: 2000).", reply_markup=input_menu_kb())
            return ASK_VALUE

    # Следующее поле
    field_i += 1
    if field_i >= len(FIELDS):
        st["editing"] = None
        save_db(db)
        s = Section(**st["sections"][idx])
        await update.message.reply_text(
            "Готово ✅\n\n" + format_section(s, idx),
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )
        return ConversationHandler.END

    st["editing"]["field_i"] = field_i
    save_db(db)

    _, next_label = FIELDS[field_i]
    await update.message.reply_text(
        f"Теперь введи: **{next_label}**",
        parse_mode="Markdown",
        reply_markup=input_menu_kb(),  # ✅ кнопки всегда видны
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
                CallbackQueryHandler(on_menu_click),  # ✅ чтобы работали Назад/Сброс во время ввода
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
