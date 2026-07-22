import re
import logging
import asyncio
from datetime import datetime
from collections import defaultdict
from typing import Optional, Tuple
import io
import textwrap
from PIL import Image, ImageDraw, ImageFont
import aiohttp

from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx, fetch_image, get_movie_details
from database.users_chats_db import db
from pyrogram import Client, filters, enums
from info import CHANNELS, MOVIE_UPDATE_CHANNEL, LINK_PREVIEW, ABOVE_PREVIEW, BAD_WORDS, LANDSCAPE_POSTER, TMDB_POSTER
from Script import script
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from utils import temp
from pymongo.errors import PyMongoError, DuplicateKeyError
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait

logger = logging.getLogger(__name__)

# ============ HELPERS ============
def ensure_str(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode('utf-8')
        except UnicodeDecodeError:
            return str(value)
    return str(value)

def is_valid_url(url):
    url = ensure_str(url)
    return url.startswith(('http://', 'https://'))

# ============ GENERATE LANDSCAPE POSTER ============
async def generate_landscape_poster(
    title: str,
    backdrop_url: Optional[str] = None,
    portrait_url: Optional[str] = None,
    cast_names: Optional[list] = None,
    tagline: Optional[str] = None,
    year: Optional[str] = None
) -> Optional[bytes]:
    """
    Priority: 1. Portrait (has title) → center on landscape canvas (NO overlay).
              2. Backdrop + overlay (title, cast, tagline, year).
              3. Solid background + title (fallback).
    """
    W, H = 1920, 1080
    bg = None

    # 1. Portrait (has title)
    if portrait_url and is_valid_url(portrait_url):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(portrait_url, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        poster = Image.open(io.BytesIO(data)).convert("RGBA")
                        poster_w, poster_h = poster.size
                        target_h = int(H * 0.75)
                        target_w = int(poster_w * (target_h / poster_h))
                        if target_w > W:
                            target_w = W
                            target_h = int(poster_h * (target_w / poster_w))
                        poster = poster.resize((target_w, target_h), Image.Resampling.LANCZOS)
                        canvas = Image.new("RGB", (W, H), (15, 15, 25))
                        x = (W - target_w) // 2
                        y = (H - target_h) // 2 - 30
                        canvas.paste(poster, (x, y), poster)
                        bg = canvas
        except Exception as e:
            logger.warning(f"Portrait fetch failed: {e}")

    # 2. Backdrop + overlay
    if bg is None and backdrop_url and is_valid_url(backdrop_url):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(backdrop_url, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        img = Image.open(io.BytesIO(data)).convert("RGBA")
                        img = img.resize((W, H), Image.Resampling.LANCZOS)
                        draw = ImageDraw.Draw(img)

                        # Load fonts
                        font_paths = [
                            "assets/font.ttf",
                            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
                        ]
                        title_font = sub_font = small_font = None
                        for path in font_paths:
                            try:
                                title_font = ImageFont.truetype(path, int(W * 0.08))
                                sub_font = ImageFont.truetype(path, int(W * 0.035))
                                small_font = ImageFont.truetype(path, int(W * 0.025))
                                break
                            except:
                                continue
                        if title_font is None:
                            title_font = sub_font = small_font = ImageFont.load_default()

                        # Gradient
                        for i in range(0, int(H * 0.35)):
                            alpha = int(200 * (1 - i / (H * 0.35)))
                            draw.rectangle([(0, i), (W, i+1)], fill=(0, 0, 0, alpha))
                            y_bottom = H - i
                            draw.rectangle([(0, y_bottom), (W, y_bottom+1)], fill=(0, 0, 0, alpha))

                        # Cast
                        cast_text = " | ".join(ensure_str(c) for c in (cast_names or [])[:3]).upper()
                        if cast_text:
                            bbox = draw.textbbox((0, 0), cast_text, font=sub_font)
                            tw = bbox[2] - bbox[0]
                            draw.text(((W - tw)//2, int(H * 0.08)), cast_text, font=sub_font, fill=(255, 215, 0, 255))

                        # Title
                        wrapped_title = textwrap.fill(ensure_str(title).upper(), width=14)
                        bbox = draw.textbbox((0, 0), wrapped_title, font=title_font)
                        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
                        draw.text(((W - tw)//2, (H - th)//2 - th//2), wrapped_title, font=title_font, fill=(255, 255, 255, 255))

                        # Tagline
                        if tagline:
                            tagline = ensure_str(tagline).upper()
                            bbox = draw.textbbox((0, 0), tagline, font=sub_font)
                            tw = bbox[2] - bbox[0]
                            draw.text(((W - tw)//2, (H//2) + int(H * 0.06)), tagline, font=sub_font, fill=(255, 255, 200, 255))

                        # Year
                        year = ensure_str(year)
                        bottom_text = f"ONLY IN THEATRES  {year}" if year else "ONLY IN THEATRES"
                        bbox = draw.textbbox((0, 0), bottom_text, font=small_font)
                        tw = bbox[2] - bbox[0]
                        draw.text(((W - tw)//2, H - int(H * 0.1)), bottom_text, font=small_font, fill=(255, 255, 255, 255))

                        bg = img
        except Exception as e:
            logger.warning(f"Backdrop overlay failed: {e}")

    # 3. Fallback solid background
    if bg is None:
        bg = Image.new("RGB", (W, H), (15, 15, 25))
        draw = ImageDraw.Draw(bg)
        try:
            font = ImageFont.truetype("assets/font.ttf", 120)
        except:
            font = ImageFont.load_default()
        draw.text((W//2, H//2), ensure_str(title).upper(), font=font, fill=(255,255,255), anchor="mm")

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="JPEG", quality=92)
    return out.getvalue()

# ============ DUPLICATE CHECK (optional but recommended) ============
async def is_movie_posted_in_channel(client, movie_name: str, channel_id: int, limit: int = 300) -> bool:
    try:
        async for message in client.get_chat_history(chat_id=channel_id, limit=limit):
            if not message.caption:
                continue
            if movie_name.lower() in message.caption.lower():
                logger.info(f"✅ Existing post found for '{movie_name}' (msg_id: {message.id})")
                return True
    except Exception as e:
        logger.error(f"Channel check error: {e}")
    return False

# ============ CONSTANTS (unchanged) ============
IGNORE_WORDS = {
    "rarbg", "dub", "sub", "sample", "mkv", "aac", "combined",
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
    "primevideo", "hotstar", "zee5", "jio", "jhs", "aha", "hbo", "paramount", 
    "apple", "hoichoi", "sunnxt", "viki"
} | BAD_WORDS

CAPTION_LANGUAGES = {
    "hin": "Hindi", "hindi": "Hindi",
    "tam": "Tamil", "tamil": "Tamil",
    "kan": "Kannada", "kannada": "Kannada",
    "tel": "Telugu", "telugu": "Telugu",
    "mal": "Malayalam", "malayalam": "Malayalam",
    "eng": "English", "english": "English",
    "pun": "Punjabi", "punjabi": "Punjabi",
    "ben": "Bengali", "bengali": "Bengali",
    "mar": "Marathi", "marathi": "Marathi",
    "guj": "Gujarati", "gujarati": "Gujarati",
    "urd": "Urdu", "urdu": "Urdu",
    "kor": "Korean", "korean": "Korean",
    "jpn": "Japanese", "japanese": "Japanese",
}

OTT_PLATFORMS = {
    "nf": "Netflix", "netflix": "Netflix",
    "sonyliv": "SonyLiv", "sony": "SonyLiv", "sliv": "SonyLiv",
    "amzn": "Amazon Prime Video", "prime": "Amazon Prime Video", "primevideo": "Amazon Prime Video",
    "hotstar": "Disney+ Hotstar", "zee5": "Zee5",
    "jio": "JioHotstar", "jhs": "JioHotstar",
    "aha": "Aha", "hbo": "HBO Max", "paramount": "Paramount+",
    "apple": "Apple TV+", "hoichoi": "Hoichoi", "sunnxt": "Sun NXT", "viki": "Viki"
}

STANDARD_GENRES = {
    'Action', 'Adventure', 'Animation', 'Biography', 'Comedy', 'Crime', 'Documentary',
    'Drama', 'Family', 'Fantasy', 'Film-Noir', 'History', 'Horror', 'Music',
    'Musical', 'Mystery', 'Romance', 'Sci-Fi', 'Sport', 'Thriller', 'War', 'Western'
}

CLEAN_PATTERN = re.compile(r'@[^ \n\r\t\.,:;!?()\[\]{}<>\\/"\'=_%]+|\bwww\.[^\s\]\)]+|\([\@^]+\)|\[[\@^]+\]')
NORMALIZE_PATTERN = re.compile(r"[._]+|[()\[\]{}:;'–!,.?_]")
QUALITY_PATTERN = re.compile(
    r"\b(?:HDCam|HDTC|CamRip|TS|TC|TeleSync|DVDScr|DVDRip|PreDVD|"
    r"WEBRip|WEB-DL|TVRip|HDTV|WEB DL|WebDl|BluRay|BRRip|BDRip|"
    r"360p|480p|720p|1080p|2160p|4K|1440p|540p|240p|140p|HEVC|HDRip)\b", 
    re.IGNORECASE
)
YEAR_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?![A-Za-z0-9])")
RANGE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,2})\s*(?:to|-)\s*(?:E(?:p(?:isode)?)?)?0*(\d{1,3})',re.IGNORECASE)
SINGLE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,3})', re.IGNORECASE)
NAMED_REGEX = re.compile(r'Season\s*0*(\d{1,2})[\s\-,:]*Ep(?:isode)?\s*0*(\d{1,3})', re.IGNORECASE)
EP_ONLY_RANGE = re.compile(r'\b(?:EP|Episode)0*(\d{1,3})\s*-\s*0*(\d{1,3})\b',re.IGNORECASE)

MEDIA_FILTER = filters.document | filters.video | filters.audio
locks = defaultdict(asyncio.Lock)

def clean_mentions_links(text: str) -> str:
    return CLEAN_PATTERN.sub("", text or "").strip()

def normalize(s: str) -> str:
    s = NORMALIZE_PATTERN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def remove_ignored_words(text: str) -> str:
    IGNORE_WORDS_LOWER = {w.lower() for w in IGNORE_WORDS}
    return " ".join(word for word in text.split() if word.lower() not in IGNORE_WORDS_LOWER)

def get_qualities(text: str) -> str:
    qualities = QUALITY_PATTERN.findall(text)
    return ", ".join(qualities) if qualities else "N/A"

def extract_ott_platform(text: str) -> str:
    text = text.lower()
    platforms = {plat for key, plat in OTT_PLATFORMS.items() if key in text}
    return " | ".join(platforms) if platforms else "N/A"

def extract_season_episode(filename: str) -> Tuple[Optional[int], Optional[str]]:
    if m := EP_ONLY_RANGE.search(filename):
        return 1, f"{int(m.group(1))}-{int(m.group(2))}"
    for pattern in (RANGE_REGEX, SINGLE_REGEX, NAMED_REGEX):
        if m := pattern.search(filename):
            season = int(m.group(1))
            if pattern == RANGE_REGEX:
                ep = f"{m.group(2)}-{m.group(3)}"
            else:
                ep = m.group(2)
            return season, ep
    return None, None

def extract_media_info(filename: str, caption: str):
    filename = normalize(clean_mentions_links(filename).title())
    caption_clean = clean_mentions_links(caption).lower() if caption else ""
    unified = f"{caption_clean} {filename.lower()}".strip()

    season = episode = year = None
    tag = "#MOVIE"
    processed_raw = base_raw = filename
    quality = get_qualities(caption_clean) or get_qualities(filename.lower()) or "N/A"
    ott_platform = extract_ott_platform(f"{filename} {caption_clean}")

    lang_keys = {k for k in CAPTION_LANGUAGES if k in caption_clean or k in filename.lower()}
    language = ", ".join(sorted({CAPTION_LANGUAGES[k] for k in lang_keys})) if lang_keys else "N/A"

    season, episode = extract_season_episode(filename)
    if season is not None:
        tag = "#SERIES"
        if m := (RANGE_REGEX.search(filename) or SINGLE_REGEX.search(filename) or NAMED_REGEX.search(filename) or EP_ONLY_RANGE.search(filename)):
            match_str = m.group(0)
            start_idx = filename.lower().find(match_str.lower())
            end_idx = start_idx + len(match_str)
            processed_raw = filename[:end_idx]
            base_raw = filename[:start_idx]
            if year_match := YEAR_PATTERN.search(filename.lower()[end_idx:]):
                y = year_match.group(0)
                yi = filename.lower().find(y, end_idx)
                if yi != -1:
                    processed_raw = filename[:yi+4]
                    base_raw += f" {y}"
    else:
        if year_match := YEAR_PATTERN.search(unified):
            year = year_match.group(0)
            year_idx = filename.lower().find(year.lower())
            if year_idx != -1:
                processed_raw = filename[:year_idx + 4]
                base_raw = processed_raw
        else:
            if qual_match := QUALITY_PATTERN.search(unified):
                qual_str = qual_match.group(0)
                qual_idx = filename.lower().find(qual_str.lower())
                if qual_idx != -1:
                    processed_raw = filename[:qual_idx]
                    base_raw = processed_raw

    base_name = normalize(remove_ignored_words(normalize(base_raw)))
    if year and year not in base_name:
        base_name += f" {year}"

    if base_name.endswith(")"):
        base_name = re.sub(r"\s+\(\d{4}\)$", "", base_name)
        if year:
            base_name += f" {year}"

    # Remove season/episode tokens from base_name
    def _strip_season_episode_tokens(name: str) -> str:
        if not name:
            return name
        year_match = re.search(r'\(?\b(19|20)\d{2}\b\)?\s*$', name)
        year_part = ""
        if year_match:
            year_part = year_match.group(0)
            name = name[:year_match.start()].strip()
        patterns = [
            r'\bS\d{1,2}E\d{1,2}\b',
            r'\bS\d{1,2}\b',
            r'\bE\d{1,2}\b',
            r'\b\d{1,2}x\d{1,2}\b',
            r'\bSeason\s*\d{1,2}\b',
            r'\bEp(?:isode)?\.?\s*\d{1,3}\b',
            r'\bEpisode\s*\d{1,3}\b',
            r'\bPart\s*\d{1,2}\b'
        ]
        for p in patterns:
            name = re.sub(p, ' ', name, flags=re.IGNORECASE)
        name = re.sub(r'[_\.\-]+', ' ', name)
        name = re.sub(r'\s+', ' ', name).strip()
        if year_part:
            y = re.search(r'(19|20)\d{2}', year_part)
            if y:
                name = f"{name} {y.group(0)}"
        return name.strip()

    base_name = _strip_season_episode_tokens(base_name)
    if not base_name:
        base_name = normalize(remove_ignored_words(normalize(processed_raw))) or filename

    return {
        "processed": normalize(processed_raw),
        "base_name": base_name,
        "tag": tag,
        "season": season,
        "episode": episode,
        "year": year,
        "quality": quality,
        "ott_platform": ott_platform,
        "language": language
    }

# ============ MAIN HANDLERS ============
@Client.on_message(filters.chat(CHANNELS) & MEDIA_FILTER)
async def media_handler(bot, message):
    media = next(
        (getattr(message, ft) for ft in ("document", "video", "audio")
         if getattr(message, ft, None)),
        None
    )
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
        logger.exception("Error processing media")

async def process_and_send_update(bot, filename, caption):
    try:
        media_info = extract_media_info(filename, caption)
        base_name = media_info["base_name"]
        processed = media_info["processed"]

        lock = locks[base_name]
        async with lock:
            await _process_with_lock(bot, filename, caption, media_info, base_name, processed)
    except PyMongoError as e:
        logger.error(f"Database error in process_and_send_update: {e}")
    except Exception as e:
        logger.exception(f"Processing failed in process_and_send_update: {e}")

# ============ PROCESS WITH LOCK ============
async def _process_with_lock(bot, filename, caption, media_info, base_name, processed):
    if not hasattr(db, 'movie_updates'):
        db.movie_updates = db.db.movie_updates

    movie_doc = await db.movie_updates.find_one({"_id": base_name})
    error_tmdb = False
    file_data = {
        "filename": filename,
        "processed": processed,
        "quality": media_info["quality"],
        "language": media_info["language"],
        "ott_platform": media_info["ott_platform"],
        "timestamp": datetime.now(),
        "tag": media_info["tag"],
        "season": media_info["season"],
        "episode": media_info["episode"]
    }

    if not movie_doc:
        details = {}
        backdrop_url = None
        portrait_url = None
        if TMDB_POSTER:
            details = await get_movie_detailsx(base_name)
            if details and not details.get("error"):
                backdrop_url = ensure_str(details.get("backdrop_url"))
                poster_path = ensure_str(details.get("poster_path"))
                if poster_path:
                    if not poster_path.startswith(('http://', 'https://')):
                        portrait_url = f"https://image.tmdb.org/t/p/original{poster_path}"
                    else:
                        portrait_url = poster_path
            else:
                error_tmdb = True
                details = await get_movie_details(base_name) or {}

        raw_genres = details.get("genres", "N/A")
        if isinstance(raw_genres, str):
            genre_list = [g.strip() for g in raw_genres.split(",")]
            genres = ", ".join(g for g in genre_list if g in STANDARD_GENRES) or "N/A"
        else:
            genres = ", ".join(g for g in raw_genres if g in STANDARD_GENRES) or "N/A"

        rating = details.get("rating", "N/A")
        try:
            r = float(rating)
        except:
            r = 0.0
        rating_str = str(rating) if r != 0.0 else "N/A"

        movie_data = {
            "title": ensure_str(details.get("title") or base_name),
            "tagline": ensure_str(details.get("tagline", "")),
            "cast_names": [ensure_str(c) for c in details.get("cast", [])],
            "year": ensure_str(details.get("year") or media_info["year"]),
            "backdrop_url": backdrop_url,
            "portrait_url": portrait_url
        }

        movie_doc = {
            "_id": base_name,
            "files": [file_data],
            "poster_url": backdrop_url if LANDSCAPE_POSTER else portrait_url,
            "movie_data": movie_data,
            "genres": genres,
            "rating": rating_str,
            "imdb_url": details.get("url", "") if error_tmdb else details.get("tmdb_url"),
            "year": details.get("year") or media_info["year"],
            "tag": media_info["tag"],
            "message_id": None,
            "is_photo": False,
            "error_tmdb": error_tmdb,
            "is_backdrop": bool(backdrop_url)
        }
        try:
            await db.movie_updates.insert_one(movie_doc)
            await send_movie_update(bot, base_name)
        except DuplicateKeyError:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if movie_doc and not any(f["filename"] == filename for f in movie_doc["files"]):
                await db.movie_updates.update_one({"_id": base_name}, {"$push": {"files": file_data}})
                await send_movie_update(bot, base_name)
    else:
        if any(f["filename"] == filename for f in movie_doc["files"]):
            return
        await db.movie_updates.update_one({"_id": base_name}, {"$push": {"files": file_data}})
        await send_movie_update(bot, base_name)

# ============ SEND MOVIE UPDATE ============
async def send_movie_update(bot, base_name):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if not movie_doc:
                return None

            text = generate_movie_message(movie_doc, base_name)
            buttons = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    'ɢᴇᴛ ғɪʟᴇs',
                    url=f"https://t.me/{temp.U_NAME}?start=getfile-{base_name.replace(' ', '-')}"
                )
            ]])

            movie_data = movie_doc.get("movie_data", {})
            if not movie_data:
                movie_data = {
                    "title": base_name,
                    "backdrop_url": movie_doc.get("poster_url") if LANDSCAPE_POSTER else None,
                    "portrait_url": None,
                    "cast_names": [],
                    "tagline": "",
                    "year": movie_doc.get("year", "")
                }
            poster_bytes = await generate_landscape_poster(
                title=movie_data.get("title", base_name),
                backdrop_url=movie_data.get("backdrop_url"),
                portrait_url=movie_data.get("portrait_url"),
                cast_names=movie_data.get("cast_names"),
                tagline=movie_data.get("tagline"),
                year=movie_data.get("year")
            )

            if poster_bytes:
                msg = await bot.send_photo(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    photo=poster_bytes,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML
                )
                is_photo = True
            else:
                send_params = {
                    "chat_id": MOVIE_UPDATE_CHANNEL,
                    "text": text,
                    "reply_markup": buttons,
                    "parse_mode": enums.ParseMode.HTML
                }
                if movie_doc.get("poster_url") and LINK_PREVIEW:
                    send_params["invert_media"] = ABOVE_PREVIEW
                msg = await bot.send_message(**send_params)
                is_photo = False

            await db.movie_updates.update_one(
                {"_id": base_name},
                {"$set": {"message_id": msg.id, "is_photo": is_photo}}
            )
            return msg
        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
        except Exception as e:
            logger.error(f"Failed to send movie update: {e}")
            break
    return None

# ============ UPDATE MOVIE MESSAGE ============
async def update_movie_message(bot, base_name):
    try:
        movie_doc = await db.movie_updates.find_one({"_id": base_name})
        if not movie_doc:
            return

        text = generate_movie_message(movie_doc, base_name)
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                'ɢᴇᴛ ғɪʟᴇs',
                url=f"https://t.me/{temp.U_NAME}?start=getfile-{base_name.replace(' ', '-')}"
            )
        ]])

        message_id = movie_doc.get("message_id")
        is_photo = movie_doc.get("is_photo", False)

        if not message_id:
            await send_movie_update(bot, base_name)
            return

        try:
            if is_photo:
                await bot.edit_message_caption(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML
                )
            else:
                await bot.edit_message_text(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    text=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML,
                    invert_media=ABOVE_PREVIEW,
                    disable_web_page_preview=not LINK_PREVIEW
                )
            return
        except (MessageIdInvalid, MessageNotModified):
            pass
        except Exception:
            try:
                await bot.delete_messages(chat_id=MOVIE_UPDATE_CHANNEL, message_ids=message_id)
                await db.movie_updates.update_one({"_id": base_name}, {"$set": {"message_id": None, "is_photo": False}})
            except Exception:
                pass
            await send_movie_update(bot, base_name)
    except Exception as e:
        logger.error(f"Update error: {e}")

