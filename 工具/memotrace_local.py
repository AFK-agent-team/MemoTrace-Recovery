"""MemoTrace local recovery entry point. Local snapshots, search, export, GUI.

The upstream wxManager handles typed messages; raw rows are always preserved.
No network service is started and no chat content is sent to external services.
"""
from pathlib import Path
import argparse
import base64
import collections
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '源码/MemoTrace'
DESKTOP_SOURCE = ROOT / '源码/WeChatMsg-2.0.5'
TZ = timezone(timedelta(hours=8))
sys.path.insert(0, str(SOURCE))
DEFAULT_CONFIG = {
    'wechat_exe': r'C:\Program Files\Tencent\Weixin\Weixin.exe',
    'account_dir': '',
    'self_username': '',
    'default_chat': '',
}


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def config():
    path = ROOT / '本地配置.json'
    if not path.exists():
        write_json(path, DEFAULT_CONFIG)
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_value), encoding='utf-8')


def json_value(value):
    if isinstance(value, bytes):
        return {'encoding': 'base64', 'data': base64.b64encode(value).decode('ascii')}
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.text_factory = lambda b: b.decode('utf-8') if _utf8(b) else b
    conn.execute('PRAGMA query_only=ON')
    return conn


def _utf8(data):
    try:
        data.decode('utf-8')
        return True
    except UnicodeDecodeError:
        return False


@dataclass(frozen=True)
class Snapshot:
    db_dir: Path
    username: str
    updated_at: str
    name: str = ''
    message_count: int | None = None


def current_snapshot():
    path = ROOT / '本地数据/current.json'
    if not path.exists():
        raise RuntimeError('尚未建立快照，请先运行“刷新微信数据”。')
    data = json.loads(path.read_text(encoding='utf-8'))
    return Snapshot(ROOT / data['db_dir'], data.get('chat') or '', data.get('updated_at') or '',
                    data.get('name') or '')


def current_data():
    return current_snapshot().db_dir


def saved_snapshots(cfg):
    """List the latest completed snapshot per conversation for this account."""
    snapshots = {}
    paths = (ROOT / '本地数据/快照').glob('*/snapshot_report.json')
    for path in sorted(paths, reverse=True):
        db_dir = path.parent / 'db_storage'
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
            info = json.loads((db_dir / 'info.json').read_text(encoding='utf-8'))
            scope = report['scope']
            if not scope['only_selected_chat'] or info['username'] != cfg['self_username']:
                continue
            if not (db_dir / 'contact/contact.db').is_file():
                continue
            username = scope['username']
            snapshots.setdefault(username, Snapshot(db_dir, username, report['finished_at'],
                                                    scope['name'] or username, report['messages']))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return snapshots


def refresh(log=print, chat=None, *, library=None):
    from target_snapshot import scope_snapshot
    cfg = config()
    return scope_snapshot(ROOT, cfg, chat or cfg.get('default_chat', ''), log, library=library)


def live_sessions(log=print):
    from live_library import LiveLibrary
    return LiveLibrary(config(), log)


def live_messages(library, session, log=print):
    from local_vfs import SourceChangedError
    username = session['username']
    try:
        return library.read(username, log)
    except SourceChangedError:
        log('微信正在同步消息，重新读取一次…')
        return library.read(username, log)


def contacts(query='', *, snapshot=None):
    snapshot = snapshot or current_snapshot()
    with closing(readonly(snapshot.db_dir / 'contact/contact.db')) as conn:
        rows = conn.execute('SELECT username, nick_name, remark FROM contact ORDER BY COALESCE(NULLIF(remark,\'\'),nick_name)').fetchall()
    query = query.casefold()
    return [{'username': u, 'nickname': n or '', 'remark': r or '', 'name': r or n or u,
             'kind': '群聊' if u.endswith('@chatroom') else '个人'}
            for u, n, r in rows if not query or query in f'{u} {n} {r}'.casefold()]


