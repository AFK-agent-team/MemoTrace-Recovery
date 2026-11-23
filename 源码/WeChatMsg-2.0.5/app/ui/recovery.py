"""Wire the original PyQt desktop forms to the local WeChat 4.x recovery engine."""
from datetime import datetime
import hashlib
import os
from pathlib import Path
import sys

import PyQt5
from PyQt5.QtCore import QCoreApplication, QDate, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QFont, QIcon
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                             QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                             QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from app.ui.chat.recovery_view import Avatars, ChatPage, ENTRY, PAGE_SIZE
from app.ui.mainwindow import Ui_MainWindow

import memotrace_local as engine
from annual_report import conversation_stats, personal_stats, write_report
from desktop_data import PreviewSource, message_key, read_avatars, read_voice
from desktop_exports import FORMATS, export_format
from group_members import export_members


ICONS = Path(__file__).resolve().parents[1] / 'resources/icons'
MEDIA_TYPES = {3, 34, 43, 47, 25769803825}
STYLE = '''
QMainWindow, #frame_info, #history { background: #edf3f6; color:#243137; }
QMenuBar, QStatusBar { background:#f7f9fa; }
QMenu::item:selected, QListWidget#navigation::item:selected { background:#dae7ed; color:#17272e; }
QListWidget { border:0; background:#f6f9fa; outline:0; }
QListWidget#navigation { background:#edf3f6; }
QListWidget#navigation::item { padding-left:8px; }
QListWidget#navigation::item:selected { border-left:2px solid #426b7b; }
QLineEdit, QComboBox, QDateEdit { padding:5px 8px; background:white; border:1px solid #d5dfe3; border-radius:4px; }
QPushButton, QToolButton { padding:6px 9px; background:#f8fafb; border:1px solid #d4dfe4; border-radius:4px; }
QPushButton:hover, QToolButton:hover { background:#e5eef2; }
QPushButton:disabled, QToolButton:disabled { color:#8b979a; }
#connectButton { background:#246a60; color:white; border:1px solid #246a60; }
#connectButton:hover { background:#1b574e; }
#connectButton:disabled { background:#9eb9b5; border-color:#9eb9b5; }
#chatTitle, #pageTitle { font-size:17px; font-weight:600; color:#202e33; }
#muted { font-size:11px; color:#63767d; }
QGroupBox { margin-top:12px; padding:10px 7px 7px; border:1px solid #d8e2e7; border-radius:6px; }
QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 5px; }
'''


def local_url(path):
    return QUrl.fromLocalFile(str(path))


def plain_label(text='', object_name=''):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setObjectName(object_name)
    return label


def qt_application():
    # PyQt5's bundled Qt prefix may contain question marks for Chinese directory names.
    plugins = Path(PyQt5.__file__).resolve().parent / 'Qt5/plugins'
    QCoreApplication.setLibraryPaths([str(plugins)])
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = str(plugins / 'platforms')
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication([sys.argv[0], '-platformpluginpath', str(plugins / 'platforms')])
    app.setApplicationName('MemoTrace 本地恢复版')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    app.setStyle('Fusion')
    return app


class Job(QThread):
    progress = pyqtSignal(str)

    def __init__(self, work, parent):
        super().__init__(parent)
        self.work = work
        self.value = None
        self.error = None

    def run(self):
        try:
            self.value = self.work(self.progress.emit)
        except Exception as error:
            self.error = str(error) or type(error).__name__


