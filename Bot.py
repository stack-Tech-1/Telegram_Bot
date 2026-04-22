"""
Telegram Background Replacement Bot
=====================================
Features:
  - Remove background from any photo
  - Replace with a solid color
  - Replace with a preset background (beach, office, studio, etc.)
  - Replace with a custom image the user sends

Requirements:
  pip install python-telegram-bot rembg Pillow aiohttp aiofiles

Usage:
  1. Set your BOT_TOKEN in config.py (or as an environment variable)
  2. Run: python bot.py
"""

import os
import io
import logging
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from PIL import Image
from rembg import remove

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "8747707317:AAG8BGaiU0HRMm-YpRk4hEfIQ0iHsZAhLEc")   # <-- put your token here

PRESETS_DIR = Path("presets")          # folder with preset background images
PRESETS_DIR.mkdir(exist_ok=True)

# ── Conversation states ────────────────────────────────────────────────────────
WAITING_FOR_PHOTO      = 0   # user hasn't sent a photo yet
CHOOSING_BG_TYPE       = 1   # user picks: color / preset / custom
WAITING_FOR_COLOR      = 2   # user types a hex color or color name
WAITING_FOR_CUSTOM_BG  = 3   # user sends their own background image

# ── Color name → hex map (common names) ───────────────────────────────────────
COLOR_NAMES = {
    "white":   (255, 255, 255),
    "black":   (0,   0,   0  ),
    "gray":    (128, 128, 128),
    "grey":    (128, 128, 128),
    "red":     (255, 0,   0  ),
    "green":   (0,   200, 0  ),
    "blue":    (0,   0,   255),
    "yellow":  (255, 255, 0  ),
    "orange":  (255, 165, 0  ),
    "purple":  (128, 0,   128),
    "pink":    (255, 182, 193),
    "cyan":    (0,   255, 255),
    "navy":    (0,   0,   128),
    "brown":   (139, 69,  19 ),
    "beige":   (245, 245, 220),
    "cream":   (255, 253, 208),
}

# ── Preset backgrounds ─────────────────────────────────────────────────────────
# Add images named exactly as below into the `presets/` folder.
# If an image is missing, a colored placeholder is used instead.
PRESETS = {
    "🏖️ Beach":        ("beach.jpg",   (135, 206, 235)),
    "🏢 Office":        ("office.jpg",  (200, 210, 220)),
    "🌿 Nature":        ("nature.jpg",  (34,  139, 34 )),
    "🌆 City":          ("city.jpg",    (70,  70,  90 )),
    "⬜ White Studio":  ("white.jpg",   (255, 255, 255)),
    "⬛ Black Studio":  ("black.jpg",   (20,  20,  20 )),
    "🌅 Sunset":        ("sunset.jpg",  (255, 140, 50 )),
    "🔵 Gradient Blue": ("blue_grad.jpg",(100, 149, 237)),
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def parse_color(text: str) -> tuple[int, int, int] | None:
    """Return (R,G,B) from a color name or #RRGGBB hex string, or None."""
    text = text.strip().lower()
    if text in COLOR_NAMES:
        return COLOR_NAMES[text]
    text = text.lstrip("#")
    if len(text) == 6:
        try:
            r = int(text[0:2], 16)
            g = int(text[2:4], 16)
            b = int(text[4:6], 16)
            return (r, g, b)
        except ValueError:
            pass
    return None


def remove_background(image_bytes: bytes) -> Image.Image:
    """Strip the background and return an RGBA PIL image."""
    result_bytes = remove(image_bytes)
    return Image.open(io.BytesIO(result_bytes)).convert("RGBA")


def composite(foreground: Image.Image, background: Image.Image) -> bytes:
    """Paste the foreground (RGBA, no bg) onto background; return JPEG bytes."""
    bg = background.convert("RGBA").resize(foreground.size, Image.LANCZOS)
    composite_img = Image.alpha_composite(bg, foreground)
    output = io.BytesIO()
    composite_img.convert("RGB").save(output, format="JPEG", quality=92)
    output.seek(0)
    return output.read()


def solid_color_background(size: tuple[int, int], color: tuple[int, int, int]) -> Image.Image:
    bg = Image.new("RGBA", size, color + (255,))
    return bg


def load_preset(preset_name: str, size: tuple[int, int]) -> Image.Image:
    filename, fallback_color = PRESETS[preset_name]
    path = PRESETS_DIR / filename
    if path.exists():
        img = Image.open(path).convert("RGBA")
        img = img.resize(size, Image.LANCZOS)
        return img
    # Fallback: solid color if image file not found
    return solid_color_background(size, fallback_color)


# ── Command handlers ───────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "👋 *Welcome to the Background Replacer Bot!*\n\n"
        "Send me any photo and I'll remove its background and let you replace it "
        "with a solid color, a preset scene, or your own custom image.\n\n"
        "📸 *Send a photo to get started!*",
        parse_mode="Markdown",
    )
    return WAITING_FOR_PHOTO


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 *How to use this bot:*\n\n"
        "1. Send any photo\n"
        "2. Choose a background type:\n"
        "   • 🎨 *Color* — type a color name or hex code (e.g. `blue` or `#FF5733`)\n"
        "   • 🖼️ *Preset* — pick from built-in backgrounds\n"
        "   • 📤 *Custom* — send your own background image\n"
        "3. Receive your edited photo!\n\n"
        "Use /start to restart at any time.",
        parse_mode="Markdown",
    )


# ── Photo received ─────────────────────────────────────────────────────────────

