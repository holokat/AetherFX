"""Reference-image attachments for the Generate box.

Files are stored under <output_dir>/attachments/<id>.<ext> with a 96 px PNG
thumbnail next to them; the generator receives the absolute paths.
"""

from __future__ import annotations

import io
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

MAX_BYTES = 25 * 1024 * 1024
ALLOWED = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif", "BMP": "bmp", "TIFF": "tif"}


class AttachmentError(ValueError):
    pass


class AttachmentStore:
    def __init__(self, directory: Path | str) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def save(self, filename: str, data: bytes) -> dict[str, Any]:
        if len(data) > MAX_BYTES:
            raise AttachmentError("image is larger than 25 MB")
        try:
            with Image.open(io.BytesIO(data)) as im:
                fmt = im.format or "PNG"
                width, height = im.size
                if fmt not in ALLOWED:
                    raise AttachmentError(f"unsupported image format {fmt}")
                ext = ALLOWED[fmt]
                thumb = im.convert("RGBA")
                thumb.thumbnail((96, 96))
                thumb_buf = io.BytesIO()
                thumb.save(thumb_buf, format="PNG")
        except (OSError, ValueError) as exc:
            raise AttachmentError(f"not a readable image: {exc}") from exc
        att_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename or "reference").name)[:80] or "reference"
        path = self.dir / f"{att_id}.{ext}"
        thumb_path = self.dir / f"{att_id}.thumb.png"
        with self._lock:
            path.write_bytes(data)
            thumb_path.write_bytes(thumb_buf.getvalue())
        return {"id": att_id, "name": safe_name, "path": str(path), "thumb": str(thumb_path),
                "width": width, "height": height, "bytes": len(data)}

    def path_for(self, att_id: str) -> Path | None:
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}", att_id or ""):
            return None
        for candidate in self.dir.glob(f"{att_id}.*"):
            if not candidate.name.endswith(".thumb.png"):
                return candidate
        return None

    def thumb_for(self, att_id: str) -> Path | None:
        path = self.dir / f"{att_id}.thumb.png"
        return path if self.path_for(att_id) and path.exists() else None

    def delete(self, att_id: str) -> bool:
        path = self.path_for(att_id)
        if path is None:
            return False
        with self._lock:
            path.unlink(missing_ok=True)
            (self.dir / f"{att_id}.thumb.png").unlink(missing_ok=True)
        return True

    def resolve(self, ids: list[str]) -> list[str]:
        """Absolute paths for the given ids; unknown ids raise."""
        out = []
        for att_id in ids:
            path = self.path_for(att_id)
            if path is None:
                raise AttachmentError(f"unknown attachment {att_id}")
            out.append(str(path.resolve()))
        return out
