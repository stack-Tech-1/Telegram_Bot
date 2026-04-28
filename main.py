"""
✨ AI Background Studio Bot — Enhanced Edition
================================================
Features:
  - Admin / Worker role system
  - Admins configure global preset, backgrounds, and AI settings
  - Workers just send photos and get results instantly
  - Generate AI backgrounds (Pollinations.AI - free, no token needed)
  - Use saved background images
  - Save & reuse settings presets
  - Export/import presets
  - One-tap "Generate Again"
  - Batch photo processing (album support)
"""

import os
import io
import time
import json
import logging
import hashlib
import urllib.parse
import requests
from PIL import Image, ImageFilter, ImageEnhance
from rembg import remove
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    PicklePersistence,
    filters,
    ContextTypes,
)

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN is missing! Set it in Railway Variables.")

# Comma-separated Telegram user IDs, e.g. ADMIN_IDS=123456789,987654321
_raw_admin_ids = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = set(
    int(x.strip()) for x in _raw_admin_ids.split(",") if x.strip().isdigit()
)
if not ADMIN_IDS:
    logger_temp = logging.getLogger(__name__)
    logger_temp.warning("⚠️ No ADMIN_IDS set! Add ADMIN_IDS to Railway Variables.")

MAX_BG_IMAGES      = 20
MAX_PRESETS        = 10
ALBUM_COLLECT_SECS = 2

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Conversation States ────────────────────────────────────────────────────────
(
    # Shared
    WAITING_PHOTO,
    # Admin-only
    CHOOSE_BG_TYPE,
    WAITING_AI_PROMPT,
    CHOOSE_SAVED_BG,
    CHOOSING_PRESET_OR_MANUAL,
    CHOOSING_ASPECT,
    CHOOSING_PLACEMENT,
    CHOOSING_SCALE,
    CHOOSING_FILTER,
    CHOOSING_REMOVE_BG,
    SAVE_PRESET_NAME,
    MANAGE_PRESETS,
    MANAGE_BACKGROUNDS,
    UPLOADING_BACKGROUNDS,
    IMPORT_PRESET,
    RESULT_ACTIONS,
    # Admin global settings
    GLOBAL_SETTINGS,
    GLOBAL_AI_PROMPT,
    GLOBAL_ACTIVE_PRESET,
    # Worker
    WORKER_WAITING_PHOTO,
    WORKER_RESULT,
) = range(21)

# ── Constants ──────────────────────────────────────────────────────────────────
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

DEFAULT_SETTINGS = {
    "aspect":     "1:1",
    "placement":  "bottom_center",
    "scale":      "large",
    "filter":     "none",
    "remove_bg":  True,
}


# ── Role Helpers ───────────────────────────────────────────────────────────────

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ── Global State Helpers (stored in bot_data) ──────────────────────────────────

def get_global_preset(ctx) -> dict | None:
    return ctx.bot_data.get("global_preset")

def set_global_preset(ctx, preset: dict):
    ctx.bot_data["global_preset"] = preset

def get_global_bg_mode(ctx) -> str:
    """Returns 'saved' or 'ai'"""
    return ctx.bot_data.get("global_bg_mode", "saved")

def set_global_bg_mode(ctx, mode: str):
    ctx.bot_data["global_bg_mode"] = mode

def get_global_ai_prompt(ctx) -> str:
    return ctx.bot_data.get("global_ai_prompt", "professional studio background")

def set_global_ai_prompt(ctx, prompt: str):
    ctx.bot_data["global_ai_prompt"] = prompt

def get_global_backgrounds(ctx) -> list:
    return ctx.bot_data.get("global_backgrounds", [])

def set_global_backgrounds(ctx, bgs: list):
    ctx.bot_data["global_backgrounds"] = bgs

def is_globally_configured(ctx) -> bool:
    """True if admin has set both a preset and at least one background or an AI prompt."""
    preset = get_global_preset(ctx)
    if not preset:
        return False
    mode = get_global_bg_mode(ctx)
    if mode == "saved" and not get_global_backgrounds(ctx):
        return False
    if mode == "ai" and not get_global_ai_prompt(ctx):
        return False
    return True


# ── Per-Admin Data Helpers ─────────────────────────────────────────────────────

def get_bg_images(ctx, uid):
    return ctx.bot_data.setdefault(f"bg_{uid}", [])

def save_bg_images(ctx, uid, images):
    ctx.bot_data[f"bg_{uid}"] = images

def get_presets(ctx, uid):
    return ctx.bot_data.setdefault(f"presets_{uid}", {})

def save_presets(ctx, uid, presets):
    ctx.bot_data[f"presets_{uid}"] = presets


# ── Image Processing ───────────────────────────────────────────────────────────

def do_remove_bg(image_bytes: bytes) -> Image.Image:
    result = remove(image_bytes)
    return Image.open(io.BytesIO(result)).convert("RGBA")


def generate_ai_background(prompt: str, width: int, height: int) -> Image.Image:
    full = prompt + ", high quality, photorealistic, scenic, no people, no humans, background only"
    encoded = urllib.parse.quote(full)
    seed = int(time.time() * 1000) % 999999
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={min(width,1024)}&height={min(height,1024)}"
        f"&nologo=true&enhance=true&seed={seed}"
    )
    logger.info(f"Pollinations seed={seed}")
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


def composite_image(subject, background, placement, scale, filter_name):
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


# ── Preset Encoding ────────────────────────────────────────────────────────────

def encode_preset(settings: dict) -> str:
    data = json.dumps(settings, separators=(",", ":"), sort_keys=True)
    checksum = hashlib.md5(data.encode()).hexdigest()[:4].upper()
    encoded = urllib.parse.quote(data)
    return f"PRESET-{checksum}-{encoded}"


def decode_preset(code: str) -> dict | None:
    try:
        if not code.startswith("PRESET-"):
            return None
        parts = code.split("-", 2)
        if len(parts) != 3:
            return None
        data = urllib.parse.unquote(parts[2])
        settings = json.loads(data)
        for k in DEFAULT_SETTINGS:
            if k not in settings:
                return None
        return settings
    except Exception:
        return None


# ── Settings Summary ───────────────────────────────────────────────────────────

