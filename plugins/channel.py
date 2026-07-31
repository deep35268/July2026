import re
import logging
import asyncio
import aiohttp
import html
import io
import textwrap
from datetime import datetime
from collections import defaultdict
import urllib.parse
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait
from pymongo.errors import DuplicateKeyError

# Plugin & Database Imports
from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx, get_movie_details
from database.users_chats_db import db
from database.ia_filterdb import save_file
from info import (
    CHANNELS, MOVIE_UPDATE_CHANNEL, BAD_WORDS, LANDSCAPE_POSTER, TMDB_POSTER
)

logger = logging.getLogger(__name__)

SESSION: Optional[aiohttp.ClientSession] = None

async def get_session() -> aiohttp.ClientSession:
    global SESSION
    if SESSION is None or SESSION.closed:
        SESSION = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=50))
    return SESSION

POSTED_MOVIES = set()
MAX_CACHE_SIZE = 500
locks = defaultdict(asyncio.Lock)

# ============ API CONFIGURATIONS ============
# Spidy API
SPIDY_API_KEY = "spidy_1wtzdn9wplo"
SPIDY_API_URL = "https://poster-api.ispidy.com/v1/fetch"

# Fanart.tv API
FANART_API_KEY = "cfa9dc054d221b8d107f8411cd20b13f"
FANART_API_URL = "https://webservice.fanart.tv/v3/movies"

# OMDb API
OMDB_API_KEY = "5f7182e"
OMDB_API_URL = "http://www.omdbapi.com/"

# OpenPosterDB API (optional – if hosted)
OPENPOSTERDB_API_KEY = "t0-free-rpdb"
OPENPOSTERDB_API_URL = "https://openposterdb.com"

# ============ IGNORED WORDS ============
IGNORE_WORDS = {
    "rarbg", "dub", "sample", "mkv", "aac", "combined", "mp4", "avi",
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
    "10bit", "10-bit", "8bit", "8-bit",
    "subtitle", "subtitles", "srt", "subs"
} | BAD_WORDS

# ============ LANGUAGE MAPPINGS ============
TMDB_LANG_MAP = {
    "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam",
    "kn": "Kannada", "en": "English", "bn": "Bengali", "mr": "Marathi",
    "gu": "Gujarati", "pa": "Punjabi", "ur": "Urdu", "ko": "Korean",
    "ja": "Japanese", "es": "Spanish", "fr": "French", "de": "German",
    "zh": "Chinese", "ru": "Russian", "it": "Italian", "pt": "Portuguese",
    "ara": "Arabic", "nl": "Dutch", "sv": "Swedish", "pl": "Polish",
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
    "spa": "Spanish", "spanish": "Spanish",
    "fre": "French", "french": "French",
    "ger": "German", "german": "German",
    "chi": "Chinese", "chinese": "Chinese",
    "rus": "Russian", "russian": "Russian",
    "ita": "Italian", "italian": "Italian",
    "por": "Portuguese", "portuguese": "Portuguese",
}

OTT_PLATFORMS = {
    "nf": "Netflix", "netflix": "Netflix",
    "sonyliv": "SonyLiv", "sony": "SonyLiv", "sliv": "SonyLiv",
    "amzn": "Amazon Prime Video", "prime": "Amazon Prime Video", "primevideo": "Amazon Prime Video",
    "hotstar": "Disney+ Hotstar", "zee5": "Zee5", "jio": "JioHotstar", "jhs": "JioHotstar",
    "aha": "Aha", "hbo": "HBO Max", "paramount": "Paramount+", "apple": "Apple TV+", 
    "hoichoi": "Hoichoi", "sunnxt": "Sun NXT", "viki": "Viki"
}

