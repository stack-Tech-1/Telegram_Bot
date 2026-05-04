"""
✨ Image Uniquification Bot
============================
Features:
  - Upload & store photos per user
  - Generate unique variations with AI backgrounds
  - Multiple generations (1-10) with randomization
  - Full effects system: noise, blur, filters, overlays
  - Reusable templates with export/import codes
"""

import os
import io
import re
import time
import json
import math
import random
import logging
import hashlib
import asyncio
import urllib.parse
import sqlite3
import requests
import numpy as np
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw
from dotenv import load_dotenv

load_dotenv()

# ── Ensure /data directory exists (Railway volume) ─────────────────────────────
os.makedirs("/data", exist_ok=True)
os.makedirs("/data/u2net_models", exist_ok=True)

# Cache rembg model to /data so it persists across Railway deploys
os.environ.setdefault("U2NET_HOME", "/data/u2net_models")

from rembg import remove

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

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN is missing!")

ADMIN_IDS = set(
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
)

MAX_PHOTOS         = 10
MAX_TEMPLATES      = 10
ALBUM_COLLECT_SECS = 2

CATEGORY_PROMPTS = {
    "nature": [
        "lush green forest with golden sunlight rays, serene, scenic",
        "misty mountain lake at sunrise, reflections, peaceful",
        "autumn forest path with colorful fallen leaves, warm light",
        "tropical rainforest canopy, green and lush, natural",
        "sunflower field at golden hour, warm yellow tones",
        "rocky mountain peak above clouds, majestic landscape",
        "peaceful river flowing through green meadow, soft light",
        "cherry blossom trees in full bloom, pink petals",
        "snowy pine forest in winter, soft white light",
        "volcanic black sand beach, dramatic sky, scenic",
        "rolling green hills with wildflowers, pastoral scene",
        "desert sand dunes at sunset, golden orange tones",
        "bamboo forest, green and serene, Japanese aesthetic",
        "waterfall in tropical jungle, lush and vibrant",
    ],
    "city": [
        "modern city skyline at night with bokeh lights, urban",
        "rainy city street with reflections on wet pavement, moody",
        "futuristic city aerial view at dusk, purple and gold tones",
        "skyscrapers from below, dramatic upward perspective",
        "european cobblestone street with warm cafe lights at night",
        "hong kong neon city lights at night, vibrant colors",
        "london cityscape with river and bridges, daytime",
        "tokyo street at night with glowing signs, urban energy",
        "dubai skyline with modern skyscrapers, luxury aesthetic",
        "paris rooftop view in the evening, romantic",
        "chicago lakefront skyline, dramatic cloudy sky",
        "city bridge with light trails from traffic at night",
        "urban park in autumn, colorful trees and city backdrop",
        "golden gate bridge in morning fog, scenic",
    ],
    "office": [
        "clean modern office interior with white walls and natural light, minimalist",
        "cozy reading nook with warm lamp light and bookshelves",
        "sleek executive office with floor-to-ceiling windows, city view",
        "modern creative workspace with plants and wooden desk",
        "elegant boardroom with long table, professional setting",
        "bright home office with white desk and plant, airy",
        "industrial loft office with exposed brick and warm lights",
        "luxury hotel lobby with marble floors, elegant interior",
        "minimalist zen office space, calm and clean",
        "modern library interior with high shelves, warm wood tones",
        "contemporary art gallery white walls, clean and bright",
        "stylish cafe interior with warm lighting, creative space",
        "modern studio space with large windows and natural light",
        "clean pharmaceutical lab interior, white and clinical",
    ],
    "abstract": [
        "colorful geometric abstract art with vibrant shapes, modern",
        "fluid paint pour with marble texture, blue and gold",
        "neon glowing abstract lines on dark background, futuristic",
        "pastel watercolor abstract wash, soft and artistic",
        "dark dramatic smoke swirls with purple and blue tones",
        "holographic iridescent abstract surface, rainbow shimmer",
        "abstract golden geometric patterns on dark background",
        "colorful ink in water explosion, abstract art",
        "glitch art abstract digital texture, vibrant and edgy",
        "abstract aurora borealis waves, green and purple",
        "crystalline geometric prism light refraction, colorful",
        "abstract oil paint texture, rich impasto strokes",
        "retro synthwave grid perspective, purple and pink neon",
        "abstract black and white swirling pattern, elegant",
    ],
    "studio": [
        "professional photography studio with white seamless backdrop and soft box lights",
        "dark dramatic studio with single spotlight on grey background",
        "clean white infinity cove studio, professional and bright",
        "elegant studio with grey gradient backdrop and professional lighting",
        "modern photo studio with wooden floor and white background",
        "high key photography studio, pure white background, bright",
        "low key dark studio with moody atmospheric lighting",
        "studio with textured grey concrete backdrop, professional",
        "clean beige studio with warm soft lighting, natural feel",
        "professional studio with seamless black background, dramatic",
        "photography studio with coloured gel lighting, teal and amber",
        "clean studio setup with reflective floor and white background",
        "vintage studio aesthetic with warm amber light, retro",
        "studio with blurred bokeh city window backdrop, urban",
    ],
    "beach": [
        "tropical beach with turquoise water and white sand, sunny day",
        "dramatic ocean sunset with orange and pink sky, golden hour",
        "mediterranean coastline with clear blue water, scenic",
        "hawaii beach with palm trees and volcanic mountains, paradise",
        "misty rocky beach at sunrise, dramatic and moody",
        "aerial view of tropical island with clear lagoon, paradise",
        "caribbean beach with hammock between palm trees, relaxing",
        "white sand beach with gentle waves and blue sky, serene",
        "greek island coastal view with whitewashed buildings, blue sea",
        "bali tropical coastline, lush and vibrant",
        "stormy beach with dramatic waves and dark clouds",
        "crystal clear shallow water over sand, tropical",
        "beach bonfire at night with starry sky, warm glow",
        "sunrise over calm ocean, soft pastel sky, peaceful",
    ],
    "minimal": [
        "soft pastel gradient background, blush pink to lavender, clean",
        "white marble texture with gold veins, elegant and minimal",
        "clean light grey gradient background, professional and simple",
        "soft blue to white gradient, clean and modern",
        "warm cream and beige tones, minimal and elegant",
        "black gradient background, sleek and sophisticated",
        "soft sage green gradient, calm and natural",
        "pearl white textured surface, clean and luxurious",
        "silver metallic gradient background, premium feel",
        "deep navy blue gradient, professional and rich",
        "dusty rose pink background, soft and minimal",
        "soft chartreuse to white gradient, fresh and modern",
        "minimal terracotta warm gradient, earthy and stylish",
        "icy light blue gradient, clean and refreshing",
    ],
    "desert": [
        "vast sahara sand dunes at golden hour, warm orange tones",
        "arizona red rock canyon at sunset, dramatic landscape",
        "cracked dry desert salt flat, endless horizon, minimalist",
        "cactus silhouettes against vivid desert sunset, orange sky",
        "namib desert dunes with star field at night, dark and vast",
        "oasis in sandy desert with palm trees, blue water",
        "sandstone arch formation in desert canyon, dramatic light",
        "wind-carved desert rock formations, warm earthy tones",
        "desert road stretching to the horizon, golden hour",
        "wadi canyon with red sandstone walls and blue sky",
        "sand dunes with dramatic shadows and light, abstract",
        "atacama desert blooming with wildflowers after rain, colorful",
        "dead vlei white clay pan with dead acacia trees, Namibia",
        "desert lightning storm at night, dramatic and powerful",
    ],
    "winter": [
        "snow-covered pine forest at dawn, soft blue light",
        "frozen lake with cracked ice pattern, aerial view",
        "snowy mountain village with warm cabin lights, cozy",
        "blizzard storm with swirling snow, dramatic white",
        "ice cave interior with blue glacial walls, ethereal",
        "heavy snowfall in a city street at night, peaceful",
        "frozen waterfall with icicles, dramatic winter scene",
        "northern finland snowy forest with aurora overhead",
        "snow-covered japanese torii gate in winter, red on white",
        "polar landscape with icebergs in dark water",
        "fresh snow on tree branches, soft morning light",
        "alpine ski slope with powder snow, blue sky",
        "winter sunrise over snowy fields, pink and gold sky",
        "frozen river with snow banks, misty winter morning",
    ],
    "cyberpunk": [
        "neon-lit cyberpunk alley with rain reflections, blue and pink",
        "futuristic mega city at night with holographic billboards",
        "cyberpunk street market with glowing neon signs, tokyo inspired",
        "dark dystopian city skyline with purple neon glow",
        "rain-soaked cyberpunk rooftop at night, moody atmosphere",
        "glowing neon grid corridor, tron-inspired digital space",
        "cyberpunk character silhouette against neon city backdrop",
        "futuristic monorail in neon-lit city, motion blur",
        "hacker den with multiple screens glowing blue in the dark",
        "virtual reality landscape, glowing wireframe grid, neon",
        "cyberpunk night market with floating drones, vivid colors",
        "dark alley with red and teal neon reflections in puddles",
        "futuristic server room with blue light, sleek and tech",
        "holographic city map projections, purple and cyan glow",
    ],
    "ancient": [
        "machu picchu at golden sunrise, misty mountain backdrop",
        "angkor wat temple with lotus pond, cambodia at dawn",
        "greek parthenon against blue sky, white marble columns",
        "egyptian pyramids at dusk, orange and gold sky",
        "roman colosseum interior with dramatic light beams",
        "stonehenge at sunset with moody dramatic sky",
        "ancient chinese great wall winding over mountains",
        "petra rose-red rock city, jordan, dramatic canyon",
        "japanese castle surrounded by cherry blossoms, spring",
        "ancient mayan ruins in jungle, overgrown with moss",
        "colosseum in rome at night with golden lights",
        "ancient egyptian temple interior with hieroglyphic walls",
        "greek island monastery on cliff edge above blue sea",
        "prehistoric cave painting illuminated by torch light, warm",
    ],
    "garden": [
        "japanese zen garden with raked gravel and bonsai, serene",
        "english rose garden in full bloom, pink and red, summer",
        "tropical botanical garden with giant lily pads and flowers",
        "lavender field in provence, purple rows to the horizon",
        "cherry blossom garden path with pink petals falling",
        "dutch tulip field with vivid colors, rows of red and yellow",
        "formal french garden with geometric hedges and fountains",
        "wildflower meadow in golden afternoon light, natural",
        "greenhouse interior with lush tropical plants, warm",
        "bamboo garden path, green and peaceful, japanese",
        "butterfly garden with colorful flowers and butterflies",
        "secret garden with moss-covered stone wall and ivy",
        "water lily pond with reflection of trees, impressionist",
        "herb garden with terracotta pots and dappled sunlight",
    ],
    "ocean": [
        "deep ocean underwater scene with rays of light, blue and teal",
        "tropical ocean surface from below, sunlight through water, blue",
        "stormy ocean waves crashing, dramatic and powerful",
        "calm turquoise lagoon from above, crystal clear water",
        "ocean floor with coral reef, colorful fish and marine life",
        "aerial view of ocean waves on white sand, turquoise water",
        "underwater cave with blue bioluminescent light",
        "dark ocean depths with glowing jellyfish, mysterious",
        "ocean horizon at sunset, gold and orange reflections",
        "waves breaking on rocky coast, sea spray and mist",
        "mediterranean sea with clear blue water and rocky cliffs",
        "tropical ocean tide pool, colorful and vibrant",
        "sailing boat on calm ocean, aerial view, blue water",
        "underwater bubbles rising to the surface, deep blue",
    ],
    "sky": [
        "dramatic storm clouds at sunset, orange and purple tones",
        "clear blue sky with white cumulus clouds, peaceful",
        "milky way galaxy night sky, stars and purple nebula",
        "aurora borealis dancing over snow, green and purple",
        "golden hour sky with scattered clouds, warm tones",
        "blue sky with light wispy cirrus clouds, serene",
        "dramatic cumulonimbus thunderstorm clouds, dark and powerful",
        "pastel pink and orange sunrise sky, soft gradient",
        "full moon in dark night sky, stars surrounding",
        "vivid rainbow against dark storm clouds",
        "aerial view above clouds, white fluffy sea of clouds",
        "red and orange volcanic sunset sky, dramatic",
        "twilight blue hour sky just after sunset, deep blue",
        "foggy misty morning sky over mountains, soft grey",
    ],
    "space": [
        "nebula with vibrant purple and blue gas clouds, stars",
        "earth from space, blue marble, cloud swirls",
        "spiral galaxy viewed from space, deep field stars",
        "saturn with rings detailed, space background",
        "supernova explosion, vivid red orange plasma, cosmic",
        "dark matter void, deep space, thousands of stars",
        "astronaut floating in space, earth in background",
        "comet streaking through space, bright tail, stars",
        "lunar surface with earth rising over horizon, apollo",
        "cosmic black hole with light bending, interstellar",
        "meteor shower over dark earth from space",
        "deep space telescope view, thousands of galaxies",
        "jupiter with great red spot, space background",
        "star-forming region pillars of creation, cosmic colors",
    ],
}

