"""Paid opt-in: parallel fresh MQTT and HTTP-subset generations."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import uuid

import pytest

from nepa.config import load_config, public_config_snapshot
from nepa.run_store import atomic_json, runtime_fingerprint, tree_hashes
from nepa.speclib.lint import digest
from nepa.speclib.plan import compile_plan
from nepa.tools.build import BuildRunner
from nepa.tools.sandbox import SandboxExecutor
from nepa.tools.verification import VerificationRunner

pytestmark = pytest.mark.live_e2e
ROOT = Path(__file__).parents[1]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def fingerprint(config):
    inputs = {}
    for protocol in ('mqtt', 'http'):
        root = ROOT / 'gold_file' / protocol
        acceptance = json.loads((root / 'acceptance.json').read_bytes())
        inputs[protocol] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                            for name in ('specIR.json', 'target.json', 'acceptance.json', *acceptance['assets'])}
    return {'commit': git('rev-parse', 'HEAD'), 'runtime': runtime_fingerprint()['package_sha256'],
            'config': digest(public_config_snapshot(config)), 'inputs': inputs,
            'image': subprocess.check_output(['docker', 'image', 'inspect', config.sandbox.image,
                                             '--format', '{{.Id}}'], text=True).strip()}


def campaign_roots():
    # User authorized two fresh CNY ledgers; old USD evidence stays in its original root.
    return {'mqtt': ROOT / 'runs/mqtt-e2e', 'http': ROOT / 'runs/http-e2e'}


def launch(protocol, runs_root):
    inputs = ROOT / 'gold_file' / protocol
    command = [sys.executable, '-m', 'nepa', 'run', '--spec', str(inputs / 'specIR.json'),
               '--target', str(inputs / 'target.json'), '--acceptance', str(inputs / 'acceptance.json'),
               '--config', str(ROOT / 'configs/default.yaml'), '--runs-root', str(runs_root)]
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=4 * 3600 + 600)
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=60)
        raise
    return {'protocol': protocol, 'returncode': process.returncode, 'stdout': stdout, 'stderr': stderr}


def verify_row(row, config, frozen, batch):
    assert row['returncode'] == 0, f'real generation failed (charged to campaign): {row}'
    run_dir = Path(json.loads(row['stdout'])['run_dir'])
    state = json.loads((run_dir / 'run.json').read_bytes())
    report = json.loads((run_dir / 'report.json').read_bytes())
    assert state['status'] == report['status'] == 'success'
    assert report['schema_version'] == '4.0'
    assert state['runtime']['package_sha256'] == frozen['runtime']
    assert state['config_sha256'] == frozen['config']
    assert state['sandbox_image'] == frozen['image']
    assert not any(entry['kind'] == 'configuration_change' for entry in state['history'])
    for source, expected in frozen['inputs'][row['protocol']].items():
        snapshot = ('spec.json' if source == 'specIR.json' else source if source in ('target.json', 'acceptance.json') else 'checks/' + source)
        assert hashlib.sha256((run_dir / 'inputs' / snapshot).read_bytes()).hexdigest() == expected
    spec = json.loads((run_dir / 'inputs/spec.json').read_bytes())
    target = json.loads((run_dir / 'inputs/target.json').read_bytes())
    acceptance = json.loads((run_dir / 'inputs/acceptance.json').read_bytes())
    initial_plan = compile_plan(spec, target)
    assert len(state['tasks']) >= len(initial_plan['tasks']) and all(t['status'] == 'passed' for t in state['tasks'].values())
    assert [r['id'] for r in report['requirements']] == [r['id'] for r in spec['requirements']]
    mandatory_ids = {r for c in acceptance['checks'] if c['required'] for r in c['req_ids']}
    assert all(r['verification']['status'] == 'scenarios_passed' for r in report['requirements'] if r['id'] in mandatory_ids)
    assert report['cache_hits'] == 0 and state['budget']['calls'] > 0
    calls = [json.loads(path.read_bytes()) for path in sorted((run_dir / 'evidence/calls').glob('*.response.json'))]
    assert calls and all(not call['response']['cached'] for call in calls)
    assert all(call['response']['provider_metadata'].get('provider') == config.coder.provider for call in calls)
    root_commit = subprocess.check_output(['git', '--git-dir', str(run_dir / 'checkpoints.git'),
                                          'rev-list', '--max-parents=0', state['accepted_checkpoint']], text=True).strip()
    assert not subprocess.check_output(['git', '--git-dir', str(run_dir / 'checkpoints.git'),
                                        'ls-tree', '-r', '--name-only', root_commit], text=True).strip()
    delivery = run_dir / state['delivery']['path']
    assert tree_hashes(delivery) == state['delivery']['files']
    rebuilt = batch / ('independent-rebuild-' + row['protocol'])
    shutil.copytree(delivery, rebuilt, symlinks=True)
    executor = SandboxExecutor(config.sandbox.image, config.sandbox.cpu, config.sandbox.mem_gb)
    builds = BuildRunner(executor, config.sandbox.build_timeout_s).run(target, rebuilt, clean=True)
    checks = VerificationRunner(executor).run(target, acceptance, rebuilt, run_dir / 'inputs/checks',
                                              batch / ('independent-checks-' + row['protocol']))
    row.update({'run_id': state['run_id'], 'budget': state['budget'], 'builds': builds, 'checks': checks,
                'delivery': str(delivery), 'tasks': len(state['tasks'])})
    assert builds['passed'] and checks['passed'], row


def run_batch():
    assert os.environ.get('NEPA_LIVE_E2E') == '1', 'paid API requires explicit opt-in'
    config = load_config(ROOT / 'configs/default.yaml')
    assert os.environ.get(config.providers[config.coder.provider].api_key_env or ''), 'configured credential missing'
    frozen = fingerprint(config)
    assert not git('status', '--porcelain'), 'freeze and commit before real acceptance'
    roots = campaign_roots()
    assert roots['mqtt'] != roots['http'], 'HTTP requires its separately authorized campaign'
    batch = ROOT / 'runs/protocol-expansion' / uuid.uuid4().hex
    batch.mkdir(parents=True)
    harness_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    record = {'status': 'running', 'frozen': frozen, 'harness_sha256': harness_sha,
              'scheduling': 'parallel-mqtt-and-http', 'campaign_roots': {k: str(v) for k, v in roots.items()},
              'runs': [], 'scope': 'configured core/subset scenarios, not full conformance or stability'}
    atomic_json(batch / 'batch.json', record)
    print(f'Starting fresh protocol generations; batch={batch}', flush=True)
    def execute(protocol):
        row = {'protocol': protocol}
        try:
            assert fingerprint(config) == frozen and not git('status', '--porcelain')
            row = launch(protocol, roots[protocol])
            verify_row(row, config, frozen, batch)
            assert fingerprint(config) == frozen
            assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == harness_sha
            row['passed'] = True
        except Exception as exc:
            row.update(passed=False, error=f'{type(exc).__name__}: {exc}')
        return row

    record['phase'] = 'parallel-generation-and-verification'
    atomic_json(batch / 'batch.json', record)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute, protocol) for protocol in ('mqtt', 'http')]
        for future in as_completed(futures):
            row = future.result()
            record['runs'].append(row)
            atomic_json(batch / 'batch.json', record)
            print(f'{row["protocol"]} completed: passed={row["passed"]}; batch={batch}', flush=True)
    record['status'] = 'passed' if len(record['runs']) == 2 and all(r['passed'] for r in record['runs']) else 'failed'
    record['phase'] = 'complete'
    atomic_json(batch / 'batch.json', record)
    return record


def test_two_protocol_real_generations():
    if os.environ.get('NEPA_LIVE_E2E') != '1':
        pytest.skip('paid real-API acceptance requires NEPA_LIVE_E2E=1')
    assert run_batch()['status'] == 'passed'