# ============ PATTERNS ============
CLEAN_PATTERN = re.compile(r'@[^ \n\r\t\.,:;!?()\[\]{}<>\\/"\'=_%]+|\bwww\.[^\s\]\)]+|\([\@^]+\)|\[[\@^]+\]')
NORMALIZE_PATTERN = re.compile(r"[._\-\+]+|[()\[\]{}:;'–!,.?]")
QUALITY_PATTERN = re.compile(
    r"\b(?:HDCam|HDTC|CamRip|TS|TC|TeleSync|DVDScr|DVDRip|PreDVD|"
    r"WEBRip|WEB-DL|TVRip|HDTV|WEB DL|WebDl|BluRay|BRRip|BDRip|"
    r"360p|480p|720p|1080p|2160p|4K|1440p|540p|240p|140p|HEVC|HDRip|x264|x265|10bit|10-bit|8bit)\b", 
    re.IGNORECASE
)
YEAR_PATTERN = re.compile(r"(?<![A-Za-z0-9])(19\d{2}|20\d{2})(?![A-Za-z0-9])")
EPISODE_CLEAN_PATTERN = re.compile(r'\b(S\d{1,2}|E\d{1,3}|Ep\d{1,3}|Episode\s*\d{1,3}|Season\s*\d{1,2}|Part\s*\d{1,2}|\d{1,2}\s*-\s*\d{1,2}|\d{1,3}\s*to\s*\d{1,3})\b', re.IGNORECASE)

MEDIA_FILTER = filters.document | filters.video | filters.audio

# ============ POSTER SOURCES ============

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

