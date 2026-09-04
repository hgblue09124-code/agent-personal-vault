import json
import hashlib
from typing import Any


def canonical_dumps(obj: Any) -> str:
    """
    Deterministic serialization of a python dict/list/object to JSON string.
    Keys are sorted and indent is set to 2 spaces for human readability and consistent hashing.
    """
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def canonical_hash(obj: Any) -> str:
    """
    Compute SHA-256 digest of canonically serialized object.
    """
    serialized = canonical_dumps(obj).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
