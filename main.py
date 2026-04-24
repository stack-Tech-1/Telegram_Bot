"""
✨ AI Background Generator Bot
================================
A Telegram bot that:
  1. Accepts a user photo
  2. Optionally removes the background (user's choice)
  3. Lets the user type a custom AI background prompt
  4. Generates a unique background via Hugging Face Inference API (free)
  5. Composites the photo onto the generated background
  6. Supports: placement, scale, filter, aspect ratio controls

Setup:
  pip install -r requirements.txt
  Set BOT_TOKEN and HF_API_TOKEN in .env or directly below.
  HF token needs "Make calls to the serverless Inference API" permission.
"""

import os
import io
import time
import logging
import requests
from pathlib import Path
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
BOT_TOKEN   = os.getenv("BOT_TOKEN", "8747707317:AAG8BGaiU0HRMm-YpRk4hEfIQ0iHsZAhLEc")
HF_API_TOKEN = os.getenv("HF_API_TOKEN")

# Hugging Face model via new router endpoint
HF_MODEL = "black-forest-labs/FLUX.1-schnell"
HF_API_URL = f"https://router.huggingface.co/hf-inference/models/{HF_MODEL}/v1/text-to-image"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Conversation States ────────────────────────────────────────────────────────
(
    WAITING_PHOTO,
    ASK_REMOVE_BG,
    WAITING_PROMPT,
    CHOOSING_ASPECT,
    CHOOSING_PLACEMENT,
    CHOOSING_SCALE,
    CHOOSING_FILTER,
    CONFIRMING,
) = range(8)

# ── Filter definitions ─────────────────────────────────────────────────────────
FILTERS = {
    "none":        "🎨 None",
    "warm":        "🔆 Warm",
    "cool":        "❄️ Cool",
    "cinematic":   "🎬 Cinematic",
    "vintage":     "📷 Vintage",
    "sharp":       "✨ Sharp",
    "soft":        "🌸 Soft",
    "grayscale":   "⬛ Grayscale",
}

ASPECT_RATIOS = {
    "1:1":   (1024, 1024),
    "4:3":   (1024, 768),
    "16:9":  (1024, 576),
    "9:16":  (576,  1024),
    "3:4":   (768,  1024),
}

PLACEMENTS = {
    "center":       "⬛ Center",
    "bottom_center":"⬇️ Bottom Center",
    "bottom_left":  "↙️ Bottom Left",
    "bottom_right": "↘️ Bottom Right",
    "top_center":   "⬆️ Top Center",
}

SCALES = {
    "small":  0.40,
    "medium": 0.60,
    "large":  0.80,
    "full":   0.95,
}


# ── Image Helpers ──────────────────────────────────────────────────────────────

def do_remove_bg(image_bytes: bytes) -> Image.Image:
    result = remove(image_bytes)
    return Image.open(io.BytesIO(result)).convert("RGBA")


def generate_background(prompt: str, width: int, height: int) -> Image.Image:
    """Call Hugging Face Inference API and return a PIL image."""
    headers = {
        "Authorization": f"Bearer {HF_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": prompt + ", high quality, detailed, photorealistic, 8k, no people, no humans",
        "parameters": {
            "width":  min(width, 768),
            "height": min(height, 768),
            "num_inference_steps": 4,
            "guidance_scale": 3.5,
        },
    }

    # New HF router endpoint returns image bytes directly
    for attempt in range(3):
        resp = requests.post(
            HF_API_URL,
            json=payload,
            headers=headers,
            timeout=120,
        )

        if resp.status_code == 503:
            try:
                wait = resp.json().get("estimated_time", 20)
            except Exception:
                wait = 20
            logger.info(f"Model loading, waiting {wait}s…")
            time.sleep(min(float(wait), 30))
            continue

        if not resp.ok:
            try:
                err = resp.json()
            except Exception:
                err = resp.text
            raise RuntimeError(f"HF API error {resp.status_code}: {err}")

        # Check content type — should be image
        content_type = resp.headers.get("content-type", "")
        if "image" in content_type:
            return Image.open(io.BytesIO(resp.content)).convert("RGBA")

        # Sometimes returns JSON with image URL
        try:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                img_url = data[0].get("url") or data[0].get("image")
                if img_url:
                    img_resp = requests.get(img_url, timeout=30)
                    return Image.open(io.BytesIO(img_resp.content)).convert("RGBA")
        except Exception:
            pass

        # Last resort — try treating content as image bytes
        return Image.open(io.BytesIO(resp.content)).convert("RGBA")

    raise TimeoutError("Hugging Face model took too long to load. Please try again in a moment.")


def apply_filter(img: Image.Image, filter_name: str) -> Image.Image:
    img = img.convert("RGB")
    if filter_name == "none":
        return img.convert("RGBA")
    elif filter_name == "warm":
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