async def photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User sent a photo — download it and ask what background they want."""
    msg = await update.message.reply_text("⏳ Downloading your photo…")

    photo = update.message.photo[-1]           # highest resolution
    file  = await photo.get_file()
    image_bytes = await file.download_as_bytearray()

    context.user_data["original_bytes"] = bytes(image_bytes)

    await msg.edit_text("🔍 Got it! What type of background would you like?")

    keyboard = [
        [InlineKeyboardButton("🎨 Solid Color",   callback_data="type_color")],
        [InlineKeyboardButton("🖼️ Preset Scene",  callback_data="type_preset")],
        [InlineKeyboardButton("📤 My Own Image",  callback_data="type_custom")],
    ]
    await update.message.reply_text(
        "Choose a background type:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_BG_TYPE


# ── Background type chosen ─────────────────────────────────────────────────────

async def bg_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data   # "type_color" | "type_preset" | "type_custom"

    if choice == "type_color":
        await query.edit_message_text(
            "🎨 Type a color name (e.g. `blue`, `white`, `black`) or a hex code (e.g. `#FF5733`):",
            parse_mode="Markdown",
        )
        return WAITING_FOR_COLOR

    elif choice == "type_preset":
        buttons = [
            [InlineKeyboardButton(name, callback_data=f"preset_{name}")]
            for name in PRESETS.keys()
        ]
        await query.edit_message_text(
            "🖼️ Choose a preset background:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return CHOOSING_BG_TYPE   # stay here to catch preset callback

    elif choice == "type_custom":
        await query.edit_message_text("📤 Send me the image you want to use as the background:")
        return WAITING_FOR_CUSTOM_BG

    return CHOOSING_BG_TYPE


async def preset_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    preset_name = query.data.replace("preset_", "", 1)

    if preset_name not in PRESETS:
        await query.edit_message_text("❌ Unknown preset. Please try again with /start.")
        return ConversationHandler.END

    await query.edit_message_text(f"✨ Applying *{preset_name}* background… please wait.", parse_mode="Markdown")

    image_bytes = context.user_data.get("original_bytes")
    if not image_bytes:
        await query.message.reply_text("❌ Original photo not found. Please start over with /start.")
        return ConversationHandler.END

    try:
        fg    = remove_background(image_bytes)
        bg    = load_preset(preset_name, fg.size)
        final = composite(fg, bg)
        await query.message.reply_photo(photo=final, caption=f"✅ Background replaced with {preset_name}!")
    except Exception as e:
        logger.error(f"Error applying preset: {e}")
        await query.message.reply_text("❌ Something went wrong. Please try again.")

    return ConversationHandler.END


# ── Color path ─────────────────────────────────────────────────────────────────

async def color_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    color = parse_color(update.message.text or "")
    if not color:
        await update.message.reply_text(
            "❌ Couldn't understand that color. Try a name like `blue` or a hex like `#3498DB`:",
            parse_mode="Markdown",
        )
        return WAITING_FOR_COLOR

    msg = await update.message.reply_text("✨ Removing background and applying color…")
    image_bytes = context.user_data.get("original_bytes")
    if not image_bytes:
        await msg.edit_text("❌ Original photo not found. Please start over with /start.")
        return ConversationHandler.END

    try:
        fg    = remove_background(image_bytes)
        bg    = solid_color_background(fg.size, color)
        final = composite(fg, bg)
        hex_str = "#{:02X}{:02X}{:02X}".format(*color)
        await update.message.reply_photo(photo=final, caption=f"✅ Background replaced with {hex_str}!")
        await msg.delete()
    except Exception as e:
        logger.error(f"Error applying color: {e}")
        await msg.edit_text("❌ Something went wrong. Please try again.")

    return ConversationHandler.END


# ── Custom background path ─────────────────────────────────────────────────────

async def custom_bg_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo and not update.message.document:
        await update.message.reply_text("Please send an image file as a photo or document.")
        return WAITING_FOR_CUSTOM_BG

    msg = await update.message.reply_text("✨ Processing… this may take a moment.")

    # Download background image
    if update.message.photo:
        bg_file = await update.message.photo[-1].get_file()
    else:
        bg_file = await update.message.document.get_file()
    bg_bytes = await bg_file.download_as_bytearray()

    image_bytes = context.user_data.get("original_bytes")
    if not image_bytes:
        await msg.edit_text("❌ Original photo not found. Please start over with /start.")
        return ConversationHandler.END

    try:
        fg         = remove_background(image_bytes)
        bg_img     = Image.open(io.BytesIO(bytes(bg_bytes))).convert("RGBA")
        final      = composite(fg, bg_img)
        await update.message.reply_photo(photo=final, caption="✅ Background replaced with your custom image!")
        await msg.delete()
    except Exception as e:
        logger.error(f"Error applying custom bg: {e}")
        await msg.edit_text("❌ Something went wrong. Please try again.")

    return ConversationHandler.END


# ── Cancel ─────────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Cancelled. Send /start to begin again.")
    context.user_data.clear()
    return ConversationHandler.END


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.PHOTO, photo_received),
        ],
        states={
            WAITING_FOR_PHOTO: [
                MessageHandler(filters.PHOTO, photo_received),
            ],
            CHOOSING_BG_TYPE: [
                CallbackQueryHandler(bg_type_chosen,  pattern="^type_"),
                CallbackQueryHandler(preset_chosen,   pattern="^preset_"),
            ],
            WAITING_FOR_COLOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, color_received),
            ],
            WAITING_FOR_CUSTOM_BG: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, custom_bg_received),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("help", help_cmd))

    logger.info("Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()