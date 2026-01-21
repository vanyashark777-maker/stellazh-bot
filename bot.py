import os
import math
import logging
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -------------------- ЛОГИ --------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("stellazhbot")

# -------------------- НАСТРОЙКИ / ЕДИНИЦЫ --------------------
# Все размеры вводим в мм (так проще для расчёта материалов)
MM_IN_M = 1000.0

# -------------------- ДАННЫЕ КАЛЬКУЛЯТОРА --------------------
@dataclass
class CalcInput:
    # геометрия
    height_mm: int = 2000     # высота
    width_mm: int = 1000      # ширина секции
    depth_mm: int = 400       # глубина
    sections: int = 1         # количество секций (рядом)
    levels: int = 5           # уровней (полок)
    # материалы / конструкция
    shelf_thickness_mm: int = 16   # толщина полки (ЛДСП)
    post_type: str = "metal"       # metal/wood
    # расчётные допуски
    waste_percent: float = 7.0     # запас на отходы (%)
    # цена (можно менять)
    price_post: float = 450.0      # цена 1 стойки
    price_beam: float = 250.0      # цена 1 балки (перемычки)
    price_shelf: float = 600.0     # цена 1 полки
    price_fasteners_pack: float = 200.0  # крепёж (условный комплект)
    fasteners_per_section: int = 1       # комплектов крепежа на секцию

@dataclass
class CalcResult:
    posts: int
    beams: int
    shelves: int
    total_weight_est_kg: float
    price_total: float

# -------------------- СОСТОЯНИЕ ПОЛЬЗОВАТЕЛЕЙ --------------------
# user_id -> {"step": str, "data": CalcInput}
USER: Dict[int, Dict[str, Any]] = {}

# Шаги диалога
STEP_NONE = "none"
STEP_HEIGHT = "height"
STEP_WIDTH = "width"
STEP_DEPTH = "depth"
STEP_SECTIONS = "sections"
STEP_LEVELS = "levels"
STEP_THICKNESS = "thickness"
STEP_WASTE = "waste"
STEP_PRICES = "prices"   # редактирование цен (по желанию)

# -------------------- МЕНЮ --------------------
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧮 Калькулятор", callback_data="sec:calc")],
            [InlineKeyboardButton("⚙️ Параметры", callback_data="sec:params")],
            [InlineKeyboardButton("ℹ️ О боте", callback_data="sec:about")],
        ]
    )

def calc_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✍️ Ввести размеры", callback_data="calc:edit")],
            [InlineKeyboardButton("📌 Быстрый расчёт", callback_data="calc:run")],
            [InlineKeyboardButton("🔁 Сброс", callback_data="calc:reset")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="sec:start")],
        ]
    )

def params_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("♻️ Запас/отходы (%)", callback_data="par:waste")],
            [InlineKeyboardButton("📏 Толщина полки (мм)", callback_data="par:thickness")],
            [InlineKeyboardButton("💰 Цены", callback_data="par:prices")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="sec:start")],
        ]
    )

def back_to_calc_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="sec:calc")]])

def ensure_user(user_id: int) -> None:
    if user_id not in USER:
        USER[user_id] = {"step": STEP_NONE, "data": CalcInput()}

def set_step(user_id: int, step: str) -> None:
    ensure_user(user_id)
    USER[user_id]["step"] = step

def get_step(user_id: int) -> str:
    ensure_user(user_id)
    return USER[user_id]["step"]

def get_data(user_id: int) -> CalcInput:
    ensure_user(user_id)
    return USER[user_id]["data"]

def reset_user(user_id: int) -> None:
    USER[user_id] = {"step": STEP_NONE, "data": CalcInput()}

# -------------------- РАСЧЁТ (ЛОГИКА) --------------------
def calc_stellazh(inp: CalcInput) -> CalcResult:
    """
    Примерная модель:
    - Стойки: для каждой секции 2 передние + 2 задние = 4, но между секциями стойки могут делиться
      Упрощение: posts = (sections + 1) * 2 * 2? Нет.
      Нормально для ряда секций: стойки по ширине "общие": (sections + 1) * 2 (перед/зад)
      Итого posts = (sections + 1) * 2 (перед/зад)
    - Балки (перемычки): на каждый уровень 2 балки спереди и 2 сзади (вдоль ширины секции)
      beams = sections * levels * 4
    - Полки: shelves = sections * levels
    """
    sections = max(1, int(inp.sections))
    levels = max(1, int(inp.levels))

    posts = (sections + 1) * 2  # (sections+1) стоек по ширине, и 2 ряда (перед/зад)
    beams = sections * levels * 4
    shelves = sections * levels

    # Пример оценки веса (очень грубо, чтобы было что-то):
    # полка ЛДСП: плотность ~ 650 кг/м3, объём = L*D*T
    density_ldsp = 650.0  # kg/m3
    width_m = inp.width_mm / MM_IN_M
    depth_m = inp.depth_mm / MM_IN_M
    thick_m = inp.shelf_thickness_mm / MM_IN_M
    one_shelf_kg = width_m * depth_m * thick_m * density_ldsp
    total_shelves_kg = one_shelf_kg * shelves

    # стойки/балки оценим условно
    post_kg = 2.2 if inp.post_type == "metal" else 1.5
    beam_kg = 0.8
    total_weight = total_shelves_kg + posts * post_kg + beams * beam_kg

    # запас/отходы применим к весу как к "материалу"
    total_weight *= (1.0 + inp.waste_percent / 100.0)

    # Стоимость
    fasteners_packs = sections * max(1, int(inp.fasteners_per_section))
    price_total = (
        posts * inp.price_post
        + beams * inp.price_beam
        + shelves * inp.price_shelf
        + fasteners_packs * inp.price_fasteners_pack
    )
    price_total *= (1.0 + inp.waste_percent / 100.0)

    return CalcResult(
        posts=posts,
        beams=beams,
        shelves=shelves,
        total_weight_est_kg=round(total_weight, 2),
        price_total=round(price_total, 2),
    )