def settings_summary(s: dict) -> str:
    return (
        f"📐 Aspect: *{s['aspect']}*\n"
        f"📍 Placement: *{PLACEMENTS[s['placement']]}*\n"
        f"📏 Scale: *{s['scale'].capitalize()}*\n"
        f"✨ Filter: *{FILTERS[s['filter']]}*\n"
        f"✂️ Remove BG: *{'Yes' if s['remove_bg'] else 'No'}*"
    )


# ══════════════════════════════════════════════════════════════════════════════
# /start — routes to admin or worker menu
# ══════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    uid = update.effective_user.id

    if is_admin(uid):
        return await show_admin_home(update.message, context, edit=False)
    else:
        return await show_worker_home(update.message, context, edit=False)


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN SECTION
# ══════════════════════════════════════════════════════════════════════════════

async def show_admin_home(msg_or_query, context, edit=False):
    uid = msg_or_query.from_user.id if hasattr(msg_or_query, 'from_user') else msg_or_query.chat.id
    presets = get_presets(context, uid)
    bgs = get_bg_images(context, uid)
    global_preset = get_global_preset(context)
    configured = is_globally_configured(context)

    status = "✅ Configured" if configured else "⚠️ Not configured yet"
    active = f"*{list(get_presets(context, uid).keys())[0]}*" if global_preset else "None"

    keyboard = [
        [InlineKeyboardButton("📸 Process My Photos", callback_data="admin_process")],
        [InlineKeyboardButton("🌍 Global Worker Settings", callback_data="admin_global")],
        [InlineKeyboardButton("🖼️ My Backgrounds", callback_data="go_backgrounds"),
         InlineKeyboardButton("⚙️ My Presets",     callback_data="go_presets")],
    ]
    text = (
        "👑 *AI Background Studio — Admin Panel*\n\n"
        f"🌍 Worker config: *{status}*\n"
        f"🖼️ Saved backgrounds: *{len(bgs)}/{MAX_BG_IMAGES}*\n"
        f"⚙️ Saved presets: *{len(presets)}/{MAX_PRESETS}*\n\n"
        "What would you like to do?"
    )

    if edit:
        try:
            await msg_or_query.edit_message_caption(text, parse_mode="Markdown",
                                                    reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await msg_or_query.edit_message_text(text, parse_mode="Markdown",
                                                 reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await msg_or_query.reply_text(text, parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(keyboard))
    return WAITING_PHOTO


async def admin_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if not is_admin(uid):
        await query.answer("⛔ Not authorized.", show_alert=True)
        return WAITING_PHOTO

    async def safe_edit(text, reply_markup=None):
        kwargs = {"parse_mode": "Markdown"}
        if reply_markup:
            kwargs["reply_markup"] = reply_markup
        try:
            await query.edit_message_caption(text, **kwargs)
        except Exception:
            await query.edit_message_text(text, **kwargs)

    if query.data == "admin_process":
        await safe_edit(
            "📸 *Send me your photo(s) to process!*\n\n"
            "_You can send a single photo or an album of multiple photos at once._"
        )
        return WAITING_PHOTO

    elif query.data == "go_backgrounds":
        return await show_backgrounds_menu(query, context, uid)

    elif query.data == "go_presets":
        return await show_presets_menu(query, context, uid)

    elif query.data == "admin_global":
        return await show_global_settings(query, context)

    elif query.data == "back_home":
        return await show_admin_home(query, context, edit=True)

    return WAITING_PHOTO


# ── Global Worker Settings ─────────────────────────────────────────────────────

async def show_global_settings(query, context) -> int:
    preset = get_global_preset(context)
    bg_mode = get_global_bg_mode(context)
    bgs = get_global_backgrounds(context)
    ai_prompt = get_global_ai_prompt(context)
    configured = is_globally_configured(context)

    preset_label = "⚠️ Not set" if not preset else "✅ Set"
    bg_label = f"🖼️ {len(bgs)} saved image(s)" if bg_mode == "saved" else f"🤖 AI: {ai_prompt[:30]}"
    status = "✅ Workers can use the bot" if configured else "⚠️ Workers cannot use the bot yet"

    keyboard = [
        [InlineKeyboardButton("⚙️ Set Active Preset", callback_data="global_set_preset")],
        [InlineKeyboardButton("🖼️ Set Worker Backgrounds", callback_data="global_set_backgrounds")],
        [InlineKeyboardButton(
            f"{'🤖 Switch to AI Mode' if bg_mode == 'saved' else '🖼️ Switch to Saved BG Mode'}",
            callback_data="global_toggle_bg_mode"
        )],
        [InlineKeyboardButton("◀️ Back", callback_data="back_home")],
    ]

    if bg_mode == "ai":
        keyboard.insert(2, [InlineKeyboardButton("✏️ Set AI Prompt", callback_data="global_set_ai_prompt")])

    text = (
        "🌍 *Global Worker Settings*\n\n"
        f"📋 Active preset: *{preset_label}*\n"
        f"🎨 Background mode: *{'Saved Images' if bg_mode == 'saved' else 'AI Generated'}*\n"
        f"🖼️ Background: *{bg_label}*\n\n"
        f"{status}"
    )

    try:
        await query.edit_message_caption(text, parse_mode="Markdown",
                                         reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        await query.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(keyboard))
    return GLOBAL_SETTINGS


async def global_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if not is_admin(uid):
        await query.answer("⛔ Not authorized.", show_alert=True)
        return GLOBAL_SETTINGS

    if query.data == "global_set_preset":
        presets = get_presets(context, uid)
        if not presets:
            await query.answer("⚠️ No presets saved yet. Create one first via My Presets.", show_alert=True)
            return GLOBAL_SETTINGS

        keyboard = [
            [InlineKeyboardButton(f"⚡ {name}", callback_data=f"global_activate_{name}")]
            for name in presets
        ]
        keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="back_global")])
        try:
            await query.edit_message_caption(
                "⚙️ *Choose which preset workers will use:*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            await query.edit_message_text(
                "⚙️ *Choose which preset workers will use:*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        return GLOBAL_ACTIVE_PRESET

    elif query.data == "global_toggle_bg_mode":
        current = get_global_bg_mode(context)
        new_mode = "ai" if current == "saved" else "saved"
        set_global_bg_mode(context, new_mode)
        await query.answer(f"Switched to {'AI' if new_mode == 'ai' else 'Saved Images'} mode!")
        return await show_global_settings(query, context)

    elif query.data == "global_set_ai_prompt":
        try:
            await query.edit_message_caption(
                "🤖 *Set AI Background Prompt*\n\n"
                "Type the prompt workers' photos will use for AI backgrounds:\n\n"
                "Example: `luxury penthouse rooftop at sunset`",
                parse_mode="Markdown",
            )
        except Exception:
            await query.edit_message_text(
                "🤖 *Set AI Background Prompt*\n\n"
                "Type the prompt workers' photos will use for AI backgrounds:\n\n"
                "Example: `luxury penthouse rooftop at sunset`",
                parse_mode="Markdown",
            )
        return GLOBAL_AI_PROMPT

    elif query.data == "global_set_backgrounds":
        bgs = get_global_backgrounds(context)
        try:
            await query.edit_message_caption(
                f"🖼️ *Set Worker Backgrounds*\n\n"
                f"Current: *{len(bgs)}* background(s) saved for workers.\n\n"
                "Send background images now — they will replace the current worker backgrounds.\n"
                "When done, send /done",
                parse_mode="Markdown",
            )
        except Exception:
            await query.edit_message_text(
                f"🖼️ *Set Worker Backgrounds*\n\n"
                f"Current: *{len(bgs)}* background(s) saved for workers.\n\n"
                "Send background images now — they will replace the current worker backgrounds.\n"
                "When done, send /done",
                parse_mode="Markdown",
            )
        context.user_data["uploading_global_bgs"] = True
        context.user_data["new_global_bgs"] = []
        return UPLOADING_BACKGROUNDS

    elif query.data == "back_global":
        return await show_global_settings(query, context)

    elif query.data == "back_home":
        return await show_admin_home(query, context, edit=True)

    return GLOBAL_SETTINGS


async def global_active_preset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "back_global":
        return await show_global_settings(query, context)

    if query.data.startswith("global_activate_"):
        name = query.data.replace("global_activate_", "")
        presets = get_presets(context, uid)
        if name in presets:
            set_global_preset(context, dict(presets[name]))
            await query.answer(f"✅ '{name}' is now the active worker preset!", show_alert=True)
        return await show_global_settings(query, context)

    return GLOBAL_ACTIVE_PRESET


async def global_ai_prompt_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    prompt = update.message.text.strip()
    if len(prompt) < 3:
        await update.message.reply_text("Please be more descriptive.")
        return GLOBAL_AI_PROMPT

    set_global_ai_prompt(context, prompt)
    await update.message.reply_text(
        f"✅ AI prompt set to:\n_\"{prompt}\"_\n\nWorkers will now get AI backgrounds with this prompt.",
        parse_mode="Markdown",
    )
    # Show global settings again via a new message
    keyboard = [[InlineKeyboardButton("◀️ Back to Global Settings", callback_data="admin_global")]]
    await update.message.reply_text(
        "Use the button below to go back.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return GLOBAL_SETTINGS


# ══════════════════════════════════════════════════════════════════════════════
# PHOTO RECEIVED — shared between admin and worker
# ══════════════════════════════════════════════════════════════════════════════

async def _flush_album(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fired by job_queue after collection window; proceeds with gathered photos."""
    job = context.job
    chat_id = job.chat_id
    uid = job.user_id

    album_key = f"album_{uid}"
    album_data = context.bot_data.pop(album_key, None)
    if not album_data:
        return

    subject_bytes_list = album_data["bytes_list"]
    n = len(subject_bytes_list)
    context.application.user_data[uid]["subject_bytes_list"] = subject_bytes_list

    if is_admin(uid):
        # Admin: show background type choice
        bgs = get_bg_images(context, uid)
        keyboard = [
            [InlineKeyboardButton("🤖 Generate AI Backgrounds", callback_data="bg_ai")],
        ]
        if bgs:
            keyboard.append([InlineKeyboardButton(
                f"🖼️ Use My Saved Backgrounds ({len(bgs)})", callback_data="bg_saved"
            )])
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"📥 Got *{n} photo{'s' if n > 1 else ''}*!\n\n"
                "🎨 *What background do you want?*\n"
                "Choose how to create the backgrounds:"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        # Worker: show simple generate button
        if not is_globally_configured(context):
            await context.bot.send_message(
                chat_id=chat_id,
                text="⏳ *Bot not configured yet.* Contact your admin.",
                parse_mode="Markdown",
            )
            return

        keyboard = [[InlineKeyboardButton(
            f"⚡ Generate {n} photo{'s' if n > 1 else ''}",
            callback_data="worker_generate"
        )]]
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📥 Got *{n} photo{'s' if n > 1 else ''}*! Ready to process.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    photo = update.message.photo[-1]
    media_group_id = update.message.media_group_id

    file = await photo.get_file()
    image_bytes = bytes(await file.download_as_bytearray())

    album_key = f"album_{uid}"

    if media_group_id:
        if album_key not in context.bot_data:
            context.bot_data[album_key] = {"bytes_list": [], "media_group_id": media_group_id}
        context.bot_data[album_key]["bytes_list"].append(image_bytes)

        for j in context.job_queue.get_jobs_by_name(f"flush_{uid}"):
            j.schedule_removal()

        context.job_queue.run_once(
            _flush_album,
            when=ALBUM_COLLECT_SECS,
            chat_id=update.effective_chat.id,
            user_id=uid,
            name=f"flush_{uid}",
        )
        return WAITING_PHOTO

    else:
        context.user_data["subject_bytes_list"] = [image_bytes]

        if is_admin(uid):
            bgs = get_bg_images(context, uid)
            keyboard = [
                [InlineKeyboardButton("🤖 Generate AI Background", callback_data="bg_ai")],
            ]
            if bgs:
                keyboard.append([InlineKeyboardButton(
                    f"🖼️ Use My Saved Backgrounds ({len(bgs)})", callback_data="bg_saved"
                )])
            await update.message.reply_text(
                "📥 Got *1 photo*!\n\n🎨 *What background do you want?*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return CHOOSE_BG_TYPE
        else:
            if not is_globally_configured(context):
                await update.message.reply_text(
                    "⏳ *Bot not configured yet.* Contact your admin.",
                    parse_mode="Markdown",
                )
                return WAITING_PHOTO

            keyboard = [[InlineKeyboardButton("⚡ Generate", callback_data="worker_generate")]]
            await update.message.reply_text(
                "📥 Got *1 photo*! Ready to process.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return WORKER_WAITING_PHOTO


# ══════════════════════════════════════════════════════════════════════════════
# WORKER SECTION
# ══════════════════════════════════════════════════════════════════════════════

async def show_worker_home(msg_or_query, context, edit=False):
    configured = is_globally_configured(context)

    if configured:
        preset = get_global_preset(context)
        bg_mode = get_global_bg_mode(context)
        bgs = get_global_backgrounds(context)
        bg_info = f"{len(bgs)} background(s)" if bg_mode == "saved" else "AI generated"
        keyboard = [[InlineKeyboardButton("📸 Send Photos", callback_data="worker_send_photos")]]
        text = (
            "✨ *AI Background Studio*\n\n"
            "Send your photos and get them processed instantly!\n\n"
            f"🎨 Background: *{bg_info}*\n"
            f"✂️ Remove BG: *{'Yes' if preset.get('remove_bg') else 'No'}*"
        )
    else:
        keyboard = []
        text = (
            "✨ *AI Background Studio*\n\n"
            "⏳ *Bot not configured yet.*\n\n"
            "Please contact your admin to set up the bot."
        )

    if edit:
        try:
            await msg_or_query.edit_message_caption(
                text, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
            )
        except Exception:
            await msg_or_query.edit_message_text(
                text, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
            )
    else:
        await msg_or_query.reply_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )
    return WORKER_WAITING_PHOTO


async def worker_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "worker_send_photos":
        try:
            await query.edit_message_caption(
                "📸 *Send me your photo(s)!*\n\n"
                "_You can send a single photo or an album of multiple photos at once._",
                parse_mode="Markdown",
            )
        except Exception:
            await query.edit_message_text(
                "📸 *Send me your photo(s)!*\n\n"
                "_You can send a single photo or an album of multiple photos at once._",
                parse_mode="Markdown",
            )
        return WORKER_WAITING_PHOTO

    elif query.data == "worker_generate":
        # Pull album data if needed
        if "subject_bytes_list" not in context.user_data:
            app_ud = context.application.user_data.get(uid, {})
            if "subject_bytes_list" in app_ud:
                context.user_data["subject_bytes_list"] = app_ud.pop("subject_bytes_list")

        n = len(context.user_data.get("subject_bytes_list", [None]))
        status = f"⏳ *Generating {n} photo{'s' if n > 1 else ''}… please wait.*"
        try:
            await query.edit_message_caption(status, parse_mode="Markdown")
        except Exception:
            await query.edit_message_text(status, parse_mode="Markdown")

        await do_worker_generate(query.message, context, uid)
        return WORKER_RESULT

    elif query.data == "worker_generate_again":
        n = len(context.user_data.get("subject_bytes_list", [None]))
        # Advance background cycle
        bgs = get_global_backgrounds(context)
        if bgs:
            n_photos = len(context.user_data.get("subject_bytes_list", []))
            current = context.user_data.get("worker_bg_start_index", 0)
            context.user_data["worker_bg_start_index"] = (current + n_photos) % len(bgs)

        status = f"⏳ *Generating {n} photo{'s' if n > 1 else ''}… please wait.*"
        try:
            await query.edit_message_caption(status, parse_mode="Markdown")
        except Exception:
            await query.edit_message_text(status, parse_mode="Markdown")

        await do_worker_generate(query.message, context, uid)
        return WORKER_RESULT

    elif query.data == "worker_back_home":
        return await show_worker_home(query, context, edit=True)

    return WORKER_WAITING_PHOTO


async def do_worker_generate(msg, context, uid):
    """Generate photos for a worker using global admin settings."""
    preset = get_global_preset(context)
    bg_mode = get_global_bg_mode(context)
    bgs = get_global_backgrounds(context)
    ai_prompt = get_global_ai_prompt(context)
    subject_bytes_list = context.user_data.get("subject_bytes_list", [])
    n = len(subject_bytes_list)
    s = preset
    width, height = ASPECT_RATIOS[s["aspect"]]
    start_idx = context.user_data.get("worker_bg_start_index", 0)

    try:
        await msg.reply_text(
            f"⚙️ *Processing {n} photo{'s' if n > 1 else ''}…*",
            parse_mode="Markdown"
        )

        results = []

        for i, subject_bytes in enumerate(subject_bytes_list):
            # Step 1: Subject
            if s["remove_bg"]:
                if i == 0:
                    await msg.reply_text("✂️ Removing backgrounds…")
                subject = do_remove_bg(subject_bytes)
            else:
                subject = Image.open(io.BytesIO(subject_bytes)).convert("RGBA")

            # Step 2: Background
            if bg_mode == "ai":
                if i == 0:
                    await msg.reply_text("🤖 Generating AI backgrounds…")
                time.sleep(0.15)
                background = generate_ai_background(ai_prompt, width, height)
                label = f"🤖 AI background"
            else:
                idx = (start_idx + i) % len(bgs)
                if i == 0:
                    await msg.reply_text("🖼️ Loading backgrounds…")
                file = await msg._bot.get_file(bgs[idx])
                bg_bytes = bytes(await file.download_as_bytearray())
                background = Image.open(io.BytesIO(bg_bytes)).convert("RGBA")
                background = background.resize((width, height), Image.LANCZOS)
                label = f"🖼️ Background {idx + 1}"

            # Step 3: Composite
            final = composite_image(
                subject, background,
                s["placement"], SCALES[s["scale"]], s["filter"]
            )
            results.append((final, label))

        # Step 4: Send
        keyboard = [
            [InlineKeyboardButton("⚡ Generate Again (new BGs)", callback_data="worker_generate_again")],
            [InlineKeyboardButton("🏠 Back to Menu", callback_data="worker_back_home")],
        ]

        if n == 1:
            final_bytes, label = results[0]
            await msg.reply_photo(
                photo=final_bytes,
                caption=f"✅ *Done!* {label}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            media_group = []
            for i, (final_bytes, label) in enumerate(results):
                cap = f"✅ *Done! {n} photos*" if i == 0 else f"Photo {i+1}/{n} — {label}"
                media_group.append(InputMediaPhoto(
                    media=io.BytesIO(final_bytes),
                    caption=cap,
                    parse_mode="Markdown" if i == 0 else None,
                ))
            await msg.reply_media_group(media=media_group)
            await msg.reply_text(
                f"⬆️ Your *{n} photos* are ready above!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    except Exception as e:
        logger.error(f"Worker generation error: {e}")
        await msg.reply_text(
            f"❌ Something went wrong: `{str(e)[:200]}`\n\nSend /start to try again.",
            parse_mode="Markdown",
        )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN — Background Type Choice (for admin's own processing)
# ══════════════════════════════════════════════════════════════════════════════

async def bg_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if "subject_bytes_list" not in context.user_data:
        app_ud = context.application.user_data.get(uid, {})
        if "subject_bytes_list" in app_ud:
            context.user_data["subject_bytes_list"] = app_ud.pop("subject_bytes_list")

    n = len(context.user_data.get("subject_bytes_list", [None]))

    if query.data == "bg_ai":
        context.user_data["bg_type"] = "ai"
        await query.edit_message_text(
            "🤖 *AI Background*\n\n"
            f"Each of your *{n} photo{'s' if n > 1 else ''}* will get a unique AI-generated background.\n\n"
            "Describe the background you want:\n\n"
            "Examples:\n"
            "• `luxury penthouse rooftop at sunset`\n"
            "• `magical forest with glowing lights`\n"
            "• `futuristic cyberpunk city at night`\n"
            "• `professional studio with soft bokeh`\n"
            "• `tropical beach with turquoise water`\n\n"
            "_Type your description:_",
            parse_mode="Markdown",
        )
        return WAITING_AI_PROMPT

    elif query.data == "bg_saved":
        context.user_data["bg_type"] = "saved"
        bgs = get_bg_images(context, uid)
        keyboard = [
            [InlineKeyboardButton(f"🖼️ Background {i+1}", callback_data=f"pickbg_{i}")]
            for i in range(len(bgs))
        ]
        await query.edit_message_text(
            f"🖼️ *Choose starting background* ({len(bgs)} saved)\n\n"
            f"Your *{n} photo{'s' if n > 1 else ''}* will each get a different background, cycling in order.\n\n"
            "Pick which background to start from:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return CHOOSE_SAVED_BG

    return CHOOSE_BG_TYPE


async def ai_prompt_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    prompt = update.message.text.strip()
    if len(prompt) < 3:
        await update.message.reply_text("Please be more descriptive.")
        return WAITING_AI_PROMPT
    context.user_data["ai_prompt"] = prompt
    return await ask_preset_or_manual(update.message, context)


async def saved_bg_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.replace("pickbg_", ""))
    context.user_data["saved_bg_start_index"] = idx
    n = len(context.user_data.get("subject_bytes_list", [None]))
    await query.edit_message_text(
        f"✅ Starting from *Background {idx + 1}*!\n\n"
        f"Your {n} photo{'s' if n > 1 else ''} will cycle through backgrounds from there.\n\n"
        "Now configuring settings…"
    )
    return await ask_preset_or_manual(query.message, context)


# ── Preset or Manual Settings ──────────────────────────────────────────────────

async def ask_preset_or_manual(msg, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = msg.chat.id
    presets = get_presets(context, uid)
    keyboard = [[InlineKeyboardButton("🔧 Configure Manually", callback_data="use_manual")]]
    if presets:
        for name in list(presets.keys())[:5]:
            keyboard.insert(0, [InlineKeyboardButton(f"⚡ {name}", callback_data=f"usepreset_{name}")])
    keyboard.append([InlineKeyboardButton("📥 Import Preset Code", callback_data="import_preset")])
    await msg.reply_text(
        "⚙️ *Settings*\n\n"
        + ("Choose a saved preset or configure manually:" if presets else "Configure your settings:"),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_PRESET_OR_MANUAL


async def preset_or_manual_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "use_manual":
        context.user_data["settings"] = dict(DEFAULT_SETTINGS)
        return await ask_remove_bg(query, context)

    elif query.data == "import_preset":
        await query.edit_message_text(
            "📥 *Import Preset*\n\nPaste your preset code (starts with `PRESET-`):",
            parse_mode="Markdown",
        )
        return IMPORT_PRESET

    elif query.data.startswith("usepreset_"):
        name = query.data.replace("usepreset_", "")
        presets = get_presets(context, uid)
        if name in presets:
            context.user_data["settings"] = dict(presets[name])
            await query.edit_message_text(
                f"✅ *Preset '{name}' applied!*\n\n"
                + settings_summary(context.user_data["settings"]),
                parse_mode="Markdown",
            )
            return await confirm_and_generate(query, context)

    return CHOOSING_PRESET_OR_MANUAL


async def import_preset_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = update.message.text.strip()
    settings = decode_preset(code)
    if not settings:
        await update.message.reply_text(
            "❌ Invalid preset code. Try again or send /start to restart."
        )
        return IMPORT_PRESET
    context.user_data["settings"] = settings
    await update.message.reply_text(
        f"✅ *Preset imported!*\n\n" + settings_summary(settings),
        parse_mode="Markdown",
    )
    return await confirm_and_generate_msg(update.message, context)


# ── Manual Settings Steps ──────────────────────────────────────────────────────

async def ask_remove_bg(query, context):
    s = context.user_data["settings"]
    keyboard = [
        [InlineKeyboardButton("✅ Yes, remove it", callback_data="rbg_yes"),
         InlineKeyboardButton("❌ No, keep it",     callback_data="rbg_no")],
    ]
    await query.edit_message_text(
        f"✂️ *Remove Background?*\n\nCurrent: *{'Yes' if s['remove_bg'] else 'No'}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_REMOVE_BG


async def remove_bg_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["settings"]["remove_bg"] = query.data == "rbg_yes"
    keyboard = [[InlineKeyboardButton(r, callback_data=f"aspect_{r}") for r in ASPECT_RATIOS]]
    await query.edit_message_text(
        "📐 *Aspect Ratio*\n\nChoose the output size:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_ASPECT


async def aspect_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["settings"]["aspect"] = query.data.replace("aspect_", "")
    keyboard = [[InlineKeyboardButton(label, callback_data=f"place_{k}")] for k, label in PLACEMENTS.items()]
    await query.edit_message_text(
        "📍 *Placement*\n\nWhere should your photo sit?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_PLACEMENT


async def placement_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["settings"]["placement"] = query.data.replace("place_", "")
    keyboard = [
        [InlineKeyboardButton("🔹 Small (40%)",  callback_data="scale_small"),
         InlineKeyboardButton("🔷 Medium (60%)", callback_data="scale_medium")],
        [InlineKeyboardButton("🔶 Large (80%)",  callback_data="scale_large"),
         InlineKeyboardButton("🟠 Full (95%)",   callback_data="scale_full")],
    ]
    await query.edit_message_text(
        "📏 *Scale*\n\nHow large should your photo be?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_SCALE


async def scale_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["settings"]["scale"] = query.data.replace("scale_", "")
    items = list(FILTERS.items())
    keyboard = [
        [InlineKeyboardButton(items[i][1], callback_data=f"filter_{items[i][0]}"),
         InlineKeyboardButton(items[i+1][1], callback_data=f"filter_{items[i+1][0]}")]
        for i in range(0, len(items) - 1, 2)
    ]
    await query.edit_message_text(
        "✨ *Filter*\n\nChoose a filter for the background:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_FILTER


async def filter_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["settings"]["filter"] = query.data.replace("filter_", "")
    s = context.user_data["settings"]
    n = len(context.user_data.get("subject_bytes_list", [None]))
    bg_type = context.user_data.get("bg_type", "ai")
    prompt_line = f"🤖 AI: _{context.user_data.get('ai_prompt', '')}_ \n" if bg_type == "ai" else "🖼️ Saved backgrounds (cycling)\n"
    keyboard = [
        [InlineKeyboardButton(f"✅ Generate {n} photo{'s' if n > 1 else ''}!", callback_data="confirm_yes")],
        [InlineKeyboardButton("💾 Save as Preset & Generate", callback_data="save_and_generate")],
        [InlineKeyboardButton("🔄 Start Over", callback_data="confirm_no")],
    ]
    await query.edit_message_text(
        f"🚀 *Ready!*\n\n📸 Photos: *{n}*\n{prompt_line}" + settings_summary(s),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return RESULT_ACTIONS


# ── Confirm & Generate (Admin) ─────────────────────────────────────────────────

async def confirm_and_generate(query, context):
    s = context.user_data["settings"]
    n = len(context.user_data.get("subject_bytes_list", [None]))
    bg_type = context.user_data.get("bg_type", "ai")
    prompt_line = f"🤖 AI: _{context.user_data.get('ai_prompt', '')}_\n" if bg_type == "ai" else "🖼️ Saved backgrounds (cycling)\n"
    keyboard = [
        [InlineKeyboardButton(f"✅ Generate {n} photo{'s' if n > 1 else ''}!", callback_data="confirm_yes")],
        [InlineKeyboardButton("💾 Save as Preset & Generate", callback_data="save_and_generate")],
        [InlineKeyboardButton("🔄 Start Over", callback_data="confirm_no")],
    ]
    await query.edit_message_text(
        f"🚀 *Ready!*\n\n📸 Photos: *{n}*\n{prompt_line}" + settings_summary(s),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return RESULT_ACTIONS


async def confirm_and_generate_msg(msg, context):
    s = context.user_data["settings"]
    n = len(context.user_data.get("subject_bytes_list", [None]))
    keyboard = [
        [InlineKeyboardButton(f"✅ Generate {n} photo{'s' if n > 1 else ''}!", callback_data="confirm_yes")],
        [InlineKeyboardButton("💾 Save as Preset & Generate", callback_data="save_and_generate")],
        [InlineKeyboardButton("🔄 Start Over", callback_data="confirm_no")],
    ]
    await msg.reply_text(
        f"🚀 *Ready!*\n\n📸 Photos: *{n}*\n" + settings_summary(s),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return RESULT_ACTIONS


async def result_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_no":
        try:
            await query.edit_message_text("🔄 Send /start to begin again.")
        except Exception:
            await query.edit_message_caption("🔄 Send /start to begin again.")
        context.user_data.clear()
        return ConversationHandler.END

    elif query.data == "save_and_generate":
        try:
            await query.edit_message_text(
                "💾 *Save Preset*\n\nType a name for this preset:",
                parse_mode="Markdown",
            )
        except Exception:
            await query.edit_message_caption(
                "💾 *Save Preset*\n\nType a name for this preset:",
                parse_mode="Markdown",
            )
        return SAVE_PRESET_NAME

    elif query.data in ("confirm_yes", "generate_again"):
        if query.data == "generate_again" and context.user_data.get("bg_type") == "saved":
            uid = query.from_user.id
            bgs = get_bg_images(context, uid)
            if bgs:
                n_photos = len(context.user_data.get("subject_bytes_list", []))
                current_start = context.user_data.get("saved_bg_start_index", 0)
                context.user_data["saved_bg_start_index"] = (current_start + n_photos) % len(bgs)

        n = len(context.user_data.get("subject_bytes_list", [None]))
        status = f"⏳ *Generating {n} photo{'s' if n > 1 else ''}… please wait.*"
        try:
            await query.edit_message_caption(status, parse_mode="Markdown")
        except Exception:
            await query.edit_message_text(status, parse_mode="Markdown")

        await do_generate_batch(query.message, context, query.from_user.id)
        return RESULT_ACTIONS

    return RESULT_ACTIONS


async def save_preset_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    name = update.message.text.strip()[:30]
    presets = get_presets(context, uid)

    if len(presets) >= MAX_PRESETS:
        await update.message.reply_text(
            f"❌ Max {MAX_PRESETS} presets reached. Delete one first via My Presets."
        )
        return SAVE_PRESET_NAME

    presets[name] = dict(context.user_data["settings"])
    save_presets(context, uid, presets)
    await update.message.reply_text(f"✅ Preset *'{name}'* saved!", parse_mode="Markdown")
    await do_generate_batch(update.message, context, uid)
    return RESULT_ACTIONS


# ── Core Generation (Admin batch) ──────────────────────────────────────────────

async def do_generate_batch(msg, context, uid):
    d = context.user_data
    s = d["settings"]
    width, height = ASPECT_RATIOS[s["aspect"]]
    bg_type = d.get("bg_type", "ai")
    subject_bytes_list = d.get("subject_bytes_list", [])
    n = len(subject_bytes_list)
    bgs = get_bg_images(context, uid)
    start_idx = d.get("saved_bg_start_index", 0)

    try:
        await msg.reply_text(
            f"⚙️ *Processing {n} photo{'s' if n > 1 else ''}…*",
            parse_mode="Markdown"
        )
        results = []

        for i, subject_bytes in enumerate(subject_bytes_list):
            if s["remove_bg"]:
                if i == 0:
                    await msg.reply_text("✂️ Removing backgrounds…")
                subject = do_remove_bg(subject_bytes)
            else:
                subject = Image.open(io.BytesIO(subject_bytes)).convert("RGBA")

            if bg_type == "ai":
                if i == 0:
                    await msg.reply_text("🤖 Generating AI backgrounds…")
                prompt = d.get("ai_prompt", "professional studio background")
                time.sleep(0.15)
                background = generate_ai_background(prompt, width, height)
                label = f"🤖 {prompt[:35]}"
            else:
                idx = (start_idx + i) % len(bgs)
                if i == 0:
                    await msg.reply_text("🖼️ Loading backgrounds…")
                file = await msg._bot.get_file(bgs[idx])
                bg_bytes = bytes(await file.download_as_bytearray())
                background = Image.open(io.BytesIO(bg_bytes)).convert("RGBA")
                background = background.resize((width, height), Image.LANCZOS)
                label = f"🖼️ Background {idx + 1}"

            final = composite_image(
                subject, background,
                s["placement"], SCALES[s["scale"]], s["filter"]
            )
            results.append((final, label))

        keyboard = [
            [InlineKeyboardButton("⚡ Generate Again (new BGs)", callback_data="generate_again")],
            [InlineKeyboardButton("🏠 Back to Menu", callback_data="back_home")],
        ]

        if n == 1:
            final_bytes, label = results[0]
            await msg.reply_photo(
                photo=final_bytes,
                caption=f"✅ *Done!*\n\n{label}\n{settings_summary(s)}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            media_group = []
            for i, (final_bytes, label) in enumerate(results):
                cap = f"✅ *Done! {n} photos*\n\n{label}\n{settings_summary(s)}" if i == 0 else f"Photo {i+1}/{n} — {label}"
                media_group.append(InputMediaPhoto(
                    media=io.BytesIO(final_bytes),
                    caption=cap,
                    parse_mode="Markdown" if i == 0 else None,
                ))
            await msg.reply_media_group(media=media_group)
            await msg.reply_text(
                f"⬆️ Your *{n} photos* are ready above!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    except Exception as e:
        logger.error(f"Generation error: {e}")
        await msg.reply_text(
            f"❌ Something went wrong: `{str(e)[:200]}`\n\nSend /start to try again.",
            parse_mode="Markdown",
        )


# ── Backgrounds Management (Admin) ─────────────────────────────────────────────

async def show_backgrounds_menu(query, context, uid):
    bgs = get_bg_images(context, uid)
    keyboard = [
        [InlineKeyboardButton("➕ Upload New Background", callback_data="bg_upload")],
        [InlineKeyboardButton("🗑️ Clear All Backgrounds", callback_data="bg_clear")],
        [InlineKeyboardButton("◀️ Back", callback_data="back_home")],
    ]
    await query.edit_message_text(
        f"🖼️ *My Backgrounds*\n\n"
        f"Saved: *{len(bgs)}/{MAX_BG_IMAGES}*\n\n"
        "Upload images to use as backgrounds for your own processing.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return MANAGE_BACKGROUNDS


async def backgrounds_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "bg_upload":
        bgs = get_bg_images(context, uid)
        context.user_data["uploading_global_bgs"] = False
        await query.edit_message_text(
            f"📤 *Upload Backgrounds*\n\n"
            f"Current: {len(bgs)}/{MAX_BG_IMAGES}\n\n"
            "Send your background images now.\nWhen done, send /done",
        )
        return UPLOADING_BACKGROUNDS

    elif query.data == "bg_clear":
        save_bg_images(context, uid, [])
        keyboard = [[InlineKeyboardButton("◀️ Back to Menu", callback_data="back_home")]]
        await query.edit_message_text(
            "🗑️ All backgrounds cleared!",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return MANAGE_BACKGROUNDS

    elif query.data == "back_home":
        return await show_admin_home(query, context, edit=True)

    return MANAGE_BACKGROUNDS


async def bg_upload_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id

    # Check if uploading global worker backgrounds or personal admin backgrounds
    if context.user_data.get("uploading_global_bgs"):
        new_bgs = context.user_data.setdefault("new_global_bgs", [])
        if len(new_bgs) >= MAX_BG_IMAGES:
            await update.message.reply_text(f"❌ Max {MAX_BG_IMAGES} backgrounds reached.")
            return UPLOADING_BACKGROUNDS
        file_id = update.message.photo[-1].file_id
        new_bgs.append(file_id)
        await update.message.reply_text(
            f"✅ Worker background {len(new_bgs)} saved! Send more or /done to finish."
        )
    else:
        bgs = get_bg_images(context, uid)
        if len(bgs) >= MAX_BG_IMAGES:
            await update.message.reply_text(f"❌ Max {MAX_BG_IMAGES} backgrounds reached.")
            return UPLOADING_BACKGROUNDS
        file_id = update.message.photo[-1].file_id
        bgs.append(file_id)
        save_bg_images(context, uid, bgs)
        await update.message.reply_text(
            f"✅ Background {len(bgs)} saved! Send more or /done to finish."
        )
    return UPLOADING_BACKGROUNDS


async def bg_upload_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id

    if context.user_data.get("uploading_global_bgs"):
        new_bgs = context.user_data.get("new_global_bgs", [])
        set_global_backgrounds(context, new_bgs)
        context.user_data.pop("uploading_global_bgs", None)
        context.user_data.pop("new_global_bgs", None)
        keyboard = [[InlineKeyboardButton("🌍 Back to Global Settings", callback_data="admin_global")]]
        await update.message.reply_text(
            f"✅ *{len(new_bgs)} worker background(s) saved!*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        bgs = get_bg_images(context, uid)
        keyboard = [[InlineKeyboardButton("🏠 Back to Menu", callback_data="back_home")]]
        await update.message.reply_text(
            f"✅ *{len(bgs)} background(s) saved!*\n\nSend /start to go back.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    return MANAGE_BACKGROUNDS


# ── Presets Management (Admin) ─────────────────────────────────────────────────

async def show_presets_menu(query, context, uid):
    presets = get_presets(context, uid)
    global_preset = get_global_preset(context)
    keyboard = []
    for name in presets:
        is_active = global_preset and presets[name] == global_preset
        label = f"{'🌍 ' if is_active else ''}{'⚡ ' + name}"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"global_activate_{name}"),
            InlineKeyboardButton("📤 Export", callback_data=f"exportpreset_{name}"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"delpreset_{name}"),
        ])
    keyboard.append([InlineKeyboardButton("📥 Import Preset", callback_data="importpreset_menu")])
    keyboard.append([InlineKeyboardButton("◀️ Back",          callback_data="back_home")])

    await query.edit_message_text(
        f"⚙️ *My Presets* ({len(presets)}/{MAX_PRESETS})\n\n"
        "🌍 = currently active for workers\n\n"
        + ("No presets yet." if not presets else "Tap a preset name to set it as the worker preset."),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return MANAGE_PRESETS


async def presets_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    presets = get_presets(context, uid)

    if query.data.startswith("global_activate_") and query.data in [f"global_activate_{n}" for n in presets]:
        name = query.data.replace("global_activate_", "")
        if name in presets:
            set_global_preset(context, dict(presets[name]))
            await query.answer(f"✅ '{name}' is now the active worker preset!", show_alert=True)
        return await show_presets_menu(query, context, uid)

    elif query.data.startswith("exportpreset_"):
        name = query.data.replace("exportpreset_", "")
        if name in presets:
            code = encode_preset(presets[name])
            await query.edit_message_text(
                f"📤 *Export Preset: '{name}'*\n\n`{code}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_presets")
                ]]),
            )
        return MANAGE_PRESETS

    elif query.data.startswith("delpreset_"):
        name = query.data.replace("delpreset_", "")
        if name in presets:
            del presets[name]
            save_presets(context, uid, presets)
        return await show_presets_menu(query, context, uid)

    elif query.data == "importpreset_menu":
        await query.edit_message_text(
            "📥 *Import Preset*\n\nPaste your preset code:",
            parse_mode="Markdown",
        )
        context.user_data["importing_from_menu"] = True
        return IMPORT_PRESET

    elif query.data == "back_presets":
        return await show_presets_menu(query, context, uid)

    elif query.data == "back_home":
        return await show_admin_home(query, context, edit=True)

    return MANAGE_PRESETS


async def import_preset_menu_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    code = update.message.text.strip()
    settings = decode_preset(code)
    if not settings:
        await update.message.reply_text("❌ Invalid code. Try again or /start to cancel.")
        return IMPORT_PRESET
    await update.message.reply_text("✅ Valid preset! Type a name to save it:", parse_mode="Markdown")
    context.user_data["importing_settings"] = settings
    return SAVE_PRESET_NAME


# ── Cancel ─────────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Send /start to begin again.")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    persistence = PicklePersistence(filepath="/data/bot_persistence.pkl")
    app = Application.builder().token(BOT_TOKEN).persistence(persistence).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            # ── Shared ──
            WAITING_PHOTO: [
                MessageHandler(filters.PHOTO, photo_received),
                CallbackQueryHandler(admin_home_callback, pattern="^(admin_process|admin_global|go_backgrounds|go_presets|back_home)$"),
                CallbackQueryHandler(bg_type_chosen, pattern="^bg_(ai|saved)$"),
                CallbackQueryHandler(worker_callback, pattern="^(worker_send_photos|worker_generate|worker_generate_again|worker_back_home)$"),
            ],
            # ── Admin ──
            CHOOSE_BG_TYPE: [
                CallbackQueryHandler(bg_type_chosen, pattern="^bg_(ai|saved)$"),
            ],
            WAITING_AI_PROMPT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ai_prompt_received),
            ],
            CHOOSE_SAVED_BG: [
                CallbackQueryHandler(saved_bg_chosen, pattern="^pickbg_"),
            ],
            CHOOSING_PRESET_OR_MANUAL: [
                CallbackQueryHandler(preset_or_manual_chosen, pattern="^(use_manual|usepreset_|import_preset)"),
            ],
            CHOOSING_REMOVE_BG: [
                CallbackQueryHandler(remove_bg_chosen, pattern="^rbg_"),
            ],
            CHOOSING_ASPECT: [
                CallbackQueryHandler(aspect_chosen, pattern="^aspect_"),
            ],
            CHOOSING_PLACEMENT: [
                CallbackQueryHandler(placement_chosen, pattern="^place_"),
            ],
            CHOOSING_SCALE: [
                CallbackQueryHandler(scale_chosen, pattern="^scale_"),
            ],
            CHOOSING_FILTER: [
                CallbackQueryHandler(filter_chosen, pattern="^filter_"),
            ],
            RESULT_ACTIONS: [
                CallbackQueryHandler(result_action, pattern="^(confirm_yes|confirm_no|save_and_generate|generate_again)$"),
                CallbackQueryHandler(admin_home_callback, pattern="^back_home$"),
            ],
            SAVE_PRESET_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_preset_name_received),
            ],
            MANAGE_BACKGROUNDS: [
                CallbackQueryHandler(backgrounds_callback, pattern="^bg_"),
                CallbackQueryHandler(admin_home_callback, pattern="^back_home$"),
            ],
            UPLOADING_BACKGROUNDS: [
                MessageHandler(filters.PHOTO, bg_upload_received),
                CommandHandler("done", bg_upload_done),
            ],
            MANAGE_PRESETS: [
                CallbackQueryHandler(presets_callback, pattern="^(global_activate_|exportpreset_|delpreset_|importpreset_menu|back_presets|back_home)"),
            ],
            IMPORT_PRESET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, import_preset_menu_received),
            ],
            # ── Admin Global Settings ──
            GLOBAL_SETTINGS: [
                CallbackQueryHandler(global_settings_callback, pattern="^(global_set_preset|global_toggle_bg_mode|global_set_ai_prompt|global_set_backgrounds|back_global|back_home|admin_global)$"),
            ],
            GLOBAL_AI_PROMPT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, global_ai_prompt_received),
                CallbackQueryHandler(global_settings_callback, pattern="^admin_global$"),
            ],
            GLOBAL_ACTIVE_PRESET: [
                CallbackQueryHandler(global_active_preset_callback, pattern="^(global_activate_|back_global)"),
            ],
            # ── Worker ──
            WORKER_WAITING_PHOTO: [
                MessageHandler(filters.PHOTO, photo_received),
                CallbackQueryHandler(worker_callback, pattern="^(worker_send_photos|worker_generate|worker_generate_again|worker_back_home)$"),
            ],
            WORKER_RESULT: [
                CallbackQueryHandler(worker_callback, pattern="^(worker_generate_again|worker_back_home)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_message=False,
        per_chat=True,
        per_user=True,
    )

    app.add_handler(conv)
    logger.info("🚀 AI Background Studio (Enhanced) is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()