async def fetch_omdb_poster(query: str, year: Optional[str] = None) -> Optional[str]:
    try:
        session = await get_session()
        params = {
            "apikey": OMDB_API_KEY,
            "t": query,
            "type": "movie"
        }
        if year and year != "N/A":
            params["y"] = year
        async with session.get(OMDB_API_URL, params=params, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("Response") == "True":
                    poster = data.get("Poster")
                    if poster and poster.startswith(('http://', 'https://')) and poster != "N/A":
                        logger.info(f"✅ OMDb: Found poster for '{query}'")
                        return poster
                else:
                    logger.info(f"❌ OMDb: No result for '{query}'")
            else:
                logger.warning(f"⚠️ OMDb: Status {resp.status} for '{query}'")
    except Exception as e:
        logger.error(f"❌ OMDb error for '{query}': {e}")
    return None

async def fetch_fanart_landscape_poster(tmdb_id: str) -> Optional[str]:
    if not tmdb_id:
        return None
    try:
        session = await get_session()
        url = f"{FANART_API_URL}/{tmdb_id}?api_key={FANART_API_KEY}"
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                backgrounds = data.get("moviebackground", [])
                if backgrounds and isinstance(backgrounds, list):
                    best = max(backgrounds, key=lambda x: int(x.get("likes", 0)))
                    landscape_url = best.get("url")
                    if landscape_url and landscape_url.startswith(('http://', 'https://')):
                        logger.info(f"✅ Fanart.tv: Found landscape (likes: {best.get('likes')}) for TMDB ID {tmdb_id}")
                        return landscape_url
            elif resp.status == 404:
                logger.info(f"❌ Fanart.tv: No data for TMDB ID {tmdb_id}")
            else:
                logger.warning(f"⚠️ Fanart.tv: Status {resp.status} for TMDB ID {tmdb_id}")
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Fanart.tv timeout for TMDB ID {tmdb_id}")
    except Exception as e:
        logger.error(f"❌ Fanart.tv error for TMDB ID {tmdb_id}: {e}")
    return None

async def fetch_openposterdb_landscape(tmdb_id: str) -> Optional[str]:
    if not tmdb_id:
        return None
    try:
        session = await get_session()
        url = f"{OPENPOSTERDB_API_URL}/t0-free-rpdb/tmdb/backdrop-default/{tmdb_id}.jpg"
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                logger.info(f"✅ OpenPosterDB: Found backdrop for TMDB ID {tmdb_id}")
                return url
            else:
                logger.info(f"❌ OpenPosterDB: No backdrop for TMDB ID {tmdb_id}")
    except Exception as e:
        logger.error(f"❌ OpenPosterDB error for TMDB ID {tmdb_id}: {e}")
    return None

async def fetch_spidy_landscape_poster(query: str, is_series: bool = False, year: Optional[str] = None) -> Optional[str]:
    try:
        session = await get_session()
        clean_query = query
        clean_query = re.sub(r'\bS\d{1,2}E\d{1,2}\b', '', clean_query, flags=re.IGNORECASE)
        clean_query = re.sub(r'\bS\d{1,2}\b', '', clean_query, flags=re.IGNORECASE)
        clean_query = re.sub(r'\bE\d{1,3}\b', '', clean_query, flags=re.IGNORECASE)
        clean_query = re.sub(r'\bSeason\s*\d{1,2}\b', '', clean_query, flags=re.IGNORECASE)
        clean_query = re.sub(r'\bEpisode\s*\d{1,3}\b', '', clean_query, flags=re.IGNORECASE)
        clean_query = re.sub(r'\bPart\s*\d{1,2}\b', '', clean_query, flags=re.IGNORECASE)
        clean_query = re.sub(r'\b\d{1,2}\s*-\s*\d{1,2}\b', '', clean_query, flags=re.IGNORECASE)
        clean_query = re.sub(r'\b\d{1,3}\s*to\s*\d{1,3}\b', '', clean_query, flags=re.IGNORECASE)
        clean_query = re.sub(r'\s+', ' ', clean_query).strip()
        
        if clean_query != query:
            logger.info(f"🧹 Cleaned query for Spidy: '{clean_query}' (original: '{query}')")
        else:
            logger.info(f"🔍 Spidy query: '{clean_query}'")
        
        params = {
            "api_key": SPIDY_API_KEY,
            "title": clean_query,
        }
        if year and year != "N/A":
            params["year"] = year
        
        async with session.get(SPIDY_API_URL, params=params, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("results", [])
                if not results or not isinstance(results, list):
                    logger.warning(f"⚠️ Spidy API: No results or invalid format for '{clean_query}'")
                    return None
                
                query_lower = clean_query.lower()
                for item in results:
                    item_title = item.get("title", "")
                    if item_title.lower() == query_lower:
                        landscape = item.get("landscape")
                        if landscape and landscape.startswith(('http://', 'https://')):
                            logger.info(f"✅ Spidy API: Found exact title match '{item_title}' for '{clean_query}'")
                            return landscape
                
                for item in results:
                    landscape = item.get("landscape")
                    if landscape and landscape.startswith(('http://', 'https://')):
                        logger.info(f"✅ Spidy API: Found fallback landscape for '{clean_query}' (title: {item.get('title')})")
                        return landscape
                
                logger.warning(f"⚠️ Spidy API: No landscape in any result for '{clean_query}'")
            elif resp.status == 404:
                logger.info(f"❌ Spidy API: Poster not found for '{clean_query}'")
            elif resp.status == 429:
                logger.warning(f"⚠️ Spidy API: Rate limit exceeded for '{clean_query}'")
            else:
                logger.warning(f"⚠️ Spidy API: Status {resp.status} for '{clean_query}'")
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Spidy API timeout for '{query}'")
    except Exception as e:
        logger.error(f"❌ Spidy API error for '{query}': {e}")
    return None

async def get_landscape_poster_only(movie_name: str, is_series: bool = False, year: Optional[str] = None, tmdb_id: Optional[str] = None) -> Optional[str]:
    # 1️⃣ Spidy
    spidy_backdrop = await fetch_spidy_landscape_poster(movie_name, is_series, year)
    if spidy_backdrop:
        logger.info(f"✅ Spidy poster found for '{movie_name}'")
        return spidy_backdrop

    # 2️⃣ Fanart.tv
    if tmdb_id:
        fanart_backdrop = await fetch_fanart_landscape_poster(tmdb_id)
        if fanart_backdrop:
            logger.info(f"✅ Fanart.tv poster found for '{movie_name}'")
            return fanart_backdrop

    # 3️⃣ OMDb
    omdb_poster = await fetch_omdb_poster(movie_name, year)
    if omdb_poster:
        logger.info(f"✅ OMDb poster found for '{movie_name}'")
        return omdb_poster

    # 4️⃣ TMDB
    if LANDSCAPE_POSTER:
        try:
            details = await get_movie_detailsx(movie_name)
            if details and details.get('backdrop_url'):
                backdrop = details['backdrop_url']
                if "t/p/" in backdrop:
                    backdrop = re.sub(r'/t/p/w\d+/', '/t/p/original/', backdrop)
                    backdrop = re.sub(r'/t/p/w\d+x\d+/', '/t/p/original/', backdrop)
                logger.info(f"✅ TMDB poster found for '{movie_name}'")
                return backdrop
        except Exception as e:
            logger.error(f"TMDB backdrop error: {e}")

    # 5️⃣ Cinemeta
    ai_backdrop = await fetch_cinemeta_ai_poster(movie_name, is_series)
    if ai_backdrop:
        logger.info(f"✅ Cinemeta poster found for '{movie_name}'")
        return ai_backdrop

    # 6️⃣ OpenPosterDB
    if tmdb_id:
        openposter_backdrop = await fetch_openposterdb_landscape(tmdb_id)
        if openposter_backdrop:
            logger.info(f"✅ OpenPosterDB poster found for '{movie_name}'")
            return openposter_backdrop

    logger.info(f"❌ No poster found for '{movie_name}' from any source")
    return None

# ============ CLEANING AND EXTRACTION ============

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
    SUBTITLE_KEYWORDS = {"sub", "subtitle", "subtitles", "srt", "subs"}
    found = set()
    text_lower = text.lower()
    for sep in ['.', '_', '-', '+', ' ', '(', ')', '[', ']', '{', '}', ';', ',']:
        text_lower = text_lower.replace(sep, ' ')
    tokens = text_lower.split()
    for token in tokens:
        if token in SUBTITLE_KEYWORDS:
            continue
        for lang_key, lang_name in CAPTION_LANGUAGES.items():
            if lang_key in token:
                found.add(lang_name)
                break
    return found

# ============================================================
# 🟢 MAIN FUNCTION – EXTRACTS LANGUAGES & SOURCE TAGS
# ============================================================
def extract_media_info(filename: str, caption: str):
    filename_cleaned = clean_mentions_links(filename)
    filename_normalized = normalize(filename_cleaned)

    tag = "#MOVIE"
    year = None
    
    # 🟢 Extract source tags ONLY (WEBDL, BLURAY, PREDVD, ORG, etc.)
    source_keywords = [
        "WEBDL", "WEBRIP", "BLURAY", "BRRIP", "PREDVD", 
        "DVDSCR", "DVDRIP", "HDTV", "HDCAM", "HDRIP", "ORG"
    ]
    source_tags = []
    for part in re.split(r'[._\-+ ]', filename_normalized):
        part_clean = part.upper()
        if part_clean in source_keywords:
            source_tags.append(f"#{part_clean}")
    
    tags_str = " ".join(source_tags[:2]) if source_tags else ""
    
    # Quality tags are EXCLUDED from display
    quality = QUALITY_PATTERN.findall(filename_normalized)
    quality_str = ", ".join(quality) if quality else "N/A"
    
    ott_platform = extract_ott_platform(filename_normalized)
    
    # 🟢 Extract languages (NO DEFAULT HINDI)
    lang_set = set()
    lang_set.update(extract_languages_from_text(filename_normalized))
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

    normalized_key = re.sub(r'[^a-z0-9]', '', base_name.lower())

    return {
        "processed": filename_normalized,
        "base_name": base_name.title(),
        "tag": tag,
        "year": year,
        "quality": quality_str,
        "ott_platform": ott_platform,
        "language": language,      # extracted languages only, no fallback
        "normalized_key": normalized_key,
        "tags": tags_str           # source tags only
    }

def extract_ott_platform(text: str) -> str:
    text = text.lower()
    platforms = {plat for key, plat in OTT_PLATFORMS.items() if key in text}
    return " | ".join(platforms) if platforms else "N/A"

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
        normalized_key = media_info["normalized_key"]
        movie_key = normalized_key
        
        if len(POSTED_MOVIES) > MAX_CACHE_SIZE:
            POSTED_MOVIES.clear()
        
        if movie_key in POSTED_MOVIES:
            return

        async with locks[normalized_key]:
            if movie_key in POSTED_MOVIES:
                return
            POSTED_MOVIES.add(movie_key)
            
            try:
                await _process_with_lock(bot, filename, caption, media_info, base_name, normalized_key)
            finally:
                await asyncio.sleep(12)
                POSTED_MOVIES.discard(movie_key)
                
    except Exception as e:
        logger.exception(f"Processing execution failed: {e}")

async def _process_with_lock(bot, filename, caption, media_info, base_name, normalized_key):
    if not hasattr(db, 'movie_updates'):
        db.movie_updates = db.db.movie_updates

    error_tmdb = False
    file_data = {
        "filename": filename,
        "quality": media_info["quality"],
        "language": media_info["language"],  # extracted languages only
        "timestamp": datetime.now()
    }

    try:
        details = {}
        tmdb_language_override = None
        tmdb_id = None
        
        if TMDB_POSTER:
            try:
                details = await get_movie_detailsx(base_name)
                if not details or details.get("error"):
                    error_tmdb = True
                else:
                    orig_lang = details.get("original_language") or details.get("lang")
                    if orig_lang:
                        tmdb_language_override = TMDB_LANG_MAP.get(orig_lang.lower())
                    tmdb_id = details.get("id") or details.get("tmdb_id")
            except Exception:
                error_tmdb = True
                
        if not TMDB_POSTER or error_tmdb or not details:
            details = await get_movie_details(base_name) or {}
            if not tmdb_id:
                tmdb_id = details.get("id") or details.get("tmdb_id")

        rating_val = "N/A"
        if details.get("rating"):
            try:
                rating_val = f"{float(details.get('rating')):.1f}"
            except ValueError:
                pass

        year_val = media_info["year"]
        if not year_val and details.get("year"):
            year_val = str(details.get("year")).strip()
        
        is_series = (media_info["tag"] == "#SERIES")
        if not year_val and is_series:
            try:
                session = await get_session()
                encoded_query = urllib.parse.quote(base_name)
                search_url = f"https://v3-cinemeta.strem.io/catalog/series/top/search={encoded_query}.json"
                async with session.get(search_url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        metas = data.get("metas", [])
                        if metas and metas[0].get("year"):
                            raw_year = metas[0].get("year")
                            year_match = re.search(r'\b(19\d{2}|20\d{2})\b', str(raw_year))
                            if year_match:
                                year_val = year_match.group(1)
            except Exception as e:
                logger.error(f"Error fetching series year from Cinemeta: {e}")

        year_val = year_val or None
        
        # ============================================================
        # 🟢 LANGUAGE LOGIC – NO DEFAULT HINDI
        # ============================================================
        lang_set = set()
        raw_lang = media_info["language"]
        if raw_lang != "N/A":
            lang_set.update(l.strip() for l in raw_lang.split(",") if l.strip())
        
        # Add TMDB original language ONLY if we have one
        if tmdb_language_override and tmdb_language_override != "N/A":
            lang_set.add(tmdb_language_override)
        
        # 🟢 NO DEFAULT FALLBACK – if no languages found, keep "N/A"
        if lang_set:
            final_language = ", ".join(sorted(lang_set))
        else:
            final_language = "N/A"
        
        file_data["language"] = final_language
        
        # ============================================================
        # GET POSTER
        # ============================================================
        final_poster = await get_landscape_poster_only(base_name, is_series, year_val, str(tmdb_id) if tmdb_id else None)

        if not final_poster:
            logger.info(f"❌ Poster NOT found for '{base_name}'. Skipping post creation.")
            return

        existing_movie = await db.movie_updates.find_one({"_id": normalized_key})
        
        post_exists = False
        if existing_movie and existing_movie.get("message_id"):
            try:
                msg = await bot.get_messages(chat_id=MOVIE_UPDATE_CHANNEL, message_ids=existing_movie["message_id"])
                if msg:
                    post_exists = True
                    logger.info(f"✅ Post found for '{base_name}' (message_id={existing_movie['message_id']})")
            except MessageIdInvalid:
                logger.warning(f"⚠️ Post for '{base_name}' (ID {existing_movie['message_id']}) no longer exists. Will repost.")
                post_exists = False
            except Exception as e:
                logger.error(f"Error checking post for '{base_name}': {e}")
                post_exists = False
        
        if post_exists:
            file_exists = any(f.get("filename") == filename for f in existing_movie.get("files", []))
            
            update_fields = {}
            if existing_movie.get("rating") == "N/A" and rating_val != "N/A":
                update_fields["rating"] = rating_val
            if not existing_movie.get("year") and year_val:
                update_fields["year"] = year_val
            if not existing_movie.get("poster_url") and final_poster:
                update_fields["poster_url"] = final_poster
            if existing_movie.get("language") != final_language and final_language != "N/A":
                update_fields["language"] = final_language
            # 🟢 Update tags if changed
            if existing_movie.get("tags") != media_info.get("tags"):
                update_fields["tags"] = media_info.get("tags", "")

            if not file_exists:
                await db.movie_updates.update_one(
                    {"_id": normalized_key}, 
                    {"$push": {"files": file_data}, "$set": update_fields} if update_fields else {"$push": {"files": file_data}}
                )
            elif update_fields:
                await db.movie_updates.update_one({"_id": normalized_key}, {"$set": update_fields})
            
            logger.info(f"✏️ Editing existing post for '{base_name}'")
            await send_movie_update(bot, normalized_key, is_update=True)
            return
        
        if existing_movie:
            update_fields = {
                "message_id": None,
                "first_posted_at": datetime.now()
            }
            if existing_movie.get("rating") == "N/A" and rating_val != "N/A":
                update_fields["rating"] = rating_val
            if not existing_movie.get("year") and year_val:
                update_fields["year"] = year_val
            if not existing_movie.get("poster_url") and final_poster:
                update_fields["poster_url"] = final_poster
            if existing_movie.get("language") != final_language and final_language != "N/A":
                update_fields["language"] = final_language
            if existing_movie.get("tags") != media_info.get("tags"):
                update_fields["tags"] = media_info.get("tags", "")
            
            await db.movie_updates.update_one(
                {"_id": normalized_key},
                {"$push": {"files": file_data}, "$set": update_fields}
            )
            logger.info(f"🔄 Reposting '{base_name}' because old post was auto-deleted.")
            msg = await send_movie_update(bot, normalized_key, is_update=False)
            if msg:
                await db.movie_updates.update_one({"_id": normalized_key}, {"$set": {"message_id": msg.id}})
            return
        
        movie_doc = {
            "_id": normalized_key,
            "title": base_name,
            "files": [file_data],
            "poster_url": final_poster,
            "rating": rating_val,
            "year": year_val,
            "tag": media_info["tag"],
            "language": final_language,
            "tags": media_info.get("tags", ""),
            "message_id": None,
            "is_posted": True,
            "first_posted_at": datetime.now()
        }
        
        try:
            await db.movie_updates.insert_one(movie_doc)
            msg = await send_movie_update(bot, normalized_key, is_update=False)
            if msg:
                await db.movie_updates.update_one({"_id": normalized_key}, {"$set": {"message_id": msg.id}})
        except DuplicateKeyError:
            await db.movie_updates.update_one(
                {"_id": normalized_key},
                {"$push": {"files": file_data}, "$set": {"message_id": None, "first_posted_at": datetime.now()}}
            )
            msg = await send_movie_update(bot, normalized_key, is_update=False)
            if msg:
                await db.movie_updates.update_one({"_id": normalized_key}, {"$set": {"message_id": msg.id}})

    except Exception as e:
        logger.error(f"Error in backend lock verification process: {e}")

# ============================================================
# 🟢 SEND MOVIE UPDATE – CORRECT AUDIO LINE
# ============================================================
async def send_movie_update(bot, normalized_key, is_update=False):
    try:
        movie_doc = await db.movie_updates.find_one({"_id": normalized_key})
        if not movie_doc:
            return None

        display_title = movie_doc.get("title", normalized_key)
        text = generate_movie_message(movie_doc, display_title)
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton(text='♻️ 𝐉𝐎𝐈𝐍 𝐑𝐄𝐐𝐔𝐄𝐒𝐓 𝐆𝐑𝐎𝐔𝐏 ♻️', url="https://t.me/+l-EIo3NnnJAxODE9")]])
        
        poster_url = movie_doc.get("poster_url")
        if not poster_url or not poster_url.startswith(('http://', 'https://')):
            logger.info(f"⚠️ Invalid poster URL for '{display_title}'. Skipping post creation.")
            return None

        sent_msg = None

        if is_update and movie_doc.get("message_id"):
            try:
                sent_msg = await bot.edit_message_media(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=movie_doc["message_id"],
                    media=InputMediaPhoto(media=poster_url, caption=text, parse_mode=enums.ParseMode.HTML),
                    reply_markup=buttons
                )
            except MessageNotModified:
                sent_msg = movie_doc
            except FloodWait as e:
                await asyncio.sleep(e.value)
                return await send_movie_update(bot, normalized_key, is_update)
            except MessageIdInvalid:
                logger.warning(f"Message ID invalid for {display_title}, will send new.")
                is_update = False
            except Exception as e:
                logger.error(f"Edit media error: {e}")
                try:
                    sent_msg = await bot.edit_message_caption(
                        chat_id=MOVIE_UPDATE_CHANNEL,
                        message_id=movie_doc["message_id"],
                        caption=text,
                        reply_markup=buttons,
                        parse_mode=enums.ParseMode.HTML
                    )
                except Exception:
                    pass
            
            if sent_msg:
                return sent_msg
            else:
                logger.warning(f"Update failed for {display_title}, not creating duplicate.")
                return None

        try:
            sent_msg = await bot.send_photo(
                chat_id=MOVIE_UPDATE_CHANNEL,
                photo=poster_url,
                caption=text,
                reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML
            )
        except FloodWait as e:
            await asyncio.sleep(e.value)
            return await send_movie_update(bot, normalized_key, is_update)
        except Exception as e:
            logger.error(f"New send failed: {e}")
            return None

        if sent_msg and hasattr(sent_msg, 'id'):
            await db.movie_updates.update_one({"_id": normalized_key}, {"$set": {"message_id": sent_msg.id}})
            asyncio.create_task(verify_and_correct_post_with_ai(bot, sent_msg.id, normalized_key, buttons))
            return sent_msg

    except Exception as e:
        logger.error(f"Failed to push update layout: {e}")
    return None

