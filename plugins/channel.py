import re
import logging
import asyncio
import aiohttp
import html
import json
from datetime import datetime
from collections import defaultdict
import urllib.parse
from typing import Optional, Tuple, Dict, List
from bs4 import BeautifulSoup
import io
from PIL import Image, ImageDraw, ImageFont  # ਟੈਕਸਟ ਓਵਰਲੇ ਲਈ (ਜੇਕਰ ਲੋੜ ਹੋਵੇ)

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
    TMDB_API_KEY, GEMINI_API_KEY  # ← ਇਹ info.py ਤੋਂ ਆ ਰਿਹਾ ਹੈ
)

logger = logging.getLogger(__name__)

# ============ GEMINI AI CONFIGURATION ============
# GEMINI_API_KEY ਹੁਣ info.py ਤੋਂ import ਹੋ ਰਿਹਾ ਹੈ

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

# ================================================================
# 🧠 AI LANGUAGE DETECTION (FIXED - NO DEFAULT HINDI)
# ================================================================
async def detect_language_with_ai(movie_name: str) -> Optional[str]:
    """
    Direct REST API ਨਾਲ Gemini AI ਦੁਆਰਾ ਸਹੀ ਭਾਸ਼ਾ ਪਛਾਣਨ ਵਾਲਾ ਫੰਕਸ਼ਨ
    """
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set. Skipping AI detection.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""
    You are an expert movie language analyzer.
    Task: Identify the exact primary original audio language of the movie/show titled: "{movie_name}".

    STRICT RULES:
    1. Analyze the title, origin country, director, and actors associated with this title.
    2. Output strictly raw JSON format without markdown code blocks.
    3. If the title is ambiguous or if you are not 100% sure, return "Unknown".
    4. DO NOT default to "English" or "Hindi" just because you are unsure.

    JSON Structure:
    {{"language": "Punjabi"}} or {{"language": "Unknown"}}
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
                lang = data.get("language")
                if lang and lang != "Unknown":
                    return lang.title()
                return None
            else:
                logger.error(f"Gemini API returned status {resp.status}")
                return None
    except Exception as e:
        logger.error(f"AI Language Detection Error: {e}")
        return None  # ❌ IKKO HINDI NAHI RETURN KAREGA

# ================================================================
# 🖼️ GOOGLE IMAGES HD POSTER FETCHER (NO CHROMEDRIVER REQUIRED)
# ================================================================
async def fetch_google_poster(movie_name: str) -> Optional[bytes]:
    """
    Google Images ਤੋਂ HD ਮੂਵੀ ਪੋਸਟਰ ਲੱਭੋ (ਜਿਸ 'ਤੇ ਮੂਵੀ ਦਾ ਨਾਮ ਲਿਖਿਆ ਹੋਵੇ)
    """
    try:
        search_query = f"{movie_name} movie poster hd"
        encoded_query = urllib.parse.quote(search_query)
        url = f"https://www.google.com/search?q={encoded_query}&tbm=isch"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        session = await get_session()
        async with session.get(url, headers=headers, timeout=15) as resp:
            if resp.status != 200:
                return None
            html_content = await resp.text()
        
        soup = BeautifulSoup(html_content, 'html.parser')
        # Google Images 'ਚ ਪਹਿਲੀ ਵੱਡੀ ਫੋਟੋ ਲੱਭੋ
        img_tag = soup.find('img', {'class': 'rg_i'}) or soup.find('img', {'jsname': 'Q4LuWd'})
        
        img_url = None
        if img_tag:
            img_url = img_tag.get('src') or img_tag.get('data-src')
        
        if not img_url:
            # ਕਦੇ-ਕਦੇ src base64 ਹੁੰਦਾ ਹੈ, ਤਾਂ ਅਸੀਂ data-src ਖੋਜਦੇ ਹਾਂ
            for tag in soup.find_all('img'):
                if tag.get('data-src') and 'http' in tag['data-src']:
                    img_url = tag['data-src']
                    break
        
        if not img_url or not img_url.startswith('http'):
            return None
        
        # ਫੋਟੋ ਡਾਊਨਲੋਡ ਕਰੋ
        async with session.get(img_url, headers=headers, timeout=15) as img_resp:
            if img_resp.status == 200:
                return await img_resp.read()
            return None
            
    except Exception as e:
        logger.error(f"Google Poster Fetch Error: {e}")
        return None

# ================================================================
# 🖼️ TMDB POSTER FETCHER
# ================================================================
async def fetch_tmdb_poster(movie_name: str) -> Optional[str]:
    """
    TMDB API ਤੋਂ ਪੋਸਟਰ URL ਲਓ।
    """
    try:
        details = await get_movie_detailsx(movie_name)
        if details and details.get('poster_url'):
            poster = details['poster_url']
            if "t/p/w" in poster:
                poster = re.sub(r'/t/p/w\d+/', '/t/p/original/', poster)
            return poster
    except Exception as e:
        logger.error(f"TMDB Poster Fetch Error: {e}")
    return None

# ================================================================
# 🧹 CLEANING AND EXTRACTION FUNCTIONS
# ================================================================
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

