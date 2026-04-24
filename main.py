"""
✨ AI Background Studio Bot — Batch Edition
=============================================
Features:
  - Upload up to 100 photos to a personal library
  - Configure settings (prompt, aspect ratio, placement, scale, filter, versions)
  - One-tap batch generation: all photos × N versions with unique AI backgrounds
  - Export/import settings templates to share with workers
  - Parallel generation for speed
"""

import os
import io
import time
import json
import asyncio
import logging
import hashlib
import urllib.parse
import requests
from PIL import Image, ImageFilter, ImageEnhance
from rembg import remove
from dotenv import load_dotenv

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

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN is missing!")

# Authorized user IDs (empty = allow everyone, fill to restrict)
# Example: ALLOWED_USERS = {123456789, 987654321}
ALLOWED_USERS: set = set()

MAX_PHOTOS = 100
MAX_PARALLEL = 5  # generate this many backgrounds at once

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Conversation States ────────────────────────────────────────────────────────
(
    MAIN_MENU,
    UPLOADING_PHOTOS,
    IN_SETTINGS,
    SETTINGS_PROMPT,
    SETTINGS_ASPECT,
    SETTINGS_PLACEMENT,
    SETTINGS_SCALE,
    SETTINGS_FILTER,
    SETTINGS_VERSIONS,
    SETTINGS_REMOVE_BG,
    IMPORT_TEMPLATE,
) = range(11)

# ── Defaults ───────────────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "prompt":     "professional studio background with soft bokeh",
    "aspect":     "1:1",
    "placement":  "bottom_center",
    "scale":      "large",
    "filter":     "none",
    "versions":   2,
    "remove_bg":  True,
}

ASPECT_RATIOS = {
    "1:1":  (1024, 1024),
    "4:3":  (1024, 768),
    "16:9": (1024, 576),
    "9:16": (576,  1024),
    "3:4":  (768,  1024),
}

PLACEMENTS = {
    "center":        "⬛ Center",
    "bottom_center": "⬇️ Bottom Center",
    "bottom_left":   "↙️ Bottom Left",
    "bottom_right":  "↘️ Bottom Right",
    "top_center":    "⬆️ Top Center",
}

SCALES = {
    "small":  0.40,
    "medium": 0.60,
    "large":  0.80,
    "full":   0.95,
}

FILTERS = {
    "none":      "🎨 None",
    "warm":      "🔆 Warm",
    "cool":      "❄️ Cool",
    "cinematic": "🎬 Cinematic",
    "vintage":   "📷 Vintage",
    "sharp":     "✨ Sharp",
    "soft":      "🌸 Soft",
    "grayscale": "⬛ Grayscale",
}


# ── Per-user data helpers ──────────────────────────────────────────────────────
# Stored in context.bot_data keyed by user_id (persists for session)

