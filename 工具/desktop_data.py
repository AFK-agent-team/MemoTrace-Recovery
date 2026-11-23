"""Read-only data access for the 2.0.5 desktop port."""
from contextlib import closing
from functools import lru_cache
import html
import json
from pathlib import Path
import re
import sqlite3

import apsw
from PIL import Image

import memotrace_local as engine
from local_media import ImageDecoder


HEAD_IMAGE = 'head_image/head_image.db'
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
VIDEO_EXTENSIONS = {'.mp4', '.m4v', '.mov', '.webm'}
RESOURCES = engine.SOURCE / 'exporter/resources'
DEFAULT_AVATAR = RESOURCES / 'default_avatar.png'


def file_under(root, name):
    if not isinstance(name, (str, Path)) or not name:
        return None
    try:
        candidate = Path(name)
        if '..' in candidate.parts or (candidate.anchor and not candidate.is_relative_to(root)):
            return None
        path = (root / candidate).resolve()
        if path.is_relative_to(root) and path.is_file():
            return path
    except (OSError, ValueError):
        pass
    return None


def read_avatars(usernames, *, library=None, snapshot=None):
    usernames = sorted({u for u in usernames if u})
    result = {}
    if not usernames:
        return result

    def collect(query):
        for start in range(0, len(usernames), 250):
            batch = usernames[start:start + 250]
            placeholders = ','.join('?' for _ in batch)
            sql = (f'SELECT username, image_buffer FROM head_image WHERE username IN ({placeholders}) '
                   'AND length(image_buffer) BETWEEN 1 AND 2097152')
            result.update((row[0], row[1]) for row in query(sql, batch) if isinstance(row[1], bytes))

    try:
        if library is not None and HEAD_IMAGE in library.keys:
            with library.open(HEAD_IMAGE) as db:
                collect(lambda sql, batch: ((r['username'], r['image_buffer']) for r in db.query(sql, batch)))
        elif snapshot is not None:
            path = snapshot.db_dir / HEAD_IMAGE
            if path.is_file():
                with closing(engine.readonly(path)) as db:
                    collect(db.execute)
    except (OSError, sqlite3.Error, apsw.Error, RuntimeError, ValueError):
        # Avatars are optional. Message reads still work if a thumbnail is missing.
        pass
    return result


def read_voice(server_id, *, library=None, snapshot=None):
    sql = 'SELECT voice_data FROM VoiceInfo WHERE svr_id=? AND length(voice_data) <= 67108864'
    args = (int(server_id),)
    if library is not None:
        for relative in sorted(r for r in library.keys if re.fullmatch(r'message/media_\d+\.db', r)):
            with library.open(relative) as db:
                if not db.query("SELECT name FROM sqlite_master WHERE name='VoiceInfo'"):
                    continue
                rows = db.query(sql, args)
                if rows:
                    return rows[0]['voice_data']
    elif snapshot is not None:
        for path in sorted((snapshot.db_dir / 'message').glob('media_*.db')):
            with closing(engine.readonly(path)) as db:
                if not db.execute("SELECT name FROM sqlite_master WHERE name='VoiceInfo'").fetchone():
                    continue
                row = db.execute(sql, args).fetchone()
                if row:
                    return row[0]
    return b''


@lru_cache(maxsize=1)
def emoji_assets():
    return {p.stem: p for p in (RESOURCES / 'emoji').glob('*.png')}


def emoji_html(value, image_url):
    """Escape a message and replace only the bundled, recognized [emoji] tokens."""
    assets = emoji_assets()
    value = str(value or '')
    fragments = []
    start = 0
    for token in re.finditer(r'\[([^\[\]\r\n]{1,20})\]', value):
        asset = assets.get(token[1])
        if asset is None:
            continue
        fragments.append(html.escape(value[start:token.start()]))
        url = image_url(asset)
        if url:
            fragments.append(f'<img width="20" height="20" src="{html.escape(url, quote=True)}"'
                             f' alt="{html.escape(token[0], quote=True)}">')
        else:
            fragments.append(html.escape(token[0]))
        start = token.end()
    fragments.append(html.escape(value[start:]))
    return ''.join(fragments).replace('\n', '<br>')


def message_key(row):
    if row.get('server_id') not in (None, '', 0, '0'):
        return ('server', str(row['server_id']))
    return ('local', row['source_db'], str(row['local_id']))


class PreviewSource:
    def __init__(self, cfg, chat):
        self.account = Path(cfg['account_dir']).resolve()
        self.decoder = ImageDecoder(self.account, cfg['wechat_exe'])
        self.chat = chat
        self.archive = None

    def archived_assets(self):
        if self.archive is not None:
            return self.archive
        self.archive = {}
        archives = engine.ROOT.parent / self.chat['kind'] / engine.safe_name(self.chat['name']) / '05_原始记录'
        for folder in sorted(archives.glob('导出_*'), reverse=True):
            folder = folder.resolve()
            if not folder.is_relative_to(archives.resolve()):
                continue
            try:
                report = json.loads((folder / '导出校验.json').read_text(encoding='utf-8'))
                if report['chat']['username'] != self.chat['username']:
                    continue
                with (folder / '消息.jsonl').open(encoding='utf-8') as stream:
                    for line in stream:
                        row = json.loads(line)
                        key = message_key(row)
                        for attachment in row.get('attachments', ()):
                            path = file_under(folder, attachment.get('decoded_path') or attachment.get('path'))
                            if path and path.suffix.lower() != '.dat':
                                self.archive.setdefault(key, {}).setdefault(attachment.get('field', 'path'), path)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return self.archive

    def image(self, path):
        if path is None:
            return None
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            return path
        if path.suffix.lower() == '.dat':
            try:
                if path.stat().st_size <= 64 * 1024 * 1024:
                    return self.decoder.decoded_path(path)
            except (OSError, ValueError, Image.DecompressionBombError):
                return None
        return None

    def assets(self, row):
        kind = row['type']
        if kind not in (3, 34, 43, 47, 25769803825):
            return None, None, False
        main = file_under(self.account, row.get('path'))
        thumb = file_under(self.account, row.get('thumb_path'))
        if kind in (3, 47):
            if path := self.image(main):
                return path, path, False
            saved = self.archived_assets().get(message_key(row), {})
            if path := self.image(saved.get('path')):
                return path, path, False
            preview = self.image(thumb) or self.image(saved.get('thumb_path'))
            return preview, preview, bool(preview)
        saved = self.archived_assets().get(message_key(row), {}) if main is None or (kind == 43 and thumb is None) else {}
        main = main or saved.get('path')
        if kind == 43:
            video = main if main and main.suffix.lower() in VIDEO_EXTENSIONS else None
            preview = self.image(thumb or saved.get('thumb_path'))
            return preview, video or preview, video is None and preview is not None
        return None, main, False

    def prepare(self, rows):
        return {message_key(row): self.assets(row)
                for row in rows if row['type'] in (3, 34, 43, 47, 25769803825)}