# ================================================================
# 📥 MAIN HANDLERS
# ================================================================
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

# ================================================================
# 🔒 PROCESS WITH LOCK (LANGUAGE FIX APPLIED)
# ================================================================
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
        # ✅ LANGUAGE FIX: TMDB -> Gemini AI -> Filename -> Unknown
        # ======================================================
        final_language = None

        # 1. TMDB ਤੋਂ ਮਿਲੀ ਭਾਸ਼ਾ
        if tmdb_language_override and tmdb_language_override != "N/A":
            final_language = tmdb_language_override
            logger.info(f"✅ Language from TMDB for {base_name}: {final_language}")

        # 2. ਜੇਕਰ TMDB ਨਾ ਮਿਲੇ, ਤਾਂ Gemini AI ਵਰਤੋਂ
        if not final_language:
            logger.info(f"🔍 Fetching AI Language for: {base_name}")
            ai_lang = await detect_language_with_ai(base_name)
            if ai_lang and ai_lang != "Unknown":
                final_language = ai_lang
                logger.info(f"✅ Language from AI for {base_name}: {final_language}")

        # 3. ਜੇਕਰ ਉੱਪਰੋਂ ਕੁਝ ਨਾ ਮਿਲੇ, ਤਾਂ ਫਾਈਲਨੇਮ/ਕੈਪਸ਼ਨ ਤੋਂ ਲੱਭੋ
        if not final_language:
            if media_info["language"] != "N/A":
                final_language = media_info["language"]
                logger.info(f"✅ Language from filename/caption for {base_name}: {final_language}")

        # 4. ਅੰਤ ਵਿੱਚ ਕੁਝ ਨਾ ਮਿਲੇ ਤਾਂ 'Unknown' ਰੱਖੋ (Hindi ਨਹੀਂ)
        if not final_language:
            final_language = "Unknown"
            logger.warning(f"⚠️ No language found for {base_name}. Marked as Unknown.")

        file_data["language"] = final_language

        # ======================================================
        # 🖼️ POSTER FETCH: Google -> TMDB (Fallback)
        # ======================================================
        poster_bytes = None
        poster_url = None
        final_poster = None

        # 1. ਪਹਿਲਾਂ Google Images ਤੋਂ HD ਪੋਸਟਰ ਲੱਭੋ
        logger.info(f"🔍 Searching Google for poster: {base_name}")
        poster_bytes = await fetch_google_poster(base_name)
        if poster_bytes:
            final_poster = poster_bytes  # ਇਹ bytes ਵਿੱਚ ਹੈ
            logger.info(f"✅ Google poster found for {base_name}")
        
        # 2. ਜੇਕਰ Google ਤੋਂ ਨਾ ਮਿਲੇ, ਤਾਂ TMDB poster/backdrop ਵਰਤੋਂ
        if not final_poster:
            logger.info(f"🔍 Falling back to TMDB for poster: {base_name}")
            # a) TMDB Poster URL
            poster_url = await fetch_tmdb_poster(base_name)
            if poster_url:
                final_poster = poster_url  # ਇਹ URL ਹੈ
                logger.info(f"✅ TMDB poster found for {base_name}")
            else:
                # b) TMDB Backdrop (Landscape)
                backdrop = await get_landscape_poster_only(base_name, is_series)
                if backdrop:
                    final_poster = backdrop
                    logger.info(f"✅ TMDB backdrop found for {base_name}")

        if not final_poster:
            logger.info(f"❌ No poster found for '{base_name}'. Skipping.")
            return

        # Database 'ਚ ਸੇਵ ਕਰਨ ਤੋਂ ਪਹਿਲਾਂ poster URL/bytes store ਕਰੀਏ
        # ਮੈਂ ਇੱਥੇ poster_url ਨੂੰ store ਕਰ ਰਿਹਾ ਹਾਂ, ਪਰ ਜੇਕਰ bytes ਹੈ ਤਾਂ ਅਸੀਂ ਉਸ ਨੂੰ 
        # send_movie_update 'ਚ handle ਕਰਾਂਗੇ। ਆਸਾਨੀ ਲਈ ਮੈਂ final_poster ਨੂੰ ਇੱਕ variable 'ਚ ਰੱਖਦਾ ਹਾਂ।
        
        existing_movie = await db.movie_updates.find_one({"_id": base_name})
        if existing_movie:
            file_exists = any(f.get("filename") == filename for f in existing_movie.get("files", []))
            if not file_exists:
                await db.movie_updates.update_one({"_id": base_name}, {"$push": {"files": file_data}})
                # updated poster ਭੇਜਣ ਲਈ, ਅਸੀਂ final_poster ਨੂੰ db 'ਚ save ਕਰਾਂਗੇ?
                # ਜਾਂ ਅਸੀਂ ਸਿੱਧਾ send_movie_update 'ਚ ਭੇਜ ਦੇਵਾਂਗੇ (ਜੋ db ਤੋਂ ਪੜ੍ਹਦਾ ਹੈ)
                # ਆਓ ਅਸੀਂ db 'ਚ poster_url ਨੂੰ update ਕਰੀਏ ਜੇਕਰ ਇਹ URL ਹੈ
                if isinstance(final_poster, str) and final_poster.startswith('http'):
                    await db.movie_updates.update_one({"_id": base_name}, {"$set": {"poster_url": final_poster}})
                await send_movie_update(bot, base_name, is_update=True, poster_data=final_poster)
            return

        # ਨਵੀਂ movie ਲਈ
        # ਜੇਕਰ final_poster bytes ਹੈ, ਤਾਂ ਅਸੀਂ ਉਸ ਨੂੰ DB 'ਚ ਨਹੀਂ ਸੇਵ ਕਰ ਸਕਦੇ (MongoDB ਲਈ ਵੱਡਾ ਹੈ)
        # ਇਸ ਲਈ ਅਸੀਂ ਸਿਰਫ਼ URL ਸੇਵ ਕਰਾਂਗੇ (ਜਾਂ ਅਸੀਂ poster_bytes ਨੂੰ send_movie_update 'ਚ pass ਕਰਾਂਗੇ)
        # ਆਓ async function 'ਚ poster_data pass ਕਰੀਏ
        poster_url_to_save = None
        if isinstance(final_poster, str) and final_poster.startswith('http'):
            poster_url_to_save = final_poster
        
        movie_doc = {
            "_id": base_name,
            "files": [file_data],
            "poster_url": poster_url_to_save,  # ਸਿਰਫ਼ URL, bytes ਨਹੀਂ
            "rating": rating_val,
            "year": year_val,
            "tag": media_info["tag"],
            "language": final_language,
            "message_id": None,
            "is_posted": True
        }
        
        await db.movie_updates.insert_one(movie_doc)
        # poster_data ਨੂੰ ਫੰਕਸ਼ਨ 'ਚ pass ਕਰੋ
        msg = await send_movie_update(bot, base_name, is_update=False, poster_data=final_poster)
        if msg:
            await db.movie_updates.update_one({"_id": base_name}, {"$set": {"message_id": msg.id}})

    except Exception as e:
        logger.error(f"Error in lock process: {e}")

