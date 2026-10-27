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

    def test_config_requires_no_google_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.toml'
            path.write_text('[telegram]\ntoken = "123:test"\n')
            cfg = bot.load_config(path)
            self.assertEqual(cfg['storage']['directory'], bot.DEFAULT_STORAGE)
            self.assertEqual(cfg['storage']['min_free_mb'], 1024)
            path.write_text('[telegram]\ntoken = "123:test"\n[storage]\ndirectory = "relative"\n')
            with self.assertRaises(ValueError):
                bot.load_config(path)

    def test_local_save_survives_restart_and_keeps_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {'directory': str(Path(tmp) / 'archive'), 'min_free_mb': 0}
            storage = bot.LocalStorage(cfg)
            storage.check()
            source = Path(tmp) / 'input'
            source.write_bytes(b'photo')
            one = storage.save(123, source, '1.jpg')
            storage = bot.LocalStorage(cfg)
            storage.check()
            two = storage.save(123, source, '1.jpg')
            other = storage.save(456, source, '1.jpg')
            self.assertNotEqual(one, two)
            self.assertEqual(one.parent, two.parent)
            self.assertNotEqual(one.parent, other.parent)
            self.assertEqual(one.read_bytes(), b'photo')
            self.assertEqual(two.read_bytes(), b'photo')
            self.assertEqual(one.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(one.parent.glob('.pending-*')), [])

    def test_storage_failure_cleans_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = bot.LocalStorage({'directory': tmp, 'min_free_mb': 0})
            source = Path(tmp) / 'input'
            source.write_bytes(b'photo')
            with patch('bot.shutil.copyfileobj', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    storage.save(123, source, '1.jpg')
            self.assertEqual(list((Path(tmp) / '123').iterdir()), [])

    def test_low_space_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = bot.LocalStorage({'directory': tmp, 'min_free_mb': 1})
            with patch('bot.shutil.disk_usage') as usage:
                usage.return_value.free = 0
                with self.assertRaises(OSError):
                    storage.check_space(10)
            (Path(tmp) / '123').symlink_to(tmp, target_is_directory=True)
            with self.assertRaises(ValueError):
                storage.save(123, Path(tmp) / 'unused', '1.jpg')


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return {'telegram': {'token': '123456:testing', 'greeting': 'Привет!',
                             'allowed_user_ids': []}, 'limit': 20 * 1024**2}

    async def test_start_and_reject_non_media(self):
        from unittest.mock import AsyncMock
        storage = MagicMock()
        app = bot.make_app(self.config(), storage)
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
        storage.save.assert_not_called()

    async def test_photo_saved_and_temp_removed(self):
        from unittest.mock import AsyncMock
        storage = MagicMock()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        storage = bot.LocalStorage({'directory': tmp.name, 'min_free_mb': 0})
        storage.check()
        app = bot.make_app(self.config(), storage)
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
        saved = list((Path(tmp.name) / '123').glob('*.jpg'))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), b'\xff\xd8\xffphoto')
        self.assertEqual(list(Path(tmp.name).glob('.tg-media-*')), [])
        update.message.reply_text.assert_awaited_once_with('Сохранено.')

    async def test_disallowed_user_and_large_video(self):
        from unittest.mock import AsyncMock
        cfg = self.config()
        cfg['telegram']['allowed_user_ids'] = [999]
        storage = MagicMock()
        update = MagicMock()
        update.effective_chat.type = 'private'
        update.effective_user.id = 123
        update.message.reply_text = AsyncMock()
        app = bot.make_app(cfg, storage)
        await app.handlers[0][1].callback(update, None)
        update.message.reply_text.assert_not_awaited()
        cfg['telegram']['allowed_user_ids'] = []
        app = bot.make_app(cfg, storage)
        update.message.photo = []
        update.message.document = None
        update.message.video.file_size = cfg['limit'] + 1
        await app.handlers[0][1].callback(update, None)
        update.message.reply_text.assert_awaited_once()
        storage.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