STYLE_MODIFIERS = [
    "ultra realistic", "cinematic lighting", "4k HDR", "golden hour", "soft bokeh",
    "dramatic shadows", "moody atmosphere", "vivid colors", "aerial view",
    "wide angle lens", "telephoto compression", "misty fog", "bright sunshine",
    "film grain", "professional photography", "award winning photo",
    "volumetric lighting", "long exposure", "blue hour", "overcast light",
]

def generate_random_prompt() -> tuple:
    category = random.choice(list(CATEGORY_PROMPTS.keys()))
    base = random.choice(CATEGORY_PROMPTS[category])
    modifier = random.choice(STYLE_MODIFIERS)
    return f"{base}, {modifier}", category


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Conversation States ────────────────────────────────────────────────────────
(
    MAIN_MENU,
    UPLOADING_PHOTOS,
    SETTINGS_MENU,
    TEMPLATES_MENU,
    TEMPLATE_ACTION,
    TEMPLATE_SAVE_NAME,
    TEMPLATE_IMPORT_CODE,
    UNIQ_SETTINGS_MENU,
    SET_GENERATIONS,
    SET_CREATIVE_SIZE,
    SET_TRANSPARENCY,
    SET_POSITION,
    SET_NOISE,
    SET_FILTER,
    SET_BLUR_BG,
    SET_BLUR_FG,
    SET_OVERLAY,
) = range(17)

# ── Default Settings ───────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "generations":   3,
    "creative_size": 6,
    "transparency":  10,
    "position":      "center",
    "noise":         2,
    "filter":        "none",
    "blur_bg":       0,
    "blur_fg":       0,
    "overlay":       "none",
    "remove_bg":     False,
}

