"""Offline annual reports for the recovered WeChat 4.x data sources."""
import calendar
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
import re
import sqlite3
from string import Template

import memotrace_local as engine
from desktop_data import emoji_assets
from live_library import message_table
from local_vfs import SourceChangedError
from target_snapshot import ident
from wxManager import MessageType


SYSTEM_TYPES = {MessageType.System, 10002}
SHARD_NAME = re.compile(r'(?:biz_)?message_\d+\.db')
TEMPLATE = engine.DESKTOP_SOURCE / 'app/web_ui/templates/recovery_annual.html'


class YearStats:
    def __init__(self, year):
        self.year = year
        self.start = datetime(year, 1, 1, tzinfo=engine.TZ)
        self.begin = int(self.start.timestamp())
        self.end = int(datetime(year + 1, 1, 1, tzinfo=engine.TZ).timestamp())
        self.days = Counter()
        self.hours = Counter()
        self.types = Counter()
        self.ranking = Counter()
        self.words = Counter()
        self.emojis = Counter()
        self.total = self.sent = self.received = self.characters = 0
        self.first = self.last = None

    def add(self, bucket, kind, amount, sent, named, first, last):
        day, hour = divmod(bucket, 24)
        self.days[day] += amount
        self.hours[hour] += amount
        self.types[MessageType.name(kind)] += amount
        self.total += amount
        if kind not in SYSTEM_TYPES:
            self.sent += sent
            self.received += named - sent
        self.first = first if self.first is None else min(self.first, first)
        self.last = last if self.last is None else max(self.last, last)

    def merge(self, other):
        if not other.total:
            return
        self.days.update(other.days)
        self.hours.update(other.hours)
        self.types.update(other.types)
        self.ranking.update(other.ranking)
        self.total += other.total
        self.sent += other.sent
        self.received += other.received
        self.first = other.first if self.first is None else min(self.first, other.first)
        self.last = other.last if self.last is None else max(self.last, other.last)