class ExportOptions(QDialog):
    def __init__(self, parent, kind, rows):
        super().__init__(parent)
        archive = kind == 'archive'
        self.setWindowTitle('完整归档' if archive else f'导出为{FORMATS[kind][0]}')
        self.resize(460, 325)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.range = QCheckBox('按日期筛选')
        self.range.setEnabled(not archive)
        self.start = QDateEdit(QDate.fromString(rows[0]['time'][:10], 'yyyy-MM-dd'))
        self.end = QDateEdit(QDate.fromString(rows[-1]['time'][:10], 'yyyy-MM-dd'))
        for edit in (self.start, self.end):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat('yyyy-MM-dd')
            edit.setEnabled(False)
            self.range.toggled.connect(edit.setEnabled)
        form.addRow(self.range)
        form.addRow('起始日', self.start)
        form.addRow('截止日', self.end)
        self.type_list = QListWidget()
        self.type_list.setMaximumHeight(125)
        for key, name in sorted({r['type']: r['type_name'] for r in rows}.items(), key=lambda pair: pair[1]):
            item = QListWidgetItem(name)
            item.setData(ENTRY, key)
            item.setCheckState(Qt.Checked)
            self.type_list.addItem(item)
        if not archive:
            form.addRow('消息类型', self.type_list)
        layout.addLayout(form)
        self.files = QCheckBox('保存本机可用附件')
        self.files.setChecked(True)
        self.files.setEnabled(not archive)
        self.decode = QCheckBox('尝试用已登录的微信解码未缓存图片')
        self.files.toggled.connect(self.decode.setEnabled)
        layout.addWidget(self.files)
        layout.addWidget(self.decode)
        target = QHBoxLayout()
        self.output = QLineEdit()
        self.output.setPlaceholderText('默认保存到资料库的对应会话目录')
        self.output.setReadOnly(True)
        choose = QPushButton('选择目录…')
        choose.clicked.connect(self.choose_directory)
        target.addWidget(self.output, 1)
        target.addWidget(choose)
        layout.addLayout(target)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('导出')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_directory(self):
        if path := QFileDialog.getExistingDirectory(self, '选择导出目录', str(engine.ROOT.parent)):
            self.output.setText(path)

    def options(self):
        days = None
        if self.range.isChecked():
            days = self.start.date().toPyDate(), self.end.date().toPyDate()
            if days[0] > days[1]:
                raise ValueError('起始日不能晚于截止日。')
        selected = {self.type_list.item(i).data(ENTRY) for i in range(self.type_list.count())
                    if self.type_list.item(i).checkState() == Qt.Checked}
        if not selected:
            raise ValueError('请至少选择一种消息类型。')
        return {'date_range': days, 'types': selected if len(selected) != self.type_list.count() else None,
                'include_attachments': self.files.isChecked(),
                'decode_images': self.files.isChecked() and self.decode.isChecked(),
                'output_root': self.output.text() or None}

    def accept(self):
        try:
            self.options()
        except ValueError as error:
            QMessageBox.information(self, '检查导出范围', str(error))
            return
        super().accept()


