"""MemoTrace desktop shell for current WeChat sessions and saved snapshots."""
from pathlib import Path
import os
from queue import Empty, Queue
import sys
from threading import Thread
import tkinter as tk
from tkinter import font, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

import memotrace_local as engine
from group_members import export_members


INK = '#182d37'
MUTED = '#586e75'
BG = '#f0f5f4'
PANEL = '#ffffff'
RAIL = '#183840'
RAIL_TEXT = '#d7e8e8'
RAIL_ACTIVE = '#2a535a'
LINE = '#d8e5e3'
SELECTED = '#e0f2ed'
GREEN = '#d7efe5'
ACCENT = '#116859'
ACCENT_HOVER = '#0b5045'
FONT = 'Microsoft YaHei UI'
PAGE_SIZE = 40
ALL_TYPES = '全部类型'


def label(parent, text='', *, size=10, bold=False, fg=INK, **options):
    return tk.Label(parent, text=text, background=parent.cget('background'), foreground=fg,
                    font=(FONT, size, 'bold' if bold else 'normal'), **options)


def conversation_icon(window, is_group):
    image = Image.new('RGBA', (96, 96))
    draw = ImageDraw.Draw(image)
    fill, stroke = ('#daf0e7', '#277260') if is_group else ('#e2ebf2', '#3a6179')
    draw.rounded_rectangle((0, 0, 95, 95), radius=24, fill=fill)
    if is_group:
        draw.ellipse((20, 25, 46, 51), fill=stroke)
        draw.ellipse((50, 25, 76, 51), fill=stroke)
        draw.rounded_rectangle((15, 54, 47, 71), radius=8, fill=stroke)
        draw.rounded_rectangle((49, 54, 81, 71), radius=8, fill=stroke)
    else:
        draw.ellipse((34, 21, 62, 49), fill=stroke)
        draw.rounded_rectangle((22, 53, 74, 74), radius=10, fill=stroke)
    return ImageTk.PhotoImage(image.resize((32, 32), Image.Resampling.LANCZOS), master=window)