# ============================================================
# 🟢 GENERATE MOVIE MESSAGE – NO DEFAULT HINDI
# ============================================================
def generate_movie_message(movie_doc, display_title) -> str:
    all_languages = set()
    for file in movie_doc["files"]:
        if file.get("language") and file["language"] != "N/A":
            all_languages.update(l.strip() for l in file["language"].split(",") if l.strip())
    
    if movie_doc.get("language") and movie_doc["language"] != "N/A":
        all_languages.update(l.strip() for l in movie_doc["language"].split(",") if l.strip())
    
    # 🟢 ONLY extracted languages – NO DEFAULT
    if all_languages:
        language_str = " ".join(f"#{lang}" for lang in sorted(all_languages))
    else:
        language_str = ""  # Empty if no languages found
    
    # 🟢 Source tags from DB
    tags = movie_doc.get("tags", "")
    
    # Combine language and tags
    if language_str and tags:
        audio_line = f"➥ Aᴜᴅɪᴏ Tʀᴀᴄᴋ:- 🔊 {language_str} {tags}"
    elif language_str:
        audio_line = f"➥ Aᴜᴅɪᴏ Tʀᴀᴄᴋ:- 🔊 {language_str}"
    elif tags:
        audio_line = f"➥ Aᴜᴅɪᴏ Tʀᴀᴄᴋ:- 🔊 {tags}"
    else:
        audio_line = "➥ Aᴜᴅɪᴏ Tʀᴀᴄᴋ:- 🔊 N/A"
    
    title = html.escape(display_title.upper())
    title = re.sub(r'\b10BIT\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s+', ' ', title).strip()
    
    year_val = str(movie_doc.get("year", "")).strip()
    year_val = re.sub(r'[()\[\]]', '', year_val)
    is_series = (movie_doc.get("tag") == "#SERIES")
    year_str = "" if is_series else f" ({html.escape(year_val)})" if year_val and year_val != "None" and year_val not in title else ""
    
    rating_raw = movie_doc.get("rating", "N/A")
    rating_str = rating_raw if rating_raw != "N/A" else "N/A"
    
    return (
        f"🎬 <code>{title}{year_str}</code>\n"
        f"<i>📌 (Touch to Copy)</i>\n\n"
        f"⭐ IMDb: {rating_str}\n\n"
        f"{audio_line}\n\n"
        f"✅ Added"
    )

