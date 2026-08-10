import json
from pathlib import Path

from nepa.speclib.lint import lint_target


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _spec() -> dict:
    return {"schema_version": "3.0", "protocol": {"name": "MQTT", "version": "3.1.1", "roles": ["client", "server"]}, "types": [], "messages": [], "requirements": []}


def test_target_lint_accepts_default_profile_with_spec(tmp_path):
    target = _write(tmp_path / "target.json", {"roles": ["server"], "language": {"name": "C", "version": "C99"}})
    spec = _write(tmp_path / "spec.json", _spec())
    assert lint_target(target, spec)["valid"]


def test_target_lint_rejects_historical_dual_role(tmp_path):
    target = _write(tmp_path / "target.json", {"roles": ["client", "server"], "language": {"name": "C", "version": "C99"}})
    report = lint_target(target)
    assert not report["valid"]
    assert any(error["code"] == "TARGET_ROLE_UNSUPPORTED" for error in report["errors"])


def test_target_lint_rejects_unsupported_language(tmp_path):
    target = _write(tmp_path / "target.json", {"roles": ["server"], "language": {"name": "C", "version": "C11"}})
    report = lint_target(target)
    assert not report["valid"]
    assert any(error["code"] == "TARGET_LANGUAGE_UNSUPPORTED" for error in report["errors"])


def test_target_lint_rejects_extra_field(tmp_path):
    target = _write(tmp_path / "target.json", {"roles": ["server"], "language": {"name": "C", "version": "C99"}, "backend": "c99"})
    report = lint_target(target)
    assert not report["valid"]
