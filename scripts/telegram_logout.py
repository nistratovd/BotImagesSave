"""One-time cloud -> local migration; the token never enters command arguments."""
import asyncio
import logging
import os
import sys
import tomllib
from telegram import Bot


async def main():
    logging.basicConfig(level=logging.CRITICAL)
    with open(os.environ.get('BOT_CONFIG', '/etc/botimagessave/config.toml'), 'rb') as source:
        cfg = tomllib.load(source)
    try:
        async with Bot(cfg['telegram']['token']) as bot:
            if not await bot.log_out():
                raise RuntimeError('logOut failed')
    except Exception as error:
        print('Cloud logOut failed (%s); do not switch yet.' % type(error).__name__)
        return 1
    print('Cloud logOut succeeded. Start the local Bot API; cloud login is blocked for 10 minutes.')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
