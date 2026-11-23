"""Read-only local Weixin SQLCipher adapter. No keys or process dumps are saved.

Based on MemoTrace's documented SQLCipher 4 page layout (MIT).
Only the explicitly selected Weixin executable is inspected.
"""
import ctypes
from ctypes import wintypes as W
import hashlib
import hmac
from pathlib import Path
import re
import struct
import time

PAGE = 4096
RESERVE = 80


def mac_key(key, salt):
    return hashlib.pbkdf2_hmac('sha512', key, bytes(v ^ 0x3a for v in salt), 2, 32)


def valid_key(key, page, number=1, salt=None):
    salt = salt or page[:16]
    start = 16 if number == 1 else 0
    actual = hmac.digest(mac_key(key, salt), page[start:-64] + struct.pack('<I', number), 'sha512')
    return hmac.compare_digest(actual, page[-64:])


def derive(passphrase, salt):
    return hashlib.pbkdf2_hmac('sha512', passphrase, salt, 256000, 32)


def decrypt_page(page, number, key, salt):
    from Crypto.Cipher import AES
    if len(page) != PAGE:
        raise ValueError('Incomplete database page')
    if not any(page):
        return page
    if not valid_key(key, page, number, salt):
        raise ValueError(f'Page authentication failed: {number}')
    start = 16 if number == 1 else 0
    plain = AES.new(key, AES.MODE_CBC, page[-80:-64]).decrypt(page[start:-80])
    return (b'SQLite format 3\x00' if number == 1 else b'') + plain + page[-80:]


class MemoryReader:
    """PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, never write/inject/debug."""
    def __init__(self, pid):
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.ReadProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        self.k.ReadProcessMemory.restype = W.BOOL
        self.k.VirtualQueryEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        self.k.VirtualQueryEx.restype = ctypes.c_size_t
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.handle = self.k.OpenProcess(0x10 | 0x400, False, pid)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), 'Cannot read selected Weixin process')

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None

    def read(self, address, size):
        buf = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        self.k.ReadProcessMemory(self.handle, address, buf, size, ctypes.byref(count))
        return buf.raw[:count.value]

    def regions(self):
        class MBI(ctypes.Structure):
            _fields_ = [('base', ctypes.c_void_p), ('allocation', ctypes.c_void_p),
                        ('allocation_protect', W.DWORD), ('size', ctypes.c_size_t),
                        ('state', W.DWORD), ('protect', W.DWORD), ('type', W.DWORD)]
        addr = 0
        info = MBI()
        while self.k.VirtualQueryEx(self.handle, addr, ctypes.byref(info), ctypes.sizeof(info)):
            end = int(info.base or 0) + info.size
            if end <= addr:
                break
            if info.state == 0x1000 and (info.protect & 0xff) & 0xe6 and not info.protect & 0x100:
                yield int(info.base), info.size
            addr = end


