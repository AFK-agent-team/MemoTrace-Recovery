"""Target-local image decoder. Derived from MemoTrace MIT and wechatapi Apache-2.0.

Only decodes files explicitly referenced by the selected chat. No downloading,
no image key persistence, no message sending or UI hooks.
"""
from pathlib import Path
import io
import hashlib
import re
import shutil
import struct
from tempfile import NamedTemporaryFile
import time
from Crypto.Cipher import AES
from PIL import Image
from local_crypto import MemoryReader

MAGICS = (b'\xff\xd8\xff', b'\x89PNG', b'GIF8', b'RIFF', b'wxgf')


class ImageDecoder:
    def __init__(self, account, exe):
        self.account = Path(account).resolve()
        self.exe = Path(exe).resolve()
        self.aes_key = None
        self.xor_key = None
        self.scanned = False
        self.allow_scan = False
        self.cache = Path(__file__).resolve().parents[1] / '本地数据/图片缓存'
        self.cache.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def plausible(data):
        return any(data.startswith(m) for m in MAGICS)

    def scan(self, ciphertext):
        import psutil
        self.scanned = True
        deadline = time.monotonic() + 45
        tested = set()
        pattern = re.compile(rb'[0-9a-zA-Z]{16,32}')
        for process in psutil.process_iter(['name', 'exe', 'pid', 'cmdline']):
            if process.info['name'] != 'Weixin.exe' or not process.info['exe'] or Path(process.info['exe']).resolve() != self.exe:
                continue
            if any(a.startswith('--type=') for a in (process.info['cmdline'] or [])):
                continue
            reader = MemoryReader(process.pid)
            try:
                for address, size in reader.regions():
                    for offset in range(0, size, 4 * 1024 * 1024):
                        if time.monotonic() > deadline:
                            return None
                        block = reader.read(address + offset, min(size - offset, 4 * 1024 * 1024 + 32))
                        for match in pattern.finditer(block):
                            key = match.group()[:16]
                            if key in tested:
                                continue
                            tested.add(key)
                            plain = AES.new(key, AES.MODE_ECB).decrypt(ciphertext)
                            if self.plausible(plain):
                                self.aes_key = key
                                return key
            finally:
                reader.close()
        return None

    def __call__(self, source, destination, name):
        cached = self.decoded_path(source)
        result = Path(destination) / f'{name}{cached.suffix}'
        shutil.copyfile(cached, result)
        return result

    def decoded_path(self, source):
        """Return a validated displayable image in the local, content-addressed cache."""
        source = Path(source).resolve()
        if not source.is_relative_to(self.account):
            raise ValueError('Image source is outside the selected account')
        data = source.read_bytes()
        if not data:
            raise ValueError('Image file is empty')
        source_hash = hashlib.sha256(data).hexdigest()
        for suffix in ('.jpg', '.png', '.gif', '.webp'):
            cached = self.cache / (source_hash + suffix)
            if cached.is_file():
                try:
                    with Image.open(cached) as im:
                        im.load()
                    return cached
                except (OSError, ValueError):
                    continue
        if self.plausible(data):
            plain = data
        elif data[:6] in {b'\x07\x08V1\x08\x07', b'\x07\x08V2\x08\x07'}:
            aes_size, xor_size = struct.unpack_from('<II', data, 6)
            padded = (aes_size // 16 + 1) * 16
            if len(data) < 15 + padded + xor_size:
                raise ValueError('Invalid V2 image length')
            ciphertext = data[15:31]
            candidates = [k for k in (self.aes_key, b'43e7d25eb1b9bb64', b'cfcd208495d565ef') if k]
            key = next((k for k in candidates if self.plausible(AES.new(k, AES.MODE_ECB).decrypt(ciphertext))), None)
            if key is None and not self.scanned and self.allow_scan:
                key = self.scan(ciphertext)
            if key is None:
                raise ValueError('Image AES key unavailable; open a group image in Weixin then retry decoding')
            self.aes_key = key
            tail = data[-xor_size:] if xor_size else b''
            if xor_size and len(tail) >= 2 and (tail[-2] ^ 0xff) == (tail[-1] ^ 0xd9):
                self.xor_key = tail[-2] ^ 0xff
            aes = AES.new(key, AES.MODE_ECB).decrypt(data[15:15 + padded])
            pad = aes[-1]
            if not 1 <= pad <= 16 or aes[-pad:] != bytes([pad]) * pad:
                raise ValueError('Image padding check failed')
            end = len(data) - xor_size if xor_size else len(data)
            middle = data[15 + padded:end]
            xkey = self.xor_key if self.xor_key is not None else 0x88
            plain = aes[:-pad] + middle + bytes(v ^ xkey for v in tail)
        else:
            plain = None
            for magic in (b'\xff\xd8\xff', b'\x89PNG', b'GIF8'):
                key = data[0] ^ magic[0]
                candidate = bytes(v ^ key for v in data)
                if candidate.startswith(magic):
                    plain = candidate
                    break
            if plain is None:
                raise ValueError('Unknown image container')
        if plain.startswith(b'wxgf'):
            raise ValueError('WXGF container retained; display conversion is not yet supported')
        # A full image decode must succeed before it is reported as recovered.
        with Image.open(io.BytesIO(plain)) as im:
            im.load()
            suffix = {'JPEG': 'jpg', 'PNG': 'png', 'GIF': 'gif', 'WEBP': 'webp'}.get(im.format, 'png')
        result = self.cache / f'{source_hash}.{suffix}'
        handle = NamedTemporaryFile(dir=self.cache, prefix='decode-', delete=False)
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(plain)
            temporary.replace(result)
        finally:
            temporary.unlink(missing_ok=True)
        return result
