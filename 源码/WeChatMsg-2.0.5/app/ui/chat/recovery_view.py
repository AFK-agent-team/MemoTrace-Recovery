"""The 2.0.5 chat forms and bubble widgets with a WeChat 4.x data source."""
from pathlib import Path

from PyQt5.QtCore import QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QFontMetrics, QImageReader, QMovie, QPixmap
from PyQt5.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidgetItem, QMenu,
                             QPlainTextEdit, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QToolButton,
                             QVBoxLayout, QWidget)

from app.components.bubble_message import BubbleMessage, ChatWidget, ImageMessage, MessageType, Notice
from app.ui.chat.chatInfoUi import Ui_Form as ChatInfoForm
from app.ui.chat.chatUi import Ui_Form as ConversationForm

from desktop_data import DEFAULT_AVATAR, emoji_html, message_key
from desktop_exports import FORMATS, body


ENTRY = Qt.UserRole
PAGE_SIZE = 45


class Avatars:
    def __init__(self):
        self.data = {}
        self.pixmaps = {}
        self.default = QPixmap(str(DEFAULT_AVATAR))

    def reset(self, data=None):
        self.data = dict(data or {})
        self.pixmaps.clear()

    def add(self, data):
        for username, content in data.items():
            self.pixmaps.pop(username, None)
            self.data[username] = content

    def get(self, username):
        if username not in self.pixmaps:
            image = QPixmap()
            if (data := self.data.get(username)) and image.loadFromData(data):
                self.pixmaps[username] = image.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            else:
                self.pixmaps[username] = self.default
        return self.pixmaps[username]


class ConversationDelegate(QStyledItemDelegate):
    def __init__(self, avatars, parent):
        super().__init__(parent)
        self.avatars = avatars

    def sizeHint(self, option, index):
        return QSize(228, 62)

    def paint(self, painter, option, index):
        record = index.data(ENTRY)
        if not record:
            super().paint(painter, option, index)
            return
        painter.save()
        rect = option.rect
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, QColor('#dfe9ed'))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(rect, QColor('#e9eff2'))
        icon = self.avatars.get(record['username'])
        painter.drawPixmap(rect.x() + 9, rect.y() + 9, 42, 42, icon)
        left = rect.x() + 60
        font = option.font
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(QColor('#243137'))
        metrics = QFontMetrics(font)
        stamp = (record.get('updated_at') or '')[5:10].replace('-', '/')
        available = rect.width() - 68 - (39 if stamp else 0)
        painter.drawText(left, rect.y() + 24, metrics.elidedText(record['name'], Qt.ElideRight, max(10, available)))
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor('#607075'))
        if stamp:
            painter.drawText(rect.right() - 39, rect.y() + 24, stamp)
        preview = ' '.join((record.get('preview') or '').split())
        painter.drawText(left, rect.y() + 44, QFontMetrics(font).elidedText(preview, Qt.ElideRight, rect.width() - 68))
        painter.restore()


