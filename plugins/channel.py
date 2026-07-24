import re
import logging
import asyncio
import aiohttp
import html
import io
import textwrap
import json
from datetime import datetime
from collections import defaultdict
import urllib.parse
from typing import Optional, Tuple, Dict, List
from bs4 import BeautifulSoup

# Pillow (PIL) for image editing
from PIL import Image, ImageDraw, ImageFont

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait
from pymongo.errors import PyMongoError, DuplicateKeyError

# Plugin & Database Imports
from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx, fetch_image, get_movie_details
from database.users_chats_db import db
from database.ia_filterdb import save_file
from utils import temp
from Script import script
from info import (
    CHANNELS, MOVIE_UPDATE_CHANNEL, LINK_PREVIEW, ABOVE_PREVIEW, 
    BAD_WORDS, LANDSCAPE_POSTER, TMDB_POSTER, NOR_IMG, IMDB_TEMPLATE,
    TMDB_API_KEY
)

logger = logging.getLogger(__name__)

# ============ GEMINI AI CONFIGURATION ============
GEMINI_API_KEY = "AIzaSyCUuLfxuwA19ILtBjuWTZFUlPe1y7tA0JA"

SESSION: Optional[aiohttp.ClientSession] = None

async def get_session() -> aiohttp.ClientSession:
    global SESSION
    if SESSION is None or SESSION.closed:
        SESSION = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=50))
    return SESSION

POSTED_MOVIES = set()
MAX_CACHE_SIZE = 500
locks = defaultdict(asyncio.Lock)

IGNORE_WORDS = {
    "rarbg", "dub", "sub", "sample", "mkv", "aac", "combined", "mp4", "avi",
    "action", "adventure", "animation", "biography", "comedy", "crime", 
    "documentary", "drama", "fantasy", "film-noir", "history", 
    "horror", "music", "musical", "mystery", "romance", "sci-fi", "sport", 
    "thriller", "war", "western", "hdcam", "hdtc", "camrip", "ts", "tc", 
    "telesync", "dvdscr", "dvdrip", "predvd", "webrip", "web-dl", "tvrip", 
    "hdtv", "web dl", "webdl", "bluray", "brrip", "bdrip", "360p", "480p", 
    "720p", "1080p", "2160p", "4k", "1440p", "540p", "240p", "140p", "hevc", 
    "hdrip", "hin", "hindi", "tam", "tamil", "kan", "kannada", "tel", "telugu", 
    "mal", "malayalam", "eng", "english", "pun", "punjabi", "ben", "bengali", 
    "mar", "marathi", "guj", "gujarati", "urd", "urdu", "kor", "korean", "jpn", 
    "japanese", "nf", "netflix", "sonyliv", "sony", "sliv", "amzn", "prime", 
    "primevideo", "hotstar", "zee5", "jio", "jiohotstar", "jhs", "aha", "hbo", "paramount", 
    "apple", "hoichoi", "sunnxt", "viki", "x264", "x265", "avc", "dd5", "dovi", "hdr",
    "10bit", "10-bit", "8bit", "8-bit"
} | BAD_WORDS

# ============ TMDB LANGUAGE CODE TO FULL NAME MAPPING ============
TMDB_LANG_MAP = {
    "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam",
    "kn": "Kannada", "en": "English", "bn": "Bengali", "mr": "Marathi",
    "gu": "Gujarati", "pa": "Punjabi", "ur": "Urdu", "ko": "Korean",
    "ja": "Japanese", "es": "Spanish", "fr": "French", "de": "German",
    "zh": "Chinese", "ru": "Russian", "it": "Italian", "pt": "Portuguese",
    "ar": "Arabic", "nl": "Dutch", "sv": "Swedish", "pl": "Polish",
    "vi": "Vietnamese", "th": "Thai", "id": "Indonesian", "ms": "Malay",
    "tr": "Turkish", "el": "Greek", "he": "Hebrew", "cs": "Czech",
    "da": "Danish", "fi": "Finnish", "hu": "Hungarian", "no": "Norwegian",
    "ro": "Romanian", "sk": "Slovak", "sl": "Slovenian", "hr": "Croatian",
}