def get_user_photos(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> list:
    return context.bot_data.setdefault(f"photos_{user_id}", [])

def get_user_settings(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> dict:
    stored = context.bot_data.get(f"settings_{user_id}", {})
    return {**DEFAULT_SETTINGS, **stored}

def save_user_settings(context: ContextTypes.DEFAULT_TYPE, user_id: int, settings: dict):
    context.bot_data[f"settings_{user_id}"] = settings


# ── Image Processing ───────────────────────────────────────────────────────────

def remove_background(image_bytes: bytes) -> Image.Image:
    result = remove(image_bytes)
    return Image.open(io.BytesIO(result)).convert("RGBA")


def generate_background(prompt: str, width: int, height: int, seed: int = None) -> Image.Image:
    if seed is None:
        seed = int(time.time() * 1000) % 999999
    full_prompt = prompt + ", high quality, photorealistic, scenic, no people, no humans, background only"
    encoded = urllib.parse.quote(full_prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={min(width, 1024)}&height={min(height, 1024)}"
        f"&nologo=true&enhance=true&seed={seed}"
    )
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGBA")


def apply_filter(img: Image.Image, filter_name: str) -> Image.Image:
    img = img.convert("RGB")
    if filter_name == "warm":
        r, g, b = img.split()
        r = ImageEnhance.Brightness(r).enhance(1.15)
        b = ImageEnhance.Brightness(b).enhance(0.85)
        img = Image.merge("RGB", (r, g, b))
    elif filter_name == "cool":
        r, g, b = img.split()
        r = ImageEnhance.Brightness(r).enhance(0.85)
        b = ImageEnhance.Brightness(b).enhance(1.15)
        img = Image.merge("RGB", (r, g, b))
    elif filter_name == "cinematic":
        img = ImageEnhance.Contrast(img).enhance(1.3)
        img = ImageEnhance.Color(img).enhance(0.85)
        r, g, b = img.split()
        r = ImageEnhance.Brightness(r).enhance(1.05)
        b = ImageEnhance.Brightness(b).enhance(0.92)
        img = Image.merge("RGB", (r, g, b))
    elif filter_name == "vintage":
        img = ImageEnhance.Color(img).enhance(0.6)
        img = ImageEnhance.Contrast(img).enhance(0.9)
        img = ImageEnhance.Brightness(img).enhance(1.1)
    elif filter_name == "sharp":
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    elif filter_name == "soft":
        img = img.filter(ImageFilter.GaussianBlur(radius=0.8))
        img = ImageEnhance.Brightness(img).enhance(1.05)
    elif filter_name == "grayscale":
        img = img.convert("L").convert("RGB")
    return img.convert("RGBA")


def get_paste_position(bg_size, fg_size, placement):
    bw, bh = bg_size
    fw, fh = fg_size
    positions = {
        "center":        ((bw - fw) // 2, (bh - fh) // 2),
        "bottom_center": ((bw - fw) // 2, bh - fh - 20),
        "bottom_left":   (20,             bh - fh - 20),
        "bottom_right":  (bw - fw - 20,   bh - fh - 20),
        "top_center":    ((bw - fw) // 2, 20),
    }
    return positions.get(placement, positions["center"])


def composite(subject: Image.Image, background: Image.Image,
              placement: str, scale: float, filter_name: str) -> bytes:
    bg = background.convert("RGBA")
    ow, oh = subject.size
    max_dim = int(min(bg.size) * scale)
    ratio = min(max_dim / ow, max_dim / oh)
    subject = subject.resize((int(ow * ratio), int(oh * ratio)), Image.LANCZOS)
    bg = apply_filter(bg, filter_name)
    x, y = get_paste_position(bg.size, subject.size, placement)
    result = bg.copy()
    result.paste(subject, (x, y), subject)
    out = io.BytesIO()
    result.convert("RGB").save(out, format="JPEG", quality=93)
    out.seek(0)
    return out.read()


# ── Settings template encoding ─────────────────────────────────────────────────

def encode_template(settings: dict) -> str:
    """Encode settings to a short shareable string."""
    data = json.dumps(settings, separators=(",", ":"))
    encoded = urllib.parse.quote(data)
    # Create a short checksum
    checksum = hashlib.md5(data.encode()).hexdigest()[:4].upper()
    return f"TMPL-{checksum}-{encoded}"


def decode_template(template_str: str) -> dict | None:
    """Decode a settings template string."""
    try:
        if not template_str.startswith("TMPL-"):
            return None
        parts = template_str.split("-", 2)
        if len(parts) != 3:
            return None
        encoded = parts[2]
        data = urllib.parse.unquote(encoded)
        settings = json.loads(data)
        # Validate keys
        for key in DEFAULT_SETTINGS:
            if key not in settings:
                return None
        return settings
    except Exception:
        return None


# ── Keyboards ──────────────────────────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Upload Photos", callback_data="menu_upload")],
        [InlineKeyboardButton("⚡ Generate Unique Photos", callback_data="menu_generate")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings")],
    ])


def settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Background Style",  callback_data="set_prompt")],
        [InlineKeyboardButton("📐 Aspect Ratio",      callback_data="set_aspect")],
        [InlineKeyboardButton("📍 Placement",          callback_data="set_placement")],
        [InlineKeyboardButton("📏 Scale",              callback_data="set_scale")],
        [InlineKeyboardButton("✨ Filter",             callback_data="set_filter")],
        [InlineKeyboardButton("🔢 Versions per Photo", callback_data="set_versions")],
        [InlineKeyboardButton("✂️ Remove Background",  callback_data="set_removebg")],
        [InlineKeyboardButton("📤 Export Template",    callback_data="set_export")],
        [InlineKeyboardButton("📥 Import Template",    callback_data="set_import")],
        [InlineKeyboardButton("◀️ Back to Menu",       callback_data="menu_back")],
    ])


# ── Handlers ───────────────────────────────────────────────────────────────────

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return ConversationHandler.END

    photos = get_user_photos(context, user_id)
    settings = get_user_settings(context, user_id)

    await update.message.reply_text(
        "✨ *AI Background Studio*\n\n"
        f"📸 Photos in library: *{len(photos)}/{MAX_PHOTOS}*\n"
        f"⚙️ Style: _{settings['prompt'][:40]}..._\n"
        f"🔢 Versions per photo: *{settings['versions']}*\n"
        f"✂️ Remove background: *{'Yes' if settings['remove_bg'] else 'No'}*\n\n"
        "Choose an action:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return MAIN_MENU


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "menu_upload":
        photos = get_user_photos(context, user_id)
        remaining = MAX_PHOTOS - len(photos)
        await query.edit_message_text(
            f"📸 *Upload Photos*\n\n"
            f"Library: *{len(photos)}/{MAX_PHOTOS}* photos\n"
            f"You can upload *{remaining}* more.\n\n"
            "Send your photos now (one by one or as an album).\n"
            "When done, send /done to return to the menu.\n\n"
            "_Send /clear to clear your entire library._",
            parse_mode="Markdown",
        )
        return UPLOADING_PHOTOS

    elif query.data == "menu_generate":
        photos = get_user_photos(context, user_id)
        if not photos:
            await query.edit_message_text(
                "❌ *No photos in library!*\n\n"
                "Please upload photos first.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📸 Upload Photos", callback_data="menu_upload"),
                ]]),
            )
            return MAIN_MENU

        settings = get_user_settings(context, user_id)
        total = len(photos) * settings["versions"]
        await query.edit_message_text(
            f"⚡ *Starting batch generation!*\n\n"
            f"📸 Photos: *{len(photos)}*\n"
            f"🔢 Versions each: *{settings['versions']}*\n"
            f"🎯 Total to generate: *{total}*\n\n"
            f"🎨 Style: _{settings['prompt'][:50]}_\n\n"
            "⏳ This will take a while. I'll send photos as they complete!",
            parse_mode="Markdown",
        )
        # Run generation in background
        asyncio.create_task(run_batch_generation(query.message, context, user_id))
        return MAIN_MENU

    elif query.data == "menu_settings":
        settings = get_user_settings(context, user_id)
        await query.edit_message_text(
            f"⚙️ *Current Settings*\n\n"
            f"🎨 Style: _{settings['prompt'][:50]}_\n"
            f"📐 Aspect: *{settings['aspect']}*\n"
            f"📍 Placement: *{PLACEMENTS[settings['placement']]}*\n"
            f"📏 Scale: *{settings['scale'].capitalize()}*\n"
            f"✨ Filter: *{FILTERS[settings['filter']]}*\n"
            f"🔢 Versions: *{settings['versions']}*\n"
            f"✂️ Remove BG: *{'Yes' if settings['remove_bg'] else 'No'}*\n\n"
            "What would you like to change?",
            parse_mode="Markdown",
            reply_markup=settings_keyboard(),
        )
        return IN_SETTINGS

    elif query.data == "menu_back":
        photos = get_user_photos(context, user_id)
        settings = get_user_settings(context, user_id)
        await query.edit_message_text(
            "✨ *AI Background Studio*\n\n"
            f"📸 Photos in library: *{len(photos)}/{MAX_PHOTOS}*\n"
            f"⚙️ Style: _{settings['prompt'][:40]}..._\n"
            f"🔢 Versions per photo: *{settings['versions']}*\n"
            f"✂️ Remove background: *{'Yes' if settings['remove_bg'] else 'No'}*\n\n"
            "Choose an action:",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
        return MAIN_MENU

    return MAIN_MENU


# ── Upload Photos ──────────────────────────────────────────────────────────────

async def photo_upload_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    photos = get_user_photos(context, user_id)

    if len(photos) >= MAX_PHOTOS:
        await update.message.reply_text(
            f"❌ Library is full ({MAX_PHOTOS} photos max).\n"
            "Send /clear to clear it first."
        )
        return UPLOADING_PHOTOS

    # Save highest resolution file_id
    file_id = update.message.photo[-1].file_id
    photos.append(file_id)
    context.bot_data[f"photos_{user_id}"] = photos

    await update.message.reply_text(
        f"✅ Photo saved! Library: *{len(photos)}/{MAX_PHOTOS}*\n"
        "_Keep sending photos, or /done when finished._",
        parse_mode="Markdown",
    )
    return UPLOADING_PHOTOS


async def upload_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    photos = get_user_photos(context, user_id)
    await update.message.reply_text(
        f"✅ *Upload complete!*\n\n"
        f"📸 *{len(photos)}* photos saved to your library.\n\n"
        "Choose an action:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return MAIN_MENU


async def clear_library(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    context.bot_data[f"photos_{user_id}"] = []
    await update.message.reply_text(
        "🗑️ Library cleared!\n\nSend photos to upload, or /done to go back.",
    )
    return UPLOADING_PHOTOS


# ── Batch Generation ───────────────────────────────────────────────────────────

async def run_batch_generation(message, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Run all photo generations in parallel batches."""
    photos = get_user_photos(context, user_id)
    settings = get_user_settings(context, user_id)
    width, height = ASPECT_RATIOS[settings["aspect"]]

    total = len(photos) * settings["versions"]
    completed = 0
    failed = 0

    status_msg = await message.reply_text(
        f"⏳ Generating... *0/{total}* complete",
        parse_mode="Markdown",
    )

    # Build all tasks: (file_id, version_number, seed)
    tasks = []
    for file_id in photos:
        for v in range(settings["versions"]):
            seed = int(time.time() * 1000 + len(tasks)) % 999999
            tasks.append((file_id, v + 1, seed))

    # Process in parallel batches
    semaphore = asyncio.Semaphore(MAX_PARALLEL)

    async def process_one(file_id: str, version: int, seed: int):
        nonlocal completed, failed
        async with semaphore:
            try:
                # Download photo from Telegram
                file = await context.bot.get_file(file_id)
                img_bytes = await file.download_as_bytearray()
                img_bytes = bytes(img_bytes)

                # Remove background if needed
                if settings["remove_bg"]:
                    subject = await asyncio.get_event_loop().run_in_executor(
                        None, remove_background, img_bytes
                    )
                else:
                    subject = Image.open(io.BytesIO(img_bytes)).convert("RGBA")

                # Generate background
                bg = await asyncio.get_event_loop().run_in_executor(
                    None, generate_background,
                    settings["prompt"], width, height, seed
                )

                # Composite
                final = await asyncio.get_event_loop().run_in_executor(
                    None, composite, subject, bg,
                    settings["placement"], SCALES[settings["scale"]], settings["filter"]
                )

                # Send result
                caption = f"✅ Photo {photos.index(file_id)+1} · Version {version}"
                await message.reply_photo(photo=final, caption=caption)
                completed += 1

            except Exception as e:
                logger.error(f"Error processing photo: {e}")
                failed += 1
                completed += 1

            # Update status every 3 completions
            if completed % 3 == 0 or completed == total:
                try:
                    await status_msg.edit_text(
                        f"⏳ Generating... *{completed}/{total}* complete"
                        + (f"\n⚠️ {failed} failed" if failed else ""),
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

    # Run all tasks
    await asyncio.gather(*[process_one(fid, v, s) for fid, v, s in tasks])

    # Final summary
    await status_msg.edit_text(
        f"✅ *Generation complete!*\n\n"
        f"📸 *{completed - failed}/{total}* photos generated successfully"
        + (f"\n⚠️ {failed} failed" if failed else "") +
        "\n\nSend /start to go back to the menu.",
        parse_mode="Markdown",
    )


# ── Settings Handlers ──────────────────────────────────────────────────────────

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    settings = get_user_settings(context, user_id)

    if query.data == "set_prompt":
        await query.edit_message_text(
            "🎨 *Background Style*\n\n"
            f"Current: _{settings['prompt']}_\n\n"
            "Type a new background description:\n\n"
            "Examples:\n"
            "• `luxury penthouse rooftop at sunset`\n"
            "• `magical forest with glowing lights`\n"
            "• `futuristic cyberpunk city at night`\n"
            "• `professional studio with soft bokeh`\n"
            "• `tropical beach with turquoise water`",
            parse_mode="Markdown",
        )
        context.user_data["editing"] = "prompt"
        return SETTINGS_PROMPT

    elif query.data == "set_aspect":
        keyboard = [[InlineKeyboardButton(r, callback_data=f"aspect_{r}")] for r in ASPECT_RATIOS]
        keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="back_settings")])
        await query.edit_message_text(
            f"📐 *Aspect Ratio*\n\nCurrent: *{settings['aspect']}*\n\nChoose:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return SETTINGS_ASPECT

    elif query.data == "set_placement":
        keyboard = [[InlineKeyboardButton(label, callback_data=f"place_{k}")] for k, label in PLACEMENTS.items()]
        keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="back_settings")])
        await query.edit_message_text(
            f"📍 *Placement*\n\nCurrent: *{PLACEMENTS[settings['placement']]}*\n\nChoose:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return SETTINGS_PLACEMENT

    elif query.data == "set_scale":
        keyboard = [
            [InlineKeyboardButton("🔹 Small (40%)",  callback_data="scale_small"),
             InlineKeyboardButton("🔷 Medium (60%)", callback_data="scale_medium")],
            [InlineKeyboardButton("🔶 Large (80%)",  callback_data="scale_large"),
             InlineKeyboardButton("🟠 Full (95%)",   callback_data="scale_full")],
            [InlineKeyboardButton("◀️ Back", callback_data="back_settings")],
        ]
        await query.edit_message_text(
            f"📏 *Scale*\n\nCurrent: *{settings['scale'].capitalize()}*\n\nChoose:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return SETTINGS_SCALE

    elif query.data == "set_filter":
        items = list(FILTERS.items())
        keyboard = [
            [InlineKeyboardButton(items[i][1], callback_data=f"filter_{items[i][0]}"),
             InlineKeyboardButton(items[i+1][1], callback_data=f"filter_{items[i+1][0]}")]
            for i in range(0, len(items) - 1, 2)
        ]
        keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="back_settings")])
        await query.edit_message_text(
            f"✨ *Filter*\n\nCurrent: *{FILTERS[settings['filter']]}*\n\nChoose:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return SETTINGS_FILTER

    elif query.data == "set_versions":
        keyboard = [
            [InlineKeyboardButton(str(i), callback_data=f"ver_{i}") for i in range(1, 6)],
            [InlineKeyboardButton(str(i), callback_data=f"ver_{i}") for i in range(6, 11)],
            [InlineKeyboardButton("◀️ Back", callback_data="back_settings")],
        ]
        await query.edit_message_text(
            f"🔢 *Versions per Photo*\n\nCurrent: *{settings['versions']}*\n\n"
            "How many unique versions per photo?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return SETTINGS_VERSIONS

    elif query.data == "set_removebg":
        current = settings["remove_bg"]
        keyboard = [
            [InlineKeyboardButton("✅ Yes — remove background", callback_data="rbg_yes"),
             InlineKeyboardButton("❌ No — keep original",       callback_data="rbg_no")],
            [InlineKeyboardButton("◀️ Back", callback_data="back_settings")],
        ]
        await query.edit_message_text(
            f"✂️ *Remove Background*\n\nCurrent: *{'Yes' if current else 'No'}*\n\n"
            "Should the bot remove the background before placing on new one?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return SETTINGS_REMOVE_BG

    elif query.data == "set_export":
        template = encode_template(settings)
        await query.edit_message_text(
            "📤 *Export Settings Template*\n\n"
            "Share this code with your workers:\n\n"
            f"`{template}`\n\n"
            "They can import it using the Import Template option in Settings.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back to Settings", callback_data="back_settings")
            ]]),
        )
        return IN_SETTINGS

    elif query.data == "set_import":
        await query.edit_message_text(
            "📥 *Import Settings Template*\n\n"
            "Paste the template code you received:\n\n"
            "_It starts with_ `TMPL-`",
            parse_mode="Markdown",
        )
        context.user_data["editing"] = "import"
        return IMPORT_TEMPLATE

    elif query.data == "back_settings":
        await query.edit_message_text(
            f"⚙️ *Current Settings*\n\n"
            f"🎨 Style: _{settings['prompt'][:50]}_\n"
            f"📐 Aspect: *{settings['aspect']}*\n"
            f"📍 Placement: *{PLACEMENTS[settings['placement']]}*\n"
            f"📏 Scale: *{settings['scale'].capitalize()}*\n"
            f"✨ Filter: *{FILTERS[settings['filter']]}*\n"
            f"🔢 Versions: *{settings['versions']}*\n"
            f"✂️ Remove BG: *{'Yes' if settings['remove_bg'] else 'No'}*\n\n"
            "What would you like to change?",
            parse_mode="Markdown",
            reply_markup=settings_keyboard(),
        )
        return IN_SETTINGS

    return IN_SETTINGS


async def settings_value_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle aspect/placement/scale/filter/versions/removebg inline selections."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    settings = get_user_settings(context, user_id)
    data = query.data

    if data.startswith("aspect_"):
        settings["aspect"] = data.replace("aspect_", "")
    elif data.startswith("place_"):
        settings["placement"] = data.replace("place_", "")
    elif data.startswith("scale_"):
        settings["scale"] = data.replace("scale_", "")
    elif data.startswith("filter_"):
        settings["filter"] = data.replace("filter_", "")
    elif data.startswith("ver_"):
        settings["versions"] = int(data.replace("ver_", ""))
    elif data == "rbg_yes":
        settings["remove_bg"] = True
    elif data == "rbg_no":
        settings["remove_bg"] = False

    save_user_settings(context, user_id, settings)

    await query.edit_message_text(
        f"✅ *Setting saved!*\n\n"
        f"⚙️ *Current Settings*\n\n"
        f"🎨 Style: _{settings['prompt'][:50]}_\n"
        f"📐 Aspect: *{settings['aspect']}*\n"
        f"📍 Placement: *{PLACEMENTS[settings['placement']]}*\n"
        f"📏 Scale: *{settings['scale'].capitalize()}*\n"
        f"✨ Filter: *{FILTERS[settings['filter']]}*\n"
        f"🔢 Versions: *{settings['versions']}*\n"
        f"✂️ Remove BG: *{'Yes' if settings['remove_bg'] else 'No'}*",
        parse_mode="Markdown",
        reply_markup=settings_keyboard(),
    )
    return IN_SETTINGS


async def settings_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text input for prompt and template import."""
    user_id = update.effective_user.id
    editing = context.user_data.get("editing")
    text = update.message.text.strip()

    if editing == "prompt":
        if len(text) < 3:
            await update.message.reply_text("Please enter a longer description.")
            return SETTINGS_PROMPT
        settings = get_user_settings(context, user_id)
        settings["prompt"] = text
        save_user_settings(context, user_id, settings)
        await update.message.reply_text(
            f"✅ *Background style saved!*\n\n_{text}_",
            parse_mode="Markdown",
            reply_markup=settings_keyboard(),
        )
        context.user_data.pop("editing", None)
        return IN_SETTINGS

    return IN_SETTINGS


async def import_template_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    text = update.message.text.strip()
    settings = decode_template(text)

    if not settings:
        await update.message.reply_text(
            "❌ Invalid template code. Please check and try again.\n"
            "_It should start with_ `TMPL-`",
            parse_mode="Markdown",
        )
        return IMPORT_TEMPLATE

    save_user_settings(context, user_id, settings)
    await update.message.reply_text(
        f"✅ *Template imported successfully!*\n\n"
        f"🎨 Style: _{settings['prompt'][:50]}_\n"
        f"📐 Aspect: *{settings['aspect']}*\n"
        f"📍 Placement: *{PLACEMENTS[settings['placement']]}*\n"
        f"📏 Scale: *{settings['scale'].capitalize()}*\n"
        f"✨ Filter: *{FILTERS[settings['filter']]}*\n"
        f"🔢 Versions: *{settings['versions']}*\n"
        f"✂️ Remove BG: *{'Yes' if settings['remove_bg'] else 'No'}*",
        parse_mode="Markdown",
        reply_markup=settings_keyboard(),
    )
    return IN_SETTINGS


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Send /start to open the menu.",
    )
    return ConversationHandler.END


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_callback, pattern="^menu_"),
            ],
            UPLOADING_PHOTOS: [
                MessageHandler(filters.PHOTO, photo_upload_received),
                CommandHandler("done", upload_done),
                CommandHandler("clear", clear_library),
            ],
            IN_SETTINGS: [
                CallbackQueryHandler(settings_callback,       pattern="^set_|^back_settings"),
                CallbackQueryHandler(main_menu_callback,      pattern="^menu_back"),
            ],
            SETTINGS_PROMPT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, settings_text_input),
            ],
            SETTINGS_ASPECT: [
                CallbackQueryHandler(settings_value_callback, pattern="^aspect_"),
                CallbackQueryHandler(settings_callback,       pattern="^back_settings"),
            ],
            SETTINGS_PLACEMENT: [
                CallbackQueryHandler(settings_value_callback, pattern="^place_"),
                CallbackQueryHandler(settings_callback,       pattern="^back_settings"),
            ],
            SETTINGS_SCALE: [
                CallbackQueryHandler(settings_value_callback, pattern="^scale_"),
                CallbackQueryHandler(settings_callback,       pattern="^back_settings"),
            ],
            SETTINGS_FILTER: [
                CallbackQueryHandler(settings_value_callback, pattern="^filter_"),
                CallbackQueryHandler(settings_callback,       pattern="^back_settings"),
            ],
            SETTINGS_VERSIONS: [
                CallbackQueryHandler(settings_value_callback, pattern="^ver_"),
                CallbackQueryHandler(settings_callback,       pattern="^back_settings"),
            ],
            SETTINGS_REMOVE_BG: [
                CallbackQueryHandler(settings_value_callback, pattern="^rbg_"),
                CallbackQueryHandler(settings_callback,       pattern="^back_settings"),
            ],
            IMPORT_TEMPLATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, import_template_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_message=False,
        per_chat=True,
        per_user=True,
    )

    app.add_handler(conv)
    logger.info("🚀 AI Background Studio Bot (Batch Edition) is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()