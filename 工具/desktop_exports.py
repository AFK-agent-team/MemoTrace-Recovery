"""Portable exports from a selected WeChat 4.x snapshot."""
import csv
from datetime import datetime
import html
from io import BytesIO
from pathlib import Path
import re
import shutil
from urllib.parse import quote

from PIL import Image

import memotrace_local as engine
from desktop_data import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, emoji_html, file_under, read_avatars
from local_media import ImageDecoder


FORMATS = {
    'docx': ('Word 文档', '聊天记录.docx'),
    'csv': ('CSV 表格', '聊天记录.csv'),
    'html': ('微信式 HTML', '聊天记录.html'),
    'txt': ('纯文本 TXT', '聊天记录.txt'),
    'md': ('Markdown 时间线', '聊天记录.md'),
    'ai': ('AI 对话专用 TXT', 'AI对话.txt'),
    'json': ('JSON 数据', '聊天记录.json'),
    'pdf': ('PDF 文档', '聊天记录.pdf'),
    'xlsx': ('Excel 工作簿', '聊天记录.xlsx'),
}
HEADERS = ('时间', '发送者', '消息类型', '内容', '附件', '消息 ID')


def body(row):
    text = str(row.get('text') or f'[{row["type_name"]}]')
    if ref := row.get('quote'):
        text += f'\n引用 {ref["sender"]}：{ref["text"] or ""}'
    for child in row.get('nested_messages', ()):
        text += f'\n转发 · {child["time"]} · {child["sender"]}\n{body(child)}'
    return text


def attachments(row):
    return [p for item in row.get('attachments', ()) if (p := item.get('decoded_path') or item.get('path'))]


def columns(row):
    return (row['time'], row['sender'], row['type_name'], body(row),
            '\n'.join(attachments(row)), row['server_id'])


def write_table(path, records, kind):
    if kind == 'csv':
        def csv_cell(value):
            value = str(value)
            return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value

        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADERS)
            writer.writerows(tuple(csv_cell(value) for value in columns(row)) for row in records)
    else:
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell

        book = Workbook(write_only=True)
        sheet = book.create_sheet('聊天记录')
        sheet.freeze_panes = 'D2'
        for letter, width in zip('ABCDEF', (22, 23, 15, 75, 37, 24)):
            sheet.column_dimensions[letter].width = width
        sheet.append(HEADERS)
        for row in records:
            cells = []
            for value in columns(row):
                cell = WriteOnlyCell(sheet, value=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))[:32767])
                cell.data_type = 's'
                cells.append(cell)
            sheet.append(cells)
        book.save(path)


def write_text(path, chat, records, kind):
    with path.open('w', encoding='utf-8') as stream:
        if kind != 'ai':
            stream.write(('# ' if kind == 'md' else '') + chat['name'] + '\n\n')
        last_day = ''
        for row in records:
            day = row['time'][:10]
            if kind == 'md' and day != last_day:
                stream.write(f'## {day}\n\n')
                last_day = day
            if kind == 'ai':
                stream.write(f'{row["sender"]}：{body(row)}\n\n')
                continue
            heading = f'{row["time"]}  {row["sender"]}  [{row["type_name"]}]'
            stream.write(('### ' if kind == 'md' else '') + heading + '\n\n' + body(row) + '\n\n')
            for relative in attachments(row):
                stream.write(f'- [附件]({quote(relative, safe="/")})\n' if kind == 'md' else f'附件  {relative}\n')


def first_image(row, folder):
    return next((path for name in attachments(row) if (path := file_under(folder, name))
                 and path.suffix.lower() in IMAGE_EXTENSIONS), None)


def write_docx(path, chat, records):
    from docx import Document
    from docx.shared import Inches

    def xml_text(value):
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))

    document = Document()
    document.add_heading(xml_text(chat['name']), 0)
    for row in records:
        document.add_paragraph(xml_text(f'{row["time"]}  {row["sender"]}  {row["type_name"]}'), style='Caption')
        document.add_paragraph(xml_text(body(row)))
        if preview := first_image(row, path.parent):
            try:
                with Image.open(preview) as image:
                    image.thumbnail((960, 760))
                    png = BytesIO()
                    image.save(png, format='PNG')
                    png.seek(0)
                    document.add_picture(png, width=Inches(min(4.7, max(0.6, image.width / 150))))
            except (OSError, ValueError, Image.DecompressionBombError):
                pass
        for relative in attachments(row):
            document.add_paragraph(xml_text(relative), style='Caption')
    document.save(path)


