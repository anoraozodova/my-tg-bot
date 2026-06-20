import os

# Робот на Render сам подставит сюда твой реальный токен из панели управления
token: str = os.environ.get("BOT_TOKEN", "")

# Берем ID из настроек хостинга. Если там пусто — по умолчанию твой ID
admin_env = os.environ.get("ADMIN_ID")
whitelist: list[int] | None = [int(admin_env)] if admin_env else None

blacklist: list[int] | None = None
logs: int | None = None
max_filesize: int = 50000000
max_user_concurrent_downloads: int = 5
max_global_concurrent_downloads: int = 3
max_retries: int = 3
retry_delay: int = 5
output_folder: str = "/tmp/satoru"

allowed_domains: list[str] = [
    "youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com", "youtube-nocookie.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "instagram.com", "www.instagram.com", "twitter.com", "www.twitter.com",
    "x.com", "www.x.com", "bsky.app", "www.bsky.app",
]

secret_key: str = os.environ.get("SECRET_KEY", "_sWTddP1snTNo65Hu0VcRqCpUWsIxaFmH7mB319RGq8")
js_runtime: dict[str, dict[str, str] | None] | None = None
forward_to: int | None = Noneset)
forward_permissions: list[int] = []
