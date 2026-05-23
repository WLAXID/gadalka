"""Файловый кэш ответов API.

Простая идея: ключ кэша = SHA256(url + sorted_params + body).
Значение = JSON ``{"status_code": ..., "headers": ..., "body": ...}``.

Используется в HTTP-клиентах для экономии при пере-проходах
(особенно — для exploratory работы в Jupyter).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CachedResponse:
    """Сериализуемый снимок ответа."""

    status_code: int
    body: str
    headers: dict[str, str]

    def json(self) -> Any:
        return json.loads(self.body)


class FileCache:
    """Дисковый KV-кэш на JSON.

    Безопасно использовать из нескольких async-тасок: каждая запись —
    отдельный файл, перезаписи атомарны (через rename).
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_key(method: str, url: str, params: dict | None, body: Any = None) -> str:
        h = hashlib.sha256()
        h.update(method.upper().encode())
        h.update(b"|")
        h.update(url.encode())
        h.update(b"|")
        if params:
            items = sorted(params.items())
            h.update(json.dumps(items, sort_keys=True, default=str).encode())
        h.update(b"|")
        if body is not None:
            h.update(json.dumps(body, sort_keys=True, default=str).encode())
        return h.hexdigest()

    def _path(self, key: str) -> Path:
        # Раскидываем по подпапкам по первым символам — чтобы папка не разрасталась
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> CachedResponse | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return CachedResponse(
                status_code=data["status_code"],
                body=data["body"],
                headers=data.get("headers", {}),
            )
        except Exception:
            return None

    def put(self, key: str, resp: CachedResponse) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "status_code": resp.status_code,
                    "body": resp.body,
                    "headers": resp.headers,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)

    def clear(self) -> int:
        """Удалить весь кэш. Возвращает количество удалённых файлов."""
        count = 0
        for p in self.root.rglob("*.json"):
            p.unlink()
            count += 1
        return count