class Desktop:
    def __init__(self, window):
        self.window = window
        self.cfg = engine.config()
        self.snapshots = engine.saved_snapshots(self.cfg)
        self.library = None
        self.live_catalog = {}
        self.catalog_mode = 'saved'
        self.session = None
        self.active_snapshot = None
        self.rows = []
        self.matches = []
        self.page = 0
        self.chat_ids = {}
        self.roster_paths = {}
        self.last_output = None
        self.events = Queue()
        self.busy = False
        self.target = tk.StringVar(value=self.cfg.get('default_chat', ''))
        self.chat_search = tk.StringVar()
        self.search = tk.StringVar()
        self.kind = tk.StringVar(value=ALL_TYPES)
        self.status = tk.StringVar(value='打开微信并保持登录，即可读取全部会话。')
        self.decode = tk.BooleanVar(value=True)
        self.roster_name = tk.StringVar()
        self.views = {}
        self.nav = {}
        self.wrap_labels = []
        self.controls = []

        window.title('留痕 · MemoTrace 本地恢复版')
        window.configure(background=BG)
        window.geometry('1240x790')
        window.minsize(980, 620)
        window.option_add('*Font', (FONT, 10))
        self.catalog_font = font.Font(window, family=FONT, size=10)
        self.configure_styles()
        self.icons = {
            '群聊': conversation_icon(window, True),
            '个人': conversation_icon(window, False),
        }
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.build_shell()
        self.build_tools()
        self.build_chat()
        self.build_contacts()
        self.build_profile()
        self.build_menu()
        self.reload_rosters()
        self.render_catalog()
        self.show('聊天')
        self.update_actions()
        first = next((s.username for s in self.snapshots.values() if self.target.get() in (s.name, s.username)),
                     next(iter(self.snapshots), None))
        if first is not None:
            self.load(first)
        window.after(50, self.poll)

    def configure_styles(self):
        style = ttk.Style(self.window)
        style.theme_use('clam')
        style.configure('TButton', background=PANEL, foreground=INK, bordercolor=LINE, lightcolor=PANEL,
                        darkcolor=PANEL, padding=(11, 7), relief='flat')
        style.map('TButton', background=[('disabled', BG), ('pressed', SELECTED), ('active', BG)],
                  foreground=[('disabled', '#718486')])
        style.configure('Accent.TButton', background=ACCENT, foreground=PANEL, bordercolor=ACCENT,
                        lightcolor=ACCENT, darkcolor=ACCENT)
        style.map('Accent.TButton', background=[('disabled', '#70928d'), ('active', ACCENT_HOVER)],
                  foreground=[('disabled', PANEL)], bordercolor=[('disabled', '#70928d'), ('active', ACCENT_HOVER)])
        style.configure('Tab.TButton', padding=(6, 6))
        style.configure('Selected.Tab.TButton', background=SELECTED, foreground=ACCENT, bordercolor=SELECTED)
        style.configure('TEntry', fieldbackground=PANEL, foreground=INK, bordercolor=LINE, padding=(8, 6))
        style.configure('TCombobox', fieldbackground=PANEL, foreground=INK, bordercolor=LINE, padding=(7, 5))
        style.map('TCombobox', fieldbackground=[('readonly', PANEL)], foreground=[('readonly', INK)])
        style.configure('Treeview', rowheight=32, background=PANEL, fieldbackground=PANEL, foreground=INK,
                        bordercolor=LINE)
        style.configure('Catalog.Treeview', rowheight=max(49, self.catalog_font.metrics('linespace') * 2 + 10),
                        borderwidth=0, padding=0, relief='flat', font=self.catalog_font)
        style.map('Treeview', background=[('selected', SELECTED)], foreground=[('selected', INK)])
        style.configure('TCheckbutton', background=PANEL, foreground=INK)
        style.map('TCheckbutton', background=[('active', PANEL)])
        style.configure('Vertical.TScrollbar', background='#dbe7e4', troughcolor=PANEL, bordercolor=PANEL,
                        arrowsize=10)
        style.configure('Activity.Horizontal.TProgressbar', background=ACCENT, troughcolor=SELECTED,
                        bordercolor=PANEL, lightcolor=ACCENT, darkcolor=ACCENT, thickness=5)

    def build_shell(self):
        footer = tk.Frame(self.window, bg=PANEL, padx=14, highlightbackground=LINE, highlightthickness=1)
        footer.pack(side='bottom', fill='x')
        self.status_dot = tk.Canvas(footer, width=12, height=12, bg=PANEL, highlightthickness=0)
        self.status_dot.pack(side='left', padx=(3, 6))
        self.status_light = self.status_dot.create_oval(2, 2, 10, 10, outline='', fill='#88a09f')
        label(footer, textvariable=self.status, size=9, fg=MUTED, anchor='w', wraplength=770, justify='left').pack(
            side='left', fill='x', expand=True, pady=9)
        self.open_output = ttk.Button(footer, text='打开输出目录', command=lambda: os.startfile(self.last_output))
        self.open_output.pack(side='right', padx=(10, 0), pady=4)
        self.activity = ttk.Progressbar(footer, length=64, mode='indeterminate',
                                        style='Activity.Horizontal.TProgressbar')

        rail = tk.Frame(self.window, bg=RAIL, width=104)
        rail.pack(side='left', fill='y')
        rail.pack_propagate(False)
        label(rail, '留痕', size=20, bold=True, fg=PANEL).pack(pady=(23, 24))
        main = tk.Frame(self.window, bg=BG)
        main.pack(side='left', fill='both', expand=True)
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(0, weight=1)
        for name in ('聊天', '工具', '好友', '我的'):
            btn = tk.Button(rail, text=name, relief='flat', bd=0, bg=RAIL, fg=RAIL_TEXT,
                            activebackground=RAIL_ACTIVE, activeforeground=PANEL, highlightthickness=0,
                            anchor='center', pady=11, cursor='hand2', command=lambda name=name: self.show(name))
            btn.pack(fill='x', padx=9, pady=3)
            self.nav[name] = btn
            self.views[name] = tk.Frame(main, bg=BG)
            self.views[name].grid(row=0, column=0, sticky='nsew')

    def build_menu(self):
        menu = tk.Menu(self.window)
        for title, items in (
            ('工具', [('从微信读取指定会话', lambda: self.show('工具')),
                      ('打开聊天学习资料库', lambda: os.startfile(engine.ROOT.parent))]),
            ('数据', [('读取全部微信会话', self.connect_live), ('重新加载本地快照列表', self.reload_catalog),
                      ('打开输出目录', self.open_output.invoke)]),
            ('帮助', [('查看使用说明', lambda: os.startfile(engine.ROOT / 'README.md'))]),
            ('关于', [('关于本地恢复版', lambda: self.show('我的'))]),
        ):
            submenu = tk.Menu(menu, tearoff=False)
            for text, command in items:
                submenu.add_command(label=text, command=command)
            menu.add_cascade(label=title, menu=submenu)
        self.window.configure(menu=menu)

    def show(self, name):
        for key, view in self.views.items():
            self.nav[key].configure(bg=RAIL_ACTIVE if key == name else RAIL,
                                    fg=PANEL if key == name else RAIL_TEXT)
            if key == name:
                view.grid()
            else:
                view.grid_remove()

    def heading(self, frame, title, subtitle):
        label(frame, title, size=22, bold=True).pack(anchor='w', padx=28, pady=(25, 6))
        label(frame, subtitle, fg=MUTED, justify='left', wraplength=770).pack(anchor='w', padx=28, pady=(0, 19))

    def button(self, parent, text, command, *, style='TButton', **pack):
        btn = ttk.Button(parent, text=text, command=command, style=style)
        btn.pack(**pack)
        return btn

    def build_tools(self):
        frame = self.views['工具']
        self.heading(frame, '工具', '保存指定会话的离线快照，或导出群成员名单。')
        card = tk.Frame(frame, bg=PANEL, padx=18, pady=16, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill='x', padx=24)
        label(card, '群名、联系人名或内部用户名', bold=True).pack(anchor='w', pady=(0, 8))
        self.target_entry = ttk.Entry(card, textvariable=self.target, width=52)
        self.target_entry.pack(anchor='w', fill='x')
        actions = tk.Frame(card, bg=PANEL)
        actions.pack(anchor='w', pady=(11, 8))
        self.controls.extend([
            self.target_entry,
            self.button(actions, '保存会话快照', self.refresh_target, side='left', style='Accent.TButton'),
            self.button(actions, '导出群成员', self.export_target_members, side='left', padx=(9, 0)),
        ])
        label(card, '输入准确名称，重名时请使用内部用户名。', fg=MUTED).pack(anchor='w')
        ttk.Checkbutton(card, text='导出聊天时尝试从已登录微信解码本地图片',
                        variable=self.decode).pack(anchor='w', pady=(14, 0))

        archives = tk.Frame(frame, bg=PANEL, padx=18, pady=16, highlightbackground=LINE, highlightthickness=1)
        archives.pack(fill='x', padx=24, pady=16)
        label(archives, '已导出的群成员名单', bold=True).pack(anchor='w', pady=(0, 8))
        row = tk.Frame(archives, bg=PANEL)
        row.pack(fill='x')
        self.roster_combo = ttk.Combobox(row, textvariable=self.roster_name, state='readonly')
        self.roster_combo.pack(side='left', fill='x', expand=True)
        self.roster_open = self.button(row, '打开名单目录', self.open_roster, side='left', padx=(9, 0))

    def build_chat(self):
        frame = self.views['聊天']
        sidebar = tk.Frame(frame, bg=PANEL, width=264, highlightbackground=LINE, highlightthickness=1)
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)
        label(sidebar, '会话', size=20, bold=True).pack(anchor='w', padx=17, pady=(17, 10))
        self.connect_button = self.button(sidebar, '读取全部微信会话', self.connect_live, fill='x',
                                          padx=17, style='Accent.TButton')
        modes = tk.Frame(sidebar, bg=PANEL)
        modes.pack(fill='x', padx=17, pady=(12, 8))
        self.catalog_tabs = {
            'live': self.button(modes, '微信会话', lambda: self.switch_catalog('live'),
                                side='left', expand=True, fill='x', padx=(0, 4)),
            'saved': self.button(modes, '本地快照', lambda: self.switch_catalog('saved'),
                                 side='left', expand=True, fill='x', padx=(4, 0)),
        }
        label(sidebar, '搜索会话', size=9, fg=MUTED).pack(anchor='w', padx=17)
        self.chat_query = ttk.Entry(sidebar, textvariable=self.chat_search)
        self.chat_query.pack(fill='x', padx=17, pady=(3, 8))
        self.catalog_count = label(sidebar, fg=MUTED, size=9, anchor='w')
        self.catalog_count.pack(anchor='w', padx=17, pady=(0, 5))
        catalog = tk.Frame(sidebar, bg=PANEL)
        catalog.pack(fill='both', expand=True, padx=(9, 6), pady=(0, 8))
        self.chat_list = ttk.Treeview(catalog, style='Catalog.Treeview', show='tree', selectmode='browse')
        self.chat_list.column('#0', minwidth=90, width=225, stretch=True)
        scroll = ttk.Scrollbar(catalog, orient='vertical', command=self.chat_list.yview)
        self.chat_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.chat_list.pack(side='left', fill='both', expand=True)
        self.chat_list.bind('<<TreeviewSelect>>', self.select_chat)
        self.chat_search.trace_add('write', self.render_catalog)

        pane = tk.Frame(frame, bg=BG)
        pane.pack(side='left', fill='both', expand=True)
        header = tk.Frame(pane, bg=PANEL, padx=18, pady=13, highlightbackground=LINE, highlightthickness=1)
        header.pack(fill='x')
        bar = tk.Frame(header, bg=PANEL)
        bar.pack(fill='x')
        self.export_button = self.button(bar, '导出记录', self.export_selected, side='right', style='Accent.TButton')
        self.members_button = self.button(bar, '导出群成员', self.export_selected_members, side='right', padx=7)
        self.refresh_button = self.button(bar, '刷新', self.refresh_selected, side='right')
        identity = tk.Frame(bar, bg=PANEL)
        identity.pack(side='left', fill='x', expand=True, padx=(0, 10))
        self.chat_title = label(identity, '聊天记录', size=16, bold=True, anchor='w', justify='left', wraplength=245)
        self.chat_title.pack(anchor='w', fill='x')
        self.chat_source = label(identity, '选择会话后查看消息', fg=MUTED, size=9)
        self.chat_source.pack(anchor='w', pady=(4, 0))

        filters = tk.Frame(pane, bg=BG, padx=18, pady=10)
        filters.pack(fill='x')
        label(filters, '搜索消息', size=9, fg=MUTED).pack(side='left')
        ttk.Entry(filters, textvariable=self.search).pack(side='left', fill='x', expand=True, padx=(8, 9))
        self.types = ttk.Combobox(filters, textvariable=self.kind, values=[ALL_TYPES], state='readonly', width=14)
        self.types.pack(side='left')
        self.search.trace_add('write', self.filter_messages)
        self.types.bind('<<ComboboxSelected>>', self.filter_messages)

        pager = tk.Frame(pane, bg=PANEL, padx=18, pady=6, highlightbackground=LINE, highlightthickness=1)
        pager.pack(side='bottom', fill='x')
        self.page_count = label(pager, fg=MUTED, size=9)
        self.page_count.pack(side='left')
        self.newer = self.button(pager, '较新', lambda: self.move_page(1), side='right')
        self.older = self.button(pager, '较早', lambda: self.move_page(-1), side='right', padx=7)

        viewport = tk.Frame(pane, bg=BG)
        viewport.pack(fill='both', expand=True, padx=(18, 11))
        self.canvas = tk.Canvas(viewport, background=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(viewport, orient='vertical', command=self.canvas.yview)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.messages = tk.Frame(self.canvas, bg=BG, padx=6)
        self.message_window = self.canvas.create_window(0, 0, anchor='nw', window=self.messages)
        self.messages.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self.resize_messages)
        self.window.bind('<MouseWheel>', self.scroll_messages, add='+')
        self.render_messages()

    def build_contacts(self):
        frame = self.views['好友']
        self.heading(frame, '好友', '查看所选会话关联的联系人。群成员名单可在「工具」页导出。')
        self.contact_source = label(frame, '请先在「聊天」选择一个本地会话', fg=MUTED)
        self.contact_source.pack(anchor='w', padx=24, pady=(0, 8))
        panel = tk.Frame(frame, bg=BG)
        panel.pack(fill='both', expand=True, padx=24, pady=(0, 18))
        self.contact_list = ttk.Treeview(panel, columns=('name', 'remark', 'username'), show='headings')
        for key, title, width in [('name', '微信昵称', 180), ('remark', '联系人备注', 170), ('username', '内部用户名', 250)]:
            self.contact_list.heading(key, text=title)
            self.contact_list.column(key, width=width, minwidth=100)
        scroll = ttk.Scrollbar(panel, orient='vertical', command=self.contact_list.yview)
        scroll.pack(side='right', fill='y')
        self.contact_list.configure(yscrollcommand=scroll.set)
        self.contact_list.pack(fill='both', expand=True)

    def build_profile(self):
        frame = self.views['我的']
        self.heading(frame, '我的', 'MemoTrace 本地恢复版')
        for title, value in (
            ('当前配置的微信账号', self.cfg['self_username']),
            ('微信数据目录', self.cfg['account_dir']),
            ('本地资料库', str(engine.ROOT.parent)),
        ):
            label(frame, title, bold=True).pack(anchor='w', padx=24, pady=(12, 4))
            label(frame, value, fg=MUTED, justify='left', wraplength=740).pack(anchor='w', padx=24)
        self.button(frame, '打开聊天学习资料库', lambda: os.startfile(engine.ROOT.parent),
                    anchor='w', padx=24, pady=21)

    def reload_rosters(self):
        self.roster_paths = {
            directory.parent.name: directory for directory in sorted((engine.ROOT.parent / '群聊').glob('*/群成员名单'))
            if any(directory.glob('群成员_*.txt'))
        }
        self.roster_combo.configure(values=list(self.roster_paths))
        if self.roster_name.get() not in self.roster_paths:
            self.roster_name.set(next(iter(self.roster_paths), ''))
        self.roster_open.configure(state='normal' if self.roster_paths else 'disabled')

    def open_roster(self):
        path = self.roster_paths.get(self.roster_name.get())
        if path and path.is_dir():
            os.startfile(path)

    def reload_catalog(self):
        if self.busy:
            return
        self.snapshots = engine.saved_snapshots(self.cfg)
        self.render_catalog()
        self.reload_rosters()

    def catalog(self):
        if self.catalog_mode == 'live':
            return self.live_catalog
        return {s.username: {'username': s.username, 'name': s.name, 'updated_at': s.updated_at,
                             'kind': '群聊' if s.username.endswith('@chatroom') else '个人'}
                for s in self.snapshots.values()}

    def connect_live(self):
        self.start('正在读取微信的会话索引…',
                   lambda: engine.live_sessions(log=self.log), self.live_connected)

    def live_connected(self, library):
        self.library = library
        self.live_catalog = {session['username']: session for session in library.sessions}
        self.show('聊天')
        self.switch_catalog('live', reload=True)
        if not self.live_catalog:
            self.status.set('微信本机的会话索引为空。' + self.live_warning())

    def live_warning(self):
        if getattr(self.library, 'warnings', ()):
            return ' 部分本机数据库未能读取。'
        return ''

    def switch_catalog(self, mode, prefer=None, *, reload=False):
        if self.busy or (mode == 'live' and self.library is None):
            return
        if self.catalog_mode == mode and prefer is None and not reload:
            return
        if prefer is None and self.session:
            prefer = self.session['username']
        self.catalog_mode = mode
        self.session = None
        if self.chat_search.get():
            self.chat_search.set('')
        else:
            self.render_catalog()
        catalog = self.catalog()
        username = prefer if prefer in catalog else next(iter(catalog), None)
        if username is not None:
            self.load(username)
        else:
            self.clear_messages()
            self.chat_title.configure(text='聊天记录')
            self.chat_source.configure(text='微信本机' if mode == 'live' else '本地快照')
            self.contact_source.configure(text='请选择一个会话')
            self.update_actions()
            if mode == 'saved':
                self.status.set('暂无快照。可在「工具」页保存指定会话。')

    def render_catalog(self, *_):
        query = self.chat_search.get().strip().casefold()
        catalog = self.catalog()
        self.chat_list.delete(*self.chat_list.get_children())
        self.chat_ids.clear()
        active_row = None
        for session in catalog.values():
            username = session['username']
            if query not in f'{session["name"]} {username}'.casefold():
                continue
            icon = self.icons['群聊' if session['kind'] == '群聊' else '个人']
            lines = [self.catalog_line(session['name'])]
            details = session.get('preview') or str(session.get('updated_at') or '')[:10]
            if details:
                lines.append(self.catalog_line(details))
            iid = self.chat_list.insert('', 'end', text='\n'.join(lines), image=icon)
            self.chat_ids[iid] = username
            if self.session and self.session['username'] == username:
                active_row = iid
        if active_row:
            self.chat_list.selection_set(active_row)
            self.chat_list.focus(active_row)
            self.chat_list.see(active_row)
        shown = len(self.chat_ids)
        total = len(catalog)
        text = f'{shown:,} 个会话' if shown == total else f'{shown:,} / {total:,} 个会话'
        self.catalog_count.configure(text=text)
        for mode, tab in self.catalog_tabs.items():
            tab.configure(style='Selected.Tab.TButton' if mode == self.catalog_mode else 'Tab.TButton')

    def catalog_line(self, value):
        text = '  ' + ' '.join(str(value).split())[:120]
        max_width = 179
        measure = self.catalog_font.measure
        if measure(text) <= max_width:
            return text
        low, high = 0, len(text)
        while low + 1 < high:
            middle = (low + high) // 2
            if measure(text[:middle] + '…') <= max_width:
                low = middle
            else:
                high = middle
        return text[:low] + '…'

    def select_chat(self, _):
        selection = self.chat_list.selection()
        if self.busy or not selection:
            return
        username = self.chat_ids.get(selection[0])
        if username and (self.session is None or username != self.session['username']):
            self.load(username)

    def clear_messages(self):
        self.rows = []
        self.active_snapshot = None
        self.contact_list.delete(*self.contact_list.get_children())
        self.kind.set(ALL_TYPES)
        self.types.configure(values=[ALL_TYPES])
        self.search.set('')

    def load(self, username):
        if self.busy:
            return
        session = self.catalog()[username]
        self.session = session
        source = '微信本机' if self.catalog_mode == 'live' else '本地快照'
        stamp = str(session.get('updated_at') or '')[:19].replace('T', ' ')
        self.chat_title.configure(text=session['name'])
        self.chat_source.configure(text=' · '.join(filter(None, (source, session['kind'], stamp))))
        self.contact_source.configure(text=f'{source} · {session["name"]}')
        self.clear_messages()
        for iid, catalog_username in self.chat_ids.items():
            if catalog_username == username:
                self.chat_list.selection_set(iid)
                self.chat_list.focus(iid)
                self.chat_list.see(iid)
                break

        if self.catalog_mode == 'live':
            library = self.library
            task = lambda: engine.live_messages(library, session, log=self.log)
        else:
            snapshot = self.snapshots[username]
            self.active_snapshot = snapshot
            task = lambda: self.read_snapshot(snapshot)
        self.start(f'正在读取 {session["name"]} 的消息…', task, self.loaded, self.read_failed)

    def read_snapshot(self, snapshot):
        chat = engine.resolve_chat(snapshot.username, snapshot=snapshot)
        rows = list(engine.parsed_messages(chat, snapshot=snapshot))
        for record in rows:
            record.pop('_audio_buffer', None)
        return chat, rows, engine.contacts(snapshot=snapshot)

    def loaded(self, result):
        chat, self.rows, contacts = result
        self.chat_title.configure(text=chat['name'])
        for contact in contacts:
            if contact['kind'] == '个人':
                self.contact_list.insert('', 'end', values=(contact['nickname'], contact['remark'], contact['username']))
        self.types.configure(values=[ALL_TYPES] + sorted({r['type_name'] for r in self.rows}))
        self.filter_messages()
        count = f'已读取 {len(self.rows):,} 条消息'
        if self.catalog_mode == 'live':
            notice = '' if self.session.get('can_export', True) else ' 未匹配名称的会话可浏览。'
            self.status.set(f'已载入 {len(self.live_catalog):,} 个微信会话 · {count}。' + self.live_warning() + notice)
        else:
            self.status.set(f'本地快照 · {count}。')

    def read_failed(self, _):
        self.chat_source.configure(text='读取失败 · 点击「刷新」重试')

    def filter_messages(self, *_):
        query = self.search.get().strip().casefold()
        kind = self.kind.get()
        self.matches = [r for r in self.rows if (kind == ALL_TYPES or r['type_name'] == kind)
                        and query in f'{r["sender"]} {r["text"]}'.casefold()]
        self.page = max(0, (len(self.matches) - 1) // PAGE_SIZE)
        self.render_messages(to_bottom=not query and kind == ALL_TYPES)

    def move_page(self, delta):
        self.page += delta
        self.render_messages()

    def render_messages(self, *, to_bottom=False):
        for widget in self.messages.winfo_children():
            widget.destroy()
        self.wrap_labels.clear()
        start = self.page * PAGE_SIZE
        end = min(len(self.matches), start + PAGE_SIZE)
        self.page_count.configure(text=f'{start + 1 if end else 0}–{end} / {len(self.matches):,} 条匹配'
                                       f'（共 {len(self.rows):,} 条）')
        self.older.configure(state='normal' if self.page else 'disabled')
        self.newer.configure(state='normal' if end < len(self.matches) else 'disabled')
        if not self.matches:
            if self.rows:
                hint = '没有符合筛选条件的消息。'
            elif self.session:
                hint = '此处显示所选会话的本机消息。'
            else:
                hint = '点击「读取全部微信会话」，或选择已保存的快照。'
            label(self.messages, hint, fg=MUTED).pack(pady=42)
        last_day = ''
        for record in self.matches[start:end]:
            day = record['time'][:10]
            if day != last_day:
                date_label = label(self.messages, day, size=9, fg=MUTED, padx=10, pady=4)
                date_label.configure(bg='#e4edeb')
                date_label.pack(pady=(8, 10))
                last_day = day
            own = record['sender_id'] == self.cfg['self_username']
            system = record['sender'] == '系统'
            box = tk.Frame(self.messages, bg=BG)
            box.pack(anchor='center' if system else 'e' if own else 'w', pady=(2, 11))
            sender = str(record['sender'])
            if len(sender) > 35:
                sender = sender[:34] + '…'
            label(box, f'{sender} · {record["time"][11:]} · {record["type_name"]}',
                  size=9, fg=MUTED).pack(anchor='e' if own else 'w')
            value = str(record['text'] or f'[{record["type_name"]}]')
            preview = value[:480].strip() + ('…' if len(value) > 480 else '')
            bubble = label(box, preview, justify='left', anchor='w', padx=13, pady=9, cursor='hand2')
            bubble.configure(bg='#e4edeb' if system else GREEN if own else PANEL)
            bubble.pack(anchor='e' if own else 'w', pady=(3, 0))
            bubble.bind('<Button-1>', lambda _, r=record: self.show_message(r))
            self.wrap_labels.append(bubble)
        self.resize_messages()
        self.messages.update_idletasks()
        self.canvas.yview_moveto(1 if to_bottom else 0)

    def resize_messages(self, event=None):
        width = event.width if event else self.canvas.winfo_width()
        self.canvas.itemconfigure(self.message_window, width=width)
        for bubble in self.wrap_labels:
            bubble.configure(wraplength=max(210, int(width * 0.72)))

    def scroll_messages(self, event):
        widget = event.widget
        while widget is not None:
            if widget == self.canvas:
                self.canvas.yview_scroll(-int(event.delta / 120) * 3, 'units')
                return 'break'
            widget = getattr(widget, 'master', None)

    def show_message(self, record):
        popup = tk.Toplevel(self.window, bg=BG)
        popup.title('消息详情')
        popup.geometry('680x470')
        popup.transient(self.window)
        label(popup, str(record['sender']), size=14, bold=True).pack(anchor='w', padx=18, pady=(14, 0))
        label(popup, f'{record["time"]} · {record["type_name"]}', fg=MUTED, size=9).pack(
            anchor='w', padx=18, pady=(3, 11))
        content = tk.Frame(popup, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        content.pack(fill='both', expand=True, padx=18, pady=(0, 15))
        text = tk.Text(content, wrap='word', padx=13, pady=12, borderwidth=0, bg=PANEL, fg=INK)
        scrollbar = ttk.Scrollbar(content, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        text.pack(side='left', fill='both', expand=True)
        body = str(record['text'] or f'[{record["type_name"]}]')
        if quote := record.get('quote'):
            body += f'\n\n引用 {quote["sender"]}\n{quote["text"]}'
        if links := record.get('links'):
            body += '\n\n链接\n' + '\n'.join(links)
        if nested := record.get('nested_messages'):
            body += '\n\n转发内容\n' + '\n\n'.join(
                f'{r["time"]}  {r["sender"]}\n{r["text"]}' for r in engine.flatten_records(nested))
        text.insert('1.0', body + f'\n\n来源  {record["source_db"]} / local_id={record["local_id"]}'
                                 f'\n解析状态  {record["parse_status"]}')
        text.configure(state='disabled')

    def input_target(self):
        value = self.target.get().strip()
        if not value:
            messagebox.showinfo('选择目标', '请输入准确名称或内部用户名。', parent=self.window)
        return value

    def refresh_target(self):
        if name := self.input_target():
            self.refresh(name)

    def refresh_selected(self):
        username = self.session['username']
        if self.catalog_mode == 'live':
            self.load(username)
        else:
            self.refresh(username)

    def refresh(self, name):
        self.start('正在从微信读取指定会话…', lambda: engine.refresh(log=self.log, chat=name), self.refreshed)

    def refreshed(self, report):
        self.snapshots = engine.saved_snapshots(self.cfg)
        self.show('聊天')
        self.switch_catalog('saved', prefer=report['scope']['username'])

    def export_selected(self):
        session = self.session
        snapshot = self.active_snapshot
        library = self.library
        decode = self.decode.get()

        def export():
            source = snapshot
            if source is None:
                report = engine.refresh(log=self.log, chat=session['username'], library=library)
                source = engine.saved_snapshots(self.cfg)[report['scope']['username']]
            return engine.export_chat(source.username, log=self.log, decode_images=decode, snapshot=source)

        self.start(f'正在导出 {session["name"]}…', export, self.exported)

    def exported(self, result):
        self.last_output, summary = result
        self.snapshots = engine.saved_snapshots(self.cfg)
        self.status.set(f'已导出 {summary["messages"]:,} 条记录及本机可用附件')

    def export_target_members(self):
        if name := self.input_target():
            self.members(name)

    def export_selected_members(self):
        self.members(self.session['username'])

    def members(self, name):
        self.start('正在读取微信联系人库中的群成员表…', lambda: export_members(name), self.members_exported)

    def members_exported(self, result):
        report, stem = result
        self.last_output = stem.parent
        self.reload_rosters()
        self.roster_name.set(stem.parent.parent.name)
        self.status.set(f'{report["group_name"]} · 已导出 {report["member_count"]} 位群成员'
                        f' · 未解析姓名 {report["unresolved_names"]}')

    def close(self):
        if self.busy:
            self.status.set('请等待当前操作完成后关闭窗口。')
        else:
            self.window.destroy()

    def update_actions(self):
        state = 'disabled' if self.busy else 'normal'
        for control in self.controls + [self.connect_button, self.chat_query]:
            control.configure(state=state)
        self.chat_list.state(['disabled' if self.busy else '!disabled'])
        self.connect_button.configure(text='刷新全部微信会话' if self.library else '读取全部微信会话')
        self.catalog_tabs['live'].configure(state=state if self.library else 'disabled')
        self.catalog_tabs['saved'].configure(state=state)
        self.refresh_button.configure(state=state if self.session else 'disabled')
        exportable = self.session and self.session.get('can_export', True)
        self.export_button.configure(state=state if self.rows and exportable else 'disabled')
        group_selected = self.session and self.session['username'].endswith('@chatroom')
        self.members_button.configure(state=state if group_selected else 'disabled')
        self.open_output.configure(state='normal' if self.last_output else 'disabled')
        light = '#bf8734' if self.busy else '#2b8c75' if self.catalog_mode == 'live' else '#88a09f'
        self.status_dot.itemconfigure(self.status_light, fill=light)
        if self.busy and not self.activity.winfo_manager():
            self.activity.pack(side='right', padx=(8, 0))
            self.activity.start()
        elif not self.busy and self.activity.winfo_manager():
            self.activity.stop()
            self.activity.pack_forget()

    def log(self, message):
        self.events.put(('log', message))

    def start(self, message, task, done, failed=None):
        if self.busy:
            return
        self.busy = True
        self.status.set(message)
        self.update_actions()

        def work():
            try:
                self.events.put(('done', (done, task())))
            except Exception as exc:
                self.events.put(('error', (failed, str(exc))))

        Thread(target=work, daemon=True).start()

    def poll(self):
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except Empty:
                break
            if kind == 'log':
                self.status.set(payload)
                continue
            self.busy = False
            try:
                if kind == 'error':
                    failed, message = payload
                    if failed:
                        failed(message)
                    raise RuntimeError(message)
                done, result = payload
                done(result)
            except Exception as exc:
                self.status.set(f'操作未完成 · {exc}')
                messagebox.showerror('操作未完成', str(exc), parent=self.window)
            self.update_actions()
        self.window.after(50, self.poll)


def run():
    # Conda's venv does not copy Tcl resources into the venv.
    tcl = Path(sys.base_prefix) / 'tcl'
    for name, folder in [('TCL_LIBRARY', 'tcl8.6'), ('TK_LIBRARY', 'tk8.6')]:
        if (tcl / folder).is_dir():
            os.environ.setdefault(name, str(tcl / folder))
    window = tk.Tk()
    Desktop(window)
    window.mainloop()
