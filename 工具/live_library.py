"""Index local WeChat conversations and open their messages through the read-only VFS."""
from collections import defaultdict
from contextlib import ExitStack
from datetime import datetime
import hashlib
from pathlib import Path
import re

from local_crypto import find_keys
from local_vfs import ScopedDB
from memotrace_local import TZ, _utf8, message_values, parse_records, sorted_message_rows
from target_snapshot import ident

CONTACT_DB = 'contact/contact.db'
SESSION_DB = 'session/session.db'
MEDIA_LOOKUP = ('hardlink/hardlink.db', 'emoticon/emoticon.db')


def message_table(username):
    return 'Msg_' + hashlib.md5(username.encode('utf-8')).hexdigest()


def contact_record(username, row):
    nickname = row.get('nick_name') or ''
    remark = row.get('remark') or ''
    return {'username': username, 'name': remark or nickname or username,
            'nickname': nickname, 'remark': remark,
            'kind': '群聊' if username.endswith('@chatroom') else '个人'}


class LiveLibrary:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.original = Path(cfg['account_dir']) / 'db_storage'
        shards = sorted(
            p.relative_to(self.original).as_posix()
            for p in (self.original / 'message').glob('*.db')
            if re.fullmatch(r'(?:biz_)?message_\d+\.db', p.name)
        )
        optional = [*MEDIA_LOOKUP, 'head_image/head_image.db']
        optional.extend(p.relative_to(self.original).as_posix()
                        for p in (self.original / 'message').glob('media_*.db'))
        paths = [CONTACT_DB, SESSION_DB, *shards,
                 *(p for p in optional if (self.original / p).is_file())]
        self.keys = find_keys(self.original, cfg['wechat_exe'], log=log, timeout=90, relative_files=paths)
        missing = [p for p in (CONTACT_DB, SESSION_DB, *shards) if p not in self.keys]
        if missing or not shards:
            files = '、'.join(missing) if missing else '未找到消息分库'
            raise RuntimeError(f'微信会话索引未完成：{files}。请保持微信登录，稍后重试。')

        log('正在建立微信会话索引…')
        with self.open(CONTACT_DB) as db:
            self.contacts = {
                row['username']: contact_record(row['username'], row)
                for row in db.query("SELECT username, nick_name, remark FROM contact WHERE username <> ''")
            }
        with self.open(SESSION_DB) as db:
            records = db.query(
                'SELECT username, last_timestamp, CAST(summary AS BLOB) AS summary '
                "FROM SessionTable WHERE username <> '' ORDER BY sort_timestamp DESC"
            )
        candidates = set(self.contacts)
        session_rows = {}
        for record in records:
            session_rows.setdefault(record['username'], record)
        candidates.update(session_rows)

        self.table_paths = defaultdict(list)
        for path in shards:
            with self.open(path) as db:
                tables = db.query("SELECT name FROM sqlite_master WHERE type='table'")
                names = {row['name'] for row in tables}
                for name in sorted(names):
                    if re.fullmatch(r'Msg_[0-9a-f]{32}', name):
                        self.table_paths[name].append(path)
                if 'Name2Id' in names:
                    candidates.update(row['user_name'] for row in db.query(
                        "SELECT user_name FROM Name2Id WHERE user_name <> ''"
                    ))
        table_names = {message_table(user): user for user in sorted(candidates)}
        self.sessions = [self.session(user, row) for user, row in session_rows.items()]
        for table in sorted(self.table_paths):
            user = table_names.get(table)
            if user and user in session_rows:
                continue
            user = user or f'unmapped:{table[4:]}'
            self.sessions.append(self.session(user, {}, table=table))
        self.by_username = {session['username']: session for session in self.sessions}
        self.warnings = tuple(
            f'无法读取附件库 {path}' for path in optional
            if (self.original / path).is_file() and path not in self.keys
        )

    def open(self, relative):
        return ScopedDB(self.original / relative, self.keys[relative])

    def session(self, username, row, *, table=None):
        entry = dict(self.contacts.get(username) or contact_record(username, {}))
        if username.startswith('unmapped:'):
            entry['name'] = '未匹配名称的会话 ' + username[9:17]
        stamp = row.get('last_timestamp') or 0
        preview = row.get('summary') or b''
        return {**entry, 'table': table or message_table(username),
                'can_export': not username.startswith('unmapped:'),
                'updated_at': datetime.fromtimestamp(stamp, TZ).isoformat(timespec='seconds') if stamp else '',
                'preview': preview.decode('utf-8', errors='replace') if isinstance(preview, bytes) else preview}

    def read(self, username, log):
        from wxManager.log import logger
        from wxManager.manager_v4 import DataBaseV4
        from wxManager.model import Contact, Me
        from wxManager.parser.wechat_v4 import FACTORY_REGISTRY
        from wxManager.parser.util.protocbuf.roomdata_pb2 import ChatRoomData

        chat = self.by_username[username]
        table = ident(chat['table'])
        raw = []
        log(f'正在只读打开 {chat["name"]} 的消息…')
        for path in self.table_paths.get(chat['table'], ()):
            with self.open(path) as db:
                columns = [column['name'] for column in db.query(f'PRAGMA table_info({table})')]
                fields = [
                    f'CAST(msg.{ident(col)} AS BLOB) AS {ident(col)}'
                    if col in {'source', 'message_content', 'compress_content'} else f'msg.{ident(col)}'
                    for col in columns
                ]
                for row in db.query(
                    f'SELECT {", ".join(fields)}, names.user_name AS sender_username FROM {table} msg '
                    'LEFT JOIN Name2Id names ON msg.real_sender_id=names.rowid'
                ):
                    for col in ('source', 'message_content', 'compress_content'):
                        value = row.get(col)
                        if isinstance(value, bytes) and _utf8(value):
                            row[col] = value.decode('utf-8')
                    row['source_db'] = Path(path).name
                    raw.append(row)
        if not raw:
            return chat, [], []

        def person(user, group_name=''):
            entry = self.contacts.get(user) or contact_record(user, {})
            return Contact(wxid=user, remark=group_name or entry['name'], nickname=entry['nickname'])

        members = {}
        if chat['kind'] == '群聊':
            with self.open(CONTACT_DB) as db:
                rows = db.query('SELECT ext_buffer FROM chat_room WHERE username=?', (username,))
            if rows and rows[0]['ext_buffer']:
                room = ChatRoomData()
                room.ParseFromString(rows[0]['ext_buffer'])
                members = {member.wxID: person(member.wxID, member.displayName) for member in room.members if member.wxID}
        contacts = set(members) | {r['sender_username'] for r in raw if r.get('sender_username')}
        manager = DataBaseV4()
        manager.get_contact_by_username = person
        manager.get_chatroom_members = lambda _: members
        manager.get_audio_text = lambda _: ''
        manager.hardlink_db.create_index = lambda: None
        by_server_id = {row['server_id']: row for row in raw if row.get('server_id')}
        resolving = set()

        def referenced_message(talker, server_id):
            row = by_server_id.get(server_id)
            if talker != username or not row or server_id in resolving:
                return None
            sender = row.get('sender_username') or str(row.get('real_sender_id', ''))
            time_text = datetime.fromtimestamp(row.get('create_time') or 0, TZ).strftime('%Y-%m-%d %H:%M:%S')
            resolving.add(server_id)
            try:
                factory = FACTORY_REGISTRY.get(row['local_type'], FACTORY_REGISTRY[-1])
                return factory.create(message_values(row, sender, time_text), talker, manager)
            finally:
                resolving.remove(server_id)

        manager.get_message_by_server_id = referenced_message
        with ExitStack() as stack:
            lookups = []
            for relative, helper in zip(MEDIA_LOOKUP, (manager.hardlink_db, manager.emotion_db)):
                if relative in self.keys:
                    db = stack.enter_context(self.open(relative))
                    db.conn.set_row_trace(None)
                    helper.DB = db.conn
                    lookups.append(db)
            account = Me()
            previous = account.wxid, account.wx_dir
            log_was_disabled = logger.disabled
            account.wxid, account.wx_dir = self.cfg['self_username'], self.cfg['account_dir']
            logger.disabled = True  # The upstream parser logs raw message text on some errors.
            try:
                parsed = list(parse_records(chat, sorted_message_rows(raw), manager, include_audio=False))
                for db in lookups:
                    db.view.assert_stable()
            finally:
                logger.disabled = log_was_disabled
                account.wxid, account.wx_dir = previous
        return chat, parsed, [self.contacts[user] for user in sorted(contacts) if user in self.contacts]
