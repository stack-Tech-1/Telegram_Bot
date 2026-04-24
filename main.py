"""
✨ AI Background Studio Bot — Enhanced Edition
================================================
Features:
  - Generate AI backgrounds (Pollinations.AI - free, no token needed)
  - Use your own uploaded background images
  - Save & reuse settings presets
  - Export/import presets to share with workers
  - One-tap "Generate Again" for instant new versions
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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    PicklePersistence,  # FIX 1: import persistence
    filters,
    ContextTypes,
)

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN is missing! Set it in Railway Variables.")

MAX_BG_IMAGES = 20  # max saved background images per user
MAX_PRESETS   = 10  # max saved presets per user

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Conversation States ────────────────────────────────────────────────────────
(
    WAITING_PHOTO,
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
) = range(16)

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


# ── User Data Helpers ──────────────────────────────────────────────────────────

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
    bg = background.convert("RGBA").resize(
        (ASPECT_RATIOS.get("1:1", (1024,1024))), Image.LANCZOS
    ) if background.size != subject.size else background.convert("RGBA")

    # Resize bg to match aspect ratio dimensions stored in context
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


# ── /start ─────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    uid = update.effective_user.id
    presets = get_presets(context, uid)
    bgs = get_bg_images(context, uid)

    keyboard = [
        [InlineKeyboardButton("📸 Process a Photo", callback_data="go_process")],
        [InlineKeyboardButton("🖼️ My Backgrounds",  callback_data="go_backgrounds"),
         InlineKeyboardButton("⚙️ My Presets",      callback_data="go_presets")],
    ]
    await update.message.reply_text(
        "✨ *AI Background Studio*\n\n"
        f"🖼️ Saved backgrounds: *{len(bgs)}/{MAX_BG_IMAGES}*\n"
        f"⚙️ Saved presets: *{len(presets)}/{MAX_PRESETS}*\n\n"
        "What would you like to do?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return WAITING_PHOTO


async def start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    presets = get_presets(context, uid)
    bgs = get_bg_images(context, uid)

    async def safe_edit(text, reply_markup=None, parse_mode="Markdown"):
        """Edit message whether it's a photo (caption) or text."""
        kwargs = {"parse_mode": parse_mode}
        if reply_markup:
            kwargs["reply_markup"] = reply_markup
        try:
            await query.edit_message_caption(text, **kwargs)
        except Exception:
            await query.edit_message_text(text, **kwargs)

    if query.data == "go_process":
        await safe_edit("📸 *Send me the photo you want to process!*")
        return WAITING_PHOTO

    elif query.data == "go_backgrounds":
        return await show_backgrounds_menu(query, context, uid)

    elif query.data == "go_presets":
        return await show_presets_menu(query, context, uid)

    elif query.data == "back_home":
        keyboard = [
            [InlineKeyboardButton("📸 Process a Photo", callback_data="go_process")],
            [InlineKeyboardButton("🖼️ My Backgrounds",  callback_data="go_backgrounds"),
             InlineKeyboardButton("⚙️ My Presets",      callback_data="go_presets")],
        ]
        await safe_edit(
            "✨ *AI Background Studio*\n\n"
            f"🖼️ Saved backgrounds: *{len(bgs)}/{MAX_BG_IMAGES}*\n"
            f"⚙️ Saved presets: *{len(presets)}/{MAX_PRESETS}*\n\n"
            "What would you like to do?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return WAITING_PHOTO

    return WAITING_PHOTO


# ── Photo Received ─────────────────────────────────────────────────────────────

async def photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = await update.message.reply_text("📥 Got your photo!")
    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_bytes = bytes(await file.download_as_bytearray())
    context.user_data["subject_bytes"] = image_bytes
    await msg.delete()

    uid = update.effective_user.id
    bgs = get_bg_images(context, uid)

    # Choose background type
    keyboard = [
        [InlineKeyboardButton("🤖 Generate AI Background", callback_data="bg_ai")],
    ]
    if bgs:
        keyboard.append([InlineKeyboardButton(
            f"🖼️ Use My Saved Backgrounds ({len(bgs)})", callback_data="bg_saved"
        )])

    await update.message.reply_text(
        "🎨 *What background do you want?*\n\n"
        "Choose how to create the background:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSE_BG_TYPE


# ── Background Type Choice ─────────────────────────────────────────────────────

async def bg_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "bg_ai":
        context.user_data["bg_type"] = "ai"
        await query.edit_message_text(
            "🤖 *AI Background*\n\n"
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
        uid = query.from_user.id
        bgs = get_bg_images(context, uid)
        keyboard = [
            [InlineKeyboardButton(f"🖼️ Background {i+1}", callback_data=f"pickbg_{i}")]
            for i in range(len(bgs))
        ]
        keyboard.append([InlineKeyboardButton("🔀 Random", callback_data="pickbg_random")])
        await query.edit_message_text(
            f"🖼️ *Choose a background* ({len(bgs)} saved)\n\n"
            "Pick one or use random:",
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
    uid = query.from_user.id
    bgs = get_bg_images(context, uid)

    if query.data == "pickbg_random":
        import random
        context.user_data["saved_bg_index"] = random.randint(0, len(bgs) - 1)
    else:
        idx = int(query.data.replace("pickbg_", ""))
        context.user_data["saved_bg_index"] = idx

    await query.edit_message_text(
        f"✅ Background selected!\n\nNow configuring settings…"
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

    # Show summary + option to save as preset
    bg_type = context.user_data.get("bg_type", "ai")
    prompt_line = f"🤖 AI: _{context.user_data.get('ai_prompt', '')}_ \n" if bg_type == "ai" else "🖼️ Saved background\n"

    keyboard = [
        [InlineKeyboardButton("✅ Generate!", callback_data="confirm_yes")],
        [InlineKeyboardButton("💾 Save as Preset & Generate", callback_data="save_and_generate")],
        [InlineKeyboardButton("🔄 Start Over", callback_data="confirm_no")],
    ]
    await query.edit_message_text(
        f"🚀 *Ready!*\n\n"
        f"{prompt_line}"
        + settings_summary(s),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return RESULT_ACTIONS


# ── Confirm & Generate ─────────────────────────────────────────────────────────

async def confirm_and_generate(query, context):
    s = context.user_data["settings"]
    bg_type = context.user_data.get("bg_type", "ai")
    prompt_line = f"🤖 AI: _{context.user_data.get('ai_prompt', '')}_\n" if bg_type == "ai" else "🖼️ Saved background\n"
    keyboard = [
        [InlineKeyboardButton("✅ Generate!", callback_data="confirm_yes")],
        [InlineKeyboardButton("💾 Save as Preset & Generate", callback_data="save_and_generate")],
        [InlineKeyboardButton("🔄 Start Over", callback_data="confirm_no")],
    ]
    await query.edit_message_text(
        f"🚀 *Ready!*\n\n"
        f"{prompt_line}"
        + settings_summary(s),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return RESULT_ACTIONS


async def confirm_and_generate_msg(msg, context):
    s = context.user_data["settings"]
    keyboard = [
        [InlineKeyboardButton("✅ Generate!", callback_data="confirm_yes")],
        [InlineKeyboardButton("💾 Save as Preset & Generate", callback_data="save_and_generate")],
        [InlineKeyboardButton("🔄 Start Over", callback_data="confirm_no")],
    ]
    await msg.reply_text(
        f"🚀 *Ready!*\n\n" + settings_summary(s),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return RESULT_ACTIONS


async def result_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_no":
        await query.edit_message_text("🔄 Send /start to begin again.")
        context.user_data.clear()
        return ConversationHandler.END

    elif query.data == "save_and_generate":
        await query.edit_message_text(
            "💾 *Save Preset*\n\nType a name for this preset:",
            parse_mode="Markdown",
        )
        return SAVE_PRESET_NAME

    elif query.data in ("confirm_yes", "generate_again"):
        # FIX 2: pick a new random background each time "Generate Again" is clicked
        if query.data == "generate_again" and context.user_data.get("bg_type") == "saved":
            import random
            uid = query.from_user.id
            bgs = get_bg_images(context, uid)
            if bgs:
                context.user_data["saved_bg_index"] = random.randint(0, len(bgs) - 1)

        # FIX 1 (part): handle photo vs text message when showing "working" status
        try:
            await query.edit_message_caption("⏳ *Working on it…*", parse_mode="Markdown")
        except Exception:
            await query.edit_message_text("⏳ *Working on it…*", parse_mode="Markdown")

        await do_generate(query.message, context, query.from_user.id)
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
    await do_generate_msg(update.message, context, uid)
    return RESULT_ACTIONS


# ── Core Generation ────────────────────────────────────────────────────────────

async def do_generate(msg, context, uid):
    """Generate and send the composited photo."""
    d = context.user_data
    s = d["settings"]
    width, height = ASPECT_RATIOS[s["aspect"]]

    try:
        # Step 1: Subject
        subject_bytes = d["subject_bytes"]
        if s["remove_bg"]:
            await msg.reply_text("✂️ Removing background…")
            subject = do_remove_bg(subject_bytes)
        else:
            subject = Image.open(io.BytesIO(subject_bytes)).convert("RGBA")

        # Step 2: Background
        bg_type = d.get("bg_type", "ai")
        if bg_type == "ai":
            await msg.reply_text("🤖 Generating AI background…")
            prompt = d.get("ai_prompt", "professional studio background")
            background = generate_ai_background(prompt, width, height)
        else:
            await msg.reply_text("🖼️ Loading your background…")
            idx = d.get("saved_bg_index", 0)
            bgs = get_bg_images(context, uid)
            file = await msg._bot.get_file(bgs[idx])
            bg_bytes = bytes(await file.download_as_bytearray())
            background = Image.open(io.BytesIO(bg_bytes)).convert("RGBA")
            background = background.resize((width, height), Image.LANCZOS)

        # Step 3: Composite
        final = composite_image(
            subject, background,
            s["placement"], SCALES[s["scale"]], s["filter"]
        )

        # Step 4: Send with action buttons
        caption = (
            f"✅ *Done!*\n\n"
            f"{'🤖 ' + d.get('ai_prompt','')[:40] if bg_type == 'ai' else '🖼️ Custom background'}\n"
            f"{settings_summary(s)}"
        )
        keyboard = [
            [InlineKeyboardButton("⚡ Generate Again (new BG)", callback_data="generate_again")],
            [InlineKeyboardButton("🏠 Back to Menu", callback_data="back_home")],
        ]
        await msg.reply_photo(
            photo=final,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Generation error: {e}")
        await msg.reply_text(
            f"❌ Something went wrong: `{str(e)[:200]}`\n\nSend /start to try again.",
            parse_mode="Markdown",
        )


async def do_generate_msg(msg, context, uid):
    """Same as do_generate but called from message context."""
    await do_generate(msg, context, uid)


# ── Backgrounds Management ─────────────────────────────────────────────────────

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
        "Upload images to use as backgrounds instead of AI generation.",
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
        await query.edit_message_text(
            f"📤 *Upload Backgrounds*\n\n"
            f"Current: {len(bgs)}/{MAX_BG_IMAGES}\n\n"
            "Send your background images now.\n"
            "When done, send /done",
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
        return await start_callback(update, context)

    return MANAGE_BACKGROUNDS


async def bg_upload_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    bgs = get_bg_images(context, uid)
    if len(bgs) >= MAX_BG_IMAGES:
        await update.message.reply_text(f"❌ Max {MAX_BG_IMAGES} backgrounds reached.")
        return UPLOADING_BACKGROUNDS
    file_id = update.message.photo[-1].file_id
    bgs.append(file_id)
    save_bg_images(context, uid, bgs)
    await update.message.reply_text(
        f"✅ Background {len(bgs)} saved! Send more or /done to finish.",
    )
    return UPLOADING_BACKGROUNDS


async def bg_upload_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    bgs = get_bg_images(context, uid)
    keyboard = [[InlineKeyboardButton("🏠 Back to Menu", callback_data="back_home")]]
    await update.message.reply_text(
        f"✅ *{len(bgs)} background(s) saved!*\n\nSend /start to go back.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return MANAGE_BACKGROUNDS


# ── Presets Management ─────────────────────────────────────────────────────────

async def show_presets_menu(query, context, uid):
    presets = get_presets(context, uid)
    keyboard = []
    for name in presets:
        keyboard.append([
            InlineKeyboardButton(f"📤 Export '{name}'", callback_data=f"exportpreset_{name}"),
            InlineKeyboardButton(f"🗑️ Delete",          callback_data=f"delpreset_{name}"),
        ])
    keyboard.append([InlineKeyboardButton("📥 Import Preset", callback_data="importpreset_menu")])
    keyboard.append([InlineKeyboardButton("◀️ Back",          callback_data="back_home")])

    await query.edit_message_text(
        f"⚙️ *My Presets* ({len(presets)}/{MAX_PRESETS})\n\n"
        + ("No presets yet. Configure settings and save as preset when processing a photo." if not presets else ""),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return MANAGE_PRESETS


async def presets_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    presets = get_presets(context, uid)

    if query.data.startswith("exportpreset_"):
        name = query.data.replace("exportpreset_", "")
        if name in presets:
            code = encode_preset(presets[name])
            await query.edit_message_text(
                f"📤 *Export Preset: '{name}'*\n\n"
                f"Share this code:\n\n`{code}`",
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
        return await start_callback(update, context)

    return MANAGE_PRESETS


async def import_preset_menu_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    code = update.message.text.strip()
    settings = decode_preset(code)

    if not settings:
        await update.message.reply_text("❌ Invalid code. Try again or /start to cancel.")
        return IMPORT_PRESET

    # If importing from menu, ask for a name to save it
    await update.message.reply_text(
        f"✅ Valid preset! Type a name to save it:",
        parse_mode="Markdown",
    )
    context.user_data["importing_settings"] = settings
    return SAVE_PRESET_NAME


# ── Cancel ─────────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Send /start to begin again.")
    return ConversationHandler.END


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    # FIX 1: Add PicklePersistence so bot_data (backgrounds & presets) survives restarts
    persistence = PicklePersistence(filepath="bot_persistence.pkl")
    app = Application.builder().token(BOT_TOKEN).persistence(persistence).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_PHOTO: [
                MessageHandler(filters.PHOTO, photo_received),
                CallbackQueryHandler(start_callback, pattern="^(go_|back_home)"),
            ],
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
                CallbackQueryHandler(result_action, pattern="^(confirm_yes|confirm_no|save_and_generate|generate_again)"),
                CallbackQueryHandler(start_callback, pattern="^back_home"),
            ],
            SAVE_PRESET_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_preset_name_received),
            ],
            MANAGE_BACKGROUNDS: [
                CallbackQueryHandler(backgrounds_callback, pattern="^bg_"),
                CallbackQueryHandler(start_callback, pattern="^back_home"),
            ],
            UPLOADING_BACKGROUNDS: [
                MessageHandler(filters.PHOTO, bg_upload_received),
                CommandHandler("done", bg_upload_done),
            ],
            MANAGE_PRESETS: [
                CallbackQueryHandler(presets_callback, pattern="^(exportpreset_|delpreset_|importpreset_menu|back_presets|back_home)"),
            ],
            IMPORT_PRESET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, import_preset_menu_received),
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