def format_current(inp: CalcInput) -> str:
    return (
        "Текущие параметры:\n"
        f"• Высота: {inp.height_mm} мм\n"
        f"• Ширина секции: {inp.width_mm} мм\n"
        f"• Глубина: {inp.depth_mm} мм\n"
        f"• Секций: {inp.sections}\n"
        f"• Уровней/полок: {inp.levels}\n"
        f"• Толщина полки: {inp.shelf_thickness_mm} мм\n"
        f"• Запас/отходы: {inp.waste_percent}%\n\n"
        "Цены:\n"
        f"• Стойка: {inp.price_post}\n"
        f"• Балка: {inp.price_beam}\n"
        f"• Полка: {inp.price_shelf}\n"
        f"• Крепёж (комплект): {inp.price_fasteners_pack}\n"
    )

def format_result(res: CalcResult) -> str:
    return (
        "✅ Результат расчёта:\n"
        f"• Стоек: {res.posts}\n"
        f"• Балок: {res.beams}\n"
        f"• Полок: {res.shelves}\n"
        f"• Оценка веса: {res.total_weight_est_kg} кг\n"
        f"• Итоговая стоимость: {res.price_total} ₽\n"
    )

# -------------------- УТИЛИТЫ ВВОДА --------------------
def parse_int(text: str) -> Optional[int]:
    t = text.strip().replace(" ", "")
    if not t:
        return None
    try:
        return int(float(t))  # чтобы "2000.0" тоже прошло
    except ValueError:
        return None

def parse_float(text: str) -> Optional[float]:
    t = text.strip().replace(",", ".")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None

def parse_prices_line(text: str) -> Optional[Dict[str, float]]:
    """
    Ожидаем: 4 числа через пробел:
    стойка балка полка крепёж
    Пример: 450 250 600 200
    """
    raw = text.replace(",", " ").split()
    if len(raw) != 4:
        return None
    try:
        p1, p2, p3, p4 = map(float, raw)
        return {"post": p1, "beam": p2, "shelf": p3, "fast": p4}
    except ValueError:
        return None

