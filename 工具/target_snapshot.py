"""Persist only a single explicitly selected chat from lazy read-only DB views."""
from contextlib import closing, contextmanager
from pathlib import Path
from datetime import datetime
import hashlib
import json
import re
import sqlite3
from local_crypto import find_keys
from local_vfs import ScopedDB


def ident(value):
    return '"' + value.replace('"', '""') + '"'


def table_copy(view, destination, table, where='0', params=(), cast_messages=False):
    schema = view.query('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?', (table,))
    if not schema:
        return []
    destination.execute(schema[0]['sql'])
    columns = [c['name'] for c in view.query(f'PRAGMA table_info({ident(table)})')]
    fields = [f'CAST({ident(c)} AS BLOB) AS {ident(c)}' if cast_messages and c in {'source', 'message_content', 'compress_content'} else ident(c) for c in columns]
    rows = view.query(f'SELECT rowid AS __source_rowid__, {",".join(fields)} FROM {ident(table)} WHERE {where}', params)
    for row in rows:
        for col in ('message_content', 'source', 'compress_content'):
            if col in row and isinstance(row[col], bytes) and not row[col].startswith(b'\x28\xb5\x2f\xfd'):
                try:
                    row[col] = row[col].decode('utf-8')
                except UnicodeDecodeError:
                    pass
    fields = ['rowid'] + columns
    destination.executemany(f'INSERT INTO {ident(table)} ({",".join(ident(f) for f in fields)}) VALUES ({",".join("?" for _ in fields)})',
                            [(r['__source_rowid__'], *(r[c] for c in columns)) for r in rows])
    return rows