CAPTION_LANGUAGES = {
    "hin": "Hindi", "hindi": "Hindi", "tam": "Tamil", "tamil": "Tamil",
    "kan": "Kannada", "kannada": "Kannada", "tel": "Telugu", "telugu": "Telugu",
    "mal": "Malayalam", "malayalam": "Malayalam", "eng": "English", "english": "English",
    "pun": "Punjabi", "punjabi": "Punjabi", "ben": "Bengali", "bengali": "Bengali",
    "mar": "Marathi", "marathi": "Marathi", "guj": "Gujarati", "gujarati": "Gujarati",
    "urd": "Urdu", "urdu": "Urdu", "kor": "Korean", "korean": "Korean",
    "jpn": "Japanese", "japanese": "Japanese",
}

CLEAN_PATTERN = re.compile(r'@[^ \n\r\t\.,:;!?()\[\]{}<>\\/"\'=_%]+|\bwww\.[^\s\]\)]+|\([\@^]+\)|\[[\@^]+\]')
NORMALIZE_PATTERN = re.compile(r"[._\-\+]+|[()\[\]{}:;'â€“!,.?]")
QUALITY_PATTERN = re.compile(
    r"\b(?:HDCam|HDTC|CamRip|TS|TC|TeleSync|DVDScr|DVDRip|PreDVD|"
    r"WEBRip|WEB-DL|TVRip|HDTV|WEB DL|WebDl|BluRay|BRRip|BDRip|"
    r"360p|480p|720p|1080p|2160p|4K|1440p|540p|240p|140p|HEVC|HDRip|x264|x265|10bit|10-bit|8bit)\b", 
    re.IGNORECASE
)
YEAR_PATTERN = re.compile(r"(?<![A-Za-z0-9])(19\d{2}|20\d{2})(?![A-Za-z0-9])")
EPISODE_CLEAN_PATTERN = re.compile(r'\b(S\d{1,2}|E\d{1,3}|Ep\d{1,3}|Episode\s*\d{1,3}|Season\s*\d{1,2}|Part\s*\d{1,2}|\d{1,2}\s*-\s*\d{1,2}|\d{1,3}\s*to\s*\d{1,3})\b', re.IGNORECASE)

MEDIA_FILTER = filters.document | filters.video | filters.audio

# ============ AI LANGUAGE DETECTION (DIRECT API CALL) ============

async def detect_language_with_ai(movie_name: str) -> str:
    """
    Direct REST API ਨਾਲ Gemini AI ਦੁਆਰਾ ਸਹੀ ਭਾਸ਼ਾ ਪਛਾਣਨ ਵਾਲਾ ਫੰਕਸ਼ਨ
    """
    if not GEMINI_API_KEY:
        return "Hindi"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""
    You are an expert movie language analyzer.
    Task: Identify the exact primary original audio language of the movie/show titled: "{movie_name}".

    STRICT RULES:
    1. DO NOT DEFAULT TO ENGLISH.
    2. Check the cast, director, region, and title (e.g., "Warning 2" is Punjabi, "Carry On Jatta" is Punjabi, "Thukra Ke Mera Pyaar" is Hindi).
    3. Output strictly raw JSON format without markdown code blocks.

    JSON Structure:
    {{"language": "Punjabi"}}
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    try:
        session = await get_session()
        async with session.post(url, json=payload, timeout=10) as resp:
            if resp.status == 200:
                result = await resp.json()
                text_resp = result['candidates'][0]['content']['parts'][0]['text']
                clean_json = text_resp.replace("```json", "").replace("```", "").strip()
                data = json.loads(clean_json)
                return data.get("language", "Hindi").title()
    except Exception as e:
        logger.error(f"AI Language Detection Error: {e}")
    
    return "Hindi"

# ============ HD POSTER WITH TITLE OVERLAY ============

