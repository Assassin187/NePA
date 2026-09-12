"""Git checkpoints with metadata outside the generated writable tree."""
from __future__ import annotations
from pathlib import Path
import subprocess


class GitCheckpoints:
    def __init__(self, git_dir: Path, workspace: Path):
        self.git_dir, self.workspace = git_dir.resolve(), workspace.resolve()

    def command(self, *args: str) -> str:
        result = subprocess.run(["git", "--git-dir", str(self.git_dir), "--work-tree", str(self.workspace),
                                 "-c", "user.name=NePA", "-c", "user.email=nepa@localhost", *args],
                                cwd=self.workspace, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"checkpoint git failed: {result.stderr}")
        return result.stdout.strip()

    def initialize(self) -> str:
        result = subprocess.run(["git", "init", "--bare", str(self.git_dir)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return self.commit("empty generated project")

    def commit(self, message: str) -> str:
        self.command("add", "--all", "--force", "--", ".")
        self.command("commit", "--allow-empty", "-m", message)
        return self.command("rev-parse", "HEAD")

    def restore(self, commit: str) -> None:
        self.command("read-tree", commit)
        self.command("checkout-index", "--all", "--force")
