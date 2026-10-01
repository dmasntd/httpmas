"""
Case-insensitive headers cho httpmas.
Hỗ trợ multi-value (Set-Cookie).
"""
from typing import Dict, Iterator, List, Optional, Tuple, Union


class CaseInsensitiveHeaders:
    """Dict-like object cho HTTP headers.
    - Lookup không phân biệt hoa thường
    - Giữ thứ tự insert
    - Hỗ trợ multi-value (Set-Cookie)
    """
    __slots__ = ("_store", "_order")

    def __init__(self, data: Optional[Dict[str, str]] = None) -> None:
        self._store: Dict[str, List[Tuple[str, str]]] = {}
        self._order: List[str] = []
        if data:
            for k, v in data.items():
                self[k] = v

    def __setitem__(self, key: str, value: str) -> None:
        lower_key = key.lower()
        if lower_key not in self._store:
            self._order.append(lower_key)
        self._store[lower_key] = [(key, value)]

    def __getitem__(self, key: str) -> str:
        lower_key = key.lower()
        entries = self._store.get(lower_key)
        if entries is None:
            raise KeyError(key)
        return entries[-1][1]

    def __delitem__(self, key: str) -> None:
        lower_key = key.lower()
        if lower_key in self._store:
            del self._store[lower_key]
            self._order.remove(lower_key)

    def __contains__(self, key: str) -> bool:
        return key.lower() in self._store

    def __iter__(self) -> Iterator[str]:
        for lower_key in self._order:
            entries = self._store.get(lower_key)
            if entries:
                yield entries[0][0]

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        items = ", ".join(f"'{k}': '{v}'" for k, v in self.items())
        return f"CaseInsensitiveHeaders({{{items}}})"

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        try:
            return self[key]
        except KeyError:
            return default

    def get_list(self, key: str) -> List[str]:
        """Trả TẤT CẢ values cho header (cho Set-Cookie)."""
        lower_key = key.lower()
        entries = self._store.get(lower_key)
        if entries is None:
            return []
        return [v for _, v in entries]

    def add(self, key: str, value: str) -> None:
        """Thêm value mà không ghi đè (cho multi-value headers)."""
        lower_key = key.lower()
        if lower_key not in self._store:
            self._order.append(lower_key)
            self._store[lower_key] = []
        self._store[lower_key].append((key, value))

    def items(self) -> Iterator[Tuple[str, str]]:
        for lower_key in self._order:
            entries = self._store.get(lower_key)
            if entries:
                yield entries[-1]

    def keys(self) -> Iterator[str]:
        for lower_key in self._order:
            entries = self._store.get(lower_key)
            if entries:
                yield entries[0][0]

    def values(self) -> Iterator[str]:
        for lower_key in self._order:
            entries = self._store.get(lower_key)
            if entries:
                yield entries[-1][1]

    def update(self, other: Union[Dict[str, str], "CaseInsensitiveHeaders"]) -> None:
        if isinstance(other, CaseInsensitiveHeaders):
            for k, v in other.items():
                self[k] = v
        elif isinstance(other, dict):
            for k, v in other.items():
                self[k] = v

    def to_dict(self) -> Dict[str, str]:
        """Convert sang dict thường (backward compat)."""
        return {k: v for k, v in self.items()}

    @classmethod
    def from_raw(cls, raw_dict: Dict[str, str]) -> "CaseInsensitiveHeaders":
        return cls(raw_dict)