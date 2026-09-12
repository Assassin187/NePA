"""Production dependency assembly: no calibration or legacy stage prerequisites."""
from .agents.session import CodingSession
from .llm.client import LLMClient, Provider
from .orchestrator import Orchestrator
from .run_store import RunStore
from .tools.build import BuildRunner
from .tools.sandbox import SandboxExecutor
from .tools.verification import VerificationRunner
from .tools.workspace import WorkspaceTools


def build_orchestrator(store: RunStore, providers: dict[str, Provider] | None = None) -> Orchestrator:
    config = store.config
    executor = SandboxExecutor(config.sandbox.image, config.sandbox.cpu, config.sandbox.mem_gb)
    tools = WorkspaceTools(store.project, store.root / "inputs", store.root / "evidence", executor, config.sandbox.command_timeout_s)
    session = CodingSession(LLMClient(config, providers), store, tools,
                            BuildRunner(executor, config.sandbox.build_timeout_s), VerificationRunner(executor))
    return Orchestrator(session)