def scope_snapshot(root, cfg, chat_name, log=print, *, library=None):
    from memotrace_local import write_json, now, TZ, decode_content
    original = Path(cfg['account_dir']) / 'db_storage'
    if library is not None and (library.original != original or library.cfg['self_username'] != cfg['self_username']):
        raise ValueError('当前配置已改变，请重新读取微信会话。')
    keys = library.keys if library is not None else find_keys(original, cfg['wechat_exe'], log=log, timeout=90)
    with ScopedDB(original / 'contact/contact.db', keys['contact/contact.db']) as view:
        hits = view.query('SELECT username,nick_name,remark FROM contact WHERE username=?', (chat_name,))
        if not hits:
            hits = view.query('SELECT username,nick_name,remark FROM contact WHERE nick_name=? OR remark=?', (chat_name,) * 2)
    session = library.by_username.get(chat_name) if library is not None else None
    if len(hits) == 1:
        chat = hits[0]
    elif session and session['can_export']:
        chat = {'username': session['username'], 'nick_name': session['name'], 'remark': ''}
    else:
        raise ValueError(f'需要目标名称唯一匹配，当前匹配 {len(hits)} 个。')
    username = chat['username']
    name = chat['remark'] or chat['nick_name'] or username
    table = 'Msg_' + hashlib.md5(username.encode()).hexdigest()
    stamp = datetime.now(TZ).strftime('%Y%m%d_%H%M%S_%f')
    dataset = root / '本地数据/快照' / stamp / 'db_storage'
    dataset.mkdir(parents=True)
    members = {username, cfg['self_username']}
    md5s = set()
    server_ids = set()
    report = {'scope': {'username': username, 'name': name, 'only_selected_chat': True},
              'started_at': now(), 'keys_persisted': False, 'full_database_copies': False, 'files': []}

    def open_source(rel):
        if rel not in keys:
            raise RuntimeError(f'数据库未通过密钥认证：{rel}')
        return ScopedDB(original / rel, keys[rel])

    @contextmanager
    def create_dest(rel):
        target = dataset / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(target)) as connection, connection:
            yield connection

    if library is not None:
        message_files = library.table_paths.get(table, ())
    else:
        pattern = 'biz_message' if username.startswith('gh_') else 'message'
        message_files = sorted(p.relative_to(original).as_posix()
                               for p in (original / 'message').glob(f'{pattern}_[0-9]*.db'))
    for rel in message_files:
        log(f'只提取选定聊天：{rel}')
        with open_source(rel) as view, create_dest(rel) as out:
            rows = table_copy(view, out, table, '1', cast_messages=True)
            sender_ids = {r['real_sender_id'] for r in rows}
            sender_rows = table_copy(view, out, 'Name2Id', 'rowid IN (' + ','.join('?' for _ in sender_ids) + ')', tuple(sender_ids)) if sender_ids else table_copy(view, out, 'Name2Id')
            members.update(r['user_name'] for r in sender_rows)
            for row in rows:
                content = decode_content(row.get('message_content'))
                md5s.update(re.findall(r'\b(?:md5|rawmd5|filemd5)=["\']([0-9a-fA-F]{32})', content))
                md5s.update(re.findall(r'<(?:filemd5|md5)>([0-9a-fA-F]{32})</', content))
                if row['local_type'] == 34:
                    server_ids.add(row['server_id'])
            report['files'].append({'relative_path': rel, 'selected_message_rows': len(rows), 'pages_read': len(view.view.read_pages)})
    if not any(f['selected_message_rows'] for f in report['files']):
        raise RuntimeError('本机没有找到选定聊天的消息；未切换当前数据。')
    placeholders = ','.join('?' for _ in members)
    with open_source('contact/contact.db') as view, create_dest('contact/contact.db') as out:
        table_copy(view, out, 'contact', f'username IN ({placeholders})', tuple(members))
        table_copy(view, out, 'chat_room', 'username=?', (username,))
        table_copy(view, out, 'contact_label')
    with open_source('session/session.db') as view, create_dest('session/session.db') as out:
        table_copy(view, out, 'SessionTable', 'username=?', (username,))
    with open_source('head_image/head_image.db') as view, create_dest('head_image/head_image.db') as out:
        table_copy(view, out, 'head_image', f'username IN ({placeholders})', tuple(members))
    hash_marks = ','.join('?' for _ in md5s)
    with open_source('hardlink/hardlink.db') as view, create_dest('hardlink/hardlink.db') as out:
        dir_ids = set()
        for t in ('image_hardlink_info_v4', 'video_hardlink_info_v4', 'file_hardlink_info_v4'):
            rows = table_copy(view, out, t, f'md5 IN ({hash_marks})' if md5s else '0', tuple(md5s))
            for row in rows:
                dir_ids.update(v for k, v in row.items() if k in {'dir1', 'dir2'} and v)
        table_copy(view, out, 'dir2id', 'rowid IN (' + ','.join('?' for _ in dir_ids) + ')' if dir_ids else '0', tuple(dir_ids))
    with open_source('emoticon/emoticon.db') as view, create_dest('emoticon/emoticon.db') as out:
        table_copy(view, out, 'kNonStoreEmoticonTable', f'md5 IN ({hash_marks})' if md5s else '0', tuple(md5s))
    # Preserve only voice records referenced by this chat, never other chats' audio.
    voice_ids = tuple(server_ids)
    for rel in sorted(r for r in keys if re.fullmatch(r'message/media_\d+\.db', r)):
        with open_source(rel) as view, create_dest(rel) as out:
            table_copy(view, out, 'VoiceInfo', 'svr_id IN (' + ','.join('?' for _ in voice_ids) + ')', voice_ids)
    # Upstream initialization expects both base files even when the selected chat is only in one series.
    for relative in ('message/message_0.db', 'message/biz_message_0.db'):
        if not (dataset / relative).is_file():
            with create_dest(relative):
                pass
    write_json(dataset / 'info.json', {'username': cfg['self_username'], 'nickname': '', 'wx_dir': cfg['account_dir'], 'xor_key': 0})
    report['messages'] = sum(f['selected_message_rows'] for f in report['files'])
    report['finished_at'] = now()
    for p in dataset.rglob('*.db'):
        conn = sqlite3.connect(p)
        try:
            if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise RuntimeError(f'子集数据库校验失败：{p.name}')
        finally:
            conn.close()
    write_json(dataset.parent / 'snapshot_report.json', report)
    marker = dataset.parent / 'current.json'
    write_json(marker, {'db_dir': dataset.relative_to(root).as_posix(), 'updated_at': now(), 'chat': username, 'name': name})
    marker.replace(root / '本地数据/current.json')
    write_json(root / '验证结果/最新快照校验.json', report)
    log(f'目标聊天已恢复：{report["messages"]} 条消息；未保存其他会话消息。')
    return report