# ============ GENERATE MOVIE MESSAGE (UPDATED FORMAT) ============
def generate_movie_message(movie_doc, base_name):
    """
    Generates the movie update message in the exact format:
    🎬 CRAZY RICH ASIANS (2018)
    📌 (Touch To Copy)

    ⭐ IMDb: 7.1/10

    ➡ Audio Track:- 🔊 #English #Hindi #Tamil

    Added ✅
    """
    # Collect all languages from files
    all_languages = set()
    for file in movie_doc["files"]:
        if file.get("language") and file["language"] != "N/A":
            all_languages.update(lang.strip() for lang in file["language"].split(",") if lang.strip())
    if movie_doc.get("language") and movie_doc["language"] != "N/A":
        all_languages.update(lang.strip() for lang in movie_doc["language"].split(",") if lang.strip())

    # Build language tags (with #)
    language_tags = " ".join(f"#{lang}" for lang in sorted(all_languages)) if all_languages else "#Hindi"

    # Title and year
    title = base_name.upper()
    year_val = str(movie_doc.get("year") or "").strip()
    year_val = re.sub(r'[()\[\]]', '', year_val)
    if year_val and year_val not in title:
        title_with_year = f"{title} ({year_val})"
    else:
        title_with_year = title

    # Rating
    rating_raw = movie_doc.get("rating", "N/A")
    if rating_raw == "N/A" or rating_raw == "-":
        rating_str = "N/A"
    else:
        try:
            rating_float = float(rating_raw)
            rating_str = f"{rating_float:.1f}"
        except:
            rating_str = "N/A"

    # Build the final message
    text = f"""🎬 <code>{title_with_year}</code>
<i>📌 (Touch To Copy)</i>

⭐ IMDb: {rating_str}/10

➡ Audio Track:- 🔊 {language_tags}

Added ✅"""

    return text