class AnnualOptions(QDialog):
    def __init__(self, parent, title, *, years=(), snapshot_count=None, prefer_live=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.years = QComboBox() if years else QSpinBox()
        if years:
            for year in years:
                self.years.addItem(str(year), year)
        else:
            current = datetime.now(engine.TZ).year
            self.years.setRange(2000, current)
            self.years.setValue(current)
        form.addRow('年份', self.years)
        self.sources = QComboBox()
        if snapshot_count is not None:
            if snapshot_count:
                self.sources.addItem(f'本地快照（{snapshot_count:,} 个会话）', 'offline')
            self.sources.addItem('微信本机（需保持微信登录）', 'live')
            if prefer_live:
                self.sources.setCurrentIndex(self.sources.findData('live'))
            form.addRow('数据来源', self.sources)
        layout.addLayout(form)
        note = plain_label('按北京时间统计该自然年内的本机记录，生成可离线打开的 HTML。', 'muted')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('生成并打开')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(400, self.sizeHint().height())

    def selected_year(self):
        return self.years.currentData() if isinstance(self.years, QComboBox) else self.years.value()


class RecoveryWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.setWindowTitle('留痕 · MemoTrace 本地恢复版（2.0.5 桌面）')
        self.setWindowIcon(QIcon(str(ICONS / 'logo3.0.ico')))
        screen = QApplication.primaryScreen().availableGeometry()
        width = min(1130, screen.width() - 28)
        height = min(760, screen.height() - 46)
        self.resize(width, height)
        self.setMinimumSize(min(880, width), min(590, height))
        self.setStyleSheet(STYLE)
        self.cfg = engine.config()
        self.snapshots = engine.saved_snapshots(self.cfg)
        self.library = None
        self.entry = None
        self.snapshot = None
        self.rows = []
        self.matches = []
        self.page = 0
        self.previews = {}
        self.preview_source = None
        self.job = None
        self.last_output = None
        self.avatars = Avatars()

        self.chat = ChatPage(self.avatars)
        self.panel = self.chat.panel
        self.tools_page = self.build_tools()
        self.friends_page = self.build_friends()
        self.profile_page = self.build_profile()
        self.build_navigation()
        self.build_menu()
        self.filter_timer = QTimer(self, singleShot=True, interval=170)
        self.filter_timer.timeout.connect(self.filter_messages)
        self.chat.connect_button.clicked.connect(self.connect_live)
        self.chat.source.currentIndexChanged.connect(self.change_source)
        self.chat.lineEdit.textChanged.connect(self.render_catalog)
        self.chat.listWidget.currentItemChanged.connect(self.select_chat)
        self.panel.search.textChanged.connect(lambda _: self.filter_timer.start())
        self.panel.kinds.currentIndexChanged.connect(lambda _: self.filter_timer.start())
        self.panel.refresh.clicked.connect(self.reload_chat)
        self.panel.annual_report.setIcon(QIcon(str(ICONS / 'annual_report.svg')))
        self.panel.annual_report.clicked.connect(self.conversation_report)
        self.panel.members.clicked.connect(lambda: self.members(self.entry['username']))
        self.panel.earlier.clicked.connect(lambda: self.turn_page(1))
        self.panel.later.clicked.connect(lambda: self.turn_page(-1))
        self.panel.format_chosen.connect(self.export_chat)
        self.panel.voice_chosen.connect(self.play_voice)
        self.statusBar().showMessage('离线快照可直接浏览。微信登录后可读取全部会话。')
        self.render_catalog()
        self.update_controls()
        first = next((s.username for s in self.snapshots.values() if s.name == self.cfg.get('default_chat')),
                     next(iter(self.snapshots), None))
        if first:
            QTimer.singleShot(0, lambda: self.load_chat(first))

    def build_navigation(self):
        rail = QVBoxLayout(self.frame_info)
        rail.setContentsMargins(0, 17, 5, 10)
        self.frame_info.setFixedWidth(102)
        self.myavatar.setText('')
        self.myavatar.setAlignment(Qt.AlignCenter)
        self.myavatar.setFixedSize(53, 53)
        self.myavatar.setPixmap(self.avatars.get(self.cfg['self_username']).scaled(52, 52))
        rail.addWidget(self.myavatar, 0, Qt.AlignHCenter)
        rail.addWidget(plain_label('本地恢复版', 'muted'), 0, Qt.AlignHCenter)
        rail.addSpacing(17)
        self.listWidget.clear()
        self.listWidget.setObjectName('navigation')
        self.listWidget.setFixedHeight(220)
        self.listWidget.setIconSize(QSize(17, 17))
        for name, icon, page in (('工具', 'tool.svg', self.tools_page), ('聊天', 'chat.svg', self.chat),
                                 ('好友', 'contact.svg', self.friends_page), ('我的', 'home.svg', self.profile_page)):
            item = QListWidgetItem(QIcon(str(ICONS / icon)), name)
            item.setSizeHint(QSize(94, 51))
            self.listWidget.addItem(item)
            self.stackedWidget.addWidget(page)
        rail.addWidget(self.listWidget)
        rail.addStretch()
        settings = QPushButton('本地设置')
        settings.clicked.connect(lambda: self.listWidget.setCurrentRow(3))
        rail.addWidget(settings)
        self.listWidget.currentRowChanged.connect(self.stackedWidget.setCurrentIndex)
        self.listWidget.setCurrentRow(1)

    def build_menu(self):
        self.menu_F.clear()
        self.menu_F.setTitle('工具')
        self.menu_F.addAction('读取全部微信会话', self.connect_live)
        self.menu_F.addAction('打开聊天学习资料库', lambda: QDesktopServices.openUrl(local_url(engine.ROOT.parent)))
        self.menu_F.addSeparator()
        self.menu_F.addAction('退出', self.close)
        self.menu_data.clear()
        self.menu_data.addMenu(self.panel.export_menu)
        self.conversation_report_action = self.menu_data.addAction('当前会话年度报告', self.conversation_report)
        self.personal_report_action = self.menu_data.addAction('我的年度聊天报告', self.personal_report)
        self.menu_data.addAction('群成员与快照工具', lambda: self.listWidget.setCurrentRow(0))
        self.menu_2.clear()
        self.menu_2.addAction('使用说明', lambda: QDesktopServices.openUrl(local_url(engine.ROOT / 'README.md')))
        self.menu_about.clear()
        self.menu_about.addAction('关于留痕本地恢复版', lambda: QMessageBox.about(
            self, '关于', '基于留痕 2.0.5 的 PyQt5 桌面界面，使用本地恢复版的微信 4.x 只读后端。'))
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(80)
        self.progress.setTextVisible(False)
        self.statusBar().addPermanentWidget(self.progress)
        self.open_output = QPushButton('打开输出目录')
        self.open_output.clicked.connect(lambda: QDesktopServices.openUrl(local_url(self.last_output)))
        self.statusBar().addPermanentWidget(self.open_output)

    def build_tools(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 19, 24, 19)
        layout.addWidget(plain_label('工具', 'pageTitle'))
        scope = QGroupBox('本机微信')
        form = QFormLayout(scope)
        connect = QPushButton('读取全部微信会话')
        connect.clicked.connect(self.connect_live)
        form.addRow(connect)
        self.target = QLineEdit(self.cfg.get('default_chat', ''))
        self.target.setPlaceholderText('准确名称或内部用户名')
        form.addRow('会话或群名', self.target)
        buttons = QHBoxLayout()
        save = QPushButton('保存指定会话快照')
        save.clicked.connect(lambda: self.refresh_target(self.target.text().strip()))
        export = QPushButton('导出指定群成员')
        export.clicked.connect(lambda: self.members(self.target.text().strip()))
        buttons.addWidget(save)
        buttons.addWidget(export)
        buttons.addStretch()
        form.addRow(buttons)
        layout.addWidget(scope)
        roster = QGroupBox('已导出的群成员名单')
        row = QHBoxLayout(roster)
        self.rosters = QComboBox()
        self.roster_open = QPushButton('打开名单目录')
        self.roster_open.clicked.connect(self.open_roster)
        row.addWidget(self.rosters, 1)
        row.addWidget(self.roster_open)
        layout.addWidget(roster)
        self.reload_rosters()
        layout.addStretch()
        self.tool_controls = (connect, save, export, self.target)
        return page

    def build_friends(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 19, 24, 19)
        layout.addWidget(plain_label('好友', 'pageTitle'))
        self.contacts_source = plain_label('这里显示所选会话的本机联系人。', 'muted')
        layout.addWidget(self.contacts_source)
        self.contacts_table = QTableWidget(0, 3)
        self.contacts_table.setHorizontalHeaderLabels(('微信昵称', '联系人备注', '内部用户名'))
        self.contacts_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.contacts_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.contacts_table.verticalHeader().hide()
        layout.addWidget(self.contacts_table, 1)
        return page

    def build_profile(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 19, 24, 19)
        layout.addWidget(plain_label('我的', 'pageTitle'))
        form = QFormLayout()
        for name, value in (('配置的微信账号', self.cfg['self_username']), ('微信数据目录', self.cfg['account_dir']),
                            ('聊天学习资料库', str(engine.ROOT.parent))):
            value_label = plain_label(value)
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value_label.setWordWrap(True)
            form.addRow(name, value_label)
        layout.addLayout(form)
        self.personal_report_button = QPushButton('我的年度聊天报告')
        self.personal_report_button.setIcon(QIcon(str(ICONS / 'annual_report.svg')))
        self.personal_report_button.clicked.connect(self.personal_report)
        layout.addWidget(self.personal_report_button, 0, Qt.AlignLeft)
        open_config = QPushButton('打开本地配置目录')
        open_config.clicked.connect(lambda: QDesktopServices.openUrl(local_url(engine.ROOT)))
        layout.addWidget(open_config, 0, Qt.AlignLeft)
        layout.addStretch()
        return page

    def reload_rosters(self):
        self.rosters.clear()
        for folder in sorted((engine.ROOT.parent / '群聊').glob('*/群成员名单')):
            if any(folder.glob('群成员_*.txt')):
                self.rosters.addItem(folder.parent.name, str(folder))
        self.roster_open.setEnabled(bool(self.rosters.count()))

    def open_roster(self):
        if path := self.rosters.currentData():
            QDesktopServices.openUrl(local_url(path))

    def live(self):
        return self.chat.source.currentData() == 'live'

    def catalog(self):
        if self.live() and self.library is not None:
            return self.library.sessions
        return [{'username': s.username, 'name': s.name, 'updated_at': s.updated_at,
                 'kind': '群聊' if s.username.endswith('@chatroom') else '个人',
                 'preview': f'{s.message_count:,} 条本地记录' if s.message_count is not None else '本地快照'}
                for s in self.snapshots.values()]

    def render_catalog(self):
        search = self.chat.lineEdit.text().strip().casefold()
        sessions = [entry for entry in self.catalog()
                    if search in f'{entry["name"]} {entry["username"]} {entry.get("nickname", "")}'.casefold()]
        self.chat.set_sessions(sessions, self.entry['username'] if self.entry else None)

    def connect_live(self):
        def work(log):
            library = engine.live_sessions(log=log)
            log('正在读取本机会话头像…')
            avatars = read_avatars([self.cfg['self_username'], *(s['username'] for s in library.sessions)], library=library)
            return library, avatars

        self.start('正在连接已登录的微信…', work, self.connected)

    def connected(self, result):
        previous = self.entry['username'] if self.entry else None
        self.library, avatars = result
        self.avatars.reset(avatars)
        self.myavatar.setPixmap(self.avatars.get(self.cfg['self_username']).scaled(52, 52))
        self.listWidget.setCurrentRow(1)
        self.chat.source.blockSignals(True)
        self.chat.source.model().item(1).setEnabled(True)
        self.chat.source.setCurrentIndex(1)
        self.chat.source.blockSignals(False)
        self.change_source(previous=previous)
        if self.library.warnings:
            self.statusBar().showMessage('已读取会话索引。部分本机附件库暂不可用。')

    def change_source(self, _index=None, *, previous=None):
        self.entry = None
        self.snapshot = None
        self.rows = []
        self.matches = []
        self.panel.clear('从左侧选择一个会话')
        self.panel.label_reamrk.setText('聊天记录')
        self.panel.source.clear()
        self.panel.result.clear()
        self.contacts_table.setRowCount(0)
        self.chat.lineEdit.clear()
        catalog = self.catalog()
        self.render_catalog()
        first = next((entry for entry in catalog if entry['username'] == previous), None)
        first = first or next((entry for entry in catalog if entry['name'] == self.cfg.get('default_chat')), None)
        if first or catalog:
            self.load_chat((first or catalog[0])['username'])
        else:
            self.update_controls()

    def select_chat(self, item, _previous):
        if item and self.job is None:
            entry = item.data(ENTRY)
            if self.entry is None or self.entry['username'] != entry['username']:
                self.load_chat(entry['username'])

    def load_chat(self, username):
        if self.job is not None:
            return
        entry = next((item for item in self.catalog() if item['username'] == username), None)
        if entry is None:
            return
        library = self.library if self.live() else None
        snapshot = None if library is not None else self.snapshots[username]
        self.entry, self.snapshot = entry, snapshot
        self.rows = []
        self.matches = []
        self.panel.label_reamrk.setText(entry['name'])
        stamp = (entry.get('updated_at') or '')[:16].replace('T', ' ')
        self.panel.source.setText(' · '.join(filter(None, ('微信本机' if library is not None else '本地快照',
                                                           entry['kind'], stamp))))
        self.panel.clear('正在打开会话…')
        self.panel.result.clear()
        self.render_catalog()

        def work(log):
            if library is not None:
                chat, rows, contacts = engine.live_messages(library, entry, log=log)
            else:
                chat = engine.resolve_chat(username, snapshot=snapshot)
                rows = list(engine.parsed_messages(chat, snapshot=snapshot, include_audio=False))
                contacts = engine.contacts(snapshot=snapshot)
            users = {username, self.cfg['self_username'], *(r['sender_id'] for r in rows)}
            avatars = read_avatars(users, library=library, snapshot=snapshot)
            source = PreviewSource(self.cfg, chat)
            return rows, contacts, avatars, source, source.prepare(rows[-PAGE_SIZE:])

        self.start('正在读取消息…', work, self.chat_loaded, self.chat_failed)

    def chat_loaded(self, result):
        self.rows, contacts, avatars, self.preview_source, self.previews = result
        self.avatars.add(avatars)
        self.myavatar.setPixmap(self.avatars.get(self.cfg['self_username']).scaled(52, 52))
        self.chat.listWidget.viewport().update()
        self.panel.search.blockSignals(True)
        self.panel.search.clear()
        self.panel.search.blockSignals(False)
        self.panel.set_types(self.rows)
        self.contacts_source.setText(f'{self.entry["name"]} · 本机可用联系人')
        contacts = [contact for contact in contacts if contact['kind'] == '个人']
        self.contacts_table.setRowCount(len(contacts))
        for index, contact in enumerate(contacts):
            for column, key in enumerate(('nickname', 'remark', 'username')):
                item = QTableWidgetItem(contact[key])
                if column == 0:
                    item.setIcon(QIcon(self.avatars.get(contact['username'])))
                self.contacts_table.setItem(index, column, item)
        self.filter_messages()
        self.statusBar().showMessage(f'已载入 {len(self.rows):,} 条消息'
                                     + (f' · 微信会话 {len(self.library.sessions):,} 个' if self.live() else ' · 本地快照'))

    def chat_failed(self):
        self.panel.clear('会话读取失败，可点右上角的「刷新」重试。')
        self.contacts_table.setRowCount(0)

    def reload_chat(self):
        if self.entry:
            if self.live():
                self.load_chat(self.entry['username'])
            else:
                self.refresh_target(self.entry['username'])

    def refresh_target(self, name):
        if not name:
            QMessageBox.information(self, '保存快照', '请输入准确的会话名或内部用户名。')
            return

        def saved(report):
            self.snapshots = engine.saved_snapshots(self.cfg)
            self.listWidget.setCurrentRow(1)
            self.chat.source.blockSignals(True)
            self.chat.source.setCurrentIndex(0)
            self.chat.source.blockSignals(False)
            self.change_source(previous=report['scope']['username'])

        self.start('正在更新指定会话的快照…', lambda log: engine.refresh(log=log, chat=name), saved)

    def filter_messages(self):
        if self.job is not None or self.entry is None:
            return
        query = self.panel.search.text().strip().casefold()
        kind = self.panel.kinds.currentData()
        self.matches = [row for row in self.rows if (kind is None or row['type'] == kind)
                        and query in f'{row["sender"]} {row["text"] or ""}'.casefold()]
        self.page = 0
        self.render_page()

    def turn_page(self, change):
        if self.job is None:
            self.page += change
            self.render_page()

    def render_page(self):
        end = len(self.matches) - self.page * PAGE_SIZE
        start = max(0, end - PAGE_SIZE)
        rows = self.matches[start:end]
        needed = [row for row in rows if row['type'] in MEDIA_TYPES and message_key(row) not in self.previews]

        def show(assets):
            self.previews.update(assets)
            if rows:
                self.panel.render(rows, self.previews, self.cfg['self_username'], newest=self.page == 0)
            else:
                self.panel.clear('本机没有符合筛选条件的消息' if self.rows else '本机没有保存此会话的消息')
            self.panel.result.setText(f'{start + 1 if rows else 0}–{end} / {len(self.matches):,} 条 · 共 {len(self.rows):,} 条')
            self.update_controls()

        if needed:
            self.start('正在加载本机媒体预览…', lambda _: self.preview_source.prepare(needed), show,
                       lambda: show({}))
        else:
            show({})

    def members(self, name):
        if not name:
            QMessageBox.information(self, '群成员', '请输入准确群名或群的内部用户名。')
            return

        def saved(result):
            report, stem = result
            self.last_output = stem.parent
            self.reload_rosters()
            self.statusBar().showMessage(f'已导出 {report["member_count"]} 位群成员')

        self.start('正在读取微信群成员…', lambda _: export_members(name), saved)

    def export_chat(self, kind):
        if self.job is not None or not self.rows or not self.entry.get('can_export', True):
            return
        dialog = ExportOptions(self, kind, self.rows)
        if dialog.exec_() != QDialog.Accepted:
            return
        options = dialog.options()
        username = self.entry['username']
        library = self.library if self.live() else None
        snapshot = self.snapshot

        def work(log):
            selected = snapshot
            if selected is None:
                engine.refresh(log=log, chat=username, library=library)
                selected = engine.saved_snapshots(self.cfg)[username]
            if kind != 'archive':
                file, count = export_format(selected, kind, log=log, **options)
                return file.parent, count
            base = options['output_root']
            output = (Path(base) / ('导出_' + datetime.now(engine.TZ).strftime('%Y%m%d_%H%M%S_%f'))) if base else None
            folder, report = engine.export_chat(username, output=output, log=log, snapshot=selected,
                                                decode_images=options['decode_images'])
            return folder, report['messages']

        def saved(result):
            self.last_output, count = result
            self.snapshots = engine.saved_snapshots(self.cfg)
            self.statusBar().showMessage(f'已导出 {count:,} 条消息，可打开输出目录')

        self.start('正在导出所选会话…', work, saved)

    def conversation_report(self):
        if self.job is not None or not self.rows:
            return
        years = sorted({int(row['time'][:4]) for row in self.rows if row.get('timestamp')}, reverse=True)
        if not years:
            QMessageBox.information(self, '年度报告', '此会话没有带有效时间的消息。')
            return
        dialog = AnnualOptions(self, '当前会话年度报告', years=years)
        if dialog.exec_() != QDialog.Accepted:
            return
        year = dialog.selected_year()
        entry = self.entry
        rows = self.rows
        source = ('微信本机' if self.live() else '本地快照') + ' · ' + entry['kind']
        directory = engine.ROOT.parent / entry['kind'] / engine.safe_name(entry['name']) / '年度报告'

        def work(_):
            stats, names = conversation_stats(year, rows, self.cfg['self_username'])
            return write_report(stats, entry['name'], source, names, directory)

        self.start('正在生成当前会话年度报告…', work, self.report_ready)

    def personal_report(self):
        if self.job is not None:
            return
        snapshots = tuple(self.snapshots.values())
        dialog = AnnualOptions(self, '我的年度聊天报告', snapshot_count=len(snapshots), prefer_live=self.live())
        if dialog.exec_() != QDialog.Accepted:
            return
        year = dialog.selected_year()
        live = dialog.sources.currentData() == 'live'
        library = self.library
        directory = engine.ROOT.parent / '个人' / '年度报告'

        def work(log):
            source_library = (library or engine.live_sessions(log=log)) if live else None
            stats, names, source = personal_stats(year, self.cfg['self_username'], library=source_library,
                                                  snapshots=snapshots, log=log)
            return write_report(stats, '我的聊天年度总览', source, names, directory, personal=True)

        self.start('正在统计年度聊天记录…', work, self.report_ready)

    def report_ready(self, result):
        path, count = result
        self.last_output = path.parent
        QDesktopServices.openUrl(local_url(path))
        self.statusBar().showMessage(f'年度报告已生成 · {count:,} 条本机消息')

    def play_voice(self, selection):
        record, source = selection
        snapshot = self.snapshot
        library = self.library if self.live() else None

        def work(_):
            import pysilk
            buffer = source.read_bytes() if source and source.suffix.lower() == '.silk' else read_voice(
                record['server_id'], library=library, snapshot=snapshot)
            if not buffer:
                raise ValueError('本机没有保存这条语音的音频。')
            folder = engine.ROOT / '本地数据/语音缓存'
            folder.mkdir(parents=True, exist_ok=True)
            file = folder / (hashlib.sha256(buffer).hexdigest() + '.wav')
            if not file.is_file():
                file.write_bytes(pysilk.decode(buffer, to_wav=True, sample_rate=24000))
            return file

        def play(file):
            import winsound
            winsound.PlaySound(str(file), winsound.SND_FILENAME | winsound.SND_ASYNC)
            self.statusBar().showMessage('正在播放本机语音')

        self.start('正在准备本机语音…', work, play)

    def update_controls(self):
        available = self.job is None
        for widget in (*self.tool_controls, self.chat.connect_button, self.chat.lineEdit, self.chat.source,
                       self.chat.listWidget, self.panel.search, self.panel.kinds):
            widget.setEnabled(available)
        self.chat.connect_button.setText('刷新全部微信会话' if self.library is not None else '读取全部微信会话')
        selected = bool(self.entry)
        self.panel.refresh.setEnabled(available and selected)
        self.panel.members.setVisible(selected and self.entry['kind'] == '群聊')
        self.panel.members.setEnabled(available and selected)
        exportable = available and bool(self.rows) and self.entry.get('can_export', True)
        self.panel.tool_button.setEnabled(exportable)
        self.panel.export_menu.setEnabled(exportable)
        self.panel.annual_report.setEnabled(available and bool(self.rows))
        self.conversation_report_action.setEnabled(available and bool(self.rows))
        self.personal_report_button.setEnabled(available)
        self.personal_report_action.setEnabled(available)
        self.panel.earlier.setEnabled(available and len(self.matches) > (self.page + 1) * PAGE_SIZE)
        self.panel.later.setEnabled(available and self.page > 0)
        self.open_output.setEnabled(self.last_output is not None)
        self.progress.setVisible(not available)

    def start(self, title, work, done, failed=None):
        if self.job is not None:
            return
        self.statusBar().showMessage(title)
        self.job = job = Job(work, self)
        job.progress.connect(self.statusBar().showMessage)

        def finish():
            self.job = None
            try:
                if job.error:
                    if failed:
                        failed()
                    raise RuntimeError(job.error)
                done(job.value)
            except Exception as error:
                self.statusBar().showMessage('操作未完成')
                QMessageBox.warning(self, '留痕本地恢复版', str(error))
            finally:
                job.deleteLater()
                self.update_controls()

        job.finished.connect(finish)
        self.update_controls()
        job.start()

    def closeEvent(self, event):
        if self.job is not None:
            self.statusBar().showMessage('请等待当前读取或导出结束。')
            event.ignore()
            return
        super().closeEvent(event)


def run():
    app = qt_application()
    window = RecoveryWindow()
    window.show()
    return app.exec_()
