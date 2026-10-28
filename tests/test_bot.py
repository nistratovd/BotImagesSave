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
                                      (b'\x00\x00\x00\x10ftypisom\x00\x00\x00\x00', 'video/mp4'),
                                      (b'#!/bin/sh', None)]:
                path.write_bytes(content)
                result = bot.media_type(path)
                self.assertEqual(result[0] if result else None, expected)

    def test_iso_container_brands(self):
        def ftyp(major, compatible=(), extended=False):
            payload = major + b'\0' * 4 + b''.join(compatible)
            if extended:
                return b'\0\0\0\1ftyp' + (16 + len(payload)).to_bytes(8, 'big') + payload
            return (8 + len(payload)).to_bytes(4, 'big') + b'ftyp' + payload

        cases = [
            (ftyp(b'heic', (b'mif1',)), ('image/heic', '.heic')),
            (ftyp(b'mif1', (b'isom', b'heix')), ('image/heic', '.heic')),
            (ftyp(b'hevc', (b'msf1',)), ('image/heic-sequence', '.heics')),
            (ftyp(b'mif1'), ('image/heif', '.heif')),
            (ftyp(b'msf1'), ('image/heif-sequence', '.heifs')),
            (ftyp(b'avif', (b'mif1',)), ('image/avif', '.avif')),
            (ftyp(b'qt  '), ('video/quicktime', '.mov')),
            (ftyp(b'mp42', (b'isom',)), ('video/mp4', '.mp4')),
            (ftyp(b'heic', extended=True), ('image/heic', '.heic')),
            (ftyp(b'M4A '), None),
            (ftyp(b'zzzz'), None),
            (ftyp(b'heic')[:-1], None),
            (b'\0\0\0\x08ftyp', None),
            (b'\0\0\0\1ftyp', None),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'file'
            for data, expected in cases:
                with self.subTest(data=data):
                    path.write_bytes(data)
                    self.assertEqual(bot.media_type(path), expected)

    def test_repair_preview_apply_and_collision(self):
        from scripts.fix_image_extensions import repair
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = b'\0\0\0\x10ftypheic\0\0\0\0'
            source = root / 'image.mp4'
            source.write_bytes(image)
            video = root / 'video.mp4'
            video.write_bytes(b'\0\0\0\x10ftypmp42\0\0\0\0')
            with patch('builtins.print'):
                self.assertEqual(repair(root), 1)
                self.assertTrue(source.exists())
                self.assertFalse(source.with_suffix('.heic').exists())
                self.assertEqual(repair(root, apply=True), 1)
                self.assertFalse(source.exists())
                self.assertEqual(source.with_suffix('.heic').read_bytes(), image)
                self.assertTrue(video.exists())
                source.write_bytes(image)
                self.assertEqual(repair(root, apply=True), 0)
                self.assertTrue(source.exists())
                self.assertEqual(source.with_suffix('.heic').read_bytes(), image)

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

    def test_large_limits_and_endpoint_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.toml'
            for limit in (21, 500, 2000):
                path.write_text(f'[telegram]\ntoken="123:test"\n[storage]\nmax_file_mb={limit}\n')
                self.assertEqual(bot.load_config(path)['limit'], limit * 1024**2)
            for value in ('0', '-1', 'true', '20.5', '"500"'):
                path.write_text(f'[telegram]\ntoken="123:test"\n[storage]\nmax_file_mb={value}\n')
                with self.assertRaises(ValueError):
                    bot.load_config(path)
            for url in ('http://example.com:8081', 'http://127.0.0.1:8081/bot',
                        'http://secret@localhost:8081'):
                path.write_text(f'[telegram]\ntoken="123:test"\nlocal_api_url="{url}"\n')
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
        update.message.reply_text.assert_awaited_once_with('Привет!', parse_mode=None)
        update.message.photo = []
        update.message.video = None
        update.message.document.mime_type = 'application/pdf'
        update.message.document.file_name = 'report.pdf'
        update.message.reply_text.reset_mock()
        await app.handlers[0][1].callback(update, None)
        update.message.reply_text.assert_awaited_once_with(bot.UNSUPPORTED_FORMAT_MESSAGE)
        update.message.document.get_file.assert_not_called()
        storage.save.assert_not_called()

    async def test_unsupported_attachment_routing(self):
        from unittest.mock import AsyncMock
        from telegram import Update
        storage = MagicMock()
        app = bot.make_app(self.config(), storage)
        handler = app.handlers[0][1]
        base = {'file_id': 'file', 'file_unique_id': 'unique', 'duration': 1}
        for field, attachment in [
            ('audio', base), ('voice', base),
            ('animation', dict(base, width=10, height=10)),
            ('sticker', dict(base, width=10, height=10, is_animated=False,
                             is_video=False, type='regular')),
        ]:
            with self.subTest(field=field):
                message = {'message_id': 1, 'date': 0,
                           'chat': {'id': 123, 'type': 'private'}, field: attachment}
                update = Update.de_json({'update_id': 1, 'message': message}, app.bot)
                self.assertTrue(handler.check_update(update))
                mock_update = MagicMock()
                mock_update.effective_chat.type = 'private'
                mock_update.effective_user.id = 123
                mock_update.message.photo = []
                mock_update.message.video = None
                mock_update.message.document = None
                mock_update.message.reply_text = AsyncMock()
                await handler.callback(mock_update, None)
                mock_update.message.reply_text.assert_awaited_once_with(bot.UNSUPPORTED_FORMAT_MESSAGE)
        storage.save.assert_not_called()
        text_update = Update.de_json({'update_id': 2, 'message': {
            'message_id': 2, 'date': 0, 'chat': {'id': 123, 'type': 'private'},
            'text': 'hello'}}, app.bot)
        self.assertFalse(handler.check_update(text_update))

    async def test_formatted_greeting_from_example(self):
        from unittest.mock import AsyncMock
        import tomllib
        with open('config.example.toml', 'rb') as source:
            cfg = tomllib.load(source)
        app = bot.make_app(cfg, MagicMock())
        update = MagicMock()
        update.effective_chat.type = 'private'
        update.effective_user.id = 123
        update.message.reply_text = AsyncMock()
        await app.handlers[0][0].callback(update, None)
        text = cfg['telegram']['greeting']
        self.assertTrue(text.startswith('<b>Привет!</b>'))
        self.assertIn('500 МБ', text)
        self.assertIn('<b>User</b> (@nikuser)', text)
        self.assertIn('\n\n', text)
        self.assertLess(len(text), 4096)
        update.message.reply_text.assert_awaited_once_with(text, parse_mode='HTML')

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

    async def test_large_video_local_api_and_cloud_guard(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = root / 'api'
            api.mkdir()
            source = api / 'video.mp4'
            with source.open('wb') as out:
                out.write(b'\x00\x00\x00\x10ftypisom\x00\x00\x00\x00')
                out.truncate(21 * 1024**2)
            storage = bot.LocalStorage({'directory': str(root / 'archive'), 'min_free_mb': 0})
            storage.check()
            cfg = self.config()
            cfg['limit'] = 30 * 1024**2
            update = MagicMock()
            update.effective_chat.type = 'private'
            update.effective_user.id = 123
            message = update.message
            message.photo = []
            message.document = None
            message.chat_id = 123
            message.message_id = 7
            message.reply_text = AsyncMock()
            remote = MagicMock()
            remote.file_path = str(source)
            message.video.file_size = source.stat().st_size
            message.video.get_file = AsyncMock(return_value=remote)
            # A high configured limit must not pretend the cloud can download it.
            app = bot.make_app(cfg, storage)
            await app.handlers[0][1].callback(update, None)
            message.video.get_file.assert_not_awaited()
            self.assertIn('локальный Telegram Bot API', message.reply_text.call_args.args[0])
            cfg['telegram'].update(local_api_url='http://127.0.0.1:8081', local_file_root=str(api))
            app = bot.make_app(cfg, storage)
            self.assertEqual(app.bot.base_url, 'http://127.0.0.1:8081/bot123456:testing')
            self.assertTrue(app.bot.local_mode)
            message.reply_text.reset_mock()
            await app.handlers[0][1].callback(update, None)
            message.reply_text.assert_awaited_once_with('Сохранено.')
            saved = list((storage.root / '123').glob('*.mp4'))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].stat().st_size, 21 * 1024**2)
            self.assertTrue(source.exists())  # API owns its cache.
            # A path outside the API directory must never be archived.
            remote.file_path = str(saved[0])
            message.reply_text.reset_mock()
            await app.handlers[0][1].callback(update, None)
            self.assertIn('Не удалось', message.reply_text.call_args.args[0])
            self.assertEqual(len(list((storage.root / '123').iterdir())), 1)

    async def test_heic_document_keeps_bytes_and_extension(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as tmp:
            storage = bot.LocalStorage({'directory': tmp, 'min_free_mb': 0})
            storage.check()
            app = bot.make_app(self.config(), storage)
            update = MagicMock()
            update.effective_chat.type = 'private'
            update.effective_user.id = 123
            message = update.message
            message.photo = []
            message.video = None
            message.chat_id = 123
            message.message_id = 11
            message.reply_text = AsyncMock()
            item = message.document
            item.mime_type = 'application/octet-stream'
            item.file_name = 'photo.HEIC'
            data = b'\0\0\0\x18ftypheic\0\0\0\0mif1heic' + b'original image bytes'
            item.file_size = len(data)
            remote = MagicMock()
            async def download(path):
                Path(path).write_bytes(data)
            remote.download_to_drive = AsyncMock(side_effect=download)
            item.get_file = AsyncMock(return_value=remote)
            await app.handlers[0][1].callback(update, None)
            saved = list((storage.root / '123').iterdir())
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].suffix, '.heic')
            self.assertEqual(saved[0].read_bytes(), data)
            message.reply_text.assert_awaited_once_with('Сохранено.')
            # A misleading suffix must not allow a non-image through.
            data = b'not a photo'
            message.reply_text.reset_mock()
            await app.handlers[0][1].callback(update, None)
            self.assertEqual(len(list((storage.root / '123').iterdir())), 1)
            message.reply_text.assert_awaited_once_with(bot.UNSUPPORTED_FORMAT_MESSAGE)
            self.assertEqual(list(storage.root.glob('.tg-media-*')), [])

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
