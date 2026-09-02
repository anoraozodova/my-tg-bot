import base64
import datetime
import hashlib
import os
import re
import sqlite3
import threading
import time
import traceback
from queue import Queue
from typing import Any, Callable
from urllib.parse import urlparse

import requests
import telebot
import yt_dlp
from flask import Flask
from cryptography.fernet import Fernet
from telebot import types
from telebot.util import quick_markup
from yt_dlp.utils import DownloadCancelled, DownloadError, ExtractorError

import config

whitelist = getattr(config, "whitelist", None)
blacklist = getattr(config, "blacklist", None)
logs = getattr(config, "logs", None)
js_runtime = getattr(config, "js_runtime", None)
max_filesize = getattr(config, "max_filesize", 50000000)
max_user_concurrent_downloads = getattr(config, "max_user_concurrent_downloads", 1)
max_global_concurrent_downloads = getattr(config, "max_global_concurrent_downloads", 2)
max_retries = getattr(config, "max_retries", 3)

# Set to True temporarily to receive the exact exception text for a failed
# download in your admin DM. Off by default so expected/known failures
# (e.g. TikTok being blocked in some regions) don't spam admins.
NOTIFY_ADMIN_ON_ERROR = False
retry_delay = getattr(config, "retry_delay", 5)
allowed_domains = getattr(config, "allowed_domains", [])
forward_to: int | None = getattr(config, "forward_to", None)
forward_permissions: list[int] = getattr(config, "forward_permissions", [])

if max_user_concurrent_downloads < 1:
    max_user_concurrent_downloads = 1
if max_global_concurrent_downloads < 1:
    max_global_concurrent_downloads = 1

os.makedirs(config.output_folder, exist_ok=True)

key = hashlib.sha256(config.secret_key.encode()).digest()
cipher = Fernet(base64.urlsafe_b64encode(key))

script_dir = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(script_dir, "db.db")
db_conn = sqlite3.connect(db_path, check_same_thread=False)
db_cursor = db_conn.cursor()
db_cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_cookies (
        user_id INTEGER PRIMARY KEY,
        cookie_data TEXT NOT NULL
    )