async def create_title_only_poster(backdrop_url: str, title: str) -> Optional[bytes]:
    """
    HD Landscape Poster ਦੇ ਉੱਪਰ ਮੂਵੀ ਦਾ ਨਾਮ ਆਟੋਮੈਟਿਕ ਲਿਖਣ ਦਾ ਫੰਕਸ਼ਨ
    """
    try:
        session = await get_session()
        async with session.get(backdrop_url) as resp:
            if resp.status != 200:
                return None
            img_data = await resp.read()
        
        image = Image.open(io.BytesIO(img_data)).convert("RGBA")
        img_w, img_h = image.size
        
        draw = ImageDraw.Draw(image)
        
        try:
            font_size = int(img_w * 0.08)
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except:
            font = ImageFont.load_default()
        
        wrapped_title = textwrap.fill(title.upper(), width=16)
        
        bbox = draw.textbbox((0, 0), wrapped_title, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        
        x = (img_w - text_w) // 2
        y = (img_h - text_h) // 2
        
        # Background Overlay
        overlay = Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        padding = 30
        overlay_draw.rectangle(
            [x - padding, y - padding, x + text_w + padding, y + text_h + padding],
            fill=(0, 0, 0, 160)
        )
        image = Image.alpha_composite(image, overlay)
        draw = ImageDraw.Draw(image)
        
        # Draw Text
        draw.text((x, y), wrapped_title, font=font, fill=(255, 255, 255, 255))
        
        output = io.BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=95)
        return output.getvalue()
        
    except Exception as e:
        logger.error(f"Title poster generation failed: {e}")
        return None

# ============ POSTER FETCHERS ============

