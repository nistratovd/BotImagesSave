import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import bot


class MediaTests(unittest.TestCase):
    def test_signatures(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'file'
            for content, expected in [(b'\xff\xd8\xffdata', 'image/jpeg'),
                                      (b'\x89PNG\r\n\x1a\n', 'image/png'),
                                      (b'\x00\x00\x00\x18ftypisom', 'video/mp4'),
                                      (b'#!/bin/sh', None)]:
                path.write_bytes(content)
                result = bot.media_type(path)
                self.assertEqual(result[0] if result else None, expected)

    def test_upload_reuses_user_folder(self):
        drive = bot.Drive.__new__(bot.Drive)
        drive.root = 'root'
        drive.folders = {}
        drive.service = MagicMock()
        files = drive.service.files.return_value
        files.list.return_value.execute.return_value = {'files': [{'id': 'existing'}]}
        with patch('bot.MediaFileUpload'):
            drive.upload(123, '/unused', 'one.jpg', 'image/jpeg')
            drive.upload(123, '/unused', 'two.jpg', 'image/jpeg')
        self.assertEqual(files.list.call_count, 1)
        self.assertEqual(files.create.call_count, 2)
        self.assertEqual(files.create.call_args.kwargs['body']['parents'], ['existing'])


if __name__ == '__main__':
    unittest.main()

class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return {'telegram': {'token': '123456:testing', 'greeting': 'Привет!',
                             'allowed_user_ids': []}, 'limit': 20 * 1024**2}

    async def test_start_and_reject_non_media(self):
        from unittest.mock import AsyncMock
        drive = MagicMock()
        app = bot.make_app(self.config(), drive)
        update = MagicMock()
        update.effective_chat.type = 'private'
        update.effective_user.id = 123
        update.message.reply_text = AsyncMock()
        await app.handlers[0][0].callback(update, None)
        update.message.reply_text.assert_awaited_once_with('Привет!')
        update.message.photo = []
        update.message.video = None
        update.message.document.mime_type = 'application/pdf'
        await app.handlers[0][1].callback(update, None)
        drive.upload.assert_not_called()

    async def test_photo_saved_and_temp_removed(self):
        from unittest.mock import AsyncMock
        drive = MagicMock()
        app = bot.make_app(self.config(), drive)
        update = MagicMock()
        update.effective_chat.type = 'private'
        update.effective_user.id = 123
        update.message.reply_text = AsyncMock()
        update.message.document = None
        update.message.chat_id = 123
        update.message.message_id = 5
        item = MagicMock()
        item.file_size = 10
        remote = MagicMock()
        async def download(path):
            Path(path).write_bytes(b'\xff\xd8\xffphoto')
        remote.download_to_drive = AsyncMock(side_effect=download)
        item.get_file = AsyncMock(return_value=remote)
        update.message.photo = [item]
        await app.handlers[0][1].callback(update, None)
        drive.upload.assert_called_once()
        args = drive.upload.call_args.args
        self.assertEqual((args[0], args[2], args[3]), (123, '123_5.jpg', 'image/jpeg'))
        self.assertFalse(Path(args[1]).exists())
        update.message.reply_text.assert_awaited_once_with('Сохранено.')

    async def test_disallowed_user_and_large_video(self):
        from unittest.mock import AsyncMock
        cfg = self.config()
        cfg['telegram']['allowed_user_ids'] = [999]
        drive = MagicMock()
        update = MagicMock()
        update.effective_chat.type = 'private'
        update.effective_user.id = 123
        update.message.reply_text = AsyncMock()
        app = bot.make_app(cfg, drive)
        await app.handlers[0][1].callback(update, None)
        update.message.reply_text.assert_not_awaited()
        cfg['telegram']['allowed_user_ids'] = []
        app = bot.make_app(cfg, drive)
        update.message.photo = []
        update.message.document = None
        update.message.video.file_size = cfg['limit'] + 1
        await app.handlers[0][1].callback(update, None)
        update.message.reply_text.assert_awaited_once()
        drive.upload.assert_not_called()
