"""项目级 canonical JSON 冻结算法测试。"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest

from nepa.canonical import (
    atomic_write_canonical_json,
    canonical_json_bytes,
    canonical_sha256,
)


def test_canonical_bytes_freeze_sorting_unicode_spacing_and_numbers() -> None:
    value = {"z": 1.25, "中文": "值", "a": [1, True, None]}

    encoded = canonical_json_bytes(value)

    assert encoded == '{"a":[1,true,null],"z":1.25,"中文":"值"}'.encode()
    assert not encoded.endswith(b"\n")
    assert canonical_sha256(value) == hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_rejects_non_finite_float(value: float) -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes({"value": value})


def test_canonical_rejects_non_string_keys_at_any_depth() -> None:
    with pytest.raises(TypeError, match="must be str"):
        canonical_json_bytes({"outer": [{1: "silently coercible by json.dumps"}]})


def test_atomic_canonical_write_has_exact_bytes_and_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "inputs" / "target.json"
    value = {"version": "1", "id": "sample"}

    atomic_write_canonical_json(target, value)

    assert target.read_bytes() == b'{"id":"sample","version":"1"}'
    assert list(target.parent.glob("*.tmp")) == []