def resolve_chat(value, *, snapshot=None):
    snapshot = snapshot or current_snapshot()
    entries = contacts(snapshot=snapshot)
    hits = [c for c in entries if value == c['username']]
    if not hits:
        hits = [c for c in entries if value in (c['nickname'], c['remark'])]
    if not hits and value == snapshot.username and snapshot.name:
        return {'username': value, 'name': snapshot.name, 'nickname': '', 'remark': '',
                'kind': '群聊' if value.endswith('@chatroom') else '个人'}
    if len(hits) != 1:
        raise ValueError(f'需要唯一的准确名称或 username；当前匹配 {len(hits)} 个，请在联系人列表选择。')
    return hits[0]


def raw_messages(username, *, snapshot=None):
    snapshot = snapshot or current_snapshot()
    if snapshot.username != username:
        raise ValueError('当前快照只包含此前选定的聊天；请先刷新此目标，再读取或导出。')
    table = 'Msg_' + hashlib.md5(username.encode('utf-8')).hexdigest()
    rows = []
    paths = (snapshot.db_dir / 'message').glob('*.db')
    for path in sorted(p for p in paths if re.fullmatch(r'(?:biz_)?message_\d+\.db', p.name)):
        with closing(readonly(path)) as conn:
            if not conn.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone():
                continue
            conn.row_factory = sqlite3.Row
            result = conn.execute(f'SELECT msg.*, names.user_name AS sender_username FROM "{table}" msg '
                                  'LEFT JOIN Name2Id names ON msg.real_sender_id=names.rowid').fetchall()
            for row in result:
                d = dict(row)
                d['source_db'] = path.name
                rows.append(d)
    return sorted_message_rows(rows)


def sorted_message_rows(rows):
    # Preserve every physical row. Repeated IDs remain visible for reconciliation.
    return sorted(rows, key=lambda r: (r.get('create_time') or 0, r.get('sort_seq') or 0,
                                       r['source_db'], r['local_id']))


def open_manager(snapshot=None):
    from wxManager.manager_v4 import DataBaseV4
    manager = DataBaseV4()
    snapshot = snapshot or current_snapshot()
    if not manager.init_database(str(snapshot.db_dir)):
        raise RuntimeError('wxManager 初始化失败；请检查快照或重新刷新。')
    return manager


def close_manager(manager):
    for item in vars(manager).values():
        if hasattr(item, 'open_flag') and hasattr(item, 'close'):
            item.close()


def decode_content(value):
    if isinstance(value, bytes):
        if value.startswith(b'\x28\xb5\x2f\xfd'):
            import zstandard
            value = zstandard.ZstdDecompressor().decompress(value, max_output_size=64 * 1024 * 1024)
        return value.decode('utf-8', errors='replace').rstrip('\x00')
    return value or ''


def readable_message(msg, original=''):
    if hasattr(msg, 'content'):
        return msg.content
    if msg.type == 3:
        return '[图片]'
    if msg.type == 43:
        return f'[视频] 时长 {getattr(msg, "duration", 0)} 秒'
    if msg.type == 34:
        return '[语音] ' + (getattr(msg, 'audio_text', '') or '')
    if msg.type == 47:
        return '[表情包] ' + (getattr(msg, 'description', '') or '')
    if msg.type == 25769803825:
        return '[文件] ' + (getattr(msg, 'file_name', '') or '')
    if getattr(msg, 'title', ''):
        return '\n'.join(str(v) for v in (msg.title, getattr(msg, 'description', ''), getattr(msg, 'href', '')) if v)
    if getattr(msg, 'description', ''):
        return str(msg.description)
    return original