""")
db_conn.commit()

ses = requests.Session()
ses.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,uz;q=0.8,ru;q=0.7",
    }
)
bot = telebot.TeleBot(config.token)
last_edited = {}
download_queue: Queue[dict] = Queue()
queue_lock = threading.Lock()
active_global_downloads = 0
active_user_downloads: dict[int, int] = {}
queued_user_downloads: dict[int, int] = {}
_format_registry: dict[str, str] = {}
_format_counter = 0


def encrypt_cookie(cookie_data: str) -> str:
    """Encrypt cookie data using the secret key."""
    return cipher.encrypt(cookie_data.encode()).decode()


def decrypt_cookie(encrypted_data: str) -> str:
    """Decrypt cookie data using the secret key."""
    return cipher.decrypt(encrypted_data.encode()).decode()


def youtube_url_validation(url):
    youtube_regex = (
        r"(https?://)?(www\.|m\.)?"
        r"(youtube|youtu|youtube-nocookie)\.(com|be)/"
        r"(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})"
    )

    youtube_regex_match = re.match(youtube_regex, url)
    if youtube_regex_match:
        return youtube_regex_match

    return youtube_regex_match


def is_alibaba_url(url: str) -> bool:
    try:
        domain = urlparse(url).netloc.lower()
        if ":" in domain:
            domain = domain.split(":")[0]
        return domain == "alibaba.com" or domain.endswith(".alibaba.com")
    except (ValueError, AttributeError):
        return False


def is_allowed_domain(url):
    """
    Check if URL belongs to allowed domains: YouTube, TikTok, Instagram, Twitter/X, Bluesky
    """

    try:
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()

        # Remove port if present
        if ":" in domain:
            domain = domain.split(":")[0]

        return domain in allowed_domains
    except (ValueError, AttributeError):
        return False


@bot.message_handler(commands=["start", "help"])
def test(message):
    bot.reply_to(
        message,
        "ﾟ⋆☂︎⋆ Кидай ссылку стяну тебе видео с ютуб тикток инста твитер или блускай никто не узнает 🛸 ˚◞♡˖ᡣ𐭩⊹\n"
        "┆ ┆ ┆ ┆ ┆\n"
        "┆ ┆  ࣪ ˖☆ ࣪⭑┆ ݁˖ .☆ . ݁ ˖ \n"
        "☆⊹ ࣪ ┆ ˖ ࣪ ⊹ ࣪ ★ ⋆.˚  ⊹ ࣪\n"
        "   ࣪ ˖⋆˚★ ₊ ⊹   ࣪˖ ࣪ ₊  ࣪ ˖ \n"
        ". ݁ ⊹ ࣪ ˖    ࣪ ˖\n"
        "  .  ݁     ݁\n"
        "  .",
        disable_web_page_preview=True,
    )


def _validate_url(message, url: str) -> bool:
    """Validate URL domain and YouTube-specific rules. Returns False and replies if invalid."""
    if is_alibaba_url(url):
        return True

    if not is_allowed_domain(url):
        bot.reply_to(
            message,
            "( :: 🏷) Это что вообще такое Я работаю только с ютуб тикток инста твитер и блускай нот май стайл 𖦹°‧𓆝",
        )
        return False

    if urlparse(url).netloc in {
        "www.youtube.com",
        "youtube.com",
        "youtu.be",
        "m.youtube.com",
        "youtube-nocookie.com",
    }:
        if not youtube_url_validation(url):
            bot.reply_to(message, "₊˚✧ﾟ. Это не ссылка, а каракули какие-то 🎸 ᵕ˘͈✧ೃ")
            return False

    return True


def _make_progress_hook(message, msg) -> Callable:
    """Ничего не делаем, чтобы не перебивать комикс процентами"""
    def progress(d):
        pass

    return progress


class MissingInfoError(Exception):
    pass


def _send_media(
    message, info: Any, audio: bool, forward: bool = False, url: str | None = None
) -> None:
    """Send the downloaded file back to the user via Telegram."""

    downloads = info.get("requested_downloads") or None

    if not downloads:
        if info.get("entries") is not None and len(info.get("entries")) > 0:
            downloads = info.get("entries")[0].get("requested_downloads") or None

    if not downloads or len(downloads) == 0:
        raise MissingInfoError("No requested downloads found")

    filepath = downloads[0]["filepath"]

    with open(filepath, "rb") as f:
        channel_id = message.chat.id
        if forward:
            assert forward_to is not None, (
                "forward_to is required when forwarding videos"
            )
            channel_id = forward_to
        if audio:
            bot.send_audio(
                channel_id,
                f,
                reply_to_message_id=message.message_id,
                caption=f"݁ ˖Ი𐑼⋆ {url}",
            )
        else:
            bot.send_video(
                channel_id,
                f,
                width=downloads[0].get("width") or None,
                height=downloads[0].get("height") or None,
                caption=f"݁ ˖Ი𐑼⋆ {url}",
            )


def _cleanup_prefix(prefix: int | str) -> None:
    """Remove all files in the output folder that belong to this download."""
    prefix_str = str(prefix)
    for file in os.listdir(config.output_folder):
        if file.startswith(prefix_str):
            os.remove(os.path.join(config.output_folder, file))


def _cleanup(video_title: int) -> None:
    _cleanup_prefix(video_title)


def _sanitize_url(url: str) -> str:
    return url.strip().rstrip(".,);]>\"'")


def _normalize_media_url(url: str) -> str:
    url = url.replace("\\u002F", "/").replace("\\/", "/").strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def _upgrade_alicdn_image(url: str) -> str:
    return re.sub(
        r"\.(jpe?g|png|webp)_\d+x\d+\.(jpe?g|png|webp)$", r".\1", url, flags=re.I
    )


def _pick_best_alibaba_video(videos: list[str]) -> list[str]:
    if not videos:
        return []

    def score(video_url: str) -> int:
        value = video_url.lower()
        if "h264-hd" in value or "h265-hd" in value:
            return 30
        if "-sd" in value:
            return 20
        if "-ld" in value:
            return 10
        return 0

    return [max(videos, key=score)]


def _image_dedupe_key(url: str) -> str:
    return re.sub(r"_\d+x\d+", "", url.split("?")[0])


def _extract_alibaba_media(html: str) -> tuple[list[str], list[str]]:
    images: list[str] = []
    videos: list[str] = []
    image_keys: set[str] = set()
    video_keys: set[str] = set()

    def add_image(raw_url: str) -> None:
        url = _upgrade_alicdn_image(_normalize_media_url(raw_url))
        if "/flags/" in url or "/mobile/g/common/" in url:
            return
        if "imgextra" in url and "tps-" in url:
            return
        if "alicdn.com" not in url and "alibaba.com" not in url:
            return
        if not re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", url, re.I):
            return
        if re.search(r"_\d+x\d+\.", url):
            return
        key = _image_dedupe_key(url)
        if key in image_keys:
            return
        image_keys.add(key)
        images.append(url)

    def add_video(raw_url: str) -> None:
        url = _normalize_media_url(raw_url)
        if ".mp4" not in url.lower():
            return
        key = url.split("?")[0]
        if key in video_keys:
            return
        video_keys.add(key)
        videos.append(url)

    for match in re.finditer(
        r"(//sc\d+\.alicdn\.com/kf/[^\"'\\\s<>]+?\.(?:jpg|jpeg|png|webp))",
        html,
        re.I,
    ):
        raw = match.group(1)
        if re.search(r"_\d+x\d+\.", raw):
            continue
        add_image(raw)

    image_patterns = [
        r'"originalImageUrl"\s*:\s*"(.*?)"',
        r'"imageUrl"\s*:\s*"(.*?)"',
        r'"summImageUrl"\s*:\s*"(.*?)"',
        r'"(https?://[^"]*alicdn\.com/kf/[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"',
        r'"(//sc[^"]*alicdn\.com/kf/[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"',
    ]
    video_patterns = [
        r'"(?:videoUrl|video_url|mp4Url)"\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
        r'"(https?://[^"]*videocdn\.alibaba\.com[^"]*\.mp4[^"]*)"',
        r'"(https?://[^"]*alicdn\.com[^"]*\.mp4[^"]*)"',
        r'"(//[^"]*alicdn\.com[^"]*\.mp4[^"]*)"',
    ]

    for pattern in image_patterns:
        for match in re.finditer(pattern, html, re.I):
            add_image(match.group(1))

    for pattern in video_patterns:
        for match in re.finditer(pattern, html, re.I):
            add_video(match.group(1))

    return images, _pick_best_alibaba_video(videos)


def _alibaba_mobile_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    if host.startswith("m."):
        return None
    if host in ("alibaba.com", "www.alibaba.com"):
        return parsed._replace(netloc="m.alibaba.com").geturl()
    if host.endswith(".alibaba.com"):
        return parsed._replace(netloc=f"m.{host}").geturl()
    return None


def _fetch_alibaba_html(url: str) -> str:
    response = ses.get(url, timeout=60, allow_redirects=True)
    response.raise_for_status()
    html = response.text
    images, videos = _extract_alibaba_media(html)
    if images or videos:
        return html

    mobile_url = _alibaba_mobile_url(response.url)
    if not mobile_url:
        return html

    mobile_response = ses.get(mobile_url, timeout=60, allow_redirects=True)
    mobile_response.raise_for_status()
    return mobile_response.text


def _download_remote_file(url: str, dest: str) -> bool:
    try:
        response = ses.get(url, timeout=60, stream=True)
        response.raise_for_status()
        size = 0
        with open(dest, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_filesize:
                    return False
                f.write(chunk)
        return size > 0
    except requests.RequestException:
        return False


def _perform_alibaba_download(message, url: str) -> None:
    msg = bot.reply_to(message, "📦 Смотрю товар на Alibaba...")
    download_prefix = round(time.time() * 1000)
    saved_files: list[str] = []

    try:
        html = _fetch_alibaba_html(url)
        images, videos = _extract_alibaba_media(html)
        if not images and not videos:
            bot.edit_message_text(
                "𓏲⋆ На странице не нашла фото и видео товара, попробуй другую ссылку 📅",
                message.chat.id,
                msg.message_id,
            )
            return

        chat_id = message.chat.id
        sent_any = False

        for index, video_url in enumerate(videos):
            path = os.path.join(
                config.output_folder, f"{download_prefix}_v{index}.mp4"
            )
            if not _download_remote_file(video_url, path):
                continue
            saved_files.append(path)
            with open(path, "rb") as video_file:
                bot.send_video(
                    chat_id,
                    video_file,
                    reply_to_message_id=message.message_id,
                    caption=f"݁ ˖Ი𐑼⋆ {url}",
                )
            sent_any = True

        image_paths: list[str] = []
        for index, image_url in enumerate(images[:40]):
            ext_match = re.search(r"\.(jpg|jpeg|png|webp)", image_url, re.I)
            ext = f".{ext_match.group(1).lower()}" if ext_match else ".jpg"
            path = os.path.join(
                config.output_folder, f"{download_prefix}_i{index}{ext}"
            )
            if not _download_remote_file(image_url, path):
                continue
            saved_files.append(path)
            image_paths.append(path)

        if image_paths:
            for start in range(0, len(image_paths), 10):
                chunk = image_paths[start : start + 10]
                media_group: list[types.InputMediaPhoto] = []
                opened_files = []
                try:
                    for image_path in chunk:
                        image_file = open(image_path, "rb")
                        opened_files.append(image_file)
                        media_group.append(types.InputMediaPhoto(image_file))
                    bot.send_media_group(
                        chat_id,
                        media_group,
                        reply_to_message_id=message.message_id if start == 0 else None,
                    )
                finally:
                    for image_file in opened_files:
                        image_file.close()
            sent_any = True

        if not sent_any:
            bot.edit_message_text(
                "𓏲⋆ Нашла ссылки, но скачать не вышло — попробуй позже 📅",
                message.chat.id,
                msg.message_id,
            )
            return

        bot.delete_message(message.chat.id, msg.message_id)
    except requests.RequestException as e:
        print(f"Alibaba fetch error for {url}: {e}")
        bot.edit_message_text(
            "༉‧✰ Не смогла открыть страницу Alibaba, попробуй позже ◟♪◝⊹",
            message.chat.id,
            msg.message_id,
        )
    except Exception as e:
        print(f"Alibaba download error for {url}: {e}")
        bot.edit_message_text(
            "༉‧✰ что то пошло не так но все нипочём, когда в тебе не воспитали чувство гордости ◟♪◝⊹",
            message.chat.id,
            msg.message_id,
        )
    finally:
        _cleanup_prefix(download_prefix)


def _is_transient_error(e: Exception) -> bool:
    """Check if a yt-dlp error is transient (rate limiting, network issue) and worth retrying."""
    if isinstance(e, DownloadCancelled):
        return False

    err = str(e).lower()

    if any(
        phrase in err
        for phrase in ["rate-limit", "rate limit", "too many requests", "429"]
    ):
        return True

    if "[youtube]" in err and "sign in" in err:
        return True

    if "login required" in err:
        return True

    if "http error 5" in err:
        return True

    if any(
        phrase in err
        for phrase in [
            "timeout",
            "connection reset",
            "connection refused",
            "connection closed",
            "eof",
            "name resolution",
        ]
    ):
        return True

    return False


def extract_urls(content: str) -> list[str]:
    """Find every http(s) URL inside a message, in the order they appear."""
    return [_sanitize_url(url) for url in re.findall(r"https?://\S+", content or "")]


def check_url(content: str, message) -> dict:
    match = re.search(r"https?://\S+", content)
    url = _sanitize_url(match.group(0) if match else content)

    if not urlparse(url).scheme:
        bot.reply_to(message, "₊˚✧ﾟ. Это не ссылка, а каракули какие-то 🎸 ᵕ˘͈✧ೃ")
        return {"success": False}

    if not _validate_url(message, url):
        return {"success": False}

    return {"success": True, "url": url}


def enqueue_download(
    message,
    content,
    audio: bool = False,
    format_id: str = "mp4",
    forward: bool = False,
) -> None:
    forbidden = False
    if whitelist is not None and message.from_user.id not in whitelist:
        forbidden = True

    if blacklist is not None and message.from_user.id in blacklist:
        forbidden = True

    if forbidden:
        bot.reply_to(message, "˚𜗗˚⋆ Настроено, но не в настроении .𖥔˖‧⛆°⋆")
        return

    if not content:
        bot.reply_to(message, "₊˚✧ﾟ. Это не ссылка, а каракули какие-то 🎸 ᵕ˘͈✧ೃ")
        return

    check = check_url(content, message)
    if not check["success"]:
        return

    url = check["url"]
    user_id = message.from_user.id

    with queue_lock:
        pending = active_user_downloads.get(user_id, 0) + queued_user_downloads.get(
            user_id, 0
        )
        if pending >= max_user_concurrent_downloads:
            bot.reply_to(
                message,
                f"˚◞♡ — Сегодня…— Вальпургиева ночь, — улыбается бот. — Все ведьмы на шабаш слетаются максимум 5 веников 🛸",
            )
            return

        queued_user_downloads[user_id] = queued_user_downloads.get(user_id, 0) + 1
        should_notify_queue = (
            active_global_downloads >= max_global_concurrent_downloads
            or download_queue.qsize() > 0
        )

        download_queue.put(
            {
                "message": message,
                "url": url,
                "audio": audio,
                "format_id": format_id,
                "forward": forward,
                "user_id": user_id,
                "alibaba": is_alibaba_url(url),
            }
        )
        position = download_queue.qsize()

    if should_notify_queue:
        bot.reply_to(
            message,
            f"·˚ˎˊ˗ Все каналы заняты, твоя ссылка в очереди под номером {position}, жди 📅",
        )


_START_MESSAGE = (
    "   /)/)\n"
    " (  . .) \"eatz\"\n"
    " /づ🍥\n\n"
    "   /)/)       (\\\(\\\n"
    " (  • •)?   (• •  ) can i pwease eat that too\n"
    " /づ🍥      vv \\\n\n"
    "  (\\\(\\\         (\\\(\\\n"
    " (  • •)No.(• •  )\n"
    " 🍥⊂\\\       vv \\\n\n"
    "  (\\\(\\\  (\\\(\\\n"
    " (  • •)(• •  ) give me that!\n"
    " 🍥⊂\\\   ⊂ \\\n\n"
    "   /)/)            (\\\(\\\n"
    " ( 0 0) Noo! (. .  )\n"
    " /  づ            🍥⊂\\\n"
    " \"sad\"\n"
    "  /)/)            (\\\(\\\n"
    "(  . .)''          (• •  )\n"
    "/ vv              🍥⊂\\\n\n"
    "  /)/)      (\\\(\\\n"
    "(  • •)?   (. .  ) Ok fine, u can have it.\n"
    "/ vv      🍥⊂\\\n\n"
    "  /)/)         (\\\(\\\n"
    "(  ᵔ ᵔ)thx! (. .  )''\n"
    "/ づ🍥       vv \\\n\n"
    " btw we can share if u want!\n"
    "  /)/)         (\\\(\\\n"
    "(  ᵔ ᵔ)        (• • ) huh..?\n"
    "/ づ🍥       vv \\\n\n"
    "  /)/) (\\(\\\n"
    "( ᵔ ᵔ) (ᵔ ᵔ ) okay!\n"
    "/ づ🍥⊂ \\"
)


def _reply_start_message(message):
    """Reply with the 'starting download' message.

    Sending several links back-to-back (e.g. forwarding a batch of
    Instagram reels) can trigger Telegram flood control on this call.
    That used to raise uncaught out of `_perform_download`, which killed
    the worker thread that ran it permanently — so the queue kept growing
    and nothing downloaded ever again. Retry a couple of times with a
    short backoff, then fall back to a plain short message instead of
    letting a transient Telegram error take down the whole worker.
    """
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            return bot.reply_to(message, _START_MESSAGE)
        except Exception as e:
            last_err = e
            print(f"reply_to retry {attempt + 1}/3 failed: {e}")
            time.sleep(2 * (attempt + 1))

    try:
        return bot.reply_to(message, "⏳ Качаю...")
    except Exception:
        if last_err:
            raise last_err
        raise


def _perform_download(
    message,
    url: str,
    audio: bool = False,
    format_id: str = "mp4",
    forward: bool = False,
) -> None:
    msg = _reply_start_message(message)
    video_title = round(time.time() * 1000)

ydl_opts: yt_dlp._Params = {
    "format": format_id,
    "outtmpl": f"{config.output_folder}/{video_title}.%(ext)s",
    "progress_hooks": [
        _make_progress_hook(message, msg)
    ],
    "max_filesize": max_filesize,
    "postprocessors": (
        [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
            }
        ]
        if audio
        else []
    ),
    "socket_timeout": 30,
    "retries": 10,
    "fragment_retries": 10,
    "extractor_retries": 5,
}
    if js_runtime is not None:
        ydl_opts["js_runtimes"] = js_runtime
        ydl_opts["remote_components"] = {"ejs:github"}

    cookie_file = None
    try:
        user_id = message.from_user.id
        db_cursor.execute(
            "SELECT cookie_data FROM user_cookies WHERE user_id = ?", (user_id,)
        )
        result = db_cursor.fetchone()

        if result:
            decrypted_data = decrypt_cookie(result[0])
            cookie_file = f"{config.output_folder}/cookies_{user_id}.txt"
            with open(cookie_file, "w") as f:
                f.write(decrypted_data)
            ydl_opts["cookiefile"] = cookie_file

        for attempt in range(max_retries + 1):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                break
            except (DownloadError, ExtractorError, DownloadCancelled) as e:
                if _is_transient_error(e) and attempt < max_retries:
                    _cleanup(video_title)
                    print(f"Retry {attempt + 1}/{max_retries} for {url}: {e}")
                    time.sleep(retry_delay)
                    continue
                raise

        for send_attempt in range(max_retries + 1):
            try:
                _send_media(message, info, audio, forward, url)
                break
            except Exception as e:
                if send_attempt < max_retries:
                    print(
                        f"Send retry {send_attempt + 1}/{max_retries} for {url}: {e}"
                    )
                    time.sleep(retry_delay)
                    continue
                raise

        if forward:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg.message_id,
                text="𑁍ܓ Готово, переслала куда просили ₊˚👑‧₊˚⋅",
            )
        else:
            bot.delete_message(message.chat.id, msg.message_id)
    except MissingInfoError:
        bot.edit_message_text(
            "໒꒱·ﾟ Не вышло скачать, это видео мне не по зубам 🎸 ⸝⸝𖠚ᐝ",
            message.chat.id,
            msg.message_id,
        )
    except (DownloadError, ExtractorError) as e:
        err = str(e).lower()
        text: str
        is_instagram = "instagram.com" in url.lower()

        print(f"Download error for {url}: {type(e).__name__}: {e}")
        notify_admin_error(url, e)

        auth_phrases = [
            "login required",
            "rate-limit reached",
            "restricted video",
            "not available",
            "unable to extract shared_data",
            "unable to fetch",
            "no video formats found",
            "requested content is not available",
            "empty media response",
        ]

        if "[youtube]" in err and "sign in" in err:
            text = "˚𖡼𖤣 YouTube сегодня в плохом настроении и всех банит, попробуй позже 彡⋆⭒"
        elif is_instagram and any(phrase in err for phrase in auth_phrases):
            text = (
                "🪐 Инста просит логин или забанила по IP. Пришли мне куки инстаграма "
                "командой /cookies (файл cookies.txt), тогда должно заработать 🍥"
            )
        elif "login required" in err or "rate-limit reached" in err:
            text = "🪐 Настроено, но не в настроении. то ли нужен логин, то ли я словила бан. ⁺˚✧ﾟ."
        else:
            text = "༉‧✰ что то пошло не так но все нипочём, когда в тебе не воспитали чувство гордости ◟♪◝⊹"

        bot.edit_message_text(text, message.chat.id, msg.message_id)
    except DownloadCancelled:
        bot.edit_message_text(
            "˚༶𓅯 Стоп, это видео толще 50МБ — отменяю, не дотащу 🧃",
            message.chat.id,
            msg.message_id,
        )
    except Exception as e:
        print(f"Unexpected error for {url}: {type(e).__name__}: {e}")
        traceback.print_exc()
        notify_admin_error(url, e)
        bot.edit_message_text(
            "༉‧✰ что то пошло не так но все нипочём, когда в тебе не воспитали чувство гордости ◟♪◝⊹",
            message.chat.id,
            msg.message_id,
        )

    finally:
        if cookie_file and os.path.exists(cookie_file):
            os.remove(cookie_file)
        _cleanup(video_title)


def _download_worker() -> None:
    global active_global_downloads

    while True:
        task = download_queue.get()
        user_id = task["user_id"]

        with queue_lock:
            active_global_downloads += 1
            active_user_downloads[user_id] = active_user_downloads.get(user_id, 0) + 1
            queued_user_downloads[user_id] = max(
                0, queued_user_downloads.get(user_id, 0) - 1
            )
            if queued_user_downloads[user_id] == 0:
                del queued_user_downloads[user_id]

        try:
            if task.get("alibaba"):
                _perform_alibaba_download(task["message"], task["url"])
            else:
                _perform_download(
                    task["message"],
                    task["url"],
                    task["audio"],
                    task["format_id"],
                    task["forward"],
                )
        except Exception as e:
            # A worker thread must never die: an uncaught exception here
            # (e.g. a Telegram flood-control error while replying) used to
            # kill this thread permanently, so the queue kept growing and
            # nothing ever downloaded again.
            print(f"Worker error for {task['url']}: {type(e).__name__}: {e}")
            traceback.print_exc()
            notify_admin_error(task["url"], e)
        finally:
            with queue_lock:
                active_global_downloads -= 1
                active_user_downloads[user_id] = max(
                    0, active_user_downloads.get(user_id, 0) - 1
                )
                if active_user_downloads[user_id] == 0:
                    del active_user_downloads[user_id]
            download_queue.task_done()


def notify_admin_error(url: str, e: Exception) -> None:
    """Send the real exception text to admins so failures can actually be diagnosed.

    Disabled by default to avoid spamming admins on every known/expected
    failure (e.g. TikTok being blocked in some regions). Flip
    NOTIFY_ADMIN_ON_ERROR to True temporarily when you need to see the
    real error text for a new/unknown problem.
    """
    if not NOTIFY_ADMIN_ON_ERROR:
        return
    admin_ids = getattr(config, "admin_ids", None) or []
    if not admin_ids:
        return
    err_text = f"{type(e).__name__}: {e}"
    text = f"⚠️ Download failed\nURL: {url}\n\n{err_text[:1500]}"
    for admin_id in admin_ids:
        try:
            bot.send_message(admin_id, text)
        except Exception as notify_err:
            print(f"Failed to notify admin {admin_id}: {notify_err}")


def log(message, text: str, media: str):
    if logs:
        if message.chat.type == "private":
            chat_info = "Private chat"
        else:
            chat_info = f"Group: *{message.chat.title}* (`{message.chat.id}`)"

        bot.send_message(
            logs,
            f"Download request ({media}) from @{message.from_user.username} ({message.from_user.id})\n\n{chat_info}\n\n{text}",
        )


def get_text(message):
    if len(message.text.split(" ")) < 2:
        if message.reply_to_message and message.reply_to_message.text:
            return message.reply_to_message.text
        else:
            return None
    else:
        return message.text.split(" ")[1]


@bot.message_handler(commands=["download"])
def download_command(message):
    text = get_text(message)
    if not text:
        bot.reply_to(
            message, "ᵕ‌✦⁝ Так не работает, формат такой: /download ссылка ⋆⸜✮₊˚"
        )
        return

    log(message, text, "video")
    enqueue_download(message, text)


@bot.message_handler(commands=["audio"])
def download_audio_command(message):
    text = get_text(message)
    if not text:
        bot.reply_to(message, "ᵕ‌✦⁝ Так не работает, формат такой: /audio ссылка ⋆⸜✮₊˚")
        return

    log(message, text, "audio")
    enqueue_download(message, text, True)


@bot.message_handler(commands=["forward"])
def forward_command(message):
    if message.from_user.id not in forward_permissions:
        bot.reply_to(message, "🧸 Пересылать тебе нельзя, извини ₊˚🛸⊹.ᐟ")
        return

    if not forward_to:
        bot.reply_to(message, "🫧 Пересылка вообще не настроена, мимо 𖦹°‧")
        return

    text = get_text(message)
    if not text:
        bot.reply_to(
            message, "ᵕ‌✦⁝ Так не работает, формат такой: /forward ссылка ⋆⸜✮₊˚"
        )
        return

    log(message, text, "video")
    enqueue_download(message, text, forward=True)


@bot.message_handler(commands=["custom"])
def custom(message):
    forbidden = False
    if whitelist is not None and message.from_user.id not in whitelist:
        forbidden = True
    if blacklist is not None and message.from_user.id in blacklist:
        forbidden = True
    if forbidden:
        bot.reply_to(message, "˚𜗗˚⋆ Настроено, но не в настроении .𖥔˖‧⛆°⋆")
        return

    text = message.text if message.text else message.caption

    check = check_url(text, message)
    if not check["success"]:
        return

    url = check["url"]

    msg = bot.reply_to(message, "💭 Гляжу, что там есть... 🧃")

    try:
        with yt_dlp.YoutubeDL() as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        bot.edit_message_text(
            "𓆉˚ Не смогла достать форматы, что-то заглючило. Позже попробуй ₊𓍊₊˚",
            message.chat.id,
            msg.message_id,
        )
        return

    formats = info.get("formats") or []

    global _format_registry, _format_counter
    data = {}
    for x in formats:
        if x.get("video_ext") == "none":
            continue
        resolution = x.get("resolution") or "unknown"
        ext = x.get("ext") or "unknown"
        label = f"{resolution}.{ext}"
        if label in data:
            label = f"{resolution}.{ext} ({x.get('format_id')})"
        fid = str(_format_counter)
        _format_counter += 1
        _format_registry[fid] = x["format_id"]
        data[label] = {"callback_data": fid}

    markup = quick_markup(data, row_width=2)

    bot.delete_message(msg.chat.id, msg.message_id)
    bot.reply_to(message, "⊹ ࣪ ˖ Философия нигилизма: пугает не сам выбор, а факт того, что ты выбираешь поэтому Выбирай, что по душе ౨ৎ ⋆｡˚", reply_markup=markup)


def filter_cookies_by_domain(cookie_data: str) -> str:
    lines = cookie_data.split("\n")
    filtered_lines = []

    for line in lines:
        if line.startswith("#") or not line.strip():
            filtered_lines.append(line)
            continue

        parts = line.split("\t")
        if len(parts) < 7:
            continue

        domain = parts[0].lstrip(".")

        is_allowed = False
        for allowed_domain in allowed_domains:
            if domain == allowed_domain or domain.endswith("." + allowed_domain):
                is_allowed = True
                break

        if is_allowed:
            filtered_lines.append(line)

    return "\n".join(filtered_lines)


@bot.message_handler(commands=["id"])
def get_chat_id(message):
    bot.reply_to(message, message.chat.id)


def is_cookie_command(message):
    text = message.text or message.caption or ""
    return text.startswith("/cookie") or text.startswith("/cookies")


@bot.message_handler(func=is_cookie_command, content_types=["document", "text"])
def handle_cookie(message):
    user_id = message.from_user.id

    if not message.document:
        db_cursor.execute(
            "SELECT cookie_data FROM user_cookies WHERE user_id = ?", (user_id,)
        )
        result = db_cursor.fetchone()

        if result:
            cookie_file = f"{config.output_folder}/cookies_{user_id}_temp.txt"
            try:
                decrypted_data = decrypt_cookie(result[0])
                with open(cookie_file, "w") as f:
                    f.write(decrypted_data)

                markup = types.InlineKeyboardMarkup()
                delete_btn = types.InlineKeyboardButton(
                    "💾 Стереть", callback_data="delete_cookies"
                )
                markup.add(delete_btn)

                with open(cookie_file, "rb") as f:
                    bot.send_document(
                        message.chat.id,
                        f,
                        reply_to_message_id=message.message_id,
                        visible_file_name="cookies.txt",
                        reply_markup=markup,
                    )
            finally:
                if os.path.exists(cookie_file):
                    os.remove(cookie_file)
        else:
            bot.reply_to(
                message,
                "𓏲⋆ Куки пустые. Кинь файл вместе с этой командой, я сохраню 📅",
            )
        return

    file_info = bot.get_file(message.document.file_id)
    if not file_info.file_path:
        bot.reply_to(message, "‧°𖦹 Файл не поймала, что-то не то ✰∙.")
        return

    downloaded_file = bot.download_file(file_info.file_path)
    cookie_data = downloaded_file.decode("utf-8")

    filtered_cookie_data = filter_cookies_by_domain(cookie_data)

    encrypted_data = encrypt_cookie(filtered_cookie_data)

    db_cursor.execute(
        "INSERT OR REPLACE INTO user_cookies (user_id, cookie_data) VALUES (?, ?)",
        (user_id, encrypted_data),
    )
    db_conn.commit()
    bot.reply_to(message, "📻 Куки сохранила, теперь всё помню  ˚◞♡˖ᡣ𐭩⊹")


@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    if call.data == "delete_cookies":
        user_id = call.from_user.id
        db_cursor.execute("DELETE FROM user_cookies WHERE user_id = ?", (user_id,))
        db_conn.commit()

        bot.edit_message_caption(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            caption="🪞 Удалила! ₊˚✧ﾟ.",
            reply_markup=None,
        )
        bot.answer_callback_query(call.id, "🪞 Удалила! ₊˚✧ﾟ.")
    elif call.message.reply_to_message:
        if call.from_user.id == call.message.reply_to_message.from_user.id:
            url = get_text(call.message.reply_to_message)
            format_id = _format_registry.get(call.data)
            if not format_id:
                bot.answer_callback_query(call.id, "🌦 «Это что-то вроде тумана утром. Когда вы просыпаетесь задолго до рассвета. Он исчезает быстро. Так и чувства сгорают и формат который ты скинула» 𓇢𓆸")
                return
            bot.delete_message(call.message.chat.id, call.message.message_id)
            enqueue_download(
                call.message.reply_to_message, url, format_id=f"{format_id}+bestaudio"
            )
        else:
            bot.answer_callback_query(call.id, "— Ладно, Блестящий Плащ. Завтра — мой маршрут. И если я от страха заору «ламинирование», ты меня выносишь на руках, понял? Как принцессу. — Если скажешь « это не твоя кнопка», вынесу два раза, И даже не уроню  🛸 ⊹ ࣪ ˖")


@bot.message_handler(
    func=lambda m: True,
    content_types=[
        "text",
        "photo",
        "audio",
        "video",
        "document",
    ],
)
def handle_private_messages(message: types.Message):
    text = (
        message.text if message.text else message.caption if message.caption else None
    )

    if message.chat.type == "private":
        assert message.from_user is not None, "Error: message.from_user is None"

        should_forward = (
            forward_to is not None and message.from_user.id in forward_permissions
        )

        urls = extract_urls(text)
        if len(urls) > 1:
            bot.reply_to(message, f"⁹𓍢 Ого, {len(urls)} ссылок разом! Ставлю все в queue, по одной разберусь 🎸")
            for url in urls:
                log(message, url, "video")
                enqueue_download(message, url, forward=should_forward)
            return

        log(message, text or "<no text>", "video")
        enqueue_download(message, text, forward=should_forward)
        return


def _start_download_workers() -> None:
    for i in range(max_global_concurrent_downloads):
        worker = threading.Thread(
            target=_download_worker,
            name=f"download-worker-{i + 1}",
            daemon=True,
        )
        worker.start()


def _start_health_server() -> None:
    """Minimal HTTP server for Render (and similar platforms that require an open port)."""
    health_port = int(os.environ.get("PORT", getattr(config, "health_port", 10000)))
    app = Flask(__name__)

    @app.route("/")
    def health():
        return "OK", 200

    @app.route("/health")
    def health_check():
        return "OK", 200

    def run_server() -> None:
        app.run(host="0.0.0.0", port=health_port, debug=False, use_reloader=False)

    thread = threading.Thread(target=run_server, name="health-server", daemon=True)
    thread.start()
    print(f"health server listening on 0.0.0.0:{health_port}")


_start_download_workers()
_start_health_server()
print(f"ready as @{bot.user.username}")
bot.infinity_polling()