FILTER_LABELS = {
    "none":      "🎨 None",
    "warm":      "🔆 Warm",
    "cool":      "❄️ Cool",
    "cinematic": "🎬 Cinematic",
    "vintage":   "📷 Vintage",
    "grayscale": "⬛ Grayscale",
}

OVERLAY_LABELS = {
    "none":     "🚫 None",
    "emojis":   "😀 Emojis",
    "snow":     "❄️ Snow",
    "textures": "🌀 Textures",
}


# ══════════════════════════════════════════════════════════════════════════════
# DATA HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_user_photos(context, uid) -> list:
    return context.bot_data.get(f"photos_{uid}", [])

def set_user_photos(context, uid, photos: list):
    context.bot_data[f"photos_{uid}"] = photos

def get_user_settings(context, uid) -> dict:
    stored = context.bot_data.get(f"settings_{uid}", {})
    merged = dict(DEFAULT_SETTINGS)
    merged.update(stored)
    return merged

def set_user_settings(context, uid, settings: dict):
    context.bot_data[f"settings_{uid}"] = settings

def get_user_templates(context, uid) -> dict:
    return context.bot_data.setdefault(f"templates_{uid}", {})

def save_user_templates(context, uid, templates: dict):
    context.bot_data[f"templates_{uid}"] = templates

def get_active_template(context, uid) -> str | None:
    return context.bot_data.get(f"active_template_{uid}")

def set_active_template(context, uid, name: str | None):
    context.bot_data[f"active_template_{uid}"] = name


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE ENCODING
# ══════════════════════════════════════════════════════════════════════════════

def encode_template(settings: dict, name: str) -> str:
    payload = {"name": name, "settings": settings}
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    checksum = hashlib.md5(data.encode()).hexdigest()[:6].upper()
    encoded = urllib.parse.quote(data)
    return f"TPL-{checksum}-{encoded}"

def decode_template(code: str) -> dict | None:
    try:
        if not code.startswith("TPL-"):
            return None
        parts = code.split("-", 2)
        if len(parts) != 3:
            return None
        data = urllib.parse.unquote(parts[2])
        payload = json.loads(data)
        if "name" not in payload or "settings" not in payload:
            return None
        merged = dict(DEFAULT_SETTINGS)
        merged.update(payload["settings"])
        payload["settings"] = merged
        return payload
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

def settings_summary(s: dict) -> str:
    return (
        f"🔁 Generations: *{s['generations']}*\n"
        f"🎨 Background: _{s.get('category', 'studio').capitalize()}_\n"
        f"📐 Creative Size: *{s['creative_size']}/10*\n"
        f"💧 Transparency: *{s['transparency']}/10*\n"
        f"📍 Position: *{s['position'].capitalize()}*\n"
        f"✂️ Remove BG: *{'Yes' if s['remove_bg'] else 'No'}*\n"
        f"🌀 Noise: *{s['noise']}/10*\n"
        f"🎨 Filter: *{FILTER_LABELS[s['filter']]}*\n"
        f"🌫️ BG Blur: *{s['blur_bg']}/10*\n"
        f"🌫️ FG Blur: *{s['blur_fg']}/10*\n"
        f"✨ Overlay: *{OVERLAY_LABELS[s['overlay']]}*"
    )


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE PROCESSING
# ══════════════════════════════════════════════════════════════════════════════

BG_SIZE = (768, 768)