# ================================================================
# 📤 SEND MOVIE UPDATE (WITH POSTER FALLBACK)
# ================================================================
async def send_movie_update(bot, base_name, is_update=False, poster_data=None):
    try:
        movie_doc = await db.movie_updates.find_one({"_id": base_name})
        if not movie_doc:
            return None

        text = generate_movie_message(movie_doc, base_name)
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton(text='♻️ 𝐉𝐎𝐈𝐍 𝐑𝐄𝐐𝐔𝐄𝐒𝐓 𝐆𝐑𝐎𝐔𝐏 ♻️', url="https://t.me/+l-EIo3NnnJAxODE9")]])
        
        # 1. ਜੇਕਰ poster_data pass ਕੀਤਾ ਗਿਆ ਹੈ (bytes ਜਾਂ URL)
        if poster_data:
            if isinstance(poster_data, bytes):
                # bytes ਹੈ -> ਸਿੱਧਾ ਭੇਜੋ
                sent_msg = await bot.send_photo(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    photo=poster_data,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML
                )
                return sent_msg
            elif isinstance(poster_data, str) and poster_data.startswith('http'):
                # URL ਹੈ -> URL ਵਰਤੋਂ
                sent_msg = await bot.send_photo(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    photo=poster_data,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML
                )
                return sent_msg
        
        # 2. ਜੇਕਰ poster_data ਨਹੀਂ ਮਿਲਿਆ, DB ਤੋਂ URL ਲਓ
        poster_url = movie_doc.get("poster_url")
        if poster_url:
            sent_msg = await bot.send_photo(
                chat_id=MOVIE_UPDATE_CHANNEL,
                photo=poster_url,
                caption=text,
                reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML
            )
            return sent_msg
        
        logger.warning(f"No poster available to send for {base_name}")
        return None

    except Exception as e:
        logger.error(f"Failed to post update: {e}")
    return None

# ================================================================
# 📝 GENERATE MESSAGE (WITH # BEFORE LANGUAGE)
# ================================================================
def generate_movie_message(movie_doc, base_name) -> str:
    lang = movie_doc.get("language", "Unknown")
    tag = movie_doc.get("tag", "#MOVIE")
    year = movie_doc.get("year")
    year_str = f" ({year})" if year else ""
    
    caption = f"🎬 <code>{base_name}{year_str}</code>\n"
    caption += f"📌 (Touch To Copy)\n\n"
    caption += f"⭐ IMDb: {movie_doc.get('rating', 'N/A')}\n\n"
    # ✅ ਭਾਸ਼ਾ ਦੇ ਅੱਗੇ # ਲੱਗ ਰਿਹਾ ਹੈ
    caption += f"➡ Audio Track:- 🔊 #{lang}\n\n"
    caption += f"Added ✅"
    return caption

# ================================================================
# 🖼️ LANDSCAPE POSTER HELPER (FOR TMDB BACKDROP)
# ================================================================
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
    return None