async def fetch_cinemeta_ai_poster(query: str, is_series: bool = False) -> Optional[str]:
    try:
        session = await get_session()
        m_type = "series" if is_series else "movie"
        encoded_query = urllib.parse.quote(query)
        
        search_url = f"https://v3-cinemeta.strem.io/catalog/{m_type}/top/search={encoded_query}.json"
        async with session.get(search_url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                metas = data.get("metas", [])
                if metas:
                    best_match = metas[0]
                    background = best_match.get("background")
                    if background and any(x in background for x in ["images.metahub.space", "tmdb", "themoviedb"]):
                        return background
    except Exception as e:
        logger.error(f"Cinemeta AI Metadata Error: {e}")
    return None

async def get_landscape_poster_only(movie_name: str, is_series: bool = False) -> Optional[str]:
    if LANDSCAPE_POSTER:
        try:
            details = await get_movie_detailsx(movie_name)
            if details and details.get('backdrop_url'):
                backdrop = details['backdrop_url']
                if "t/p/" in backdrop:
                    backdrop = re.sub(r'/t/p/w\d+/', '/t/p/original/', backdrop)
                return backdrop
        except Exception as e:
            logger.error(f"TMDB backdrop error: {e}")

    ai_backdrop = await fetch_cinemeta_ai_poster(movie_name, is_series)
    if ai_backdrop:
        return ai_backdrop

    return None

# ============ CLEANING AND EXTRACTION FUNCTIONS ============

def clean_mentions_links(text: str) -> str:
    return CLEAN_PATTERN.sub("", text or "").strip()

def normalize(s: str) -> str:
    s = NORMALIZE_PATTERN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def remove_ignored_words(text: str) -> str:
    IGNORE_WORDS_LOWER = {w.lower() for w in IGNORE_WORDS}
    words = text.split()
    cleaned_words = []
    for word in words:
        if word.lower() in IGNORE_WORDS_LOWER:
            break
        cleaned_words.append(word)
    return " ".join(cleaned_words)

def extract_languages_from_text(text: str) -> set:
    found = set()
    text_lower = text.lower()
    for lang_key, lang_name in CAPTION_LANGUAGES.items():
        if re.search(rf'\b{re.escape(lang_key)}\b', text_lower):
            found.add(lang_name)
    return found

def extract_media_info(filename: str, caption: str):
    filename_cleaned = clean_mentions_links(filename)
    filename_normalized = normalize(filename_cleaned)
    caption_clean = clean_mentions_links(caption).lower() if caption else ""

    tag = "#MOVIE"
    year = None
    
    quality = QUALITY_PATTERN.findall(filename_normalized)
    quality_str = ", ".join(quality) if quality else "N/A"

    lang_set = set()
    lang_set.update(extract_languages_from_text(filename_normalized))
    lang_set.update(extract_languages_from_text(caption_clean))
    language = ", ".join(sorted(lang_set)) if lang_set else "N/A"

    if EPISODE_CLEAN_PATTERN.search(filename_normalized):
        tag = "#SERIES"
    
    clean_name = EPISODE_CLEAN_PATTERN.sub(" ", filename_normalized)
    clean_name = QUALITY_PATTERN.sub(" ", clean_name)

    year_match = YEAR_PATTERN.search(clean_name)
    if year_match:
        year = year_match.group(1)
        idx = clean_name.find(year)
        base_raw = clean_name[:idx].strip()
    else:
        base_raw = clean_name

    base_name = normalize(remove_ignored_words(base_raw))
    if not base_name:
        base_name = filename_normalized

    if tag == "#SERIES":
        base_name = re.sub(r'\bS\d{1,2}\b', '', base_name, flags=re.IGNORECASE).strip()
        base_name = normalize(base_name)

    return {
        "processed": filename_normalized,
        "base_name": base_name.title(),
        "tag": tag,
        "year": year,
        "quality": quality_str,
        "language": language
    }

# ============ MAIN HANDLERS ============

@Client.on_message(filters.chat(CHANNELS) & MEDIA_FILTER)
async def media_handler(bot, message):
    media = next((getattr(message, ft) for ft in ("document", "video", "audio") if getattr(message, ft, None)), None)
    if not media:
        return

    media.file_type = next(ft for ft in ("document", "video", "audio") if hasattr(message, ft))
    media.caption = message.caption or ""
    
    success, info = await save_file(media)
    if not success:
        return

    try:
        if await db.movie_update_status(bot.me.id):
            await process_and_send_update(bot, media.file_name, media.caption)
    except Exception:
        logger.exception("Error processing incoming media updates")

async def process_and_send_update(bot, filename, caption):
    try:
        media_info = extract_media_info(filename, caption)
        base_name = media_info["base_name"]
        movie_key = base_name.lower()
        
        if len(POSTED_MOVIES) > MAX_CACHE_SIZE:
            POSTED_MOVIES.clear()
        
        if movie_key in POSTED_MOVIES:
            return

        async with locks[base_name]:
            if movie_key in POSTED_MOVIES:
                return
            POSTED_MOVIES.add(movie_key)
            
            try:
                await _process_with_lock(bot, filename, caption, media_info, base_name)
            finally:
                await asyncio.sleep(12)
                POSTED_MOVIES.discard(movie_key)
                
    except Exception as e:
        logger.exception(f"Processing execution failed: {e}")

# ============ PROCESS WITH LOCK ============

async def _process_with_lock(bot, filename, caption, media_info, base_name):
    if not hasattr(db, 'movie_updates'):
        db.movie_updates = db.db.movie_updates

    error_tmdb = False
    file_data = {
        "filename": filename,
        "quality": media_info["quality"],
        "language": media_info["language"],
        "timestamp": datetime.now()
    }

    try:
        details = {}
        tmdb_language_override = None
        
        if TMDB_POSTER:
            try:
                details = await get_movie_detailsx(base_name)
                if not details or details.get("error"):
                    error_tmdb = True
                else:
                    orig_lang = details.get("original_language") or details.get("lang")
                    if orig_lang:
                        tmdb_language_override = TMDB_LANG_MAP.get(orig_lang.lower())
            except Exception:
                error_tmdb = True
                
        if not TMDB_POSTER or error_tmdb or not details:
            details = await get_movie_details(base_name) or {}

        rating_val = details.get("rating", "N/A")
        year_val = media_info["year"] or details.get("year")
        is_series = (media_info["tag"] == "#SERIES")

        # ======================================================
        # ✅ LANGUAGE FIX: TMDB -> Gemini AI Detection
        # ======================================================
        if tmdb_language_override and tmdb_language_override != "N/A":
            final_language = tmdb_language_override
        else:
            logger.info(f"Fetching AI Language for: {base_name}")
            ai_lang = await detect_language_with_ai(base_name)
            if ai_lang and ai_lang != "English":
                final_language = ai_lang
            else:
                final_language = media_info["language"] if media_info["language"] != "N/A" else "Hindi"
        
        file_data["language"] = final_language
        
        # Poster Selection
        final_poster = await get_landscape_poster_only(base_name, is_series)

        if not final_poster:
            logger.info(f"Poster NOT found for '{base_name}'. Skipping.")
            return

        existing_movie = await db.movie_updates.find_one({"_id": base_name})
        if existing_movie:
            file_exists = any(f.get("filename") == filename for f in existing_movie.get("files", []))
            if not file_exists:
                await db.movie_updates.update_one({"_id": base_name}, {"$push": {"files": file_data}})
                await send_movie_update(bot, base_name, is_update=True)
            return

        movie_doc = {
            "_id": base_name,
            "files": [file_data],
            "poster_url": final_poster,
            "rating": rating_val,
            "year": year_val,
            "tag": media_info["tag"],
            "language": final_language,
            "message_id": None,
            "is_posted": True
        }
        
        await db.movie_updates.insert_one(movie_doc)
        msg = await send_movie_update(bot, base_name, is_update=False)
        if msg:
            await db.movie_updates.update_one({"_id": base_name}, {"$set": {"message_id": msg.id}})

    except Exception as e:
        logger.error(f"Error in lock process: {e}")

# ============ SEND MOVIE UPDATE ============

async def send_movie_update(bot, base_name, is_update=False):
    try:
        movie_doc = await db.movie_updates.find_one({"_id": base_name})
        if not movie_doc or not movie_doc.get("poster_url"):
            return None

        text = generate_movie_message(movie_doc, base_name)
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton(text='🔥 JOIN CHANNEL 🔥', url="https://t.me/+l-EIo3NnnJAxODE9")]])
        poster_url = movie_doc.get("poster_url")

        # Generate Poster with Movie Title Overlay
        image_bytes = await create_title_only_poster(poster_url, base_name)

        if image_bytes:
            # ✅ FIX: Bytes ਨੂੰ io.BytesIO ਨਾਲ ਫਾਈਲ ਆਬਜੈਕਟ ਵਿੱਚ ਕਨਵਰਟ ਕੀਤਾ
            photo_file = io.BytesIO(image_bytes)
            photo_file.name = "poster.jpg"

            sent_msg = await bot.send_photo(
                chat_id=MOVIE_UPDATE_CHANNEL,
                photo=photo_file,
                caption=text,
                reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML
            )
            return sent_msg
        else:
            # Fallback to direct URL photo
            sent_msg = await bot.send_photo(
                chat_id=MOVIE_UPDATE_CHANNEL,
                photo=poster_url,
                caption=text,
                reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML
            )
            return sent_msg

    except Exception as e:
        logger.error(f"Failed to post update: {e}")
    return None

# ============ GENERATE MESSAGE ============

def generate_movie_message(movie_doc, base_name) -> str:
    lang = movie_doc.get("language", "Hindi")
    tag = movie_doc.get("tag", "#MOVIE")
    
    caption = f"🎬 <b>{base_name}</b>\n\n"
    caption += f"🏷 <b>Type:</b> {tag}\n"
    caption += f"🌐 <b>Language:</b> #{lang}\n"
    caption += f"⭐ <b>Rating:</b> {movie_doc.get('rating', 'N/A')}\n"
    return caption
