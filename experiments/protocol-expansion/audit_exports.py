"""Rebuild copies of the three historical deliveries against the expanded oracle."""
from pathlib import Path
import argparse
import json
import shutil

from nepa.config import load_config
from nepa.report import requirement_verification
from nepa.run_store import atomic_json, tree_hashes
from nepa.speclib.lint import digest
from nepa.tools.build import BuildRunner
from nepa.tools.sandbox import SandboxExecutor
from nepa.tools.verification import VerificationRunner

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL = ROOT / 'runs/_refactor/worktree/runs/e2e'
IDS = ['20260912T112242Z-56f67d18', '20260912T135449Z-4c036768', '20260912T135449Z-22021a32']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    inputs = args.output / 'inputs'
    inputs.mkdir()
    for name in ('specIR.json', 'target.json', 'acceptance.json'):
        shutil.copyfile(ROOT / 'gold_file/mqtt' / name, inputs / name)
    shutil.copytree(ROOT / 'gold_file/mqtt/acceptance', inputs / 'acceptance', ignore=shutil.ignore_patterns('__pycache__'))
    frozen_inputs = tree_hashes(inputs)
    config = load_config(ROOT / 'configs/default.yaml')
    executor = SandboxExecutor(config.sandbox.image, config.sandbox.cpu, config.sandbox.mem_gb)
    acceptance = json.loads((inputs / 'acceptance.json').read_text())
    records = []
    for identifier in IDS:
        source = HISTORICAL / identifier
        state = json.loads((source / 'run.json').read_text())
        original = source / state['delivery']['path']
        before = tree_hashes(original)
        assert before == state['delivery']['files']
        target = json.loads((source / 'inputs/target.json').read_text())
        spec = json.loads((source / 'inputs/spec.json').read_text())
        dest = args.output / identifier
        dest.mkdir()
        shutil.copytree(original, dest / 'rebuild', symlinks=True)
        builds = BuildRunner(executor, config.sandbox.build_timeout_s).run(target, dest / 'rebuild', clean=True)
        checks = VerificationRunner(executor).run(target, acceptance, dest / 'rebuild', inputs, dest / 'checks') if builds['passed'] else None
        result = {'build': builds, 'verification': checks}
        atomic_json(dest / 'result.json', result)
        import hashlib
        reference = {'path': str((dest / 'result.json').relative_to(ROOT)), 'sha256': hashlib.sha256((dest / 'result.json').read_bytes()).hexdigest()}
        final = {'result': result, 'evidence': reference}
        claims = {c['id']: c for t in state['tasks'].values() for c in t['claims']}
        requirements = [{'id': r['id'], 'agent_claim': claims.get(r['id']), 'verification': requirement_verification(r['id'], target, acceptance, final)} for r in spec['requirements']]
        atomic_json(dest / 'requirements.json', requirements)
        assert tree_hashes(original) == before
        assert tree_hashes(inputs) == frozen_inputs
        row = {'run_id': identifier, 'build_passed': builds['passed'], 'checks_passed': bool(checks and checks['passed']), 'result': reference,
               'requirements': str((dest / 'requirements.json').relative_to(ROOT)), 'original_unchanged': True,
               'failed_cases': {v['variant']: [c['id'] for c in v['detail'].get('checks', []) if not c['passed']] for v in checks['variants']} if checks else {}}
        records.append(row)
        atomic_json(args.output / 'summary.json', {'acceptance_sha256': digest(acceptance), 'frozen_inputs': frozen_inputs,
                    'historical_audit_only': True, 'runs': records})
        print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()
