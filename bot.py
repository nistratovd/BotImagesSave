"""Telegram media archive. No listening sockets; Files stay in private local user folders."""
import asyncio
import logging
import os
import shutil
import uuid
from urllib.parse import urlsplit
from pathlib import Path
import tempfile
import tomllib

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

LOG = logging.getLogger("media_archive")
DEFAULT_STORAGE = "/var/lib/botimagessave/media"


def load_config(path):
    with open(path, "rb") as source:
        cfg = tomllib.load(source)
    if not cfg["telegram"]["token"] or cfg["telegram"]["token"].startswith("PUT_"):
        raise ValueError("Configure telegram.token")
    limit = cfg.get("storage", {}).get("max_file_mb", 20)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("max_file_mb must be a positive integer")
    local_url = cfg["telegram"].get("local_api_url", "").rstrip("/")
    if local_url:
        url = urlsplit(local_url)
        if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1")
                or url.username or url.password or url.path or url.query or url.fragment):
            raise ValueError("local_api_url must be an HTTP loopback address without a path")
        root = Path(cfg["telegram"].get("local_file_root", "/var/lib/telegram-bot-api"))
        if not root.is_absolute():
            raise ValueError("local_file_root must be absolute")
        cfg["telegram"]["local_file_root"] = str(root)
    cfg["telegram"]["local_api_url"] = local_url
    storage = cfg.setdefault("storage", {})
    directory = Path(storage.get("directory", DEFAULT_STORAGE))
    if not directory.is_absolute():
        raise ValueError("storage.directory must be absolute")
    reserve = storage.get("min_free_mb", 1024)
    if not isinstance(reserve, int) or isinstance(reserve, bool) or reserve < 0:
        raise ValueError("storage.min_free_mb must be a nonnegative integer")
    storage["directory"] = str(directory)
    storage["min_free_mb"] = reserve
    cfg["limit"] = limit * 1024 * 1024
    return cfg


def media_type(path):
    with open(path, "rb") as source:
        h = source.read(32)
    if h.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if len(h) >= 12 and h[4:8] == b"ftyp":
        return "video/mp4", ".mp4"
    if h.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm", ".webm"
    if h[:4] == b"RIFF" and h[8:12] == b"AVI ":
        return "video/x-msvideo", ".avi"
    return None


class LocalStorage:
    def __init__(self, cfg):
        self.root = Path(cfg.get("directory", DEFAULT_STORAGE))
        self.reserve = cfg.get("min_free_mb", 1024) * 1024 * 1024

    def check(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise ValueError("Storage directory must not be a symlink")
        with tempfile.TemporaryFile(dir=self.root):
            pass
        self.check_space(0)

    def check_space(self, size):
        if shutil.disk_usage(self.root).free < self.reserve + size:
            raise OSError("Insufficient free disk space")

    def save(self, user_id, path, name):
        # Only internal names are accepted; never use names supplied by Telegram.
        if Path(name).name != name or name in (".", ".."):
            raise ValueError("Invalid file name")
        folder = self.root / str(int(user_id))
        folder.mkdir(mode=0o700, exist_ok=True)
        if folder.is_symlink():
            raise ValueError("User folder must not be a symlink")
        self.check_space(path.stat().st_size)
        destination = folder / f"{Path(name).stem}_{uuid.uuid4().hex}{Path(name).suffix}"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, prefix=".pending-", delete=False) as out:
                temporary = Path(out.name)
                with path.open("rb") as source:
                    shutil.copyfileobj(source, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return destination


def make_app(cfg, storage):
    allowed = set(cfg["telegram"].get("allowed_user_ids", []))

    def authorized(update):
        return (update.effective_chat.type == "private" and update.effective_user
                and (not allowed or update.effective_user.id in allowed))

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if authorized(update):
            await update.message.reply_text(cfg["telegram"].get("greeting", "Привет!"))

    async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not authorized(update):
            return
        message = update.message
        item = message.photo[-1] if message.photo else message.video or message.document
        if not item or (message.document and not (item.mime_type or "").startswith(("image/", "video/"))):
            return
        if not item.file_size or item.file_size > cfg["limit"]:
            await message.reply_text("Файл слишком большой. Максимум: %s MB." % (cfg["limit"] // 1024**2))
            return
        if not cfg["telegram"].get("local_api_url") and item.file_size > 20 * 1024**2:
            await message.reply_text(
                "Для файлов больше 20 MB администратор должен подключить локальный Telegram Bot API.")
            return
        try:
            storage.check_space(item.file_size * 2)
            with tempfile.TemporaryDirectory(prefix=".tg-media-", dir=storage.root) as tmp:
                path = Path(tmp) / "upload"
                remote = await item.get_file(read_timeout=600)
                if cfg["telegram"].get("local_api_url"):
                    # --local returns an absolute path, readable by this service.
                    # Copy in a worker instead of buffering large files in RAM.
                    source = Path(remote.file_path or "")
                    root = Path(cfg["telegram"]["local_file_root"]).resolve()
                    if not source.is_absolute() or not source.resolve().is_relative_to(root):
                        raise ValueError("Bot API returned a path outside local_file_root")
                    if not source.is_file() or source.stat().st_size > cfg["limit"]:
                        raise ValueError("Local file missing or exceeds configured limit")
                    await asyncio.to_thread(shutil.copyfile, source, path)
                else:
                    await remote.download_to_drive(path)
                if path.stat().st_size > cfg["limit"]:
                    raise ValueError("Downloaded file exceeds limit")
                kind = media_type(path)
                if not kind:
                    return
                _, suffix = kind
                name = f"{message.chat_id}_{message.message_id}{suffix}"
                await asyncio.to_thread(storage.save, update.effective_user.id, path, name)
            await message.reply_text("Сохранено.")
        except Exception as error:
            # Never log request URLs, exceptions or tokens.
            LOG.warning("Media operation failed (%s)", type(error).__name__)
            await message.reply_text("Не удалось сохранить файл. Попробуйте позже.")

    async def error_handler(update, context):
        LOG.warning("Update failed (%s)", type(context.error).__name__)

    builder = Application.builder().token(cfg["telegram"]["token"]).concurrent_updates(False)
    local_url = cfg["telegram"].get("local_api_url")
    if local_url:
        builder = (builder.base_url(local_url + "/bot")
                   .base_file_url(local_url + "/file/bot").local_mode(True)
                   .read_timeout(600).write_timeout(600))
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, receive))
    app.add_error_handler(error_handler)
    return app


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    os.umask(0o077)
    cfg = load_config(os.environ.get("BOT_CONFIG", "/etc/botimagessave/config.toml"))
    try:
        storage = LocalStorage(cfg["storage"])
        storage.check()
    except Exception as error:
        LOG.error("Local storage startup check failed (%s)", type(error).__name__)
        raise SystemExit(1) from None
    make_app(cfg, storage).run_polling(allowed_updates=["message"], drop_pending_updates=False)


if __name__ == "__main__":
    main()