def write_html(path, chat, records, snapshot, *, pdf=False):
    folder = path.parent
    sender_ids = {row['sender_id'] for row in records}
    avatar_data = read_avatars(sender_ids, snapshot=snapshot) if not pdf else {}
    avatar_paths = {}
    for index, (username, data) in enumerate(avatar_data.items(), start=1):
        try:
            with Image.open(BytesIO(data)) as avatar:
                avatar.thumbnail((96, 96))
                relative = f'头像/{index}.png'
                (folder / '头像').mkdir(exist_ok=True)
                avatar.save(folder / relative, 'PNG')
                avatar_paths[username] = quote(relative, safe='/')
        except (OSError, ValueError, Image.DecompressionBombError):
            continue

    def emoji_url(asset):
        if pdf:
            return asset.as_uri()
        target = folder / '表情' / asset.name
        if not target.is_file():
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(asset, target)
        return quote(target.relative_to(folder).as_posix(), safe='/')

    escape = html.escape
    account = engine.config()['self_username']
    blocks = []
    last_day = ''
    for row in records:
        day = row['time'][:10]
        if day != last_day:
            blocks.append(f'<p class="day">{escape(day)}</p>')
            last_day = day
        who = escape(row['sender'])
        metadata = f'{who} · {escape(row["time"][11:])} · {escape(row["type_name"])}'
        text = emoji_html(body(row), emoji_url)
        media = []
        image_added = False
        for relative in attachments(row):
            asset = file_under(folder, relative)
            if asset is None:
                continue
            url = asset.as_uri() if pdf else quote(relative, safe='/')
            suffix = asset.suffix.lower()
            if suffix in IMAGE_EXTENSIONS and not image_added:
                if pdf:
                    try:
                        with Image.open(asset) as im:
                            width = min(im.width, 360)
                            height = max(1, int(im.height * width / im.width))
                            if height > 260:
                                width, height = max(1, int(width * 260 / height)), 260
                        media.append(f'<p><img src="{url}" width="{width}" height="{height}"></p>')
                    except (OSError, ValueError):
                        pass
                else:
                    media.append(f'<a href="{url}"><img class="media" loading="lazy" src="{url}" alt="图片"></a>')
                image_added = True
            elif not pdf and suffix in VIDEO_EXTENSIONS:
                media.append(f'<video class="media" controls preload="none" src="{url}"></video>')
            elif not pdf and suffix == '.wav':
                media.append(f'<audio controls preload="none" src="{url}"></audio>')
            elif not pdf:
                media.append(f'<p><a href="{url}" download>附件 · {escape(asset.name)}</a></p>')
            else:
                media.append(f'<p>附件 · {escape(asset.name)}</p>')
        avatar = avatar_paths.get(row['sender_id'])
        icon = f'<img class="avatar" src="{avatar}" alt="">' if avatar else '<span class="avatar placeholder"></span>'
        content = f'<div class="bubble">{text}{"".join(media)}</div>'
        if pdf:
            blocks.append(f'<p><b>{metadata}</b></p>{content}')
        else:
            direction = 'own' if row['sender_id'] == account else 'peer'
            blocks.append(f'<article class="{direction}">{icon}<div><small>{metadata}</small>{content}</div></article>')

    name = escape(chat['name'])
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' file: data:; media-src 'self' file:; style-src 'unsafe-inline'">
<title>{name}</title><style>
body{{font:15px/1.6 "Microsoft YaHei",sans-serif;color:#222;background:#eef2f4;max-width:880px;margin:24px auto;padding:0 14px}}
h1{{font-size:22px}} article{{display:flex;gap:10px;margin:14px 0;align-items:flex-start}} article>div{{max-width:78%;min-width:0}}
.own{{flex-direction:row-reverse;text-align:right}} .avatar{{width:40px;height:40px;border-radius:5px;object-fit:cover}} .placeholder{{background:#c8d2d7}}
.day{{text-align:center;color:#6b7477;font-size:12px}} small{{color:#56616a}}
.bubble{{display:block;background:white;border-radius:8px;padding:8px 11px;overflow-wrap:anywhere;text-align:left}} .own .bubble{{background:#b2e281}}
.media{{display:block;max-width:100%;max-height:420px;margin-top:8px}} img{{vertical-align:middle}}
</style><h1>{name}</h1><p>{len(records)} 条本机消息</p>{''.join(blocks)}</html>'''
    if pdf:
        from PyQt5.QtGui import QPageSize, QPdfWriter, QTextDocument
        writer = QPdfWriter(str(path))
        writer.setResolution(96)
        writer.setPageSize(QPageSize(QPageSize.A4))
        writer.setTitle(chat['name'])
        document = QTextDocument()
        document.setHtml(page)
        document.print_(writer)
        del writer
        if not path.is_file() or path.stat().st_size < 500:
            raise OSError('PDF 写入失败')
    else:
        path.write_text(page, encoding='utf-8')


def export_format(snapshot, kind, *, date_range=None, types=None, include_attachments=True,
                  decode_images=False, output_root=None, log=print):
    chat = engine.resolve_chat(snapshot.username, snapshot=snapshot)
    raw = engine.raw_messages(snapshot.username, snapshot=snapshot)
    if date_range is not None:
        start, end = date_range
        raw = [row for row in raw if start <= datetime.fromtimestamp(row['create_time'] or 0, engine.TZ).date() <= end]
    if types is not None:
        raw = [row for row in raw if row['local_type'] in types]
    if not raw:
        raise ValueError('所选日期和消息类型没有匹配的本机消息。')

    base = (Path(output_root) if output_root else
            engine.ROOT.parent / chat['kind'] / engine.safe_name(chat['name']) / '05_原始记录').resolve()
    base.mkdir(parents=True, exist_ok=True)
    destination = base / ('格式导出_' + datetime.now(engine.TZ).strftime('%Y%m%d_%H%M%S_%f'))
    cfg = engine.config()
    decoder = ImageDecoder(cfg['account_dir'], cfg['wechat_exe']) if include_attachments else None
    if decoder:
        decoder.allow_scan = decode_images

    folder = destination
    folder.mkdir(exist_ok=False)
    incomplete = folder / '导出未完成.txt'
    try:
        incomplete.write_text('此目录正在导出。文件写入完成后会移除此标记。\n', encoding='utf-8')
        records = []
        for row in engine.parsed_messages(chat, raw, snapshot=snapshot, include_audio=include_attachments):
            if include_attachments:
                if kind == 'html' and row.get('_audio_buffer'):
                    try:
                        import pysilk
                        wav = pysilk.decode(row['_audio_buffer'], to_wav=True, sample_rate=24000)
                        audio_dir = folder / '附件/语音'
                        audio_dir.mkdir(parents=True, exist_ok=True)
                        audio = audio_dir / f'{row["source_db"]}_{row["local_id"]}.wav'
                        audio.write_bytes(wav)
                        row['attachments'].append({'status': 'saved', 'path': audio.relative_to(folder).as_posix()})
                    except (ImportError, OSError, ValueError, RuntimeError):
                        pass
                engine.save_attachments(row, folder, cfg, decoder)
            for record in engine.flatten_records([row]):
                record.pop('_audio_buffer', None)
                for item in record['attachments']:
                    item.pop('source_path', None)
            records.append(row)
            if len(records) % 200 == 0:
                log(f'已整理 {len(records):,} / {len(raw):,} 条消息')
        path = folder / FORMATS[kind][1]
        if kind in ('csv', 'xlsx'):
            write_table(path, records, kind)
        elif kind in ('txt', 'md', 'ai'):
            write_text(path, chat, records, kind)
        elif kind == 'docx':
            write_docx(path, chat, records)
        elif kind == 'json':
            engine.write_json(path, {'chat': chat, 'exported_at': engine.now(), 'messages': records})
        else:
            write_html(path, chat, records, snapshot, pdf=(kind == 'pdf'))
        incomplete.unlink()
    except Exception:
        # This path was created exclusively by this export. Retain the marker if a file is still locked.
        try:
            if folder.resolve().parent == base:
                shutil.rmtree(folder)
        except OSError:
            pass
        raise
    return path, len(records)