def nested_record(msg, source, index, level=0):
    from wxManager import MessageType
    record = {'time': msg.str_time, 'timestamp': msg.timestamp, 'sender': msg.display_name,
              'sender_id': msg.sender_id, 'type': msg.type, 'type_name': MessageType.name(msg.type),
              'text': readable_message(msg), 'local_id': f'{source["local_id"]}_{index}',
              'source_db': source['source_db'], 'server_id': str(msg.server_id),
              'attachments': [], 'links': [], 'nested_messages': []}
    for field in ('path', 'thumb_path', 'file_name', 'file_size', 'duration', 'href', 'url'):
        value = getattr(msg, field, None)
        if value is not None:
            record[field] = value
    record['links'] = re.findall(r'https?://[^\s<>"\x00]+', record['text'])
    if level < 16:
        record['nested_messages'] = [nested_record(child, record, i, level + 1) for i, child in enumerate(getattr(msg, 'messages', []))]
    return record


def parsed_messages(chat, raw=None, *, snapshot=None, include_audio=True):
    snapshot = snapshot or current_snapshot()
    raw = raw if raw is not None else raw_messages(chat['username'], snapshot=snapshot)
    manager = open_manager(snapshot)
    try:
        yield from parse_records(chat, raw, manager, include_audio=include_audio)
    finally:
        close_manager(manager)


def message_values(row, sender, time_text):
    return (row['local_id'], row.get('server_id'), row['local_type'], row.get('sort_seq'), sender,
            row.get('create_time') or 0, time_text, row.get('status'), row.get('upload_status'),
            row.get('server_seq'), row.get('origin_source'), row.get('source'), row.get('message_content'),
            row.get('compress_content'), row.get('packed_info_data') or b'')


def parse_records(chat, raw, manager, *, include_audio=True):
    from wxManager.parser.wechat_v4 import FACTORY_REGISTRY, Singleton
    from wxManager import MessageType
    try:
        members = manager.get_chatroom_members(chat['username']) if chat['kind'] == '群聊' else {}
        Singleton.contacts = {}
        Singleton.messages.messages.clear()
        Singleton.set_contacts(members)
        for r in raw:
            timestamp = r.get('create_time') or 0
            time_text = datetime.fromtimestamp(timestamp, TZ).strftime('%Y-%m-%d %H:%M:%S')
            sender = r.get('sender_username') or str(r.get('real_sender_id', ''))
            content = decode_content(r.get('message_content'))
            prefix = f'{sender}:'
            if content.startswith(prefix):
                content = content[len(prefix):].lstrip('\n')
            record = {'timestamp': timestamp, 'time': time_text, 'local_id': r['local_id'],
                      'server_id': str(r.get('server_id', 0)), 'source_db': r['source_db'],
                      'sort_seq': r.get('sort_seq'), 'sender_id': sender, 'sender': sender,
                      'type': r['local_type'], 'type_name': MessageType.name(r['local_type']),
                      'text': content, 'parse_status': 'raw', 'attachments': [], 'links': []}
            values = message_values(r, sender, time_text)
            try:
                msg = FACTORY_REGISTRY.get(r['local_type'], FACTORY_REGISTRY[-1]).create(values, chat['username'], manager)
                record['sender'] = msg.display_name
                record['parse_status'] = 'ok'
                for field in ('content', 'title', 'description', 'audio_text', 'href', 'path', 'thumb_path',
                              'file_name', 'file_size', 'duration', 'md5', 'url'):
                    value = getattr(msg, field, None)
                    if value is not None:
                        record[field] = value
                record['text'] = readable_message(msg, content)
                if r['local_type'] == MessageType.System:
                    record['sender'] = '系统'
                quote = getattr(msg, 'quote_message', None)
                if quote:
                    record['quote'] = {'sender': quote.display_name, 'time': quote.str_time,
                                       'text': readable_message(quote), 'server_id': str(quote.server_id)}
                record['nested_messages'] = [nested_record(child, record, i) for i, child in enumerate(getattr(msg, 'messages', []))]
                if include_audio and r['local_type'] == MessageType.Audio:
                    record['_audio_buffer'] = manager.get_media_buffer(r.get('server_id'))
            except Exception as exc:
                record['parse_status'] = 'fallback'
                record['parse_error'] = type(exc).__name__ + ': ' + str(exc)
            text = record.get('text') or ''
            record['links'] = list(dict.fromkeys(re.findall(r'https?://[^\s<>"\x00]+', text) + ([record['href']] if record.get('href') else [])))
            yield record
    finally:
        Singleton.contacts = {}
        Singleton.messages.messages.clear()


