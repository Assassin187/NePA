"""Host-mediated project tools; generated commands execute only in the sandbox."""
from __future__ import annotations
from dataclasses import asdict
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from ..speclib.lint import safe_relative
from .sandbox import SandboxExecutor


class WorkspaceTools:
    def __init__(self, project: Path, inputs: Path, evidence: Path, executor: SandboxExecutor, timeout_s: int = 120):
        self.project, self.inputs, self.evidence = project.resolve(), inputs.resolve(), evidence.resolve()
        self.executor, self.timeout_s = executor, timeout_s

    def path(self, name: str, *, write: bool = False) -> Path:
        name = safe_relative(name)
        if name == "inputs" or name.startswith("inputs/"):
            if write:
                raise ValueError("input and acceptance assets are read-only")
            root, name = self.inputs, name.removeprefix("inputs/")
            if name == "inputs":
                name = "."
        elif name == "evidence" or name.startswith("evidence/"):
            if write:
                raise ValueError("evidence is read-only")
            root, name = self.evidence, name.removeprefix("evidence/")
            if name == "evidence":
                name = "."
        else:
            root = self.project
            name = name.removeprefix("project/")
        path = root / name
        if not path.resolve().is_relative_to(root):
            raise ValueError("path or symlink escaped its allowed root")
        return path

    def display(self, path: Path) -> str:
        for prefix, root in (("inputs/", self.inputs), ("evidence/", self.evidence), ("", self.project)):
            if path.is_relative_to(root):
                return prefix + path.relative_to(root).as_posix()
        raise ValueError("path outside tool roots")

    def file_sha256(self, name: str) -> str:
        return hashlib.sha256(self.path(name).read_bytes()).hexdigest()

    def execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool == "list_files":
            root = self.path(args.get("path", "."))
            return {"files": [{"path": self.display(p), "size": p.lstat().st_size}
                              for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()][:500]}
        if tool == "read_file":
            path = self.path(args["path"])
            data = path.read_bytes()
            text = data.decode("utf-8")
            if "json_pointer" in args and ("start_line" in args or "end_line" in args):
                raise ValueError("json_pointer cannot be combined with start_line/end_line")
            if "json_pointer" in args:
                value = json.loads(text)
                for key in args["json_pointer"].split("/")[1:]:
                    key = key.replace("~1", "/").replace("~0", "~")
                    value = value[int(key)] if isinstance(value, list) else value[key]
                text = json.dumps(value, ensure_ascii=False, indent=2)
            elif "start_line" in args or "end_line" in args:
                if "start_line" not in args or "end_line" not in args:
                    raise ValueError("start_line and end_line must be provided together")
                start_line, end_line = args["start_line"], args["end_line"]
                lines = text.splitlines(keepends=True)
                if start_line < 1 or end_line < start_line or start_line > len(lines) or end_line > len(lines):
                    raise ValueError(
                        f"invalid one-based inclusive line range {start_line}:{end_line}; file has {len(lines)} lines"
                    )
                text = "".join(lines[start_line - 1:end_line])
            offset, limit = args.get("offset", 0), args.get("limit", 16000)
            result = {"path": self.display(path), "file_sha256": hashlib.sha256(data).hexdigest(),
                    "content": text[offset:offset + limit], "offset": offset, "offset_unit": "characters",
                    "next_offset": offset + limit if offset + limit < len(text) else None, "total_chars": len(text)}
            for key in ("json_pointer", "start_line", "end_line"):
                if key in args:
                    result[key] = args[key]
            return result
        if tool == "search":
            root = self.path(args.get("path", "."))
            try:
                pattern = re.compile(args["pattern"])
            except re.error as exc:
                raise ValueError(f"invalid search regular expression: {exc}") from exc
            matches = []
            paths = [root] if root.is_file() else sorted(root.rglob("*"))
            for path in paths:
                if not path.is_file() or path.is_symlink():
                    continue
                try:
                    for number, line in enumerate(path.read_text().splitlines(), 1):
                        if pattern.search(line):
                            matches.append({"path": self.display(path), "line": number, "text": line[:1000]})
                            if len(matches) >= 100:
                                return {"matches": matches, "truncated": True}
                except UnicodeError:
                    continue
            return {"matches": matches, "truncated": False}
        if tool in {"write_file", "replace_text"}:
            path = self.path(args["path"], write=True)
            if tool == "write_file":
                content = args["content"]
            else:
                content = path.read_text()
                if not args["old"] or content.count(args["old"]) != 1:
                    raise ValueError("replace_text old must match exactly once; read the current file first")
                content = content.replace(args["old"], args["new"], 1)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            return {"written": args["path"], "bytes": len(content.encode())}
        if tool == "run_command":
            readonly = {"/inputs": self.inputs}
            if (self.inputs / "checks").is_dir():
                readonly["/checks"] = self.inputs / "checks"
            return asdict(self.executor.exec(args["argv"], str(self.project), self.timeout_s, readonly=readonly))
        raise ValueError(f"unknown workspace tool: {tool}")