def get_paste_position(
    bg_size: tuple, fg_size: tuple, placement: str
) -> tuple[int, int]:
    bw, bh = bg_size
    fw, fh = fg_size
    positions = {
        "center":        ((bw - fw) // 2,          (bh - fh) // 2),
        "bottom_center": ((bw - fw) // 2,           bh - fh - 20),
        "bottom_left":   (20,                        bh - fh - 20),
        "bottom_right":  (bw - fw - 20,              bh - fh - 20),
        "top_center":    ((bw - fw) // 2,            20),
    }
    return positions.get(placement, positions["center"])


def composite_image(
    subject: Image.Image,
    background: Image.Image,
    placement: str,
    scale: float,
    filter_name: str,
) -> bytes:
    bg = background.convert("RGBA")

    # Scale subject
    orig_w, orig_h = subject.size
    max_dim = int(min(bg.size) * scale)
    ratio = min(max_dim / orig_w, max_dim / orig_h)
    new_w = int(orig_w * ratio)
    new_h = int(orig_h * ratio)
    subject = subject.resize((new_w, new_h), Image.LANCZOS)

    # Apply filter to background
    bg = apply_filter(bg, filter_name)

    # Paste subject
    x, y = get_paste_position(bg.size, subject.size, placement)
    result = bg.copy()
    result.paste(subject, (x, y), subject)

    # Output
    out = io.BytesIO()
    result.convert("RGB").save(out, format="JPEG", quality=93)
    out.seek(0)
    return out.read()


# ── Bot Handlers ───────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "✨ *Welcome to the AI Background Studio Bot!*\n\n"
        "I'll help you create stunning, one-of-a-kind photos with *AI-generated backgrounds*.\n\n"
        "🖼 Every background is uniquely generated — no two are the same!\n\n"
        "📸 *Send me a photo to get started!*",
        parse_mode="Markdown",
    )
    return WAITING_PHOTO


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 *How it works:*\n\n"
        "1. Send a photo\n"
        "2. Choose whether to remove the background\n"
        "3. Describe the background you want (in your own words!)\n"
        "4. Pick aspect ratio, placement, scale & filter\n"
        "5. Get your unique AI-generated photo!\n\n"
        "_Use /start to begin. Use /cancel to stop anytime._",
        parse_mode="Markdown",
    )


async def photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = await update.message.reply_text("📥 Downloading your photo…")
    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_bytes = bytes(await file.download_as_bytearray())
    context.user_data["original_bytes"] = image_bytes
    context.user_data["remove_bg"] = False  # default: don't remove

    await msg.delete()

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, remove background", callback_data="removebg_yes"),
            InlineKeyboardButton("❌ No, keep it",            callback_data="removebg_no"),
        ]
    ]
    await update.message.reply_text(
        "🖼 *Do you want to remove the background from your photo?*\n\n"
        "_Choose YES for a clean cutout, or NO to keep the original photo as-is._",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ASK_REMOVE_BG


async def remove_bg_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    remove_bg = query.data == "removebg_yes"
    context.user_data["remove_bg"] = remove_bg

    label = "✅ Background will be removed." if remove_bg else "❌ Original photo kept as-is."
    await query.edit_message_text(
        f"{label}\n\n"
        "🎨 *Now describe the background you want AI to generate.*\n\n"
        "Be creative! Examples:\n"
        "• `luxury penthouse rooftop at sunset`\n"
        "• `magical forest with glowing lights`\n"
        "• `futuristic cyberpunk city at night`\n"
        "• `professional studio with soft bokeh`\n"
        "• `tropical beach with turquoise water`\n\n"
        "_Type your background description below:_",
        parse_mode="Markdown",
    )
    return WAITING_PROMPT


async def prompt_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    prompt = update.message.text.strip()
    if len(prompt) < 3:
        await update.message.reply_text("Please describe the background in a bit more detail.")
        return WAITING_PROMPT

    context.user_data["prompt"] = prompt

    keyboard = [
        [InlineKeyboardButton(ratio, callback_data=f"aspect_{ratio}") for ratio in ASPECT_RATIOS]
    ]
    await update.message.reply_text(
        f"✅ *Prompt saved:* _{prompt}_\n\n"
        "📐 *Choose an aspect ratio for the final image:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_ASPECT


async def aspect_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    ratio = query.data.replace("aspect_", "")
    context.user_data["aspect"] = ratio

    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"place_{key}")]
        for key, label in PLACEMENTS.items()
    ]
    await query.edit_message_text(
        f"✅ *Aspect ratio:* {ratio}\n\n"
        "📍 *Where should your photo be placed on the background?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_PLACEMENT


async def placement_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    placement = query.data.replace("place_", "")
    context.user_data["placement"] = placement

    keyboard = [
        [
            InlineKeyboardButton("🔹 Small (40%)",  callback_data="scale_small"),
            InlineKeyboardButton("🔷 Medium (60%)", callback_data="scale_medium"),
        ],
        [
            InlineKeyboardButton("🔶 Large (80%)",  callback_data="scale_large"),
            InlineKeyboardButton("🟠 Full (95%)",   callback_data="scale_full"),
        ],
    ]
    await query.edit_message_text(
        f"✅ *Placement:* {PLACEMENTS[placement]}\n\n"
        "📏 *How large should your photo appear on the background?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_SCALE


async def scale_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    scale_key = query.data.replace("scale_", "")
    context.user_data["scale"] = scale_key

    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"filter_{key}")]
        for key, label in FILTERS.items()
    ]
    # Split into rows of 2
    items = list(FILTERS.items())
    keyboard = [
        [
            InlineKeyboardButton(items[i][1], callback_data=f"filter_{items[i][0]}"),
            InlineKeyboardButton(items[i+1][1], callback_data=f"filter_{items[i+1][0]}"),
        ]
        for i in range(0, len(items) - 1, 2)
    ]

    await query.edit_message_text(
        f"✅ *Scale:* {scale_key.capitalize()}\n\n"
        "🎨 *Choose a filter to apply to the background:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_FILTER


async def filter_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    filter_key = query.data.replace("filter_", "")
    context.user_data["filter"] = filter_key

    # Show summary before generating
    d = context.user_data
    summary = (
        f"🚀 *Ready to generate! Here's your summary:*\n\n"
        f"🎨 *Background prompt:* _{d['prompt']}_\n"
        f"🖼 *Remove background:* {'Yes' if d['remove_bg'] else 'No'}\n"
        f"📐 *Aspect ratio:* {d['aspect']}\n"
        f"📍 *Placement:* {PLACEMENTS[d['placement']]}\n"
        f"📏 *Scale:* {d['scale'].capitalize()}\n"
        f"✨ *Filter:* {FILTERS[d['filter']]}\n\n"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Generate!", callback_data="confirm_yes"),
            InlineKeyboardButton("🔄 Start Over", callback_data="confirm_no"),
        ]
    ]
    await query.edit_message_text(
        summary,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CONFIRMING


async def confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_no":
        await query.edit_message_text("🔄 Restarting… Send /start to begin again.")
        context.user_data.clear()
        return ConversationHandler.END

    await query.edit_message_text(
        "⏳ *Generating your unique AI background…*\n\n"
        "_This usually takes 20–60 seconds. Please wait!_",
        parse_mode="Markdown",
    )

    d = context.user_data
    image_bytes   = d["original_bytes"]
    remove_bg_opt = d["remove_bg"]
    prompt        = d["prompt"]
    aspect        = d["aspect"]
    placement     = d["placement"]
    scale_key     = d["scale"]
    filter_key    = d["filter"]

    width, height = ASPECT_RATIOS[aspect]
    scale_val     = SCALES[scale_key]

    try:
        # Step 1: Remove background if requested
        if remove_bg_opt:
            await query.message.reply_text("✂️ Removing background…")
            subject = do_remove_bg(image_bytes)
        else:
            subject = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

        # Step 2: Generate background
        await query.message.reply_text("🎨 Generating AI background… (this takes ~30–60 seconds)")
        background = generate_background(prompt, width, height)

        # Step 3: Composite
        await query.message.reply_text("🖼 Compositing your photo…")
        final_bytes = composite_image(subject, background, placement, scale_val, filter_key)

        # Step 4: Send result
        caption = (
            f"✅ *Here's your unique AI photo!*\n\n"
            f"🎨 _{prompt}_\n"
            f"Filter: {FILTERS[filter_key]} | Scale: {scale_key.capitalize()} | {aspect}"
        )
        await query.message.reply_photo(photo=final_bytes, caption=caption, parse_mode="Markdown")
        await query.message.reply_text(
            "Want another? Send a new photo anytime, or /start to begin fresh!\n"
            "Each generation is 100% unique 🎲"
        )

    except Exception as e:
        logger.error(f"Generation error: {e}")
        await query.message.reply_text(
            f"❌ Something went wrong: `{str(e)[:200]}`\n\n"
            "Please try again with /start.",
            parse_mode="Markdown",
        )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Send /start to begin again.")
    return ConversationHandler.END


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.PHOTO, photo_received),
        ],
        states={
            WAITING_PHOTO:     [MessageHandler(filters.PHOTO, photo_received)],
            ASK_REMOVE_BG:     [CallbackQueryHandler(remove_bg_chosen, pattern="^removebg_")],
            WAITING_PROMPT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_received)],
            CHOOSING_ASPECT:   [CallbackQueryHandler(aspect_chosen,    pattern="^aspect_")],
            CHOOSING_PLACEMENT:[CallbackQueryHandler(placement_chosen, pattern="^place_")],
            CHOOSING_SCALE:    [CallbackQueryHandler(scale_chosen,     pattern="^scale_")],
            CHOOSING_FILTER:   [CallbackQueryHandler(filter_chosen,    pattern="^filter_")],
            CONFIRMING:        [CallbackQueryHandler(confirmed,        pattern="^confirm_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_message=False,
        per_chat=True,
        per_user=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("help", help_cmd))

    logger.info("🚀 AI Background Studio Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()