def find_keys(db_root, exe, log=print, timeout=180, relative_files=None):
    """Return authenticated per-file raw keys, in memory only.

    Two formats: SQLCipher's raw key+salt strings, and MemoTrace 4.0's
    32-byte std::string pointer. Candidate values are never logged.
    """
    import psutil
    exe = Path(exe).resolve()
    processes = []
    for p in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
        if p.info['name'] == 'Weixin.exe' and p.info['exe'] and Path(p.info['exe']).resolve() == exe:
            if any(a.startswith('--type=') for a in (p.info['cmdline'] or [])):
                continue
            processes.append(p.info['pid'])
    if not processes:
        raise RuntimeError('Selected Weixin.exe is not running; log in to Weixin first.')
    pages = {}
    paths = [Path(db_root) / rel for rel in relative_files] if relative_files is not None else Path(db_root).rglob('*.db')
    for path in paths:
        if not path.resolve().is_relative_to(Path(db_root).resolve()):
            raise ValueError('Selected database path is outside account db_storage')
        with path.open('rb') as f:
            first = f.read(PAGE)
        if len(first) == PAGE and not first.startswith(b'SQLite format 3\x00'):
            pages[path.relative_to(db_root).as_posix()] = first
    by_salt = {}
    for rel, first in pages.items():
        by_salt.setdefault(first[:16], []).append(rel)
    found = {}
    candidates = set()
    deadline = time.monotonic() + timeout
    raw_pattern = re.compile(rb"(?<![0-9a-fA-F])([0-9a-fA-F]{96})(?![0-9a-fA-F])")
    pointer_pattern = re.compile(rb'(.{8})\x00{8}\x20\x00{7}\x2f\x00{7}', re.S)
    scanned = 0
    log(f'Read-only inspection of {len(processes)} selected Weixin process(es).')
    for pid in processes:
        reader = MemoryReader(pid)
        try:
            # Windows 4.1+ WCDB Config.Cipher adapter, adapted from jiatj/wechatapi
            # db.py (Apache-2.0). See ../研究记录/参考源码 and THIRD_PARTY_NOTICES.md.
            def locate(needles):
                hits = {needle: [] for needle in needles}
                for address, size in reader.regions():
                    for offset in range(0, size, 4 * 1024 * 1024):
                        if time.monotonic() > deadline:
                            raise TimeoutError('Read-only Config.Cipher scan timed out')
                        block = reader.read(address + offset, min(size - offset, 4 * 1024 * 1024 + 128))
                        for needle in needles:
                            start = 0
                            while True:
                                pos = block.find(needle, start)
                                if pos < 0:
                                    break
                                hits[needle].append(address + offset + pos)
                                start = pos + 1
                return hits
            name = b'com.Tencent.WCDB.Config.Cipher'
            addresses = set(locate([name])[name])
            pairs = {struct.pack('<QQ', a, len(name)) for a in addresses}
            nodes = locate(pairs)
            mask = bytes.fromhex('d2c7442458020000004889442450488b450048844c2448488944254048584c24')
            tested = set()
            for found_nodes in nodes.values():
                for address in found_nodes:
                    node = reader.read(address - 16, 80)
                    if len(node) < 64:
                        continue
                    pointer, length = struct.unpack_from('<QQ', node, 16)
                    if pointer not in addresses or length != len(name):
                        continue
                    config = struct.unpack_from('<Q', node, 40)[0]
                    obj = reader.read(config + 0x88, 40)
                    if len(obj) < 24:
                        continue
                    pointer, size = struct.unpack_from('<QQ', obj, 8)
                    if not (0x10000 <= pointer < 0x800000000000 and 0 < size <= 1024):
                        continue
                    blob = reader.read(pointer, size)
                    decoded = bytes(v ^ mask[i % len(mask)] for i, v in enumerate(blob))
                    for match in re.finditer(rb"[xX]'([0-9a-fA-F]{64,192})'", decoded):
                        run = match.group(1)
                        starts = {0}
                        if len(run) > 96:
                            starts.update(range(0, len(run) - 63, 32))
                            starts.add(len(run) - 64)
                        for start in starts:
                            key = bytes.fromhex(run[start:start + 64].decode('ascii'))
                            if key in tested or len(set(key)) < 15:
                                continue
                            tested.add(key)
                            for rel, first in pages.items():
                                if rel not in found and valid_key(key, first):
                                    found[rel] = key
            log(f'Config.Cipher objects checked; authenticated {len(found)}/{len(pages)} databases.')
            if found:
                if len(found) == len(pages):
                    return found
                continue  # do not run the slower legacy scan on a recognized 4.1 format
            for address, size in reader.regions():
                if time.monotonic() > deadline:
                    raise TimeoutError('Read-only key scan timed out')
                for offset in range(0, size, 4 * 1024 * 1024):
                    if time.monotonic() > deadline:
                        raise TimeoutError('Read-only key scan timed out')
                    data = reader.read(address + offset, min(size - offset, 4 * 1024 * 1024 + 128))
                    scanned += len(data)
                    for m in raw_pattern.finditer(data):
                        raw = bytes.fromhex(m.group(1).decode('ascii'))
                        key, salt = raw[:32], raw[32:]
                        for rel in by_salt.get(salt, []):
                            if rel not in found and valid_key(key, pages[rel]):
                                found[rel] = key
                    for m in pointer_pattern.finditer(data):
                        pointer = int.from_bytes(m.group(1), 'little')
                        if 0x10000 <= pointer < 0x7fffffffffff:
                            candidate = reader.read(pointer, 32)
                            if len(candidate) == 32:
                                candidates.add(candidate)
            log(f'Inspected selected Weixin process: {scanned // 1048576} MiB; authenticated {len(found)}/{len(pages)} databases.')
        finally:
            reader.close()
    # Legacy 4.0 mode. Keep work bounded; prioritize contact DB for a functional probe.
    unresolved = [r for r in pages if r not in found]
    unresolved.sort(key=lambda r: (not r.endswith('contact.db'), r))
    if unresolved and candidates:
        log(f'Checking {len(candidates)} legacy candidates against one database (bounded).')
        probe = unresolved[0]
        for i, candidate in enumerate(candidates):
            if time.monotonic() > deadline:
                break
            key = derive(candidate, pages[probe][:16])
            if valid_key(key, pages[probe]):
                for rel in unresolved:
                    key = derive(candidate, pages[rel][:16])
                    if valid_key(key, pages[rel]):
                        found[rel] = key
                break
    log(f'Authenticated {len(found)}/{len(pages)} database keys; no key values saved.')
    return found