def safe_name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')[:100] or '未命名'


def save_attachments(record, output, cfg, image_decoder=None):
    for child in record.get('nested_messages', []):
        save_attachments(child, output, cfg, image_decoder)
    account = Path(cfg['account_dir']).resolve()
    kind = {3: '图片', 43: '视频', 25769803825: '文件', 34: '语音', 47: '表情'}.get(record['type'])
    if not kind:
        return
    destination = output / '附件' / kind
    destination.mkdir(parents=True, exist_ok=True)
    ident = f"{record['source_db']}_{record['local_id']}"
    if record['type'] == 34:
        buffer = record.pop('_audio_buffer', None)
        if buffer:
            path = destination / f'{ident}.silk'
            path.write_bytes(buffer)
            record['attachments'].append({'status': 'saved', 'path': path.relative_to(output).as_posix(), 'format': 'silk'})
        else:
            record['attachments'].append({'status': 'missing_local_audio'})
        return
    for field in ('path', 'thumb_path'):
        relative = record.get(field)
        if not relative:
            continue
        src = (account / relative).resolve()
        if not src.is_relative_to(account):
            record['attachments'].append({'status': 'rejected_outside_account', 'field': field})
            continue
        if not src.is_file():
            record['attachments'].append({'status': 'missing_local_file', 'field': field, 'source_path': str(src)})
            continue
        dst = destination / f'{ident}_{field}_{safe_name(src.name)}'
        shutil.copy2(src, dst)
        attachment = {'status': 'saved_encrypted' if dst.suffix == '.dat' else 'saved',
                      'field': field, 'path': dst.relative_to(output).as_posix(),
                      'source_path': str(src), 'bytes': dst.stat().st_size,
                      'sha256': hashlib.sha256(dst.read_bytes()).hexdigest()}
        if dst.suffix == '.dat' and image_decoder:
            try:
                decoded = image_decoder(src, destination, f'{ident}_{field}')
                if decoded:
                    attachment['decoded_path'] = str(Path(decoded).relative_to(output).as_posix())
                    attachment['status'] = 'decoded'
            except Exception as exc:
                attachment['decode_error'] = type(exc).__name__
                attachment['decode_note'] = str(exc)
        record['attachments'].append(attachment)
    if not record['attachments']:
        record['attachments'].append({'status': 'no_local_path_resolved'})