class ChatPage(QWidget, ConversationForm):
    def __init__(self, avatars, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.setStyleSheet('')
        self.verticalLayout_2.setSpacing(8)
        self.horizontalLayout_2.setContentsMargins(7, 7, 7, 7)
        self.lineEdit.setPlaceholderText('搜索会话')
        self.lineEdit.setClearButtonEnabled(True)
        self.lineEdit.setMaximumWidth(16777215)
        self.label.hide()
        self.label_2.hide()
        self.connect_button = QPushButton('读取全部微信会话')
        self.connect_button.setObjectName('connectButton')
        self.verticalLayout_2.insertWidget(0, self.connect_button)
        self.source = QComboBox()
        self.source.addItem('本地快照', 'offline')
        self.source.addItem('微信会话', 'live')
        self.source.model().item(1).setEnabled(False)
        self.verticalLayout_2.insertWidget(1, self.source)
        self.listWidget.setFixedWidth(250)
        self.listWidget.setMouseTracking(True)
        self.listWidget.setUniformItemSizes(True)
        self.listWidget.setItemDelegate(ConversationDelegate(avatars, self.listWidget))
        self.count = QLabel()
        self.count.setObjectName('muted')
        self.verticalLayout_2.addWidget(self.count)
        self.panel = ChatPanel(avatars)
        self.stackedWidget.addWidget(self.panel)
        self.stackedWidget.setCurrentWidget(self.panel)

    def set_sessions(self, sessions, active):
        self.listWidget.blockSignals(True)
        self.listWidget.clear()
        selected = None
        for entry in sessions:
            item = QListWidgetItem(entry['name'])
            item.setData(ENTRY, entry)
            self.listWidget.addItem(item)
            if entry['username'] == active:
                selected = item
        self.listWidget.setCurrentItem(selected)
        self.listWidget.blockSignals(False)
        self.count.setText(f'{len(sessions):,} 个会话')


class ChatPanel(QWidget, ChatInfoForm):
    format_chosen = pyqtSignal(str)
    voice_chosen = pyqtSignal(object)

    def __init__(self, avatars):
        super().__init__()
        self.setupUi(self)
        self.avatars = avatars
        self.btn_clear.hide()
        self.textEdit.hide()
        self.pushButton.hide()
        self.tool_button = self.toolButton
        self.label_reamrk.setTextFormat(Qt.PlainText)
        self.label_reamrk.setWordWrap(True)
        self.label_reamrk.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.label_reamrk.setMinimumWidth(105)
        self.label_reamrk.setObjectName('chatTitle')
        self.horizontalLayout_2.setStretch(0, 1)
        self.tool_button.setText('导出聊天记录')
        self.tool_button.setPopupMode(QToolButton.InstantPopup)
        self.export_menu = QMenu('导出聊天记录', self)
        for kind, (label, filename) in FORMATS.items():
            suffix = Path(filename).suffix
            action = self.export_menu.addAction(f'{label}  ({suffix})')
            action.triggered.connect(lambda _checked, key=kind: self.format_chosen.emit(key))
        self.export_menu.addSeparator()
        self.export_menu.addAction('完整归档（原始记录与附件）', lambda: self.format_chosen.emit('archive'))
        self.tool_button.setMenu(self.export_menu)
        self.refresh = QPushButton('刷新')
        self.annual_report = QPushButton('年度报告')
        self.members = QPushButton('导出群成员')
        self.horizontalLayout_2.insertWidget(self.horizontalLayout_2.count() - 1, self.refresh)
        self.horizontalLayout_2.insertWidget(self.horizontalLayout_2.count() - 1, self.annual_report)
        self.horizontalLayout_2.insertWidget(self.horizontalLayout_2.count() - 1, self.members)
        self.source = QLabel()
        self.source.setObjectName('muted')
        self.source.setTextFormat(Qt.PlainText)
        self.verticalLayout_2.insertWidget(1, self.source)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索正文或发送者')
        self.search.setClearButtonEnabled(True)
        self.kinds = QComboBox()
        self.kinds.addItem('全部类型', None)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.kinds)
        self.verticalLayout_2.insertLayout(2, filters)

        self.scrollArea.hide()
        self.verticalLayout_2.removeWidget(self.scrollArea)
        self.scroller = ChatWidget()
        self.scroller.scrollAreaWidgetContents.setObjectName('history')
        self.verticalLayout_2.addWidget(self.scroller, 1)
        bottom = QHBoxLayout()
        self.result = QLabel()
        self.result.setObjectName('muted')
        self.earlier = QPushButton('较早')
        self.later = QPushButton('较新')
        bottom.addWidget(self.result, 1)
        bottom.addWidget(self.earlier)
        bottom.addWidget(self.later)
        self.verticalLayout_2.addLayout(bottom)
        self.clear('从左侧选择一个会话')

    def clear(self, hint=''):
        layout = self.scroller.layout0
        while layout.count():
            if widget := layout.takeAt(0).widget():
                widget.hide()
                widget.deleteLater()
        if hint:
            notice = Notice(hint)
            notice.setTextFormat(Qt.PlainText)
            self.scroller.add_message_item(notice)
            layout.addStretch()

    def set_types(self, records):
        types = {row['type']: row['type_name'] for row in records}
        self.kinds.blockSignals(True)
        self.kinds.clear()
        self.kinds.addItem('全部类型', None)
        for key, name in sorted(types.items(), key=lambda pair: (pair[1], pair[0])):
            self.kinds.addItem(name, key)
        self.kinds.blockSignals(False)

    def render(self, rows, previews, account, *, newest):
        self.clear()
        previous = None
        for record in rows:
            timestamp = record['timestamp']
            if previous is None or timestamp - previous >= 300:
                clock = Notice(record['time'])
                clock.setObjectName('muted')
                self.scroller.add_message_item(clock)
            previous = timestamp
            self.scroller.add_message_item(self.bubble(record, previews.get(message_key(record)), account))
        self.scroller.layout0.addStretch()
        self.scroller.update()
        if newest:
            QTimer.singleShot(0, self.scroller.set_scroll_bar_last)
        else:
            self.scroller.set_scroll_bar_value(0)

    def bubble(self, row, assets, account):
        value = str(row.get('text') or f'[{row["type_name"]}]')
        if row['sender'] == '系统':
            label = Notice(value)
            label.setTextFormat(Qt.PlainText)
            return label

        own = row['sender_id'] == account
        preview, original, thumb = assets or (None, None, False)
        avatar = self.avatars.get(row['sender_id'])
        max_width = min(450, max(210, int(self.scroller.scrollArea.viewport().width() * 0.70)))
        picture = QPixmap()
        if preview:
            reader = QImageReader(str(preview))
            size = reader.size()
            limit = QSize(min(max_width, 335), 212)
            if size.isValid() and (size.width() > limit.width() or size.height() > limit.height()):
                reader.setScaledSize(size.scaled(limit, Qt.KeepAspectRatio))
            picture = QPixmap.fromImage(reader.read())
        if not picture.isNull():
            image = ImageMessage(picture, own, str(preview), max_width=335, max_height=234)
            image.setFixedSize(image.pixmap().size())
            if preview.suffix.lower() == '.gif':
                movie = QMovie(str(preview), parent=image)
                if movie.isValid():
                    movie.setScaledSize(image.size())
                    image.setMovie(movie)
                    movie.start()
            content = QWidget()
            image_layout = QVBoxLayout(content)
            image_layout.setContentsMargins(0, 0, 0, 0)
            image_layout.addWidget(image)
            if row['type'] == 43 and original and not thumb:
                button = QPushButton('播放本机视频')
                button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(original))))
                image_layout.addWidget(button)
            elif thumb:
                image_layout.addWidget(QLabel('本机缩略图'))
            message_type = MessageType.Image
        else:
            content = QWidget()
            layout = QVBoxLayout(content)
            layout.setContentsMargins(0, 0, 0, 0)
            label = QLabel()
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setTextFormat(Qt.RichText)
            label.setStyleSheet('QLabel { padding:8px; border-radius:7px; background:' +
                                ('#b2e281' if own else '#fff') + '; }')
            text = value[:2000] + ('…' if len(value) > 2000 else '')
            label.setText(emoji_html(text, lambda path: path.as_uri()))
            metrics = QFontMetrics(label.font())
            width = min(max_width, max(60, max(metrics.horizontalAdvance(line) for line in text.split('\n')) + 24))
            label.setFixedWidth(width)
            label.setMinimumHeight(max(metrics.height() + 16, label.heightForWidth(width)))
            layout.addWidget(label)
            if ref := row.get('quote'):
                note = QLabel(f'{ref["sender"]}：{str(ref["text"] or "")[:180]}')
                note.setTextFormat(Qt.PlainText)
                note.setWordWrap(True)
                note.setFixedWidth(width)
                note.setMinimumHeight(max(metrics.height() + 12, note.heightForWidth(width)))
                note.setStyleSheet('padding:6px; background:#e2e8eb; color:#45565e;')
                layout.addWidget(note)
            if row['type'] == 34:
                play = QPushButton('播放语音')
                play.clicked.connect(lambda: self.voice_chosen.emit((row, original)))
                layout.addWidget(play)
            elif original and row['type'] == 25769803825:
                button = QPushButton('打开附件位置')
                button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(original.parent))))
                layout.addWidget(button)
            elif original and row['type'] == 43:
                button = QPushButton('播放本机视频')
                button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(original))))
                layout.addWidget(button)
            elif row.get('nested_messages'):
                button = QPushButton('查看转发内容')
                button.clicked.connect(lambda: self.details(row))
                layout.addWidget(button)
            message_type = MessageType.Text

        content.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        display_name = QFontMetrics(self.font()).elidedText(str(row['sender']), Qt.ElideRight, max_width)
        bubble = BubbleMessage('', avatar, message_type, own, display_name, content_widget=content)
        bubble.setContextMenuPolicy(Qt.CustomContextMenu)
        bubble.customContextMenuRequested.connect(lambda point: self.message_menu(bubble, point, row))
        return bubble

    def message_menu(self, widget, point, row):
        menu = QMenu(widget)
        menu.addAction('查看全文与来源', lambda: self.details(row))
        menu.exec_(widget.mapToGlobal(point))

    def details(self, row):
        dialog = QDialog(self)
        dialog.setWindowTitle('消息详情')
        dialog.resize(665, 470)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(f'{row["time"]}  {row["sender"]}  [{row["type_name"]}]\n\n{body(row)}\n\n'
                          f'来源  {row["source_db"]} / local_id={row["local_id"]}')
        QVBoxLayout(dialog).addWidget(text)
        dialog.exec_()
