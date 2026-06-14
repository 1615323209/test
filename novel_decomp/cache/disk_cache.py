"""Deterministic disk-based cache for API responses.

Cache key = SHA256(layer + batch_id + model + system_prompt + user_message).
Enables uninterrupted resume: same input → same output, no API call needed.
"""

import hashlib
import json
import threading
from pathlib import Path
from typing import Optional, Any


class DiskCache:
    """Thread-safe file-based cache for LLM API responses."""

    def __init__(self, cache_dir: Path, enabled: bool = True):
        self.cache_dir = Path(cache_dir)
        self.enabled = enabled
        self._lock = threading.Lock()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _make_key(
        self,
        layer: int,
        batch_id: int,
        model: str,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """Generate a deterministic cache key from request parameters."""
        payload = f"{layer}|{batch_id}|{model}|{system_prompt[:500]}|{user_message[:500]}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _file_path(self, key: str) -> Path:
        """Get the cache file path for a key."""
        return self.cache_dir / f"{key}.json"

    def get(self, key: str) -> Optional[str]:
        """Retrieve a cached response. Returns None if not found."""
        if not self.enabled:
            return None
        fp = self._file_path(key)
        if fp.exists():
            try:
                with self._lock:
                    data = json.loads(fp.read_text(encoding="utf-8"))
                return data.get("response")
            except (json.JSONDecodeError, KeyError, OSError):
                return None
        return None

    def set(self, key: str, response: str, metadata: Optional[dict] = None):
        """Store a response in the cache."""
        if not self.enabled:
            return
        fp = self._file_path(key)
        data = {
            "key": key,
            "response": response,
            "metadata": metadata or {},
        }
        try:
            with self._lock:
                fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass  # Non-fatal: cache write failure shouldn't crash the pipeline

    def has(self, key: str) -> bool:
        """Check if a key exists in cache."""
        if not self.enabled:
            return False
        return self._file_path(key).exists()

    def invalidate(self, key: str):
        """Remove a cached entry."""
        fp = self._file_path(key)
        if fp.exists():
            try:
                fp.unlink()
            except OSError:
                pass

    def clear_layer(self, layer: int):
        """Clear all cache entries for a given layer."""
        if not self.cache_dir.exists():
            return
        prefix = f'"layer": {layer}'
        for fp in self.cache_dir.glob("*.json"):
            try:
                content = fp.read_text(encoding="utf-8")
                if f'"layer": {layer}' in content or f'"layer":{layer}' in content:
                    fp.unlink()
            except OSError:
                pass

    @property
    def entry_count(self) -> int:
        """Number of cached entries."""
        if not self.cache_dir.exists():
            return 0
        return len(list(self.cache_dir.glob("*.json")))
