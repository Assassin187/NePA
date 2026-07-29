"""构建仓库默认解析 Profile/Test Bundle 描述。"""

from __future__ import annotations

from pathlib import Path

from nepa.assets import resolve_profile_source, resolve_test_bundle_source
from nepa.canonical import atomic_write_canonical_json


def build_default_assets(workspace_root: str | Path) -> tuple[Path, Path, Path]:
    root = Path(workspace_root).resolve()
    output = root / "profiles" / "resolved"
    output.mkdir(parents=True, exist_ok=True)
    target_path = output / "mqtt-client-broker.json"
    language_path = output / "c99-posix.json"
    bundle_path = output / "mqtt-3-1-1-min-gold.json"
    atomic_write_canonical_json(
        target_path,
        resolve_profile_source(
            root / "profiles" / "targets" / "mqtt-client-broker.source.json",
            kind="target",
            workspace_root=root,
        ),
    )
    atomic_write_canonical_json(
        language_path,
        resolve_profile_source(
            root / "profiles" / "languages" / "c99-posix.source.json",
            kind="language",
            workspace_root=root,
        ),
    )
    atomic_write_canonical_json(
        bundle_path,
        resolve_test_bundle_source(
            root
            / "profiles"
            / "test-bundles"
            / "mqtt-3-1-1-min-gold.source.json",
            workspace_root=root,
        ),
    )
    return target_path, language_path, bundle_path


def main() -> None:
    build_default_assets(Path.cwd())


if __name__ == "__main__":
    main()