# ============================================================
# 🟢 EXTERNAL FUNCTION FOR /setposter COMMAND
# ============================================================
async def update_poster_and_channel(client, movie_name: str, new_poster_url: str) -> dict:
    try:
        normalized_key = re.sub(r'[^a-z0-9]', '', movie_name.lower())
        
        movie_doc = await db.movie_updates.find_one({"_id": normalized_key})
        if not movie_doc:
            return {"success": False, "message": f"❌ Movie '{movie_name}' not found in DB."}
        
        await db.movie_updates.update_one(
            {"_id": normalized_key},
            {"$set": {"poster_url": new_poster_url}}
        )
        
        msg_id = movie_doc.get("message_id")
        if msg_id:
            try:
                display_title = movie_doc.get("title", movie_name)
                text = generate_movie_message(movie_doc, display_title)
                buttons = InlineKeyboardMarkup([[
                    InlineKeyboardButton(text='♻️ 𝐉𝐎𝐈𝐍 𝐑𝐄𝐐𝐔𝐄𝐒𝐓 𝐆𝐑𝐎𝐔𝐏 ♻️', url="https://t.me/+l-EIo3NnnJAxODE9")
                ]])
                
                await client.edit_message_media(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=msg_id,
                    media=InputMediaPhoto(media=new_poster_url, caption=text, parse_mode=enums.ParseMode.HTML),
                    reply_markup=buttons
                )
                return {"success": True, "message": f"✅ Poster updated for '{display_title}' in DB and Channel."}
            except Exception as e:
                logger.error(f"Failed to edit post for {movie_name}: {e}")
                return {"success": True, "message": f"⚠️ DB updated, but channel post edit failed: {str(e)}"}
        else:
            return {"success": True, "message": f"✅ DB updated for '{movie_name}', but no channel post found (auto-deleted)."}
            
    except Exception as e:
        logger.error(f"update_poster_and_channel error: {e}")
        return {"success": False, "message": f"❌ Error: {str(e)}"}

