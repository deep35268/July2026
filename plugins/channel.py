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
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, Message
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait
from pymongo.errors import DuplicateKeyError

# Plugin & Database Imports
from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx, get_movie_details
from database.users_chats_db import db
from database.ia_filterdb import save_file
from info import (
    CHANNELS, MOVIE_UPDATE_CHANNEL, BAD_WORDS, LANDSCAPE_POSTER, TMDB_POSTER, ADMINS
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

OTT_PLATFORMS = {
    "nf": "Netflix", "netflix": "Netflix",
    "sonyliv": "SonyLiv", "sony": "SonyLiv", "sliv": "SonyLiv",
    "amzn": "Amazon Prime Video", "prime": "Amazon Prime Video", "primevideo": "Amazon Prime Video",
    "hotstar": "Disney+ Hotstar", "zee5": "Zee5", "jio": "JioHotstar", "jhs": "JioHotstar",
    "aha": "Aha", "hbo": "HBO Max", "paramount": "Paramount+", "apple": "Apple TV+", 
    "hoichoi": "Hoichoi", "sunnxt": "Sun NXT", "viki": "Viki"
}

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

# ============ CREATE TITLE-ONLY POSTER (Backdrop + Title Overlay) ============

async def create_title_only_poster(backdrop_url: str, title: str) -> Optional[bytes]:
    """Download backdrop, overlay title text, return bytes."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(backdrop_url) as resp:
                if resp.status != 200:
                    return None
                img_data = await resp.read()
        
        image = Image.open(io.BytesIO(img_data)).convert("RGBA")
        img_w, img_h = image.size
        draw = ImageDraw.Draw(image)
        
        try:
            font_size = int(img_w * 0.10)
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except:
            font = ImageFont.load_default()
            font_size = 20
        
        wrapped_title = textwrap.fill(title.upper(), width=14)
        bbox = draw.textbbox((0, 0), wrapped_title, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (img_w - text_w) // 2
        y = (img_h - text_h) // 2 - int(text_h * 0.2)
        
        overlay = Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        padding = 25
        overlay_draw.rectangle(
            [x - padding, y - padding, x + text_w + padding, y + text_h + padding],
            fill=(0, 0, 0, 170)
        )
        image = Image.alpha_composite(image, overlay)
        draw = ImageDraw.Draw(image)
        draw.text((x, y), wrapped_title, font=font, fill=(255, 255, 255, 255))
        
        output = io.BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=92)
        return output.getvalue()
        
    except Exception as e:
        logger.error(f"Title-only poster generation failed: {e}")
        return None

# ============ AI & OFFICIAL LANDSCAPE POSTER FETCH ============

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
    """Fetch landscape poster from TMDB (backdrop) or Cinemeta."""
    # 1. Try TMDB first
    try:
        details = await get_movie_detailsx(movie_name)
        if details and details.get('backdrop_url'):
            backdrop = details['backdrop_url']
            if "t/p/" in backdrop:
                backdrop = re.sub(r'/t/p/w\d+/', '/t/p/original/', backdrop)
                backdrop = re.sub(r'/t/p/w\d+x\d+/', '/t/p/original/', backdrop)
            return backdrop
    except Exception as e:
        logger.error(f"TMDB backdrop error: {e}")

    # 2. Fallback to Cinemeta AI
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
    words = text.split()
    cleaned = []
    IGNORE_LOWER = {w.lower() for w in IGNORE_WORDS}
    for w in words:
        if w.lower() not in IGNORE_LOWER:
            cleaned.append(w)
    return " ".join(cleaned)

def extract_languages_from_text(text: str) -> set:
    found = set()
    text_lower = text.lower()
    for lang_key, lang_name in CAPTION_LANGUAGES.items():
        if re.search(rf'\b{re.escape(lang_key)}\b', text_lower):
            found.add(lang_name)
    return found

def extract_media_info(filename: str, caption: str = ""):
    """Extract base name, year, language, quality, etc. from filename."""
    filename_cleaned = clean_mentions_links(filename)
    filename_normalized = normalize(filename_cleaned)

    tag = "#MOVIE"
    year = None
    
    quality = QUALITY_PATTERN.findall(filename_normalized)
    quality_str = ", ".join(quality) if quality else "N/A"
    ott_platform = extract_ott_platform(filename_normalized)

    lang_set = set()
    lang_set.update(extract_languages_from_text(filename_normalized))
    language = ", ".join(sorted(lang_set)) if lang_set else "N/A"   # Will be mapped to "NO IDEA" later

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
        "base_name_key": base_name.lower(),
        "tag": tag,
        "year": year,
        "quality": quality_str,
        "ott_platform": ott_platform,
        "language": language
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
        display_name = media_info["base_name"]
        movie_key = media_info["base_name_key"]
        
        if len(POSTED_MOVIES) > MAX_CACHE_SIZE:
            POSTED_MOVIES.clear()
        
        if movie_key in POSTED_MOVIES:
            return

        async with locks[movie_key]:
            if movie_key in POSTED_MOVIES:
                return
            POSTED_MOVIES.add(movie_key)
            
            try:
                await _process_with_lock(bot, filename, caption, media_info, display_name)
            finally:
                await asyncio.sleep(12)
                POSTED_MOVIES.discard(movie_key)
                
    except Exception as e:
        logger.exception(f"Processing execution failed: {e}")

async def _process_with_lock(bot, filename, caption, media_info, display_name):
    if not hasattr(db, 'movie_updates'):
        db.movie_updates = db.db.movie_updates

    error_tmdb = False
    file_data = {
        "filename": filename,
        "quality": media_info["quality"],
        "language": media_info["language"],
        "timestamp": datetime.now()
    }

    movie_id = media_info["base_name_key"]

    try:
        details = {}
        tmdb_language_override = None
        final_poster = None
        is_series = (media_info["tag"] == "#SERIES")
        year_val = media_info["year"]
        rating_val = "N/A"

        # --- FETCH METADATA (TMDB) ---
        if TMDB_POSTER:
            try:
                details = await get_movie_detailsx(display_name)
                if not details or details.get("error"):
                    error_tmdb = True
                else:
                    orig_lang = details.get("original_language") or details.get("lang")
                    if orig_lang:
                        tmdb_language_override = TMDB_LANG_MAP.get(orig_lang.lower())
                    if details.get("rating"):
                        try:
                            rating_val = f"{float(details.get('rating')):.1f}"
                        except ValueError:
                            pass
                    if not year_val and details.get("year"):
                        year_val = str(details.get("year")).strip()
            except Exception:
                error_tmdb = True
                
        if not TMDB_POSTER or error_tmdb or not details:
            details = await get_movie_details(display_name) or {}
            if details.get("rating"):
                try:
                    rating_val = f"{float(details.get('rating')):.1f}"
                except ValueError:
                    pass
            if not year_val and details.get("year"):
                year_val = str(details.get("year")).strip()

        # --- SERIES YEAR FALLBACK (Cinemeta) ---
        if not year_val and is_series:
            try:
                session = await get_session()
                encoded_query = urllib.parse.quote(display_name)
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

        # --- FETCH POSTER (TMDB first) ---
        final_poster = await get_landscape_poster_only(display_name, is_series)

        # --- LANGUAGE OVERRIDE ---
        final_language = media_info["language"]
        if tmdb_language_override and tmdb_language_override != "N/A":
            if final_language == "N/A" or len(final_language.split(",")) <= 1:
                final_language = tmdb_language_override
            else:
                existing = set(l.strip() for l in final_language.split(","))
                existing.add(tmdb_language_override)
                final_language = ", ".join(sorted(existing))
        # if still N/A, we'll keep N/A (will be displayed as NO IDEA later)

        file_data["language"] = final_language

        # --- CHECK IF MOVIE EXISTS IN DB ---
        existing_movie = await db.movie_updates.find_one({"_id": movie_id})
        
        # --- IF POSTER IS MISSING ---
        if not final_poster:
            logger.warning(f"❌ Poster NOT found for '{display_name}'. Saving file in DB, waiting for Admin to set poster via /setposter.")
            if existing_movie:
                await db.movie_updates.update_one(
                    {"_id": movie_id},
                    {"$push": {"files": file_data}}
                )
            else:
                movie_doc = {
                    "_id": movie_id,
                    "display_title": display_name,
                    "files": [file_data],
                    "poster_url": None,
                    "poster_type": "backdrop",  # default
                    "rating": rating_val,
                    "year": year_val,
                    "tag": media_info["tag"],
                    "language": final_language,
                    "message_id": None,
                    "is_posted": False
                }
                try:
                    await db.movie_updates.insert_one(movie_doc)
                except DuplicateKeyError:
                    await db.movie_updates.update_one({"_id": movie_id}, {"$push": {"files": file_data}})
            return

        # --- POSTER IS AVAILABLE. PROCEED TO POST/UPDATE ---
        update_fields = {}
        if existing_movie:
            if existing_movie.get("rating") == "N/A" and rating_val != "N/A":
                update_fields["rating"] = rating_val
            if not existing_movie.get("year") and year_val:
                update_fields["year"] = year_val
            if not existing_movie.get("poster_url") and final_poster:
                update_fields["poster_url"] = final_poster
            if existing_movie.get("language") != final_language and final_language != "N/A":
                update_fields["language"] = final_language
            if existing_movie.get("display_title") != display_name:
                update_fields["display_title"] = display_name
            # keep poster_type as backdrop (default)

        # Case 1: Movie exists
        if existing_movie:
            post_exists = False
            if existing_movie.get("message_id"):
                try:
                    msg = await bot.get_messages(chat_id=MOVIE_UPDATE_CHANNEL, message_ids=existing_movie["message_id"])
                    if msg:
                        post_exists = True
                except MessageIdInvalid:
                    logger.warning(f"⚠️ Post for '{display_name}' (ID {existing_movie['message_id']}) no longer exists. Reposting.")
                    await db.movie_updates.update_one({"_id": movie_id}, {"$set": {"message_id": None}})
                    post_exists = False
                except Exception as e:
                    logger.error(f"Error checking post: {e}")
                    post_exists = False

            file_exists = any(f.get("filename") == filename for f in existing_movie.get("files", []))

            if post_exists:
                if not file_exists:
                    await db.movie_updates.update_one(
                        {"_id": movie_id}, 
                        {"$push": {"files": file_data}, "$set": update_fields} if update_fields else {"$push": {"files": file_data}}
                    )
                    await send_movie_update(bot, movie_id, is_update=True)
                elif update_fields:
                    await db.movie_updates.update_one({"_id": movie_id}, {"$set": update_fields})
                    await send_movie_update(bot, movie_id, is_update=True)
                return
            else:
                if not file_exists:
                    await db.movie_updates.update_one(
                        {"_id": movie_id},
                        {"$push": {"files": file_data}, "$set": {"message_id": None, **update_fields}}
                    )
                else:
                    await db.movie_updates.update_one({"_id": movie_id}, {"$set": {"message_id": None, **update_fields}})
                
                msg = await send_movie_update(bot, movie_id, is_update=False)
                if msg:
                    await db.movie_updates.update_one({"_id": movie_id}, {"$set": {"message_id": msg.id}})
                return

        # Case 2: New Movie
        movie_doc = {
            "_id": movie_id,
            "display_title": display_name,
            "files": [file_data],
            "poster_url": final_poster,
            "poster_type": "backdrop",  # from TMDB
            "rating": rating_val,
            "year": year_val,
            "tag": media_info["tag"],
            "language": final_language,
            "message_id": None,
            "is_posted": True
        }
        
        try:
            await db.movie_updates.insert_one(movie_doc)
            msg = await send_movie_update(bot, movie_id, is_update=False)
            if msg:
                await db.movie_updates.update_one({"_id": movie_id}, {"$set": {"message_id": msg.id}})
        except DuplicateKeyError:
            await db.movie_updates.update_one({"_id": movie_id}, {"$push": {"files": file_data}, "$set": {"message_id": None}})
            msg = await send_movie_update(bot, movie_id, is_update=False)
            if msg:
                await db.movie_updates.update_one({"_id": movie_id}, {"$set": {"message_id": msg.id}})

    except Exception as e:
        logger.error(f"Error in backend lock verification process: {e}")

# ============ SEND MOVIE UPDATE ============

async def send_movie_update(bot, movie_id, is_update=False):
    try:
        movie_doc = await db.movie_updates.find_one({"_id": movie_id})
        if not movie_doc:
            return None

        display_name = movie_doc.get("display_title", movie_id.title())
        
        poster_url = movie_doc.get("poster_url")
        if not poster_url:
            logger.warning(f"⚠️ No poster_url set for {display_name}. Cannot send/update.")
            return None

        text = generate_movie_message(movie_doc, display_name)
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton(text='♻️ 𝐉𝐎𝐈𝐍 𝐑𝐄𝐐𝐔𝐄𝐒𝐓 𝐆𝐑𝐎𝐔𝐏 ♻️', url="https://t.me/+l-EIo3NnnJAxODE9")]])

        sent_msg = None

        # Determine poster type: if "custom", send raw; else try overlay
        poster_type = movie_doc.get("poster_type", "backdrop")

        # --- UPDATE CASE ---
        if is_update and movie_doc.get("message_id"):
            if poster_type == "custom":
                # Edit with raw photo (caption only? we need to edit media)
                try:
                    sent_msg = await bot.edit_message_caption(
                        chat_id=MOVIE_UPDATE_CHANNEL,
                        message_id=movie_doc["message_id"],
                        caption=text,
                        reply_markup=buttons,
                        parse_mode=enums.ParseMode.HTML
                    )
                except MessageNotModified:
                    sent_msg = movie_doc
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    return await send_movie_update(bot, movie_id, is_update)
                except Exception as e:
                    logger.error(f"Edit caption (custom) failed: {e}")
                    return None
            else:
                # backdrop: try to overlay
                image_bytes = await create_title_only_poster(poster_url, display_name)
                if image_bytes:
                    media = InputMediaPhoto(media=image_bytes, caption=text, parse_mode=enums.ParseMode.HTML)
                    try:
                        sent_msg = await bot.edit_message_media(
                            chat_id=MOVIE_UPDATE_CHANNEL,
                            message_id=movie_doc["message_id"],
                            media=media,
                            reply_markup=buttons
                        )
                    except MessageNotModified:
                        sent_msg = movie_doc
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                        return await send_movie_update(bot, movie_id, is_update)
                    except MessageIdInvalid:
                        logger.warning(f"Message ID invalid for {display_name}. Sending new.")
                        await db.movie_updates.update_one({"_id": movie_id}, {"$set": {"message_id": None}})
                        return await send_movie_update(bot, movie_id, is_update=False)
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
                else:
                    # fallback: edit caption only
                    try:
                        sent_msg = await bot.edit_message_caption(
                            chat_id=MOVIE_UPDATE_CHANNEL,
                            message_id=movie_doc["message_id"],
                            caption=text,
                            reply_markup=buttons,
                            parse_mode=enums.ParseMode.HTML
                        )
                    except MessageNotModified:
                        sent_msg = movie_doc
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                        return await send_movie_update(bot, movie_id, is_update)
                    except Exception as e:
                        logger.error(f"Caption edit failed: {e}")
                        return None
            
            if sent_msg:
                return sent_msg
            else:
                return None

        # --- NEW POST CASE ---
        if poster_type == "custom":
            # Send raw photo
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
                return await send_movie_update(bot, movie_id, is_update)
            except Exception as e:
                logger.error(f"Custom photo send failed: {e}")
                # fallback to text-only
                try:
                    sent_msg = await bot.send_message(
                        chat_id=MOVIE_UPDATE_CHANNEL,
                        text=text,
                        reply_markup=buttons,
                        parse_mode=enums.ParseMode.HTML
                    )
                except Exception as e2:
                    logger.error(f"Text-only send also failed: {e2}")
                    return None
        else:
            # backdrop: generate overlay
            image_bytes = await create_title_only_poster(poster_url, display_name)
            try:
                if image_bytes:
                    sent_msg = await bot.send_photo(
                        chat_id=MOVIE_UPDATE_CHANNEL,
                        photo=image_bytes,
                        caption=text,
                        reply_markup=buttons,
                        parse_mode=enums.ParseMode.HTML
                    )
                else:
                    sent_msg = await bot.send_photo(
                        chat_id=MOVIE_UPDATE_CHANNEL,
                        photo=poster_url,
                        caption=text,
                        reply_markup=buttons,
                        parse_mode=enums.ParseMode.HTML
                    )
            except FloodWait as e:
                await asyncio.sleep(e.value)
                return await send_movie_update(bot, movie_id, is_update)
            except Exception as e:
                logger.error(f"Backdrop send failed: {e}")
                try:
                    sent_msg = await bot.send_message(
                        chat_id=MOVIE_UPDATE_CHANNEL,
                        text=text,
                        reply_markup=buttons,
                        parse_mode=enums.ParseMode.HTML
                    )
                except Exception as e2:
                    logger.error(f"Text-only send also failed: {e2}")
                    return None

        if sent_msg and hasattr(sent_msg, 'id'):
            await db.movie_updates.update_one({"_id": movie_id}, {"$set": {"message_id": sent_msg.id, "is_posted": True}})
            asyncio.create_task(verify_and_correct_post_with_ai(bot, sent_msg.id, movie_id, buttons))
            return sent_msg

    except Exception as e:
        logger.error(f"Failed to push update layout: {e}")
    return None

# ============ AI DOUBLE CHECK & AUTO CORRECTION ============

async def verify_and_correct_post_with_ai(bot, message_id: int, movie_id: str, buttons):
    try:
        await asyncio.sleep(60)
        movie_doc = await db.movie_updates.find_one({"_id": movie_id})
        if not movie_doc or not movie_doc.get("poster_url"):
            return

        display_name = movie_doc.get("display_title", movie_id.title())
        correct_text = generate_movie_message(movie_doc, display_name)
        
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
            await verify_and_correct_post_with_ai(bot, message_id, movie_id, buttons)
        except Exception as msg_err:
            logger.error(f"Error while fetching or editing live message for AI verification: {msg_err}")
            
    except Exception as e:
        logger.error(f"Critical error in AI Double-Check Engine: {e}")

# ==================================================
# 🟢 GENERATE MOVIE MESSAGE (Final)
# ==================================================

def generate_movie_message(movie_doc, display_name) -> str:
    all_languages = set()
    for file in movie_doc.get("files", []):
        if file.get("language") and file["language"] != "N/A":
            all_languages.update(l.strip() for l in file["language"].split(",") if l.strip())
    
    if movie_doc.get("language") and movie_doc["language"] != "N/A":
        all_languages.update(l.strip() for l in movie_doc["language"].split(",") if l.strip())
    
    # If no languages found, show "NO IDEA"
    if not all_languages:
        language_str = "NO IDEA"
    else:
        language_str = " ".join(f"#{lang}" for lang in sorted(all_languages))
    
    title = html.escape(display_name.upper())
    title = re.sub(r'\b10BIT\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s+', ' ', title).strip()
    
    year_val = str(movie_doc.get("year", "")).strip()
    year_val = re.sub(r'[()\[\]]', '', year_val)
    
    is_series = (movie_doc.get("tag") == "#SERIES")
    
    if is_series:
        year_str = ""
    else:
        year_str = f" ({html.escape(year_val)})" if year_val and year_val != "None" and year_val not in title else ""
    
    rating_raw = movie_doc.get("rating", "N/A")
    rating_str = rating_raw if rating_raw != "N/A" else "N/A"
    
    return (
        f"🎬 <code>{title}{year_str}</code>\n"
        f"<i>📌 (Touch to Copy)</i>\n\n"
        f"⭐ IMDb: {rating_str}\n\n"
        f"➡ Audio Track: 🔊 {language_str}\n\n"
        f"✅ Added"
    )

# ==================================================
# 🟢 ADMIN COMMAND: /setposter (Custom poster, no overlay)
# ==================================================

@Client.on_message(filters.command("setposter") & filters.user(ADMINS))
async def set_poster_cmd(bot: Client, message: Message):
    try:
        # --- 1. Extract poster URL and movie name ---
        text = message.text.split("/setposter", 1)[-1].strip()
        if not text:
            await message.reply(
                "❌ **ਗਲਤ ਫਾਰਮੈਟ!**\n\n"
                "✅ **ਸਹੀ ਤਰੀਕਾ:**\n"
                "1. ਕਿਸੇ ਫਾਈਲ 'ਤੇ reply ਕਰੋ: `/setposter https://example.com/poster.jpg`\n"
                "2. ਜਾਂ ਇਸ ਤਰ੍ਹਾਂ: `/setposter ਮੂਵੀ ਦਾ ਨਾਮ | https://example.com/poster.jpg`\n"
                "3. ਸਿਰਫ਼ ਨਾਮ (TMDB ਪੋਸਟਰ): `/setposter ਮੂਵੀ ਦਾ ਨਾਮ`\n"
                "   (ਪਰ ਇਹ overlay ਨਾਲ ਆਵੇਗਾ, ਕਿਉਂਕਿ backdrop ਹੈ)"
            )
            return
        
        parts = text.split("|")
        if len(parts) == 2:
            movie_name = parts[0].strip()
            provided_url = parts[1].strip()
        elif len(parts) == 1:
            # check if it's a URL or just name
            if parts[0].strip().startswith(("http://", "https://")):
                provided_url = parts[0].strip()
                movie_name = None
                if message.reply_to_message:
                    reply_msg = message.reply_to_message
                    for attr in ("document", "video", "audio"):
                        media = getattr(reply_msg, attr, None)
                        if media and hasattr(media, "file_name"):
                            movie_name = media.file_name
                            break
                    if not movie_name and reply_msg.caption:
                        movie_name = reply_msg.caption
                if not movie_name:
                    await message.reply(
                        "❌ **ਮੂਵੀ ਦਾ ਨਾਮ ਨਹੀਂ ਮਿਲਿਆ।**\n\n"
                        "👉 ਕਿਰਪਾ ਕਰਕੇ:\n"
                        "1. ਕਿਸੇ ਫਾਈਲ 'ਤੇ reply ਕਰਕੇ `/setposter <URL>` ਭੇਜੋ, ਜਾਂ\n"
                        "2. ਇਸ ਫਾਰਮੈਟ ਵਰਤੋ: `/setposter ਮੂਵੀ ਦਾ ਨਾਮ | URL`"
                    )
                    return
            else:
                # only name, no URL
                movie_name = parts[0].strip()
                provided_url = None
        else:
            await message.reply("❌ ਗਲਤ ਫਾਰਮੈਟ।")
            return

        # --- 2. Validate URL if provided ---
        if provided_url and not provided_url.startswith(("http://", "https://")):
            await message.reply("❌ URL `http://` ਜਾਂ `https://` ਨਾਲ ਸ਼ੁਰੂ ਹੋਣਾ ਚਾਹੀਦਾ ਹੈ।")
            return

        # --- 3. Extract movie info from filename ---
        media_info = extract_media_info(movie_name, "")
        display_name = media_info["base_name"]
        movie_id = media_info["base_name_key"]
        is_series = (media_info["tag"] == "#SERIES")

        # --- 4. Determine poster ---
        final_poster = None
        poster_type = "backdrop"  # default

        if provided_url:
            # user provided a custom URL → use it exactly
            final_poster = provided_url
            poster_type = "custom"
        else:
            # no URL: try TMDB backdrop
            final_poster = await get_landscape_poster_only(display_name, is_series)
            if final_poster:
                poster_type = "backdrop"
            else:
                await message.reply(
                    f"❌ **'{display_name}'** ਲਈ TMDB backdrop ਨਹੀਂ ਮਿਲਿਆ ਅਤੇ ਤੁਸੀਂ ਕੋਈ URL ਵੀ ਨਹੀਂ ਦਿੱਤਾ।\n\n"
                    "👉 ਕਿਰਪਾ ਕਰਕੇ URL ਪ੍ਰਦਾਨ ਕਰੋ: `/setposter ਮੂਵੀ ਦਾ ਨਾਮ | URL`"
                )
                return

        # --- 5. Fetch TMDB metadata (rating, year, language) ---
        tmdb_rating = "N/A"
        tmdb_year = media_info.get("year")
        tmdb_language = media_info.get("language", "N/A")
        tmdb_tag = media_info.get("tag", "#MOVIE")

        try:
            details = await get_movie_detailsx(display_name)
            if details and not details.get("error"):
                if details.get("rating"):
                    try:
                        tmdb_rating = f"{float(details.get('rating')):.1f}"
                    except:
                        pass
                if details.get("year") and not tmdb_year:
                    tmdb_year = str(details.get("year")).strip()
                orig_lang = details.get("original_language")
                if orig_lang:
                    tmdb_language = TMDB_LANG_MAP.get(orig_lang.lower(), tmdb_language)
        except Exception as e:
            logger.warning(f"TMDB metadata fetch failed for {display_name}: {e}")

        # If still no language, keep N/A (will show NO IDEA)

        # --- 6. Check if movie exists in DB ---
        movie_doc = await db.movie_updates.find_one({"_id": movie_id})

        if movie_doc:
            # Update existing with new poster
            await db.movie_updates.update_one(
                {"_id": movie_id},
                {"$set": {"poster_url": final_poster, "poster_type": poster_type}}
            )
            await message.reply(f"✅ **'{display_name}'** ਲਈ ਪੋਸਟਰ ਅੱਪਡੇਟ ਕਰ ਦਿੱਤਾ ਗਿਆ ਹੈ।")
        else:
            # Create new entry (empty files)
            new_doc = {
                "_id": movie_id,
                "display_title": display_name,
                "files": [],
                "poster_url": final_poster,
                "poster_type": poster_type,
                "rating": tmdb_rating,
                "year": tmdb_year,
                "tag": tmdb_tag,
                "language": tmdb_language,
                "message_id": None,
                "is_posted": False
            }
            try:
                await db.movie_updates.insert_one(new_doc)
                movie_doc = new_doc
                await message.reply(f"✅ **'{display_name}'** ਲਈ ਨਵੀਂ ਐਂਟਰੀ ਬਣਾ ਦਿੱਤੀ ਗਈ (ਬਿਨਾਂ ਫਾਈਲਾਂ ਦੇ)।")
            except DuplicateKeyError:
                movie_doc = await db.movie_updates.find_one({"_id": movie_id})
                if not movie_doc:
                    await message.reply("❌ ਐਂਟਰੀ ਬਣਾਉਣ ਵਿੱਚ ਗਲਤੀ ਆਈ।")
                    return

        # --- 7. Send/update the post ---
        if movie_doc.get("message_id"):
            await send_movie_update(bot, movie_id, is_update=True)
        else:
            msg = await send_movie_update(bot, movie_id, is_update=False)
            if msg:
                await db.movie_updates.update_one({"_id": movie_id}, {"$set": {"message_id": msg.id}})
                await message.reply("✅ ਪੋਸਟ ਚੈਨਲ 'ਤੇ ਸਫ਼ਲਤਾਪੂਰਵਕ ਭੇਜ ਦਿੱਤੀ ਗਈ!")
            else:
                await message.reply("❌ ਪੋਸਟ ਭੇਜਣ ਵਿੱਚ ਗਲਤੀ ਆਈ।")
                
    except Exception as e:
        logger.error(f"/setposter error: {e}")
        await message.reply(f"❌ ਗਲਤੀ: {e}")
