"""Lazy read-only SQLCipher view: only SQL-requested pages are decrypted.

No complete plaintext database or unrelated chat archive is written to disk.
Committed WAL pages are overlaid in memory. File-stat drift aborts a query.
"""
from pathlib import Path
import struct
import uuid
import apsw
from local_crypto import PAGE, decrypt_page, valid_key, wal_checksum, _stat


class SourceChangedError(RuntimeError):
    """The WeChat database or WAL changed during a read."""


class EncryptedView:
    def __init__(self, path, key):
        self.path = Path(path).resolve()
        self.key = key
        self.wal_path = Path(str(self.path) + '-wal')
        self.stamp = (_stat(self.path), _stat(self.wal_path))
        self.file = self.path.open('rb')
        try:
            self._initialize()
        except BaseException:
            self.file.close()
            raise

    def _initialize(self):
        self.salt = self.file.read(16)
        self.file.seek(0)
        if not valid_key(self.key, self.file.read(PAGE)):
            raise ValueError('Database key authentication failed')
        self.size = self.path.stat().st_size
        self.overlay = {}
        self.read_pages = set()
        wal = self.wal_path.read_bytes() if self.wal_path.exists() else b''
        if wal:
            if len(wal) < 32 or wal[:4] not in (bytes.fromhex('377f0682'), bytes.fromhex('377f0683')):
                raise ValueError('Invalid WAL header')
            if struct.unpack('>I', wal[8:12])[0] != PAGE:
                raise ValueError('Unsupported WAL page size')
            endian = '<' if wal[:4] == bytes.fromhex('377f0682') else '>'
            checksum = wal_checksum(wal[:24], endian=endian)
            if checksum != struct.unpack('>II', wal[24:32]):
                raise ValueError('WAL header checksum failed')
            pending = {}
            for pos in range(32, len(wal) - 24 - PAGE + 1, 24 + PAGE):
                frame = wal[pos:pos + 24 + PAGE]
                if frame[8:16] != wal[16:24]:
                    break
                checksum = wal_checksum(frame[:8] + frame[24:], checksum, endian)
                if checksum != struct.unpack('>II', frame[16:24]):
                    break
                page, commit = struct.unpack('>II', frame[:8])
                if not page:
                    break
                pending[page] = frame[24:]
                if commit:
                    self.overlay.update(pending)
                    pending.clear()
                    self.size = commit * PAGE
        self.assert_stable()

    def assert_stable(self):
        if self.stamp != (_stat(self.path), _stat(self.wal_path)):
            raise SourceChangedError(f'{self.path.name} 读取期间发生变化，请稍后重试。')

    def read(self, amount, offset):
        result = bytearray()
        first = offset // PAGE + 1
        last = (offset + amount - 1) // PAGE + 1
        for number in range(first, last + 1):
            self.read_pages.add(number)
            page = self.overlay.get(number)
            if page is None:
                self.file.seek((number - 1) * PAGE)
                page = self.file.read(PAGE)
            if len(page) != PAGE:
                raise apsw.IOError('Short encrypted page read')
            plain = decrypt_page(page, number, self.key, self.salt)
            if number == 1:
                # WAL is already merged into this immutable logical view.
                plain = plain[:18] + b'\x01\x01' + plain[20:]
            result.extend(plain)
        start = offset % PAGE
        return bytes(result[start:start + amount])

    def close(self):
        self.file.close()
        self.overlay.clear()


class ReadOnlyFile(apsw.VFSFile):
    def __init__(self, name, flags, view):
        super().__init__('', name, flags)
        self.view = view

    def xRead(self, amount, offset):
        return self.view.read(amount, offset)

    def xFileSize(self):
        return self.view.size

    def xWrite(self, data, offset):
        raise apsw.ReadOnlyError('Source database is read-only')

    def xTruncate(self, size):
        raise apsw.ReadOnlyError('Source database is read-only')


class ScopedVFS(apsw.VFS):
    def __init__(self, view):
        self.name = 'memotrace_scoped_' + uuid.uuid4().hex
        self.view = view
        super().__init__(self.name, '')

    def xOpen(self, name, flags):
        path = name.filename() if isinstance(name, apsw.URIFilename) else str(name)
        if Path(path).resolve() != self.view.path or not flags[0] & apsw.SQLITE_OPEN_READONLY:
            raise apsw.ReadOnlyError('Only selected database read is allowed')
        return ReadOnlyFile(name, flags, self.view)

    def xAccess(self, pathname, flags):
        if str(pathname).endswith(('-wal', '-journal', '-shm')):
            return False
        return super().xAccess(pathname, flags)


class ScopedDB:
    def __init__(self, path, key):
        self.view = EncryptedView(path, key)
        self.vfs = ScopedVFS(self.view)
        uri = self.view.path.as_uri() + '?immutable=1'
        self.conn = apsw.Connection(uri, flags=apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI, vfs=self.vfs.name)
        self.conn.execute('PRAGMA mmap_size=0')
        self.conn.execute('PRAGMA temp_store=MEMORY')
        self.conn.execute('PRAGMA query_only=ON')
        self.conn.set_row_trace(lambda cursor, row: dict(zip([c[0] for c in cursor.get_description()], row)))

    def query(self, sql, params=()):
        result = list(self.conn.execute(sql, params))
        self.view.assert_stable()
        return result

    def close(self):
        self.conn.close()
        self.vfs.unregister()
        self.view.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
