"""Preview or repair ISO-image files incorrectly named .mp4 by older bot versions."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import media_type, ISO_IMAGE_EXTENSIONS


def repair(root, apply=False):
    count = 0
    for source in root.rglob('*.mp4'):
        if source.is_symlink() or not source.is_file():
            continue
        kind = media_type(source)
        if not kind or kind[1] not in ISO_IMAGE_EXTENSIONS:
            continue
        destination = source.with_suffix(kind[1])
        if destination.exists() or destination.is_symlink():
            print(f'SKIP (destination exists): {source}')
            continue
        if apply:
            # Same directory/filesystem: preserve bytes and permissions, never overwrite.
            os.link(source, destination)
            source.unlink()
        print(f'{"RENAMED" if apply else "PREVIEW"}: {source} -> {destination}')
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--apply', action='store_true', help='Actually rename; default is preview only')
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error('directory must exist')
    print(f'Files matched: {repair(args.directory, args.apply)}')


if __name__ == '__main__':
    main()
