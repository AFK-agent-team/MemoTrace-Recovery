"""Regression tests using synthetic encrypted databases; no live Weixin needed."""
from pathlib import Path
import hashlib
import hmac
import json
import os
import sqlite3
import struct
import tempfile
import unittest
import apsw
from Crypto.Cipher import AES
from local_crypto import PAGE, decrypt_page, valid_key, mac_key, wal_checksum
from local_vfs import ScopedDB
from memotrace_local import ROOT, config, raw_messages, render_html, readable_message, saved_snapshots


def encrypt_page(plain, number, key, salt):
    iv = os.urandom(16)
    offset = 16 if number == 1 else 0
    body = AES.new(key, AES.MODE_CBC, iv).encrypt(plain[offset:-80])
    digest = hmac.digest(mac_key(key, salt), body + iv + struct.pack('<I', number), 'sha512')
    return (salt if number == 1 else b'') + body + iv + digest


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        cache = ROOT / '运行缓存'
        cache.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=cache)
        self.folder = Path(self.temp.name)
        self.key, self.salt = os.urandom(32), os.urandom(16)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self):
        path = self.folder / 'plain.db'
        db = apsw.Connection(str(path))
        db.reserve_bytes(reserve=80)
        db.execute('VACUUM')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE target(value TEXT)')
        db.execute('CREATE TABLE unrelated(value TEXT)')
        db.execute("INSERT INTO target VALUES ('before checkpoint')")
        db.wal_checkpoint(mode=apsw.SQLITE_CHECKPOINT_TRUNCATE)
        base = path.read_bytes()
        db.execute("INSERT INTO target VALUES ('committed WAL message')")
        db.execute("INSERT INTO unrelated VALUES ('not selected')")
        wal = Path(str(path) + '-wal').read_bytes()
        db.close()
        encrypted = self.folder / 'encrypted.db'
        encrypted.write_bytes(b''.join(encrypt_page(base[i:i + PAGE], i // PAGE + 1, self.key, self.salt)
                                       for i in range(0, len(base), PAGE)))
        new_wal = bytearray(wal[:32])
        endian = '<' if wal[:4] == bytes.fromhex('377f0682') else '>'
        checksum = wal_checksum(bytes(new_wal[:24]), endian=endian)
        new_wal[24:32] = struct.pack('>II', *checksum)
        for pos in range(32, len(wal) - 24 - PAGE + 1, 24 + PAGE):
            frame = bytearray(wal[pos:pos + 24 + PAGE])
            if frame[8:16] != wal[16:24]:
                break
            number = struct.unpack('>I', frame[:4])[0]
            frame[24:] = encrypt_page(bytes(frame[24:]), number, self.key, self.salt)
            checksum = wal_checksum(bytes(frame[:8] + frame[24:]), checksum, endian)
            frame[16:24] = struct.pack('>II', *checksum)
            new_wal.extend(frame)
        Path(str(encrypted) + '-wal').write_bytes(new_wal)
        return encrypted

    def test_page_roundtrip_and_authentication(self):
        plain = b'SQLite format 3\x00' + os.urandom(PAGE - 16 - 80) + b'\0' * 80
        encrypted = encrypt_page(plain, 1, self.key, self.salt)
        decoded = decrypt_page(encrypted, 1, self.key, self.salt)
        self.assertEqual(decoded[:-80], plain[:-80])
        damaged = bytearray(encrypted)
        damaged[121] ^= 1
        with self.assertRaises(ValueError):
            decrypt_page(bytes(damaged), 1, self.key, self.salt)
        self.assertFalse(valid_key(os.urandom(32), encrypted))

    def test_scoped_vfs_reads_committed_wal_and_blocks_write(self):
        path = self.fixture()
        before = hashlib.sha256(path.read_bytes()).digest()
        with ScopedDB(path, self.key) as db:
            rows = db.query('SELECT value FROM target ORDER BY rowid')
            self.assertEqual([r['value'] for r in rows], ['before checkpoint', 'committed WAL message'])
            self.assertEqual(db.query('SELECT value FROM target WHERE 0'), [])
            with self.assertRaises(apsw.ReadOnlyError):
                db.query('INSERT INTO target VALUES (?)', ('blocked',))
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_wrong_key_is_rejected_before_query(self):
        with self.assertRaises(ValueError):
            ScopedDB(self.fixture(), os.urandom(32))

    def test_wal_header_corruption_rejected(self):
        path = self.fixture()
        wal = Path(str(path) + '-wal')
        data = bytearray(wal.read_bytes())
        data[24] ^= 1
        wal.write_bytes(data)
        with self.assertRaises(ValueError):
            ScopedDB(path, self.key)

    def test_source_mutation_is_detected(self):
        path = self.fixture()
        with ScopedDB(path, self.key) as db:
            os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1000000000))
            with self.assertRaises(RuntimeError):
                db.query('SELECT value FROM target')

    def test_html_escapes_chat_markup(self):
        rows = [{'time': '2026-01-01', 'sender': '<svg onload=x>', 'type_name': '文本',
                 'text': '<script>alert(1)</script>', 'attachments': []}]
        render_html({'name': 'test'}, rows, self.folder)
        page = (self.folder / '聊天记录.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn("default-src 'none'", page)

    def test_saved_snapshot_contains_no_other_chat_tables(self):
        if not (ROOT / '本地配置.json').is_file():
            self.skipTest('No configured local account')
        snapshots = saved_snapshots(config())
        if not snapshots:
            self.skipTest('No saved snapshot')
        username, snapshot = next(iter(snapshots.items()))
        expected = 'Msg_' + hashlib.md5(username.encode()).hexdigest()
        count = 0
        for path in (snapshot.db_dir / 'message').glob('*.db'):
            if not path.name.startswith(('message_', 'biz_message_')):
                continue
            conn = sqlite3.connect(path)
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'Msg_%'")}
                self.assertTrue(tables <= {expected})
                if expected in tables:
                    count += conn.execute(f'SELECT count(*) FROM "{expected}"').fetchone()[0]
            finally:
                conn.close()
        report = json.loads((snapshot.db_dir.parent / 'snapshot_report.json').read_text(encoding='utf-8'))
        self.assertEqual(count, report['messages'])
        self.assertEqual(len(raw_messages(username, snapshot=snapshot)), count)


if __name__ == '__main__':
    unittest.main(verbosity=2)