def export_chat(value, output=None, log=print, decode_images=False, *, snapshot=None):
    import csv
    snapshot = snapshot or current_snapshot()
    chat = resolve_chat(value, snapshot=snapshot)
    raw = raw_messages(chat['username'], snapshot=snapshot)
    if output is None:
        output = ROOT.parent / chat['kind'] / safe_name(chat['name']) / '05_原始记录' / ('导出_' + datetime.now(TZ).strftime('%Y%m%d_%H%M%S'))
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    with (output / '原始消息.jsonl').open('w', encoding='utf-8') as f:
        for row in raw:
            f.write(json.dumps(row, ensure_ascii=False, default=json_value) + '\n')
    cfg = config()
    records = []
    # Image adapter is optional: unknown formats are kept as .dat with explicit status.
    image_decoder = None
    try:
        from local_media import ImageDecoder
        image_decoder = ImageDecoder(Path(cfg['account_dir']), cfg['wechat_exe'])
        image_decoder.allow_scan = decode_images
    except ImportError:
        pass
    for i, record in enumerate(parsed_messages(chat, raw, snapshot=snapshot), 1):
        save_attachments(record, output, cfg, image_decoder)
        record.pop('_audio_buffer', None)
        records.append(record)
        if i % 200 == 0:
            log(f'已整理 {i}/{len(raw)} 条消息')
    with (output / '消息.jsonl').open('w', encoding='utf-8') as f:
        for row in records:
            f.write(json.dumps(row, ensure_ascii=False, default=json_value) + '\n')
    with (output / '消息.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['time', 'sender', 'type_name', 'text', 'server_id', 'source_db', 'local_id', 'parse_status'], extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)
    with (output / '时间线.md').open('w', encoding='utf-8') as f:
        f.write(f'# {chat["name"]} · 本机可用记录时间线\n\n')
        f.write('范围以当前本机快照为准，不能据此断言已覆盖建群以来的全部历史。\n\n')
        date = ''
        for r in records:
            if r['time'][:10] != date:
                date = r['time'][:10]
                f.write(f'## {date}\n\n')
            f.write(f'### {r["time"][11:]} · {r["sender"]} · {r["type_name"]}\n\n')
            f.write((r['text'] or f'[{r["type_name"]}]') + '\n\n')
            for a in r['attachments']:
                p = a.get('decoded_path') or a.get('path')
                if p:
                    f.write(f'- [附件]({p}) · {a["status"]}\n\n')
            if r.get('quote'):
                q = r['quote']
                f.write('> 引用 ' + q['sender'] + '：' + q['text'].replace('\n', '\n> ') + '\n\n')
            for child in flatten_records(r.get('nested_messages', [])):
                f.write(f'> 转发 · {child["time"]} · {child["sender"]}：{child["text"]}\n\n')
                for a in child['attachments']:
                    p = a.get('decoded_path') or a.get('path')
                    if p:
                        f.write(f'- [转发内附件]({p}) · {a["status"]}\n\n')
    with (output / '资源索引.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['时间', '发送者', '类型', '来源消息', '链接或附件', '状态'])
        for r in flatten_records(records):
            for url in r['links']:
                writer.writerow([r['time'], r['sender'], '链接', r['server_id'], url, '未自动访问'])
            for a in r['attachments']:
                writer.writerow([r['time'], r['sender'], r['type_name'], r['server_id'], a.get('decoded_path') or a.get('path') or a.get('source_path', ''), a['status']])
    render_html(chat, records, output)
    summary = {'chat': chat, 'exported_at': now(), 'messages': len(records), 'raw_rows': len(raw),
               'first_message': records[0]['time'] if records else None, 'last_message': records[-1]['time'] if records else None,
               'message_types': dict(collections.Counter(r['type_name'] for r in records)),
               'parse_status': dict(collections.Counter(r['parse_status'] for r in records)),
               'attachment_status': dict(collections.Counter(a['status'] for r in flatten_records(records) for a in r['attachments'])),
               'image_messages_with_viewable_image': sum(any(a.get('decoded_path') for a in r['attachments']) for r in records if r['type'] == 3),
               'image_messages_with_only_thumbnail': sum(any(a.get('decoded_path') and a.get('field') == 'thumb_path' for a in r['attachments']) and not any(a.get('decoded_path') and a.get('field') == 'path' for a in r['attachments']) for r in records if r['type'] == 3),
               'source_databases': sorted({r['source_db'] for r in records}),
               'coverage': '本机快照范围；建群时间及设备间历史完整性尚未核实'}
    write_json(output / '导出校验.json', summary)
    log(f'完成：{len(records)} 条消息，结果位于 {output}')
    return output, summary


def flatten_records(records):
    for record in records:
        yield record
        yield from flatten_records(record.get('nested_messages', []))


def render_html(chat, records, output):
    e = html.escape
    blocks = []
    for r in flatten_records(records):
        media = []
        for a in r['attachments']:
            p = a.get('decoded_path') or a.get('path')
            if p:
                from urllib.parse import quote
                url = quote(p, safe='/')
                if a.get('decoded_path') or Path(p).suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.webp'}:
                    media.append(f'<a href="{url}"><img loading="lazy" src="{url}" alt="图片附件"></a>')
                elif Path(p).suffix.lower() == '.mp4':
                    media.append(f'<video controls preload="none" src="{url}"></video>')
                else:
                    media.append(f'<a href="{url}">附件 · {e(a["status"])}</a>')
        body = e(str(r['text'] or '[' + r['type_name'] + ']'))
        if r.get('quote'):
            body += '\n\n引用 ' + e(r['quote']['sender']) + '：' + e(r['quote']['text'])
        blocks.append(f'<article><small>{e(r["time"])} · {e(r["sender"])} · {e(r["type_name"])}</small><pre>{body}</pre>{" ".join(media)}</article>')
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'">
<title>{e(chat['name'])} · 本地聊天记录</title><style>body{{font:16px/1.65 'Microsoft YaHei',sans-serif;background:#f4f5f6;color:#20242a;max-width:900px;margin:40px auto;padding:20px}}article{{background:white;padding:18px 24px;margin:12px 0;border-radius:8px}}small{{color:#69717a}}pre{{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}}img,video{{max-width:100%;max-height:480px}}a{{color:#176962}}</style>
<h1>{e(chat['name'])}</h1><p>{len(records)} 条本机记录。导出时间 {e(now())}。未核实建群以来的完整性。</p>{''.join(blocks)}</html>'''
    (output / '聊天记录.html').write_text(page, encoding='utf-8')


def verify():
    result = []
    for path in current_data().rglob('*.db'):
        with closing(readonly(path)) as conn:
            check = conn.execute('PRAGMA quick_check').fetchall()
        result.append({'file': path.relative_to(current_data()).as_posix(), 'ok': check == [('ok',)]})
    write_json(ROOT / '验证结果/只读复核.json', result)
    if not all(r['ok'] for r in result):
        raise RuntimeError('部分数据库完整性检查失败')
    print(f'{len(result)} databases verified; all opened read-only.')


def run_desktop():
    if not (DESKTOP_SOURCE / 'app/ui/recovery.py').is_file():
        raise RuntimeError('2.0.5 桌面源码缺失。请检查「源码/WeChatMsg-2.0.5」。')
    sys.path.insert(0, str(DESKTOP_SOURCE))
    from app.ui.recovery import run

    return run()


def main():
    os.chdir(ROOT)
    parser = argparse.ArgumentParser(description='MemoTrace 本地恢复版 · 微信只读快照、浏览、搜索、导出')
    parser.add_argument('command', choices=['refresh', 'contacts', 'export', 'members', 'verify', 'gui', 'gui-tk'])
    parser.add_argument('--chat')
    parser.add_argument('--query', default='')
    parser.add_argument('--output')
    parser.add_argument('--snapshot', action='store_true', help='成员导出使用旧快照；默认读取已登录微信')
    parser.add_argument('--decode-images', action='store_true', help='允许只读读取微信进程以解码目标聊天图片')
    args = parser.parse_args()
    if args.command in ('refresh', 'export', 'members'):
        args.chat = args.chat or config().get('default_chat')
        if not args.chat:
            parser.error('请用 --chat 指定会话，或在本地配置中设置 default_chat。')
    if args.command == 'refresh':
        refresh(chat=args.chat)
    elif args.command == 'contacts':
        print(json.dumps(contacts(args.query), ensure_ascii=False, indent=2))
    elif args.command == 'export':
        export_chat(args.chat, args.output, decode_images=args.decode_images)
    elif args.command == 'members':
        from group_members import export_members
        report, stem = export_members(args.chat, live=not args.snapshot, output_dir=args.output)
        print(f'已导出 {report["member_count"]} 位群成员，未解析姓名 {report["unresolved_names"]}：{stem}')
    elif args.command == 'verify':
        verify()
    elif args.command == 'gui-tk':
        from local_gui import run
        run()
    else:
        try:
            run_desktop()
        except Exception as exc:
            if sys.stderr is None and os.name == 'nt':
                from ctypes import windll
                windll.user32.MessageBoxW(None, str(exc), '留痕本地恢复版启动失败', 0x10)
            raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'失败：{type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
