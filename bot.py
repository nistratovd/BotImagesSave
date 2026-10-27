"""Telegram media archive. No listening sockets; OAuth files stay outside Git."""
import asyncio
import logging
import os
from pathlib import Path
import tempfile
import tomllib

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

LOG = logging.getLogger("media_archive")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def load_config(path):
    with open(path, "rb") as source:
        cfg = tomllib.load(source)
    if not cfg["telegram"]["token"] or cfg["telegram"]["token"].startswith("PUT_"):
        raise ValueError("Configure telegram.token")
    if not cfg["google"]["root_folder_id"] or cfg["google"]["root_folder_id"].startswith("PUT_"):
        raise ValueError("Configure google.root_folder_id")
    limit = cfg.get("storage", {}).get("max_file_mb", 20)
    if not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("max_file_mb must be 1..20")
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


class Drive:
    def __init__(self, cfg):
        self.token_path = Path(cfg["token_file"])
        self.credentials = Credentials.from_authorized_user_file(self.token_path, SCOPES)
        self.service = build("drive", "v3", credentials=self.credentials, cache_discovery=False)
        self.root = cfg["root_folder_id"]
        self.folders = {}

    def check(self):
        root = self.service.files().get(fileId=self.root, fields="mimeType,capabilities(canAddChildren)").execute()
        if root["mimeType"] != "application/vnd.google-apps.folder" or not root.get("capabilities", {}).get("canAddChildren"):
            raise ValueError("Google root must be a writable folder")

    def upload(self, user_id, path, name, mime):
        # Calls are serialized by the bot; Google HTTP client is not thread safe.
        user = str(int(user_id))
        if user not in self.folders:
            # Root is provided by configuration, escape query literals.
            root = self.root.replace("\\", "\\\\").replace("'", "\\'")
            found = self.service.files().list(
                q=f"'{root}' in parents and name = '{user}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                fields="files(id)", pageSize=1).execute().get("files", [])
            folder = found[0]["id"] if found else self.service.files().create(
                body={"name": user, "mimeType": "application/vnd.google-apps.folder", "parents": [self.root]}, fields="id").execute()["id"]
            self.folders[user] = folder
        media = MediaFileUpload(str(path), mimetype=mime, resumable=True, chunksize=1024 * 1024)
        try:
            self.service.files().create(body={"name": name, "parents": [self.folders[user]]},
                                        media_body=media, fields="id").execute(num_retries=3)
        finally:
            media.stream().close()


def make_app(cfg, drive):
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
        try:
            with tempfile.TemporaryDirectory(prefix="tg-media-") as tmp:
                path = Path(tmp) / "upload"
                remote = await item.get_file()
                await remote.download_to_drive(path)
                if path.stat().st_size > cfg["limit"]:
                    raise ValueError("Downloaded file exceeds limit")
                kind = media_type(path)
                if not kind:
                    return
                mime, suffix = kind
                name = f"{message.chat_id}_{message.message_id}{suffix}"
                await asyncio.to_thread(drive.upload, update.effective_user.id, path, name, mime)
            await message.reply_text("Сохранено.")
        except Exception as error:
            # Never log request URLs, exceptions or tokens.
            LOG.warning("Media operation failed (%s)", type(error).__name__)
            await message.reply_text("Не удалось сохранить файл. Попробуйте позже.")

    async def error_handler(update, context):
        LOG.warning("Update failed (%s)", type(context.error).__name__)

    app = Application.builder().token(cfg["telegram"]["token"]).concurrent_updates(False).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, receive))
    app.add_error_handler(error_handler)
    return app


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(os.environ.get("BOT_CONFIG", "/etc/botimagessave/config.toml"))
    try:
        drive = Drive(cfg["google"])
        drive.check()
    except Exception as error:
        LOG.error("Google Drive startup check failed (%s)", type(error).__name__)
        raise SystemExit(1) from None
    make_app(cfg, drive).run_polling(allowed_updates=["message"], drop_pending_updates=False)


if __name__ == "__main__":
    main()