def wal_checksum(data, state=(0, 0), endian='<'):
    if len(data) % 8:
        raise ValueError('Invalid WAL checksum block length')
    s0, s1 = state
    words = struct.unpack(endian + 'I' * (len(data) // 4), data)
    for n in range(0, len(words), 2):
        s0 = (s0 + words[n] + s1) & 0xffffffff
        s1 = (s1 + words[n + 1] + s0) & 0xffffffff
    return s0, s1


def _stat(path):
    try:
        s = path.stat()
        return s.st_size, s.st_mtime_ns
    except FileNotFoundError:
        return None


def snapshot_bytes(path, tries=4):
    """Base file plus WAL from a stable file-stat interval; source is read-only."""
    path = Path(path)
    walpath = Path(str(path) + '-wal')
    for _ in range(tries):
        before = (_stat(path), _stat(walpath))
        data = path.read_bytes()
        wal = walpath.read_bytes() if walpath.exists() else b''
        after = (_stat(path), _stat(walpath))
        if before == after:
            return data, wal
        time.sleep(0.3)
    raise RuntimeError(f'Database changed during snapshot: {path.name}; retry when Weixin is idle.')


def decrypt_snapshot(source, target, key):
    """Authenticate every page; overlay committed WAL pages, then SQLite quick_check."""
    import sqlite3
    source, target = Path(source), Path(target)
    data, wal = snapshot_bytes(source)
    salt = data[:16]
    if len(data) % PAGE:
        raise ValueError('Database length is not a whole number of pages')
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.pending')
    try:
        with temp.open('wb') as out:
            for offset in range(0, len(data), PAGE):
                out.write(decrypt_page(data[offset:offset + PAGE], offset // PAGE + 1, key, salt))
            committed = []
            frames = []
            final_size = 0
            if wal:
                if len(wal) < 32 or struct.unpack('>I', wal[:4])[0] not in (0x377f0682, 0x377f0683):
                    raise ValueError('Invalid WAL header')
                if struct.unpack('>I', wal[8:12])[0] != PAGE:
                    raise ValueError('Unsupported WAL page size')
                endian = '<' if wal[:4] == bytes.fromhex('377f0682') else '>'
                checksum = wal_checksum(wal[:24], endian=endian)
                if checksum != struct.unpack('>II', wal[24:32]):
                    raise ValueError('WAL header checksum failed')
                wal_salt = wal[16:24]
                for pos in range(32, len(wal) - 24 - PAGE + 1, 24 + PAGE):
                    frame = wal[pos:pos + 24 + PAGE]
                    if frame[8:16] != wal_salt:
                        break  # allocated but inactive WAL tail
                    next_checksum = wal_checksum(frame[:8] + frame[24:], checksum, endian)
                    if next_checksum != struct.unpack('>II', frame[16:24]):
                        break  # incomplete or inactive tail, not a committed transaction
                    checksum = next_checksum
                    number, commit_size = struct.unpack('>II', frame[:8])
                    if not number:
                        break
                    frames.append((number, frame[24:]))
                    if commit_size:
                        committed = list(frames)
                        final_size = commit_size
                for number, page in committed:
                    out.seek((number - 1) * PAGE)
                    out.write(decrypt_page(page, number, key, salt))
                if final_size:
                    out.truncate(final_size * PAGE)
        conn = sqlite3.connect(f'{temp.as_uri()}?mode=ro', uri=True)
        try:
            result = conn.execute('PRAGMA quick_check').fetchall()
            if result != [('ok',)]:
                raise ValueError(f'SQLite quick_check failed: {result[:3]}')
        finally:
            conn.close()
        temp.replace(target)
        return {'file': source.name, 'base_bytes': len(data), 'wal_bytes': len(wal),
                'committed_wal_frames': len(committed), 'quick_check': 'ok'}
    finally:
        if temp.exists():
            temp.unlink()