# -------------------- ХЕНДЛЕРЫ --------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    await update.message.reply_text("Выбери раздел 👇", reply_markup=main_menu_kb())

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n"
        "/start — меню\n"
        "/help — помощь\n"
        "/reset — сброс параметров\n\n"
        "В калькуляторе вводи числа, когда бот просит."
    )

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reset_user(user_id)
    await update.message.reply_text("✅ Сбросил параметры.", reply_markup=main_menu_kb())

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    ensure_user(user_id)

    data = query.data or ""
    inp = get_data(user_id)

    # -------- СЕКЦИИ МЕНЮ --------
    if data.startswith("sec:"):
        sec = data.split(":", 1)[1]

        if sec == "start":
            set_step(user_id, STEP_NONE)
            await query.edit_message_text("Выбери раздел 👇", reply_markup=main_menu_kb())
            return

        if sec == "about":
            set_step(user_id, STEP_NONE)
            await query.edit_message_text(
                "ℹ️ Бот-калькулятор стеллажей.\n"
                "Сейчас он считает материалы/стоимость по параметрам.\n"
                "Дальше можем точнее под твою модель.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="sec:start")]]),
            )
            return

        if sec == "params":
            set_step(user_id, STEP_NONE)
            await query.edit_message_text("⚙️ Настройки:", reply_markup=params_menu_kb())
            return

        if sec == "calc":
            set_step(user_id, STEP_NONE)
            text = "🧮 Калькулятор\n\n" + format_current(inp)
            await query.edit_message_text(text, reply_markup=calc_menu_kb())
            return

    # -------- КАЛЬКУЛЯТОР --------
    if data == "calc:reset":
        reset_user(user_id)
        inp = get_data(user_id)
        text = "✅ Сбросил.\n\n" + format_current(inp)
        await query.edit_message_text(text, reply_markup=calc_menu_kb())
        return

    if data == "calc:run":
        res = calc_stellazh(inp)
        await query.edit_message_text(
            "🧮 Калькулятор\n\n" + format_current(inp) + "\n" + format_result(res),
            reply_markup=calc_menu_kb(),
        )
        return

    if data == "calc:edit":
        set_step(user_id, STEP_HEIGHT)
        await query.edit_message_text(
            "Введи ВЫСОТУ (мм). Пример: 2000",
            reply_markup=back_to_calc_kb(),
        )
        return

    # -------- ПАРАМЕТРЫ --------
    if data == "par:waste":
        set_step(user_id, STEP_WASTE)
        await query.edit_message_text("Введи запас/отходы (%). Пример: 7", reply_markup=back_to_calc_kb())
        return

    if data == "par:thickness":
        set_step(user_id, STEP_THICKNESS)
        await query.edit_message_text("Введи толщину полки (мм). Пример: 16", reply_markup=back_to_calc_kb())
        return

    if data == "par:prices":
        set_step(user_id, STEP_PRICES)
        await query.edit_message_text(
            "Введи 4 цены через пробел:\n"
            "стойка балка полка крепёж\n"
            "Пример: 450 250 600 200",
            reply_markup=back_to_calc_kb(),
        )
        return

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    step = get_step(user_id)
    inp = get_data(user_id)

    text = (update.message.text or "").strip()

    # Ничего не ждём — игнорируем (или можно подсказать /start)
    if step == STEP_NONE:
        return

    # --- пошаговый ввод размеров ---
    if step == STEP_HEIGHT:
        v = parse_int(text)
        if not v or v <= 0:
            await update.message.reply_text("Нужно число > 0. Пример: 2000")
            return
        inp.height_mm = v
        set_step(user_id, STEP_WIDTH)
        await update.message.reply_text("Ок. Теперь введи ШИРИНУ секции (мм). Пример: 1000")
        return

    if step == STEP_WIDTH:
        v = parse_int(text)
        if not v or v <= 0:
            await update.message.reply_text("Нужно число > 0. Пример: 1000")
            return
        inp.width_mm = v
        set_step(user_id, STEP_DEPTH)
        await update.message.reply_text("Теперь введи ГЛУБИНУ (мм). Пример: 400")
        return

    if step == STEP_DEPTH:
        v = parse_int(text)
        if not v or v <= 0:
            await update.message.reply_text("Нужно число > 0. Пример: 400")
            return
        inp.depth_mm = v
        set_step(user_id, STEP_SECTIONS)
        await update.message.reply_text("Сколько СЕКЦИЙ (шт)? Пример: 3")
        return

    if step == STEP_SECTIONS:
        v = parse_int(text)
        if not v or v <= 0:
            await update.message.reply_text("Нужно число > 0. Пример: 3")
            return
        inp.sections = v
        set_step(user_id, STEP_LEVELS)
        await update.message.reply_text("Сколько УРОВНЕЙ/ПОЛОК (шт)? Пример: 5")
        return

    if step == STEP_LEVELS:
        v = parse_int(text)
        if not v or v <= 0:
            await update.message.reply_text("Нужно число > 0. Пример: 5")
            return
        inp.levels = v
        set_step(user_id, STEP_NONE)
        res = calc_stellazh(inp)
        await update.message.reply_text("✅ Принято.\n\n" + format_result(res))
        return

    # --- параметры ---
    if step == STEP_THICKNESS:
        v = parse_int(text)
        if not v or v <= 0:
            await update.message.reply_text("Нужно число > 0. Пример: 16")
            return
        inp.shelf_thickness_mm = v
        set_step(user_id, STEP_NONE)
        await update.message.reply_text("✅ Толщина сохранена.\n\n" + format_current(inp))
        return

    if step == STEP_WASTE:
        v = parse_float(text)
        if v is None or v < 0 or v > 80:
            await update.message.reply_text("Введи % от 0 до 80. Пример: 7")
            return
        inp.waste_percent = float(v)
        set_step(user_id, STEP_NONE)
        await update.message.reply_text("✅ Запас сохранён.\n\n" + format_current(inp))
        return

    if step == STEP_PRICES:
        prices = parse_prices_line(text)
        if not prices:
            await update.message.reply_text(
                "Нужно 4 числа: стойка балка полка крепёж\n"
                "Пример: 450 250 600 200"
            )
            return
        inp.price_post = prices["post"]
        inp.price_beam = prices["beam"]
        inp.price_shelf = prices["shelf"]
        inp.price_fasteners_pack = prices["fast"]
        set_step(user_id, STEP_NONE)
        await update.message.reply_text("✅ Цены сохранены.\n\n" + format_current(inp))
        return

# -------------------- MAIN --------------------
def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не задан. В CMD: set BOT_TOKEN=...")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reset", cmd_reset))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    logger.info("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
