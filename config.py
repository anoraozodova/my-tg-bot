# The telegram bot token
token: str = "8629550812:AAERFOyYDFYq9Vw-RKzYrqEj-x9IpE5V-M4"

# A list of user ids that are allowed to use the bot, if None everyone is allowed
# твой ID уже здесь. Когда узнаешь ID подруги через @userinfobot — добавь его сюда:
# [7253874622, ID_подруги]
whitelist: list[int] | None = [7253874622]

# A list of user ids that are not allowed to use the bot, if None everyone is allowed
blacklist: list[int] | None = None

# The logs channel id, if none set to None
logs: int | None = None

# The maximum file size in bytes
max_filesize: int = 50000000

# Maximum number of concurrent downloads allowed per user
# поднято до 5, чтобы можно было кинуть пачку ссылок и не упереться в отказ
max_user_concurrent_downloads: int = 5

# Maximum number of concurrent downloads allowed globally (shared across all users)
# 3 видео реально качаются параллельно, остальные ждут своей очереди в FIFO
max_global_concurrent_downloads: int = 3

# How many times to retry a download on transient errors (rate limiting, network timeouts)
max_retries: int = 3

# Seconds to wait between retry attempts
retry_delay: int = 5

# The output folder for downloaded files, it gets cleared after each download
output_folder: str = "/tmp/satoru"

# The allowed domains for downloading videos
allowed_domains: list[str] = [
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "m.youtube.com",
    "youtube-nocookie.com",
    "tiktok.com",
    "www.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "instagram.com",
    "www.instagram.com",
    "twitter.com",
    "www.twitter.com",
    "x.com",
    "www.x.com",
    "bsky.app",
    "www.bsky.app",
]

# secret key used to encrypt/decrypt stores cookies
# сгенерирован случайно, не плейсхолдер из примера — не теряй этот файл
secret_key: str = "_sWTddP1snTNo65Hu0VcRqCpUWsIxaFmH7mB319RGq8"

# this is used to solve youtube challenges, you can set it to None if you don't
# need it or change the runtime like {"node": {"path": "node"}}
js_runtime: dict[str, dict[str, str] | None] | None = None

# Channel to forward videos to when using the /forward command or when
# authorized users send links in private chat. Set to None to disable.
forward_to: int | None = None

# User IDs allowed to forward videos (used when forward_to is set)
forward_permissions: list[int] = []