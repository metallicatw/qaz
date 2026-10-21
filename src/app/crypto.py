from __future__ import annotations

import base64
import os
from pathlib import Path

from nacl import secret, utils


def _box() -> secret.SecretBox:
    k = os.environ.get("APP_KEY")
    if not k:
        raise RuntimeError("APP_KEY not set")
    return secret.SecretBox(base64.b64decode(k))


def keygen() -> str:
    return base64.b64encode(utils.random(secret.SecretBox.KEY_SIZE)).decode()


def encrypt_bytes(data: bytes) -> bytes:
    return _box().encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    return _box().decrypt(data)


def encrypt_file(src: Path, dst: Path | None = None) -> Path:
    dst = dst or src.with_name(src.name + ".enc")
    dst.write_bytes(encrypt_bytes(src.read_bytes()))
    return dst


def decrypt_file(src: Path, dst: Path) -> Path:
    dst.write_bytes(decrypt_bytes(src.read_bytes()))
    return dst


def pack_dir(src: Path, dst: Path) -> Path:
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in sorted(src.rglob("*")):
            if p.is_file() and p.suffix != ".enc":
                tar.add(p, arcname=str(p.relative_to(src)))
    dst.write_bytes(encrypt_bytes(buf.getvalue()))
    return dst


def unpack_dir(src: Path, dst: Path) -> Path:
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(decrypt_bytes(src.read_bytes())), mode="r:gz") as tar:
        tar.extractall(dst, filter="data")
    return dst