def init_bg_db():
    os.makedirs("/data/backgrounds", exist_ok=True)
    con = sqlite3.connect("/data/backgrounds.db", timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS backgrounds (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT    NOT NULL,
            category TEXT    NOT NULL,
            prompt   TEXT,
            added_by INTEGER,
            added_at REAL    NOT NULL
        )
    """)
    con.commit()
    con.close()


_BG_CACHE: dict = {}

def get_random_background() -> tuple:
    con = sqlite3.connect("/data/backgrounds.db", timeout=5)
    row = con.execute("SELECT id, filename FROM backgrounds ORDER BY RANDOM() LIMIT 1").fetchone()
    con.close()
    if not row:
        raise RuntimeError(
            "No backgrounds found in the library. "
            "Ask an admin to run /gen_bgs to populate the library."
        )
    bg_id, filename = row
    if bg_id not in _BG_CACHE:
        img = Image.open(f"/data/backgrounds/{filename}").convert("RGBA")
        _BG_CACHE[bg_id] = img.resize(BG_SIZE, Image.LANCZOS)
    return bg_id, _BG_CACHE[bg_id].copy()


def delete_background(bg_id: int) -> None:
    con = sqlite3.connect("/data/backgrounds.db", timeout=5)
    row = con.execute("SELECT filename FROM backgrounds WHERE id=?", (bg_id,)).fetchone()
    con.execute("DELETE FROM backgrounds WHERE id=?", (bg_id,))
    con.commit()
    con.close()
    if row:
        try:
            os.remove(f"/data/backgrounds/{row[0]}")
        except FileNotFoundError:
            pass
    _BG_CACHE.pop(bg_id, None)


def add_background_to_db(img: Image.Image, category: str, prompt: str | None, added_by: int | None) -> int:
    con = sqlite3.connect("/data/backgrounds.db", timeout=5)
    cur = con.execute(
        "INSERT INTO backgrounds (filename, category, prompt, added_by, added_at) VALUES (?,?,?,?,?)",
        ("", category, prompt, added_by, time.time()),
    )
    new_id = cur.lastrowid
    con.commit()
    con.close()
    path = f"/data/backgrounds/{new_id}.jpg"
    img.convert("RGB").save(path, "JPEG", quality=92)
    con = sqlite3.connect("/data/backgrounds.db", timeout=5)
    con.execute("UPDATE backgrounds SET filename=? WHERE id=?", (f"{new_id}.jpg", new_id))
    con.commit()
    con.close()
    return new_id


def fetch_pollinations_image(prompt: str) -> Image.Image:
    full = prompt + ", high quality, photorealistic, no people, no text"
    encoded = urllib.parse.quote(full)
    seed = int(time.time() * 1000) % 999999
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=768&height=768&nologo=true&seed={seed}"
    )
    max_retries = 5
    for attempt in range(max_retries):
        resp = requests.get(url, timeout=120)
        if resp.status_code == 429:
            wait = 2 ** attempt
            logger.warning(f"Rate limited (429), retrying in {wait}s")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGBA")
    raise RuntimeError("Pollinations.ai rate limit exceeded after retries.")



def remove_background(image_bytes: bytes) -> Image.Image:
    result = remove(image_bytes)
    return Image.open(io.BytesIO(result)).convert("RGBA")


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
    elif filter_name == "grayscale":
        img = img.convert("L").convert("RGB")
    return img.convert("RGBA")


def apply_noise(img: Image.Image, level: int) -> Image.Image:
    """Add random noise. Level 0 = none, 10 = heavy."""
    if level == 0:
        return img
    arr = np.array(img).astype(np.int16)
    intensity = level * 6
    noise = np.random.randint(-intensity, intensity, arr.shape, dtype=np.int16)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, img.mode)


def apply_blur(img: Image.Image, level: int) -> Image.Image:
    """Gaussian blur. Level 0 = none, 10 = heavy."""
    if level == 0:
        return img
    radius = level * 0.8
    return img.filter(ImageFilter.GaussianBlur(radius=radius))


def apply_transparency(img: Image.Image, level: int) -> Image.Image:
    """Set subject opacity. Level 10 = fully opaque, 1 = nearly transparent."""
    if level >= 10:
        return img
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    alpha = int((level / 10) * 255)
    a = a.point(lambda x: min(x, alpha))
    return Image.merge("RGBA", (r, g, b, a))


def apply_overlay(img: Image.Image, overlay_type: str, gen_seed: int) -> Image.Image:
    """Apply overlay effects."""
    if overlay_type == "none":
        return img

    rng = random.Random(gen_seed)
    draw = ImageDraw.Draw(img)
    w, h = img.size

    if overlay_type == "snow":
        for _ in range(rng.randint(80, 150)):
            x = rng.randint(0, w)
            y = rng.randint(0, h)
            r = rng.randint(2, 5)
            opacity = rng.randint(150, 255)
            draw.ellipse([x-r, y-r, x+r, y+r], fill=(255, 255, 255, opacity))

    elif overlay_type == "emojis":
        emojis = ["⭐", "✨", "💫", "🌟", "❤️", "🔥"]
        try:
            from PIL import ImageFont
            font = ImageFont.load_default()
        except Exception:
            font = None
        for _ in range(rng.randint(5, 12)):
            emoji = rng.choice(emojis)
            x = rng.randint(20, w - 40)
            y = rng.randint(20, h - 40)
            draw.text((x, y), emoji, fill=(255, 255, 255, 200), font=font)

    elif overlay_type == "textures":
        for _ in range(rng.randint(200, 400)):
            x = rng.randint(0, w)
            y = rng.randint(0, h)
            opacity = rng.randint(10, 40)
            draw.point([x, y], fill=(255, 255, 255, opacity))

    return img


def composite_single(
    subject_bytes: bytes,
    settings: dict,
    gen_index: int,
    background: "Image.Image | None" = None,
) -> bytes:
    """
    Process one image for one generation.
    gen_index is used to seed randomization so each generation differs.
    """
    rng = random.Random(int(time.time() * 1000) + gen_index * 997)
    s = settings
    bw, bh = BG_SIZE

    # ── Step 1: Load background from library ──
    if background is None:
        _, background = get_random_background()

    # ── Step 2: Apply background blur ──
    background = apply_blur(background, s["blur_bg"])

    # ── Step 3: Apply filter to background ──
    background = apply_filter(background, s["filter"])

    # ── Step 4: Remove subject background ──
    if s["remove_bg"]:
        subject = remove_background(subject_bytes)
    else:
        subject = Image.open(io.BytesIO(subject_bytes)).convert("RGBA")

    # ── Step 5: Scale subject (creative_size 1-10) ──
    # creative_size 10 = 90% of background, 1 = 15%
    base_scale = 0.15 + (s["creative_size"] / 10) * 0.75
    # Add slight randomization per generation (±5%)
    jitter = rng.uniform(-0.05, 0.05)
    scale = max(0.1, min(0.95, base_scale + jitter))

    ow, oh = subject.size
    max_dim = int(min(bw, bh) * scale)
    ratio = min(max_dim / ow, max_dim / oh)
    new_w = int(ow * ratio)
    new_h = int(oh * ratio)
    subject = subject.resize((new_w, new_h), Image.LANCZOS)

    # ── Step 6: Apply foreground blur ──
    subject = apply_blur(subject, s["blur_fg"])

    # ── Step 7: Apply transparency ──
    subject = apply_transparency(subject, s["transparency"])

    # ── Step 8: Position ──
    if s["position"] == "center":
        # Slight random offset per generation
        offset_x = rng.randint(-30, 30)
        offset_y = rng.randint(-30, 30)
        x = max(0, min(bw - new_w, (bw - new_w) // 2 + offset_x))
        y = max(0, min(bh - new_h, (bh - new_h) // 2 + offset_y))
    else:  # random
        x = rng.randint(0, max(0, bw - new_w))
        y = rng.randint(0, max(0, bh - new_h))

    # ── Step 9: Composite ──
    result = background.copy()
    result.paste(subject, (x, y), subject)

    # ── Step 10: Apply noise ──
    # Vary noise slightly per generation
    noise_level = max(0, min(10, s["noise"] + rng.randint(-1, 1)))
    result = apply_noise(result, noise_level)

    # ── Step 11: Apply overlay ──
    result = apply_overlay(result, s["overlay"], gen_index)

    # ── Output ──
    out = io.BytesIO()
    result.convert("RGB").save(out, format="JPEG", quality=80)
    out.seek(0)
    return out.read()


# ══════════════════════════════════════════════════════════════════════════════
# ALBUM COLLECTION (shared helper)
# ══════════════════════════════════════════════════════════════════════════════

async def _flush_album(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    chat_id = job.chat_id
    uid = job.user_id

    album_key = f"album_{uid}"
    album_data = context.bot_data.pop(album_key, None)
    if not album_data:
        return

    file_ids = album_data["file_ids"]
    n = len(file_ids)

    # Replace stored photos
    set_user_photos(context, uid, file_ids)

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ *{n} photo{'s' if n > 1 else ''} saved successfully!*\n\nUse 📥 *Get Photos* to generate your unique images.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload Photos", callback_data="menu_upload")],
        [InlineKeyboardButton("📥 Get Photos",    callback_data="menu_get")],
        [InlineKeyboardButton("⚙️ Settings",      callback_data="menu_settings")],
    ])


# ══════════════════════════════════════════════════════════════════════════════
# /start — Main Menu
# ══════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    uid = update.effective_user.id
    photos = get_user_photos(context, uid)
    active = get_active_template(context, uid)

    text = (
        "✨ *Image Uniquification Bot*\n\n"
        f"📸 Stored photos: *{len(photos)}*\n"
        f"🧩 Active template: *{active or 'None'}*\n\n"
        "What would you like to do?"
    )
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return MAIN_MENU


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "menu_upload":
        try:
            await query.edit_message_caption(
                "📤 *Upload Photos*\n\n"
                "Send me your photos now.\n"
                "You can send a single photo or an album.\n\n"
                "⚠️ *Previous photos will be replaced.*",
                parse_mode="Markdown",
            )
        except Exception:
            await query.edit_message_text(
                "📤 *Upload Photos*\n\n"
                "Send me your photos now.\n"
                "You can send a single photo or an album.\n\n"
                "⚠️ *Previous photos will be replaced.*",
                parse_mode="Markdown",
            )
        return UPLOADING_PHOTOS

    elif query.data == "menu_get":
        return await handle_get_photos(query, context, uid)

    elif query.data == "menu_settings":
        return await show_settings_menu(query, context, uid)

    elif query.data == "back_main":
        return await show_main_menu(query, context, uid)

    return MAIN_MENU


async def show_main_menu(query_or_msg, context, uid, edit=True):
    photos = get_user_photos(context, uid)
    active = get_active_template(context, uid)
    text = (
        "✨ *Image Uniquification Bot*\n\n"
        f"📸 Stored photos: *{len(photos)}*\n"
        f"🧩 Active template: *{active or 'None'}*\n\n"
        "What would you like to do?"
    )
    if edit:
        try:
            await query_or_msg.edit_message_caption(text, parse_mode="Markdown",
                                                    reply_markup=main_menu_keyboard())
        except Exception:
            await query_or_msg.edit_message_text(text, parse_mode="Markdown",
                                                 reply_markup=main_menu_keyboard())
    else:
        await query_or_msg.reply_text(text, parse_mode="Markdown",
                                      reply_markup=main_menu_keyboard())
    return MAIN_MENU


# ══════════════════════════════════════════════════════════════════════════════
# UPLOAD PHOTOS
# ══════════════════════════════════════════════════════════════════════════════

async def photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    photo = update.message.photo[-1]
    media_group_id = update.message.media_group_id
    file_id = photo.file_id

    album_key = f"album_{uid}"

    if media_group_id:
        if album_key not in context.bot_data:
            context.bot_data[album_key] = {"file_ids": [], "media_group_id": media_group_id}

        context.bot_data[album_key]["file_ids"].append(file_id)

        for j in context.job_queue.get_jobs_by_name(f"flush_{uid}"):
            j.schedule_removal()

        context.job_queue.run_once(
            _flush_album,
            when=ALBUM_COLLECT_SECS,
            chat_id=update.effective_chat.id,
            user_id=uid,
            name=f"flush_{uid}",
        )
        return UPLOADING_PHOTOS

    else:
        # Single photo — save immediately
        set_user_photos(context, uid, [file_id])
        await update.message.reply_text(
            "✅ *1 photo saved successfully!*\n\nUse 📥 *Get Photos* to generate your unique images.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
        return MAIN_MENU


# ══════════════════════════════════════════════════════════════════════════════
# GET PHOTOS — Core Generation
# ══════════════════════════════════════════════════════════════════════════════

async def handle_get_photos(query, context, uid) -> int:
    photos = get_user_photos(context, uid)

    if not photos:
        try:
            await query.edit_message_caption(
                "❌ *You have no uploaded photos.*\n\nUse 📤 Upload Photos first.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_main")
                ]]),
            )
        except Exception:
            await query.edit_message_text(
                "❌ *You have no uploaded photos.*\n\nUse 📤 Upload Photos first.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_main")
                ]]),
            )
        return MAIN_MENU

    s = get_user_settings(context, uid)
    n_photos = len(photos)
    n_gens = s["generations"]

    try:
        await query.edit_message_caption(
            f"⏳ *Generating {n_gens} batch{'es' if n_gens > 1 else ''} of {n_photos} photo{'s' if n_photos > 1 else ''}…*\n\n"
            f"Total output: *{n_gens * n_photos} images*\n\nThis may take a while, please wait.",
            parse_mode="Markdown",
        )
    except Exception:
        await query.edit_message_text(
            f"⏳ *Generating {n_gens} batch{'es' if n_gens > 1 else ''} of {n_photos} photo{'s' if n_photos > 1 else ''}…*\n\n"
            f"Total output: *{n_gens * n_photos} images*\n\nThis may take a while, please wait.",
            parse_mode="Markdown",
        )

    await do_generate(query.message, photos, s)
    return MAIN_MENU


async def _process_single_batch(bot, photo_file_ids: list, s: dict, gen_idx: int) -> list:
    """Download photos and process one full batch. Each background is deleted after use."""
    batch_results = []
    for photo_idx, file_id in enumerate(photo_file_ids):
        file = await bot.get_file(file_id)
        image_bytes = bytes(await file.download_as_bytearray())

        bg_id, bg_img = get_random_background()
        await asyncio.to_thread(delete_background, bg_id)

        result_bytes = await asyncio.to_thread(
            composite_single,
            subject_bytes=image_bytes,
            settings=s,
            gen_index=gen_idx * 100 + photo_idx + int(time.time() * 10) % 1000,
            background=bg_img,
        )
        batch_results.append(result_bytes)
    return batch_results


async def do_generate(msg, photo_file_ids: list, s: dict):
    """Run all generations concurrently and send results as batches."""
    n_gens = s["generations"]
    n_photos = len(photo_file_ids)

    try:
        await msg.reply_text(
            f"⚙️ *Generating {n_gens} batch{'es' if n_gens > 1 else ''}…*",
            parse_mode="Markdown",
        )

        # Run batches sequentially to avoid hitting Pollinations.ai rate limits
        tasks = [
            _process_single_batch(msg._bot, photo_file_ids, s, gen_idx)
            for gen_idx in range(n_gens)
        ]
        all_results = []
        for task in tasks:
            all_results.append(await task)

        # Send results in order
        for gen_idx, batch_results in enumerate(all_results):
            if n_photos == 1:
                await msg.reply_photo(
                    photo=batch_results[0],
                    caption=f"✅ Batch {gen_idx + 1}/{n_gens}",
                )
            else:
                media_group = [
                    InputMediaPhoto(
                        media=io.BytesIO(b),
                        caption=f"Batch {gen_idx + 1}/{n_gens} — Photo {i + 1}/{n_photos}" if i > 0 else f"✅ Batch {gen_idx + 1}/{n_gens}",
                    )
                    for i, b in enumerate(batch_results)
                ]
                await msg.reply_media_group(media=media_group)

        # Done
        await msg.reply_text(
            f"✅ *Done! {n_gens} batch{'es' if n_gens > 1 else ''} generated.*",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )

    except Exception as e:
        logger.error(f"Generation error: {e}")
        await msg.reply_text(
            f"❌ *Something went wrong:* `{str(e)[:200]}`\n\nPlease try again.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS MENU
# ══════════════════════════════════════════════════════════════════════════════

async def show_settings_menu(query, context, uid) -> int:
    keyboard = [
        [InlineKeyboardButton("🧩 Templates",               callback_data="settings_templates")],
        [InlineKeyboardButton("⚙️ Uniquification Settings", callback_data="settings_uniq")],
        [InlineKeyboardButton("🔙 Back",                    callback_data="back_main")],
    ]
    try:
        await query.edit_message_caption(
            "⚙️ *Settings*\n\nChoose what to configure:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        await query.edit_message_text(
            "⚙️ *Settings*\n\nChoose what to configure:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    return SETTINGS_MENU


async def settings_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "settings_templates":
        return await show_templates_menu(query, context, uid)
    elif query.data == "settings_uniq":
        return await show_uniq_settings(query, context, uid)
    elif query.data == "back_main":
        return await show_main_menu(query, context, uid)
    return SETTINGS_MENU


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATES
# ══════════════════════════════════════════════════════════════════════════════

async def show_templates_menu(query, context, uid) -> int:
    templates = get_user_templates(context, uid)
    active = get_active_template(context, uid)

    keyboard = []
    for name in templates:
        label = f"{'✅ ' if name == active else ''}{name}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"tpl_view_{name}")])

    keyboard.append([InlineKeyboardButton("➕ Save Current Settings as Template", callback_data="tpl_save")])
    keyboard.append([InlineKeyboardButton("📥 Import Template",                   callback_data="tpl_import")])
    keyboard.append([InlineKeyboardButton("🔙 Back",                              callback_data="back_settings")])

    text = (
        f"🧩 *Templates* ({len(templates)}/{MAX_TEMPLATES})\n\n"
        f"Active: *{active or 'None'}*\n\n"
        + ("Tap a template to manage it." if templates else "No templates yet. Save your current settings as a template.")
    )
    try:
        await query.edit_message_caption(text, parse_mode="Markdown",
                                         reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        await query.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(keyboard))
    return TEMPLATES_MENU


async def templates_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "tpl_save":
        templates = get_user_templates(context, uid)
        if len(templates) >= MAX_TEMPLATES:
            await query.answer(f"❌ Max {MAX_TEMPLATES} templates reached. Delete one first.", show_alert=True)
            return TEMPLATES_MENU
        try:
            await query.edit_message_caption(
                "➕ *Save Template*\n\nType a name for this template:",
                parse_mode="Markdown",
            )
        except Exception:
            await query.edit_message_text(
                "➕ *Save Template*\n\nType a name for this template:",
                parse_mode="Markdown",
            )
        return TEMPLATE_SAVE_NAME

    elif query.data == "tpl_import":
        try:
            await query.edit_message_caption(
                "📥 *Import Template*\n\nPaste the template code (starts with `TPL-`):",
                parse_mode="Markdown",
            )
        except Exception:
            await query.edit_message_text(
                "📥 *Import Template*\n\nPaste the template code (starts with `TPL-`):",
                parse_mode="Markdown",
            )
        return TEMPLATE_IMPORT_CODE

    elif query.data.startswith("tpl_view_"):
        name = query.data.replace("tpl_view_", "")
        return await show_template_action(query, context, uid, name)

    elif query.data == "back_settings":
        return await show_settings_menu(query, context, uid)

    elif query.data == "back_main":
        return await show_main_menu(query, context, uid)

    return TEMPLATES_MENU


async def show_template_action(query, context, uid, name) -> int:
    templates = get_user_templates(context, uid)
    active = get_active_template(context, uid)
    if name not in templates:
        return await show_templates_menu(query, context, uid)

    is_active = name == active
    keyboard = [
        [InlineKeyboardButton(
            "✅ Active (tap to deactivate)" if is_active else "▶️ Apply",
            callback_data=f"tpl_toggle_{name}"
        )],
        [InlineKeyboardButton("📤 Export", callback_data=f"tpl_export_{name}")],
        [InlineKeyboardButton("🗑️ Delete", callback_data=f"tpl_delete_{name}")],
        [InlineKeyboardButton("🔙 Back",   callback_data="back_templates")],
    ]
    s = templates[name]["settings"]
    text = f"🧩 *Template: {name}*\n\n" + settings_summary(s)
    try:
        await query.edit_message_caption(text, parse_mode="Markdown",
                                         reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        await query.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(keyboard))
    return TEMPLATE_ACTION


async def template_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    templates = get_user_templates(context, uid)

    if query.data == "back_templates":
        return await show_templates_menu(query, context, uid)

    elif query.data == "back_main":
        return await show_main_menu(query, context, uid)

    elif query.data.startswith("tpl_toggle_"):
        name = query.data.replace("tpl_toggle_", "")
        active = get_active_template(context, uid)
        if active == name:
            # Deactivate
            set_active_template(context, uid, None)
            await query.answer("Template deactivated.", show_alert=False)
        else:
            # Apply — copy settings to user settings
            set_user_settings(context, uid, dict(templates[name]["settings"]))
            set_active_template(context, uid, name)
            await query.answer(f"✅ '{name}' applied!", show_alert=False)
        return await show_template_action(query, context, uid, name)

    elif query.data.startswith("tpl_export_"):
        name = query.data.replace("tpl_export_", "")
        if name in templates:
            code = encode_template(templates[name]["settings"], name)
            try:
                await query.edit_message_caption(
                    f"📤 *Export: '{name}'*\n\nShare this code:\n\n`{code}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back", callback_data="back_templates")
                    ]]),
                )
            except Exception:
                await query.edit_message_text(
                    f"📤 *Export: '{name}'*\n\nShare this code:\n\n`{code}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back", callback_data="back_templates")
                    ]]),
                )
        return TEMPLATE_ACTION

    elif query.data.startswith("tpl_delete_"):
        name = query.data.replace("tpl_delete_", "")
        if name in templates:
            del templates[name]
            save_user_templates(context, uid, templates)
            if get_active_template(context, uid) == name:
                set_active_template(context, uid, None)
        return await show_templates_menu(query, context, uid)

    return TEMPLATE_ACTION


async def template_save_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    name = update.message.text.strip()[:30]
    templates = get_user_templates(context, uid)
    s = get_user_settings(context, uid)

    templates[name] = {"settings": dict(s)}
    save_user_templates(context, uid, templates)
    set_active_template(context, uid, name)

    await update.message.reply_text(
        f"✅ *Template '{name}' saved and applied!*",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return MAIN_MENU


async def template_import_code_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    code = update.message.text.strip()
    payload = decode_template(code)

    if not payload:
        await update.message.reply_text(
            "❌ *Invalid template code.* Try again or send /start to cancel.",
            parse_mode="Markdown",
        )
        return TEMPLATE_IMPORT_CODE

    templates = get_user_templates(context, uid)
    name = payload["name"]
    # Avoid name collision
    base = name
    counter = 1
    while name in templates:
        name = f"{base} ({counter})"
        counter += 1

    templates[name] = {"settings": dict(payload["settings"])}
    save_user_templates(context, uid, templates)

    await update.message.reply_text(
        f"✅ *Template '{name}' imported!*\n\nGo to Templates to apply it.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return MAIN_MENU


# ══════════════════════════════════════════════════════════════════════════════
# UNIQUIFICATION SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

async def show_uniq_settings(query, context, uid) -> int:
    s = get_user_settings(context, uid)
    keyboard = [
        [InlineKeyboardButton(f"🔁 Generations: {s['generations']}",         callback_data="set_generations")],
        [InlineKeyboardButton("─── Background ───",                           callback_data="noop")],
        [InlineKeyboardButton(f"📐 Creative Size: {s['creative_size']}/10",   callback_data="set_creative_size")],
        [InlineKeyboardButton(f"💧 Transparency: {s['transparency']}/10",     callback_data="set_transparency")],
        [InlineKeyboardButton(f"📍 Position: {s['position'].capitalize()}",   callback_data="set_position")],
        [InlineKeyboardButton(f"✂️ Remove BG: {'Yes' if s['remove_bg'] else 'No'}", callback_data="toggle_remove_bg")],
        [InlineKeyboardButton("─── Effects ───",                              callback_data="noop")],
        [InlineKeyboardButton(f"🌀 Noise: {s['noise']}/10",                   callback_data="set_noise")],
        [InlineKeyboardButton(f"🎨 Filter: {FILTER_LABELS[s['filter']]}",     callback_data="set_filter")],
        [InlineKeyboardButton(f"🌫️ BG Blur: {s['blur_bg']}/10",              callback_data="set_blur_bg")],
        [InlineKeyboardButton(f"🌫️ FG Blur: {s['blur_fg']}/10",              callback_data="set_blur_fg")],
        [InlineKeyboardButton(f"✨ Overlay: {OVERLAY_LABELS[s['overlay']]}",  callback_data="set_overlay")],
        [InlineKeyboardButton("🔙 Back",                                       callback_data="back_settings")],
    ]
    try:
        await query.edit_message_caption(
            "⚙️ *Uniquification Settings*\n\nTap any setting to change it:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        await query.edit_message_text(
            "⚙️ *Uniquification Settings*\n\nTap any setting to change it:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    return UNIQ_SETTINGS_MENU


async def uniq_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    s = get_user_settings(context, uid)

    if query.data == "noop":
        return UNIQ_SETTINGS_MENU

    elif query.data == "back_settings":
        return await show_settings_menu(query, context, uid)

    elif query.data == "back_uniq":
        return await show_uniq_settings(query, context, uid)

    elif query.data == "toggle_remove_bg":
        s["remove_bg"] = not s["remove_bg"]
        set_user_settings(context, uid, s)
        return await show_uniq_settings(query, context, uid)

    elif query.data == "set_position":
        s["position"] = "random" if s["position"] == "center" else "center"
        set_user_settings(context, uid, s)
        return await show_uniq_settings(query, context, uid)

    elif query.data == "set_generations":
        keyboard = build_scale_keyboard("gen", 1, 10, s["generations"], "back_uniq")
        try:
            await query.edit_message_caption("🔁 *Generations*\n\nChoose how many unique batches to generate (1–10):",
                                             parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await query.edit_message_text("🔁 *Generations*\n\nChoose how many unique batches to generate (1–10):",
                                          parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return SET_GENERATIONS

    elif query.data == "set_creative_size":
        keyboard = build_scale_keyboard("cs", 1, 10, s["creative_size"], "back_uniq")
        try:
            await query.edit_message_caption("📐 *Creative Size*\n\n1 = very small, 10 = fills most of background:",
                                             parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await query.edit_message_text("📐 *Creative Size*\n\n1 = very small, 10 = fills most of background:",
                                          parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return SET_CREATIVE_SIZE

    elif query.data == "set_transparency":
        keyboard = build_scale_keyboard("tr", 1, 10, s["transparency"], "back_uniq")
        try:
            await query.edit_message_caption("💧 *Transparency*\n\n1 = nearly invisible, 10 = fully opaque:",
                                             parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await query.edit_message_text("💧 *Transparency*\n\n1 = nearly invisible, 10 = fully opaque:",
                                          parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return SET_TRANSPARENCY

    elif query.data == "set_noise":
        keyboard = build_scale_keyboard("noise", 0, 10, s["noise"], "back_uniq")
        try:
            await query.edit_message_caption("🌀 *Noise*\n\n0 = none, 10 = heavy grain:",
                                             parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await query.edit_message_text("🌀 *Noise*\n\n0 = none, 10 = heavy grain:",
                                          parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return SET_NOISE

    elif query.data == "set_blur_bg":
        keyboard = build_scale_keyboard("blurbg", 0, 10, s["blur_bg"], "back_uniq")
        try:
            await query.edit_message_caption("🌫️ *Background Blur*\n\n0 = sharp, 10 = heavily blurred:",
                                             parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await query.edit_message_text("🌫️ *Background Blur*\n\n0 = sharp, 10 = heavily blurred:",
                                          parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return SET_BLUR_BG

    elif query.data == "set_blur_fg":
        keyboard = build_scale_keyboard("blurfg", 0, 10, s["blur_fg"], "back_uniq")
        try:
            await query.edit_message_caption("🌫️ *Foreground Blur*\n\n0 = sharp, 10 = heavily blurred:",
                                             parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await query.edit_message_text("🌫️ *Foreground Blur*\n\n0 = sharp, 10 = heavily blurred:",
                                          parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return SET_BLUR_FG

    elif query.data == "set_filter":
        keyboard = [
            [InlineKeyboardButton(label, callback_data=f"filter_{key}")]
            for key, label in FILTER_LABELS.items()
        ]
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_uniq")])
        try:
            await query.edit_message_caption("🎨 *Filter*\n\nChoose a colour filter for the background:",
                                             parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await query.edit_message_text("🎨 *Filter*\n\nChoose a colour filter for the background:",
                                          parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return SET_FILTER

    elif query.data == "set_overlay":
        keyboard = [
            [InlineKeyboardButton(label, callback_data=f"overlay_{key}")]
            for key, label in OVERLAY_LABELS.items()
        ]
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_uniq")])
        try:
            await query.edit_message_caption("✨ *Overlay*\n\nChoose an overlay effect:",
                                             parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await query.edit_message_text("✨ *Overlay*\n\nChoose an overlay effect:",
                                          parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return SET_OVERLAY

    return UNIQ_SETTINGS_MENU


def build_scale_keyboard(prefix: str, min_val: int, max_val: int, current: int, back_cb: str):
    """Build a row of buttons for 1-10 scale selection."""
    row1 = [InlineKeyboardButton(
        f"{'✅' if i == current else str(i)}", callback_data=f"{prefix}_val_{i}"
    ) for i in range(min_val, min_val + 6)]
    row2 = [InlineKeyboardButton(
        f"{'✅' if i == current else str(i)}", callback_data=f"{prefix}_val_{i}"
    ) for i in range(min_val + 5, max_val + 1)]
    back = [InlineKeyboardButton("🔙 Back", callback_data=back_cb)]
    rows = [row1]
    if row2:
        rows.append(row2)
    rows.append(back)
    return rows


# Scale value callbacks
async def scale_value_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    s = get_user_settings(context, uid)
    data = query.data

    if data == "back_uniq":
        return await show_uniq_settings(query, context, uid)

    # Parse prefix_val_N
    match = re.match(r"^(\w+)_val_(\d+)$", data)
    if match:
        prefix = match.group(1)
        val = int(match.group(2))
        mapping = {
            "gen":    "generations",
            "cs":     "creative_size",
            "tr":     "transparency",
            "noise":  "noise",
            "blurbg": "blur_bg",
            "blurfg": "blur_fg",
        }
        if prefix in mapping:
            s[mapping[prefix]] = val
            set_user_settings(context, uid, s)
        return await show_uniq_settings(query, context, uid)

    return UNIQ_SETTINGS_MENU


async def filter_value_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    s = get_user_settings(context, uid)

    if query.data == "back_uniq":
        return await show_uniq_settings(query, context, uid)

    if query.data.startswith("filter_"):
        key = query.data.replace("filter_", "")
        if key in FILTER_LABELS:
            s["filter"] = key
            set_user_settings(context, uid, s)

    return await show_uniq_settings(query, context, uid)


async def overlay_value_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    s = get_user_settings(context, uid)

    if query.data == "back_uniq":
        return await show_uniq_settings(query, context, uid)

    if query.data.startswith("overlay_"):
        key = query.data.replace("overlay_", "")
        if key in OVERLAY_LABELS:
            s["overlay"] = key
            set_user_settings(context, uid, s)

    return await show_uniq_settings(query, context, uid)



async def gen_bgs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _generation_running
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only.")
        return

    if _generation_running:
        await update.message.reply_text("⚙️ Generation already running (auto-replenishment is active). Try again later.")
        return

    args = context.args
    categories = [a for a in args if a in CATEGORY_PROMPTS] or list(CATEGORY_PROMPTS.keys())

    con = sqlite3.connect("/data/backgrounds.db")
    counts = {cat: con.execute("SELECT COUNT(*) FROM backgrounds WHERE category=?", (cat,)).fetchone()[0]
              for cat in categories}
    con.close()

    total = sum(max(0, len(CATEGORY_PROMPTS[c]) - counts[c]) for c in categories)
    if total == 0:
        await update.message.reply_text("✅ All categories are already fully populated!")
        return

    msg = await update.message.reply_text(
        f"⚙️ Generating *{total}* background(s) across {len(categories)} categor{'y' if len(categories)==1 else 'ies'}…\n"
        "_This may take several minutes. I'll update you on progress._",
        parse_mode="Markdown",
    )

    _generation_running = True
    done = 0
    for cat in categories:
        prompts = CATEGORY_PROMPTS[cat]
        existing = counts[cat]
        to_generate = prompts[existing:]
        for prompt in to_generate:
            try:
                img = await asyncio.to_thread(fetch_pollinations_image, prompt)
                await asyncio.to_thread(add_background_to_db, img, cat, prompt, uid)
                done += 1
                if done % 5 == 0 or done == total:
                    await msg.edit_text(
                        f"⏳ *Progress:* {done}/{total} backgrounds generated…",
                        parse_mode="Markdown",
                    )
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"gen_bgs error for '{cat}': {e}")

    _generation_running = False
    await msg.edit_text(
        f"✅ *Done!* Generated *{done}/{total}* backgrounds.\n"
        f"Use /gen_bgs to add more anytime.",
        parse_mode="Markdown",
    )


_generation_running = False


async def _auto_replenish_worker(target: int = 500) -> None:
    global _generation_running
    _generation_running = True
    logger.info(f"Auto-replenishment started (target={target})")
    try:
        while True:
            con = sqlite3.connect("/data/backgrounds.db", timeout=5)
            current = con.execute("SELECT COUNT(*) FROM backgrounds").fetchone()[0]
            con.close()
            if current >= target:
                break
            prompt, category = generate_random_prompt()
            try:
                img = await asyncio.to_thread(fetch_pollinations_image, prompt)
                await asyncio.to_thread(add_background_to_db, img, category, prompt, None)
            except Exception as e:
                logger.warning(f"Auto-replenish error: {e}")
                await asyncio.sleep(120)
                continue
            await asyncio.sleep(30)
    finally:
        _generation_running = False
        con = sqlite3.connect("/data/backgrounds.db", timeout=5)
        count = con.execute("SELECT COUNT(*) FROM backgrounds").fetchone()[0]
        con.close()
        logger.info(f"Auto-replenishment finished. Pool size: {count}")


async def check_bg_pool(context: ContextTypes.DEFAULT_TYPE) -> None:
    if _generation_running:
        return
    con = sqlite3.connect("/data/backgrounds.db", timeout=5)
    count = con.execute("SELECT COUNT(*) FROM backgrounds").fetchone()[0]
    con.close()
    if count < 150:
        logger.info(f"Pool low ({count} remaining) — starting auto-replenishment")
        asyncio.create_task(_auto_replenish_worker(target=500))


async def pool_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only.")
        return
    con = sqlite3.connect("/data/backgrounds.db")
    total = con.execute("SELECT COUNT(*) FROM backgrounds").fetchone()[0]
    rows = con.execute(
        "SELECT category, COUNT(*) FROM backgrounds GROUP BY category ORDER BY category"
    ).fetchall()
    con.close()
    status = "🔄 Replenishment running" if _generation_running else "💤 Idle"
    lines = [f"📦 *Background Pool Status*", f"Total: *{total}* images | {status}", ""]
    for cat, count in rows:
        lines.append(f"• {cat.capitalize()}: {count}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def admin_upload_bg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return MAIN_MENU

    caption = (update.message.caption or "").strip()
    parts = caption.split()
    if len(parts) < 2 or parts[0] != "#bg":
        await update.message.reply_text(
            "To add a background, send a photo with caption:\n`#bg <category>`\n\n"
            f"Categories: {', '.join(CATEGORY_PROMPTS.keys())}",
            parse_mode="Markdown",
        )
        return MAIN_MENU

    cat = parts[1].lower()
    if cat not in CATEGORY_PROMPTS:
        await update.message.reply_text(
            f"❌ Unknown category `{cat}`.\n\nValid: {', '.join(CATEGORY_PROMPTS.keys())}",
            parse_mode="Markdown",
        )
        return MAIN_MENU

    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_bytes = bytes(await file.download_as_bytearray())
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA").resize(BG_SIZE, Image.LANCZOS)
    bg_id = await asyncio.to_thread(add_background_to_db, img, cat, None, uid)
    await update.message.reply_text(
        f"✅ Background added to *{cat.capitalize()}* library (ID: {bg_id}).",
        parse_mode="Markdown",
    )
    return MAIN_MENU


# ── Cancel ─────────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled. Send /start to begin again.",
        reply_markup=main_menu_keyboard(),
    )
    return MAIN_MENU


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    init_bg_db()
    os.makedirs("/data", exist_ok=True)
    os.makedirs("/data/u2net_models", exist_ok=True)
    persistence = PicklePersistence(filepath="/data/bot_persistence.pkl")
    app = Application.builder().token(BOT_TOKEN).persistence(persistence).build()

    app.add_handler(CommandHandler("gen_bgs", gen_bgs))
    app.add_handler(CommandHandler("pool_status", pool_status))
    app.job_queue.run_repeating(check_bg_pool, interval=60, first=30)

    # Scale value pattern covers all numeric selectors
    scale_pattern = r"^(gen|cs|tr|noise|blurbg|blurfg)_val_\d+$"

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.PHOTO & filters.Caption(["#bg"]), admin_upload_bg),
                CallbackQueryHandler(main_menu_callback, pattern="^(menu_upload|menu_get|menu_settings|back_main)$"),
            ],
            UPLOADING_PHOTOS: [
                MessageHandler(filters.PHOTO, photo_received),
                CallbackQueryHandler(main_menu_callback, pattern="^(menu_upload|menu_get|menu_settings|back_main)$"),
            ],
            SETTINGS_MENU: [
                CallbackQueryHandler(settings_menu_callback, pattern="^(settings_templates|settings_uniq|back_main)$"),
            ],
            TEMPLATES_MENU: [
                CallbackQueryHandler(templates_menu_callback,
                                     pattern="^(tpl_save|tpl_import|back_settings|back_main|tpl_view_.+)$"),
            ],
            TEMPLATE_ACTION: [
                CallbackQueryHandler(template_action_callback,
                                     pattern="^(back_templates|back_main|tpl_toggle_.+|tpl_export_.+|tpl_delete_.+)$"),
            ],
            TEMPLATE_SAVE_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, template_save_name_received),
            ],
            TEMPLATE_IMPORT_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, template_import_code_received),
            ],
            UNIQ_SETTINGS_MENU: [
                CallbackQueryHandler(uniq_settings_callback,
                                     pattern="^(noop|back_settings|back_uniq|toggle_remove_bg|set_position|"
                                             "set_generations|set_creative_size|set_transparency|set_noise|"
                                             "set_filter|set_blur_bg|set_blur_fg|set_overlay)$"),
            ],
            SET_GENERATIONS: [
                CallbackQueryHandler(scale_value_callback, pattern=scale_pattern),
                CallbackQueryHandler(scale_value_callback, pattern="^back_uniq$"),
            ],
            SET_CREATIVE_SIZE: [
                CallbackQueryHandler(scale_value_callback, pattern=scale_pattern),
                CallbackQueryHandler(scale_value_callback, pattern="^back_uniq$"),
            ],
            SET_TRANSPARENCY: [
                CallbackQueryHandler(scale_value_callback, pattern=scale_pattern),
                CallbackQueryHandler(scale_value_callback, pattern="^back_uniq$"),
            ],
            SET_NOISE: [
                CallbackQueryHandler(scale_value_callback, pattern=scale_pattern),
                CallbackQueryHandler(scale_value_callback, pattern="^back_uniq$"),
            ],
            SET_BLUR_BG: [
                CallbackQueryHandler(scale_value_callback, pattern=scale_pattern),
                CallbackQueryHandler(scale_value_callback, pattern="^back_uniq$"),
            ],
            SET_BLUR_FG: [
                CallbackQueryHandler(scale_value_callback, pattern=scale_pattern),
                CallbackQueryHandler(scale_value_callback, pattern="^back_uniq$"),
            ],
            SET_FILTER: [
                CallbackQueryHandler(filter_value_callback, pattern="^(filter_.+|back_uniq)$"),
            ],
            SET_OVERLAY: [
                CallbackQueryHandler(overlay_value_callback, pattern="^(overlay_.+|back_uniq)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_message=False,
        per_chat=True,
        per_user=True,
    )

    app.add_handler(conv)
    logger.info("🚀 Image Uniquification Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()