async def verify_and_correct_post_with_ai(bot, message_id: int, normalized_key: str, buttons):
    try:
        await asyncio.sleep(60)
        movie_doc = await db.movie_updates.find_one({"_id": normalized_key})
        if not movie_doc or not movie_doc.get("poster_url"):
            return

        display_title = movie_doc.get("title", normalized_key)
        correct_text = generate_movie_message(movie_doc, display_title)
        
        try:
            live_msg = await bot.get_messages(chat_id=MOVIE_UPDATE_CHANNEL, message_ids=message_id)
            if isinstance(live_msg, list) and live_msg:
                live_msg = live_msg[0]
            live_text = live_msg.caption if live_msg else ""
            
            if live_text and live_text.strip() == correct_text.strip():
                return
                
            logger.info(f"🔎 AI detected a mismatch in post ID {message_id}. Correcting automatically...")
            await bot.edit_message_caption(
                chat_id=MOVIE_UPDATE_CHANNEL,
                message_id=message_id,
                caption=correct_text,
                reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML
            )
            logger.info(f"✅ AI successfully auto-corrected post ID {message_id}!")
        except MessageNotModified:
            pass 
        except FloodWait as e:
            logger.warning(f"AI engine hit floodwait. Sleeping for {e.value} seconds.")
            await asyncio.sleep(e.value + 5)
            await verify_and_correct_post_with_ai(bot, message_id, normalized_key, buttons)
        except Exception as msg_err:
            logger.error(f"Error while fetching or editing live message for AI verification: {msg_err}")
            
    except Exception as e:
        logger.error(f"Critical error in AI Double-Check Engine: {e}")