def conversation_stats(year, rows, account):
    import jieba

    result = YearStats(year)
    names = {}
    tokenizer = jieba.Tokenizer()
    cache = engine.ROOT / '运行缓存'
    cache.mkdir(exist_ok=True)
    tokenizer.tmp_dir = str(cache)
    stopwords = {word.strip().casefold() for word in
                 (engine.DESKTOP_SOURCE / 'app/resources/data/stopwords.txt').read_text(
                     encoding='utf-8-sig').splitlines()}
    for row in rows:
        stamp = int(row.get('timestamp') or 0)
        if not result.begin <= stamp < result.end:
            continue
        kind = row['type']
        sender = str(row.get('sender_id') or '')
        named = sender not in ('', 'None') and not sender.isdecimal() and kind not in SYSTEM_TYPES
        result.add((stamp - result.begin) // 3600, kind, 1, int(named and sender == account), int(named), stamp, stamp)
        if named:
            names[sender] = row.get('sender') or sender
            result.ranking[sender] += 1
        if kind in (MessageType.Text, MessageType.Quote) and row.get('parse_status') == 'ok':
            value = str(row.get('text') or '')
            result.characters += len(value)
            for token in re.findall(r'\[([^\[\]\r\n]{1,20})\]', value):
                if token in emoji_assets():
                    result.emojis[token] += 1
            text = re.sub(r'https?://\S+|\[[^\[\]\r\n]{1,20}\]', ' ', value)
            result.words.update(token for word in tokenizer.cut(text, HMM=False)
                                if 2 <= len(word) <= 18 and word.isalpha() and (token := word.casefold()) not in stopwords)
    return result, names


def summarize_tables(query, tables, year, account):
    """Aggregate message metadata in SQLite without selecting message bodies."""
    result = YearStats(year)
    has_names = bool(list(query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='Name2Id'")))
    sender = ("SUM(CASE WHEN n.user_name=? THEN 1 ELSE 0 END) AS sent, COUNT(NULLIF(n.user_name,'')) AS named"
              if has_names else '0 AS sent, 0 AS named')
    join = 'LEFT JOIN Name2Id n ON m.real_sender_id=n.rowid' if has_names else ''
    params = (result.begin, account, result.begin, result.end) if has_names else (result.begin, result.begin, result.end)
    for table, username in tables:
        sql = (f'SELECT (CAST(m.create_time AS INTEGER)-?) / 3600 AS bucket, m.local_type AS kind, '
               f'COUNT(*) AS amount, {sender}, MIN(m.create_time) AS first, MAX(m.create_time) AS last '
               f'FROM {ident(table)} m {join} WHERE m.create_time >= ? AND m.create_time < ? '
               'GROUP BY bucket, kind')
        for row in query(sql, params):
            amount = row['amount']
            result.add(row['bucket'], row['kind'], amount, row['sent'], row['named'], row['first'], row['last'])
            result.ranking[username] += amount
    return result


def personal_stats(year, account, *, library=None, snapshots=(), log=print):
    result = YearStats(year)
    names = {}
    if library is not None:
        by_file = defaultdict(list)
        for session in library.sessions:
            user = session['username']
            names[user] = session['name']
            for relative in library.table_paths.get(session['table'], ()):
                by_file[relative].append((session['table'], user))
        for index, (relative, tables) in enumerate(sorted(by_file.items()), start=1):
            log(f'正在统计微信消息分库 {index} / {len(by_file)}…')
            for attempt in range(2):
                try:
                    with library.open(relative) as db:
                        shard = summarize_tables(db.query, tables, year, account)
                        db.view.assert_stable()
                    break
                except SourceChangedError:
                    if attempt:
                        raise
                    log(f'微信正在同步消息，重读分库 {index} / {len(by_file)}…')
            result.merge(shard)
        return result, names, '微信本机'

    for index, snapshot in enumerate(snapshots, start=1):
        log(f'正在统计本地快照 {index} / {len(snapshots)}…')
        names[snapshot.username] = snapshot.name
        table = message_table(snapshot.username)
        for path in sorted((snapshot.db_dir / 'message').glob('*.db')):
            if not SHARD_NAME.fullmatch(path.name):
                continue
            with closing(engine.readonly(path)) as db:
                db.row_factory = sqlite3.Row
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    result.merge(summarize_tables(db.execute, [(table, snapshot.username)], year, account))
    return result, names, f'本地快照 · {len(snapshots):,} 个已保存会话'


def activity_calendar(stats):
    peak = max(stats.days.values())
    blocks = []
    for month in range(1, 13):
        cells = []
        for week in calendar.Calendar().monthdayscalendar(stats.year, month):
            for day in week:
                if not day:
                    cells.append('<span class="blank"></span>')
                    continue
                date = stats.start.replace(month=month, day=day).date()
                amount = stats.days[(date - stats.start.date()).days]
                level = (amount * 4 + peak - 1) // peak
                label = f'{date.isoformat()} · {amount:,} 条'
                cells.append(f'<span class="day level-{level}" title="{label}" aria-label="{label}"></span>')
        blocks.append(f'<div class="month"><h3>{month} 月</h3><div class="month-grid">{"".join(cells)}</div></div>')
    return ''.join(blocks)


def histogram(points, title):
    maximum = max(amount for _, amount in points) or 1
    bars = []
    for label, amount in points:
        pct = 100 * amount / maximum
        value = f'{label} · {amount:,} 条'
        bars.append(f'<div class="bar-slot" title="{value}" aria-label="{value}">'
                    f'<div class="bar-track"><span class="bar-fill" style="--height:{pct:.2f}%"></span></div>'
                    f'<span class="axis-label">{label}</span></div>')
    return f'<div class="histogram" aria-label="{title}" role="img">{"".join(bars)}</div>'


def ranking(items):
    if not items:
        return '<p class="quiet">此范围内没有可统计的记录。</p>'
    maximum = max(amount for _, amount in items)
    return '<ol class="ranking">' + ''.join(
        f'<li style="--width:{100*amount/maximum:.2f}%"><span>{escape(name)}</span>'
        f'<strong>{amount:,}</strong></li>' for name, amount in items) + '</ol>'


def render_report(stats, name, source, names, *, personal=False):
    if not stats.total:
        raise ValueError(f'{stats.year} 年在所选来源里没有可用消息。')
    months = Counter()
    for day, amount in stats.days.items():
        months[(stats.start + timedelta(days=day)).month] += amount
    busiest = min(stats.days, key=lambda day: (-stats.days[day], day))
    peak_day = (stats.start + timedelta(days=busiest)).strftime('%m 月 %d 日')
    peak_month = min(months, key=lambda month: (-months[month], month))
    peak_hour = min(stats.hours, key=lambda hour: (-stats.hours[hour], hour))
    other = stats.total - stats.sent - stats.received
    first = datetime.fromtimestamp(stats.first, engine.TZ).strftime('%Y-%m-%d')
    last = datetime.fromtimestamp(stats.last, engine.TZ).strftime('%Y-%m-%d')
    ranks = [(names.get(key, key), amount) for key, amount in stats.ranking.most_common(10)]
    scope = f'{len(stats.ranking):,} 个活跃会话' if personal else f'{len(stats.ranking):,} 位有记录的发言者'
    words = ''
    if stats.words or stats.emojis:
        cloud = ''.join(f'<span class="word" style="--font:{min(34, 13 + round(5 * amount ** 0.35))}px">'
                        f'{escape(token)} <small>{amount:,}</small></span>'
                        for token, amount in stats.words.most_common(22))
        emojis = ' · '.join(f'{escape(token)} {amount:,} 次' for token, amount in stats.emojis.most_common(5))
        words = ('<section class="panel"><h2>聊天里的常用词</h2>'
                 f'<p class="quiet">{stats.characters:,} 个文本字符 · 本地分词统计</p>'
                 f'<div class="cloud">{cloud}</div>'
                 + (f'<p class="quiet">常用内置表情 · {emojis}</p>' if emojis else '') + '</section>')
    template = Template(TEMPLATE.read_text(encoding='utf-8'))
    return template.substitute(
        year=stats.year, name=escape(name), source=escape(source), first=first, last=last, scope=scope,
        total=f'{stats.total:,}', active_days=f'{len(stats.days):,}', sent=f'{stats.sent:,}', received=f'{stats.received:,}',
        other=f'{other:,}', peak_day=peak_day, peak_day_count=f'{stats.days[busiest]:,}',
        peak_month=f'{peak_month} 月', peak_month_count=f'{months[peak_month]:,}',
        peak_hour=f'{peak_hour:02d}:00', peak_hour_count=f'{stats.hours[peak_hour]:,}',
        months=histogram([(f'{m}月', months[m]) for m in range(1, 13)], '每月消息量'),
        hours=histogram([(f'{h:02d}', stats.hours[h]) for h in range(24)], '每小时消息量'),
        calendar=activity_calendar(stats), rank_title='会话排行' if personal else '发言排行',
        ranking=ranking(ranks), types=ranking(stats.types.most_common()), words=words,
        generated=engine.now()[:16].replace('T', ' '))


def write_report(stats, name, source, names, directory, *, personal=False):
    content = render_report(stats, name, source, names, personal=personal)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(engine.TZ).strftime('%Y%m%d_%H%M%S_%f')
    path = directory / f'{stats.year}年度报告_{stamp}.html'
    stream = path.open('x', encoding='utf-8')
    try:
        with stream:
            stream.write(content)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return path, stats.total
