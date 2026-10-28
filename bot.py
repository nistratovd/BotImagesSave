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
    if cfg["telegram"].get("greeting_parse_mode", "") not in ("", "HTML"):
        raise ValueError('greeting_parse_mode must be "HTML" or empty for plain text')
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


# ISO BMFF containers share an ftyp header: the brands identify their media type.
ISO_IMAGE_BRANDS = {
    **dict.fromkeys((b"heic", b"heix", b"heim", b"heis"), ("image/heic", ".heic")),
    **dict.fromkeys((b"hevc", b"hevx", b"hevm", b"hevs"), ("image/heic-sequence", ".heics")),
    b"avif": ("image/avif", ".avif"),
    b"avis": ("image/avif-sequence", ".avifs"),
}
ISO_GENERIC_IMAGE_BRANDS = {
    b"mif1": ("image/heif", ".heif"),
    b"msf1": ("image/heif-sequence", ".heifs"),
}
ISO_VIDEO_BRANDS = {b"isom", b"mp41", b"mp42", b"avc1", b"dash", b"M4V ", b"F4V ", b"MSNV"}
ISO_VIDEO_BRANDS.update(f"iso{i}".encode() for i in range(2, 10))
ISO_IMAGE_EXTENSIONS = {kind[1] for kind in (*ISO_IMAGE_BRANDS.values(), *ISO_GENERIC_IMAGE_BRANDS.values())}


def iso_media_type(header):
    size = int.from_bytes(header[:4], "big")
    offset = 8
    if size == 1:  # Extended-size box.
        if len(header) < 16:
            return None
        size = int.from_bytes(header[8:16], "big")
        offset = 16
    if size < offset + 8 or size > len(header) or (size - offset - 8) % 4:
        return None
    major = header[offset:offset + 4]
    compatible = [header[i:i + 4] for i in range(offset + 8, size, 4)]
    # Specific image brands override generic mif1/msf1 and isom compatibility.
    if major in ISO_IMAGE_BRANDS:
        return ISO_IMAGE_BRANDS[major]
    for brand in compatible:
        if brand in ISO_IMAGE_BRANDS:
            return ISO_IMAGE_BRANDS[brand]
    for brand in [major, *compatible]:
        if brand in ISO_GENERIC_IMAGE_BRANDS:
            return ISO_GENERIC_IMAGE_BRANDS[brand]
    if b"qt  " in [major, *compatible]:
        return "video/quicktime", ".mov"
    if any(brand in ISO_VIDEO_BRANDS for brand in [major, *compatible]):
        return "video/mp4", ".mp4"
    return None


def media_type(path):
    with open(path, "rb") as source:
        # ftyp is small; bound header reads even for multi-gigabyte files.
        h = source.read(65536)
    if h.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if len(h) >= 8 and h[4:8] == b"ftyp":
        return iso_media_type(h)
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
            await update.message.reply_text(
                cfg["telegram"].get("greeting", "Привет!"),
                parse_mode=cfg["telegram"].get("greeting_parse_mode") or None,
            )

    async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not authorized(update):
            return
        message = update.message
        item = message.photo[-1] if message.photo else message.video or message.document
        if not item:
            return
        if message.document and not (item.mime_type or "").startswith(("image/", "video/")):
            # Some Telegram clients label HEIF photos as application/octet-stream.
            # The suffix only permits inspection; the header must still match below.
            if Path(item.file_name or "").suffix.lower() not in ISO_IMAGE_EXTENSIONS:
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
