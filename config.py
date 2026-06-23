import os
from pathlib import Path

# Local secrets live in .env (gitignored). On Render set Environment variables.
_env_file = Path(__file__).with_name(".env")
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

token: str = os.environ.get("BOT_TOKEN", "")
if not token:
    raise RuntimeError(
        "BOT_TOKEN is not set. Add it to Render Environment or create a local .env file."
    )


def _parse_id_list(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(item.strip()) for item in value.split(",") if item.strip()]


admin_ids = _parse_id_list(os.environ.get("ADMIN_ID"))
whitelist_ids = _parse_id_list(os.environ.get("WHITELIST_IDS"))

if whitelist_ids:
    whitelist: list[int] | None = list(dict.fromkeys(whitelist_ids))
elif admin_ids:
    whitelist = admin_ids.copy()
else:
    whitelist = None

forward_permissions: list[int] = admin_ids

papa_env = os.environ.get("PAPA_ID")
papa_id: int | None = int(papa_env) if papa_env else None
if papa_id:
    whitelist = list(dict.fromkeys((whitelist or []) + [papa_id]))

blacklist: list[int] | None = None
logs: int | None = None
max_filesize: int = 50000000
max_user_concurrent_downloads: int = 5
max_global_concurrent_downloads: int = 3
max_retries: int = 3
retry_delay: int = 5
output_folder: str = "/tmp/satoru"
health_port: int = 10000

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

secret_key: str = os.environ.get("SECRET_KEY", "")
if not secret_key:
    raise RuntimeError(
        "SECRET_KEY is not set. Add it to Render Environment or create a local .env file."
    )

js_runtime: dict[str, dict[str, str] | None] | None = None
forward_to: int | None = None
