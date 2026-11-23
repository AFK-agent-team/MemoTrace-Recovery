"""Export one explicitly selected group's member roster, not message senders."""
import argparse
from datetime import datetime, timezone, timedelta
import importlib.util
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))


def roster_from_query(query, group):
    hits = query('SELECT username,nick_name,remark FROM contact WHERE username=? OR nick_name=? OR remark=?', (group,) * 3)
    if len(hits) != 1 or not hits[0]['username'].endswith('@chatroom'):
        raise ValueError(f'群名须唯一匹配：当前匹配 {len(hits)} 项；可改用群 username。')
    chat = hits[0]
    rows = query('SELECT ext_buffer FROM chat_room WHERE username=?', (chat['username'],))
    if len(rows) != 1:
        raise ValueError('没有群成员表数据，不能以发言者列表替代。')
    path = ROOT / '源码/MemoTrace/wxManager/parser/util/protocbuf/roomdata_pb2.py'
    spec = importlib.util.spec_from_file_location('memotrace_roster_pb2', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    room = module.ChatRoomData()
    room.ParseFromString(rows[0]['ext_buffer'])
    ids = list(dict.fromkeys(m.wxID for m in room.members if m.wxID))
    if not ids:
        raise ValueError('群成员表为空，不能宣称导出了全体成员。')
    contacts = {}
    for start in range(0, len(ids), 100):
        batch = ids[start:start + 100]
        for contact in query('SELECT username,nick_name,remark FROM contact WHERE username IN (' + ','.join('?' for _ in batch) + ')', batch):
            contacts[contact['username']] = contact
    seen, members = set(), []
    for m in room.members:
        if not m.wxID or m.wxID in seen:
            continue
        seen.add(m.wxID)
        contact = contacts.get(m.wxID, {})
        nickname, remark = contact.get('nick_name') or '', contact.get('remark') or ''
        display = m.displayName or remark or nickname or m.wxID
        members.append({'index': len(members) + 1, 'username': m.wxID, 'group_nickname': m.displayName,
                        'nickname': nickname, 'contact_remark': remark, 'display_name': display,
                        'name_resolved': bool(m.displayName or remark or nickname),
                        'contact_record_present': m.wxID in contacts})
    return {'group_name': chat['nick_name'] or chat['remark'] or chat['username'],
            'group_username': chat['username'], 'member_count': len(members),
            'raw_member_entries': len(room.members), 'invalid_empty_member_ids': sum(not m.wxID for m in room.members),
            'unresolved_names': sum(not m['name_resolved'] for m in members),
            'missing_contact_records': sum(not m['contact_record_present'] for m in members),
            'same_name_duplicates_preserved': len(members) - len({m['display_name'] for m in members}),
            'members': members}


def export_members(group, *, live=True, output_dir=None):
    started = datetime.now(TZ).isoformat(timespec='seconds')
    if live:
        from local_crypto import find_keys
        from local_vfs import ScopedDB
        cfg = json.loads((ROOT / '本地配置.json').read_text(encoding='utf-8'))
        original = Path(cfg['account_dir']) / 'db_storage'
        rel = 'contact/contact.db'
        keys = find_keys(original, cfg['wechat_exe'], timeout=60, relative_files=[rel])
        if rel not in keys:
            raise RuntimeError('当前微信联系人库密钥未通过认证；没有生成名单。')
        with ScopedDB(original / rel, keys[rel]) as db:
            report = roster_from_query(db.query, group)
            source_stamp = str(db.view.stamp)
        source = {'mode': 'live_readonly', 'database': str(original / rel), 'read_started_at': started,
                  'file_state': source_stamp, 'keys_saved': False}
    else:
        marker = json.loads((ROOT / '本地数据/current.json').read_text(encoding='utf-8'))
        path = ROOT / marker['db_dir'] / 'contact/contact.db'
        conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        try:
            report = roster_from_query(lambda sql, params=(): [dict(r) for r in conn.execute(sql, params)], group)
        finally:
            conn.close()
        source = {'mode': 'saved_snapshot', 'database': str(path), 'snapshot_at': marker['updated_at']}
    report.update({'exported_at': datetime.now(TZ).isoformat(timespec='seconds'), 'source': source,
                   'ui_member_count': None, 'ui_count_verified': False,
                   'scope': '本机已同步群成员表的全部非空唯一成员ID；不是历史发言者；尚待与界面人数核对',
                   'name_semantics': '群昵称、联系人备注和微信昵称分列；不保证为真实姓名；同名不同ID成员保留'})
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', report['group_name']).strip(' .')
    output = Path(output_dir).resolve() if output_dir else ROOT.parent / '群聊' / safe / '群成员名单'
    output.mkdir(parents=True, exist_ok=True)
    stem = output / ('群成员_' + datetime.now(TZ).strftime('%Y%m%d_%H%M%S_%f'))
    stem.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    stem.with_suffix('.txt').write_text('\n'.join(m['display_name'] for m in report['members']) + '\n', encoding='utf-8')
    # Numbered reading copy preserves duplicate names and makes counts easy to check.
    stem.with_suffix('.md').write_text('# ' + report['group_name'] + ' · 群成员名单\n\n导出时间：' + report['exported_at']
        + f'\n\n成员数：{report["member_count"]}；未解析姓名：{report["unresolved_names"]}。\n\n'
        + '\n'.join(f'{m["index"]}. {m["display_name"]}' for m in report['members']) + '\n', encoding='utf-8')
    return report, stem


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', required=True)
    parser.add_argument('--snapshot', action='store_true', help='读取旧快照；默认只读当前已登录微信')
    parser.add_argument('--output-dir')
    args = parser.parse_args()
    report, stem = export_members(args.group, live=not args.snapshot, output_dir=args.output_dir)
    print(json.dumps({'member_count': report['member_count'], 'unresolved_names': report['unresolved_names'],
                      'missing_contact_records': report['missing_contact_records'], 'mode': report['source']['mode'],
                      'output': str(stem)}, ensure_ascii=False, indent=2))
