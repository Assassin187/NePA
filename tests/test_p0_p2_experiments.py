"""Offline driver tests: scripted models, real compiler, no paid/provider network."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import socket
import tarfile
import time

import httpx
import pytest

from nepa.config import load_config
from nepa.llm.client import LLMResponse, ProviderError, DecodingError
from nepa.llm.providers.openai_compat import OpenAICompatibleProvider
from nepa.run_store import RunStore, RunStoreError
from nepa.tools.sandbox import SandboxExecutor, ExecResult

ROOT = Path(__file__).parents[1]


def load_driver(name):
    spec = importlib.util.spec_from_file_location('p0_p2_' + name, ROOT / 'experiments/p0-p2' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


driver = load_driver('driver')
baseline = load_driver('baseline')


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('offline experiment tests attempted real network')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)


def qwen_config():
    return load_config(overrides={'coder': {'provider': 'qwen', 'model': driver.PLUS, 'fast_model': driver.FLASH},
        'pricing': {f'qwen/{m}': {'schedule': 'flat', 'off_peak_multiplier': 1,
                    'input_cny_per_million_tokens': 2, 'output_cny_per_million_tokens': 8,
                    'cache_hit_input_cny_per_million_tokens': .4} for m in driver.MODELS},
        'capabilities': {f'qwen/{m}': {'stream': True, 'json_object': True, 'tool_calls': True,
                'json_requires_instruction': True, 'max_output_tokens': 16000, 'usage': 'qwen',
                'wire': {'output_token_field': 'max_completion_tokens', 'enable_thinking': False,
                         'parallel_tool_calls': False, 'completion_reserve_tokens': 10}} for m in driver.MODELS}})


def action(tool, **arguments):
    return {'tool': tool, 'arguments': arguments}


def scripted_actions(fixture):
    rows = []
    if fixture['id'] == 'compile_repair':
        rows.append(action('run_command', argv=['make', 'release']))
    rows.extend(action('read_file', path=name) for name in fixture['files'])
    if fixture['id'] == 'read_write':
        rows.append(action('write_file', path='answer.h', content='#define ANSWER 42\n'))
    if fixture['id'] == 'exact_replace':
        rows.append(action('replace_text', **fixture['exact_replace']))
    if fixture['id'] == 'compile_repair':
        rows.extend([action('write_file', path='answer.h', content='int answer(int value);\n'),
                     action('write_file', path='answer.c', content='#include "answer.h"\nint answer(int value) { return value + 2; }\n')])
    rows.append(action('finish', summary='actual fixture completed', claims=[{
        'id': 'REQ-PUBLIC-OUTPUT-001', 'status': 'already_present' if fixture['id'] == 'finish_claim' else 'implemented',
        'reason': 'main prints the required output and terminates', 'code_refs': ['main.c:1']}]))
    return rows


class ScriptedProvider(OpenAICompatibleProvider):
    """Uses actual provider payload preparation, replacing only network sending."""
    def __init__(self, config, actions):
        super().__init__('qwen', config.providers['qwen'])
        self.actions = iter(actions)
        self.prepared = []

    def send(self, prepared):
        self.prepared.append(prepared)
        value = next(self.actions)
        native = 'tools' in prepared.wire
        return LLMResponse(text='' if native else json.dumps(value),
            tool_calls=[{'id': 'offline-' + str(len(self.prepared)), 'type': 'function',
                'function': {'name': value['tool'], 'arguments': json.dumps(value['arguments'])}}] if native else [],
            model=prepared.model, tokens_in=10, tokens_out=10, cost_cny=0, parameter_support={},
            provider_metadata={'provider': 'qwen', 'requested_model_identity': prepared.model,
                'returned_model_identity': prepared.model, 'returned_model_identity_observed': True,
                'finish_reason': 'tool_calls' if native else 'stop', 'cache_hit_tokens': 0,
                'usage': {'prompt_tokens': 10, 'completion_tokens': 10}})


@pytest.fixture
def local_fixture_compiler(monkeypatch):
    def local_exec(self, cmd, cwd, timeout_s, net='none', *, readonly=None):
        # Only authored tiny fixture commands run on the host, never generated live code.
        assert cmd in (['make', 'release'], ['make', 'san'], ['make', 'clean'],
                       ['./build/release/protocol-server'], ['./build/san/protocol-server'])
        started = time.monotonic()
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
        return ExecResult(cmd, result.returncode, result.stdout, result.stderr,
                          int((time.monotonic() - started) * 1000), False, 'completed')
    monkeypatch.setattr(SandboxExecutor, 'exec', local_exec)


@pytest.mark.parametrize('model', driver.MODELS)
@pytest.mark.parametrize('mode', driver.MODES)
@pytest.mark.parametrize('fixture', driver.read(driver.FIXTURES / 'suite.json')['fixtures'], ids=lambda f: f['id'])
def test_real_session_public_fixture(tmp_path, local_fixture_compiler, fixture, model, mode):
    config = driver.experiment_config(qwen_config(), 'public_tools', model=model, mode=mode)
    # Tests use a disposable ledger; live new_store always uses runs/qwen-e2e.
    store = RunStore.initialize(tmp_path / 'runs', driver.FIXTURES / 'spec.json', driver.FIXTURES / 'target.json',
                                driver.FIXTURES / 'acceptance.json', config)
    provider = ScriptedProvider(config, scripted_actions(fixture))
    row = driver.run_fixture(store, fixture, providers={'qwen': provider})
    assert row['status'] is True and row['protocol_generation'] is False
    assert row['decisions'] <= 12
    assert driver.call_audit(store, only_model=model)
    assert store.run['phase_cost_cny']['public_tools'] > 0
    assert store.run['phase_cost_cny']['generation'] == 0
    assert all(p.model == model for p in provider.prepared)
    assert all('PUBLIC TERMINATING' in p.wire['messages'][0]['content'] for p in provider.prepared)
    assert all('building a real protocol project' not in p.wire['messages'][0]['content'] for p in provider.prepared)
    assert all('bootstrap: real project' not in json.dumps(p.wire) for p in provider.prepared)
    if fixture['id'] == 'compile_repair':
        assert any('error:' in json.dumps(p.wire) for p in provider.prepared[1:])
    if mode == 'json_object':
        assert all(p.wire['messages'][0]['content'].count('Action schema:') == 1 for p in provider.prepared)


def test_fixture_freeze_and_contracts():
    assert driver.read(driver.FIXTURES / 'frozen.json')['files'] == driver.fixture_hashes()
    suite = driver.read(driver.FIXTURES / 'suite.json')
    assert len(suite['fixtures']) == suite['sessions_per_model'] == 4
    assert suite['decisions'] == 12
    assert any(f.get('compile_failure_first') for f in suite['fixtures'])
    assert all(f['stdout'] and f['timeout_s'] == 2 and f['exit_code'] == 0 for f in suite['fixtures'])


def test_no_fallback_single_model_config():
    for model in driver.MODELS:
        cfg = driver.experiment_config(qwen_config(), 'public_tools', model=model, mode='json_object')
        assert cfg.coder.fast_model is None
        assert cfg.coder.for_task('requirements', retry=True, repair=True).model == model
        assert cfg.budgets.sessions_per_task == 1 and cfg.budgets.decisions_per_session == 12
        assert cfg.campaign.phase_max_cost_cny == {'capability': 5.0, 'public_tools': 5.0}


@pytest.mark.parametrize('model', driver.MODELS)
@pytest.mark.parametrize('mode', driver.MODES)
def test_frozen_probe_uses_real_preparer_without_network(model, mode):
    config = driver.experiment_config(qwen_config(), 'capability', model=model, mode=mode)
    client = driver.LLMClient(config)
    for index in range(2):
        prepared = client.prepare(driver.probe_request(config, index))
        assert prepared.wire['model'] == model
        assert prepared.wire['max_completion_tokens'] == 1024
        assert 'max_tokens' not in prepared.wire
        assert prepared.billable_output_tokens == 1034
        assert prepared.wire['stream_options']['include_usage'] is True
        if mode == 'tool_calls':
            assert prepared.wire['parallel_tool_calls'] is False
        else:
            assert prepared.wire['response_format'] == {'type': 'json_object'}


@pytest.mark.parametrize('kind', ['http401', 'malformed', 'wrong_identity'])
def test_raw_provider_errors_offline_are_charged_and_retained(tmp_path, monkeypatch, kind):
    config = driver.experiment_config(qwen_config(), 'capability', model=driver.FLASH, mode='json_object')
    monkeypatch.setenv('NEPA_QWEN_API_KEY', 'offline-fixture-key')
    def response(request):
        if kind == 'http401':
            return httpx.Response(401, text='{"error":{"code":"InvalidApiKey"}}')
        if kind == 'malformed':
            return httpx.Response(200, text='data: {invalid}\n\ndata: [DONE]\n\n')
        event = {'id': 'offline', 'model': driver.PLUS, 'choices': [{'index': 0, 'delta': {'content': '{}'},
                 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n')
    adapter = OpenAICompatibleProvider('qwen', config.providers['qwen'], client=httpx.Client(transport=httpx.MockTransport(response)))
    store = RunStore.initialize(tmp_path / 'runs', driver.FIXTURES / 'spec.json', driver.FIXTURES / 'target.json',
                                driver.FIXTURES / 'acceptance.json', config)
    client = driver.LLMClient(config, {'qwen': adapter})
    with pytest.raises((ProviderError, DecodingError)):
        client.complete(driver.probe_request(config, 0), store=store, task_id='bootstrap')
    assert store.run['pending_calls']
    assert store.run['phase_cost_cny']['capability'] > 0
    assert store.run['phase_cost_cny']['generation'] == 0
    assert list((store.root / 'evidence/calls').glob('*.error.json'))
    assert not list((store.root / 'evidence/calls').glob('*.response.json'))


def test_empty_collection_and_missing_private_repair_are_not_success():
    record = {'stages': {}, 'runs': [], 'paid_executed': False}
    assert driver.summary(record)['status'] is False
    record['stages'] = {s: {'status': True} for s in driver.STAGES if s != 'private-repair'}
    assert driver.summary(record)['status'] is False  # genuine private-feedback repair is still required


def test_first_failure_blocks_repeats_and_http():
    record = {'stages': {'capability': {'status': True}, 'public-tools': {'status': True},
                         'freeze': {'status': True}, 'mqtt-first': {'status': False}}}
    for stage in ('mqtt-repeat-1', 'mqtt-repeat-2', 'http'):
        with pytest.raises(ValueError, match='predecessor'):
            driver.gate(record, stage)
    with pytest.raises(ValueError, match='already attempted'):
        driver.gate(record, 'mqtt-first')
    record['stages']['mqtt-first']['status'] = True
    driver.gate(record, 'mqtt-repeat-1')
    with pytest.raises(ValueError, match='predecessor'):
        driver.gate(record, 'http')


def test_nested_exec_is_not_double_counted():
    assert baseline.execution_seconds({'execution': {'duration_ms': 1000},
                                      'detail': {'execution': {'duration_ms': 800}}}) == 1


def test_parallel_launches_all_children_before_wait_and_keeps_failure(tmp_path, monkeypatch):
    processes = []

    class Process:
        def __init__(self, argv, **kwargs):
            self.stage = argv[2]
            self.batch = Path(argv[argv.index('--batch') + 1])
            processes.append(self)

        def wait(self):
            assert len(processes) == 3
            record = driver.read(self.batch / 'experiment.json')
            driver.gate(record, self.stage)
            passed = self.batch.name != 'mqtt-b'
            record['stages'][self.stage] = {'status': passed, 'run_id': self.batch.name}
            record['runs'].append(self.batch.name)
            driver.atomic_json(self.batch / 'experiment.json', record)
            return 0 if passed else 2

    monkeypatch.setattr(driver.subprocess, 'Popen', Process)
    record = {'stages': {'freeze': {'status': True}, 'parallel': {'status': False}}, 'runs': []}
    result = driver.parallel_generation(tmp_path, record, None)
    assert result['status'] is False
    assert result['rows']['http']['status'] is True
    assert result['rows']['mqtt-a']['status'] is True
    assert result['rows']['mqtt-b']['status'] is False
    assert len(record['runs']) == 3


@pytest.mark.parametrize('name', ('deepseek_first', 'deepseek_mqtt_expanded', 'deepseek_http'))
def test_baseline_matches_existing_inventory_without_repricing(name):
    inventory = baseline.read(ROOT / 'runs/p0-baseline-inventory' / (name + '.json'))
    source = Path(inventory['run_directory'])
    before = {p: baseline.sha(p) for p in [source / 'run.json', source / 'report.json']}
    result = baseline.collect(source, name=name)
    assert result['status'] is True
    assert result['total_wall_s'] == pytest.approx(inventory['total_wall_s'])
    for metric, value in inventory['totals'].items():
        assert result['totals'][metric] == pytest.approx(value)
    for task, expected in inventory['tasks'].items():
        for metric, value in expected.items():
            assert result['tasks'][task][metric] == pytest.approx(value)
    assert result['final_export_build_and_check_s'] == pytest.approx(inventory['final_export_build_and_check_s'])
    assert {p: baseline.sha(p) for p in before} == before
    if name == 'deepseek_first':
        assert result['currency'] == 'USD' and result['calls_without_start_time'] > 0
        assert 'settled_cny' not in result['totals']
        assert 'prevents exact repricing' in result['billing_recalculation']
    else:
        assert result['currency'] == 'CNY'
        assert sum(result['format_failures'].values()) == (192 if name == 'deepseek_mqtt_expanded' else 52)


def test_scoped_manifest_ignores_unrelated_documents_but_detects_runtime(monkeypatch, tmp_path):
    # Work only in a synthetic scope; never mutate the shared runtime or user docs.
    root = tmp_path / 'scope'
    root.mkdir()
    for name in ('baseline.py', 'driver.py', 'README.md'):
        p = root / 'experiments/p0-p2' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('test')
    (root / 'tests').mkdir(exist_ok=True)
    (root / 'tests/test_p0_p2_experiments.py').write_text('test')
    for protocol in ('mqtt', 'http'):
        source = root / 'gold_file' / protocol
        source.mkdir(parents=True)
        for name, fixture_name in [('specIR.json', 'spec.json'), ('target.json', 'target.json'), ('acceptance.json', 'acceptance.json')]:
            (source / name).write_bytes((driver.FIXTURES / fixture_name).read_bytes())
    monkeypatch.setattr(driver, 'ROOT', root)
    monkeypatch.setattr(driver, 'runtime_fingerprint', lambda: {'files': {'test.py': 'frozen'}})
    monkeypatch.setattr(driver.subprocess, 'check_output', lambda *a, **kw: 'sha256:offline-image\n')
    frozen = driver.scoped_manifest(qwen_config())
    (root / 'protocol_document').mkdir()
    (root / 'protocol_document/user.md').write_text('unrelated edit')
    assert driver.scoped_manifest(qwen_config()) == frozen
    monkeypatch.setattr(driver, 'runtime_fingerprint', lambda: {'files': {'test.py': 'changed'}})
    assert driver.scoped_manifest(qwen_config()) != frozen


def test_generation_empty_snapshot_rejects_imported_source(tmp_path):
    config = qwen_config()
    store = RunStore.initialize(tmp_path / 'runs', driver.FIXTURES / 'spec.json', driver.FIXTURES / 'target.json',
                                driver.FIXTURES / 'acceptance.json', config)
    assert driver.empty_initial(store)['status'] is True
    (store.project / 'imported.c').write_text('int main(void) { return 0; }')
    with pytest.raises(ValueError, match='empty project'):
        driver.empty_initial(store)


def test_study_complete_reopens_but_never_resumes_as_generation(tmp_path):
    config = driver.experiment_config(qwen_config(), 'capability', model=driver.PLUS, mode='tool_calls')
    store = RunStore.initialize(tmp_path / 'runs', driver.FIXTURES / 'spec.json', driver.FIXTURES / 'target.json',
                                driver.FIXTURES / 'acceptance.json', config)
    store.run.update(status='study_complete', exit_code=0, reason='Study only; protocol tasks not executed')
    store.save()
    reopened = RunStore(store.root)
    assert all(t['status'] == 'pending' for t in reopened.run['tasks'].values())
    assert reopened.run['status'] != 'success'
    with pytest.raises(RunStoreError, match='study'):
        reopened.recover()
    values = copy.deepcopy(store.run)
    values['config_snapshot']['campaign']['phase'] = 'generation'
    from jsonschema import Draft202012Validator
    assert list(Draft202012Validator(driver.load_schema('run.schema.json')).iter_errors(values))


def test_real_controlled_echo_private_repair_sequence(tmp_path, monkeypatch, local_fixture_compiler):
    """Real C/server/oracle, scripted model; this is NOT Docker isolation or paid proof."""
    spec = importlib.util.spec_from_file_location('controlled_oracle', driver.PRIVATE_REPAIR / 'oracle.py')
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    observations = []

    def local_verification(self, target, acceptance, workspace, checks_root, evidence_dir, *, seed=None):
        evidence_dir.mkdir(parents=True, exist_ok=False)
        driver.atomic_json(evidence_dir / 'attempt.json', {'seed': seed, 'scope': 'offline-local-test-only'})
        rows = []
        for build in target['builds']:
            with socket.socket() as allocation:
                allocation.bind(('127.0.0.1', 0))
                port = allocation.getsockname()[1]
            process = subprocess.Popen([str(workspace / build['artifact']), '--host', '127.0.0.1', '--port', str(port)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 2
                while True:
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.1):
                            break
                    except ConnectionRefusedError:
                        assert time.monotonic() < deadline and process.poll() is None
                        time.sleep(.01)
                variant_seed = hashlib.sha256((seed + ':variant:' + build['id']).encode()).hexdigest()
                case_seed = hashlib.sha256((variant_seed + ':case:echo-complete').encode()).hexdigest()
                result = oracle.check('127.0.0.1', port, case_seed, evidence_dir / (build['id'] + '.jsonl'))
                observations.append((seed, build['id'], result))
            finally:
                process.terminate()
                out, err = process.communicate(timeout=3)
            assert process.returncode == 0 and not out and not err
            check = {'id': acceptance['checks'][0]['id'], 'returncode': 0 if result['passed'] else 1,
                     **result, 'required': True}
            rows.append({'variant': build['id'], 'passed': result['passed'],
                         'execution': {'returncode': check['returncode'], 'timed_out': False},
                         'detail': {'passed': result['passed'], 'checks': [check], 'server_returncode': 0,
                                    'early_exit': None, 'stop_timeout': False, 'sanitizer_error': False}})
        return {'passed': all(r['passed'] for r in rows), 'variants': rows}

    monkeypatch.setattr(driver.VerificationRunner, 'run', local_verification)
    config = driver.experiment_config(qwen_config(), 'public_tools', model=driver.PLUS, mode='tool_calls')
    store = RunStore.initialize(tmp_path / 'runs', driver.PRIVATE_REPAIR / 'spec.json', driver.PRIVATE_REPAIR / 'target.json',
                                driver.PRIVATE_REPAIR / 'acceptance.json', config)
    actions = [action('read_file', path='server.c'),
               action('replace_text', path='server.c', old='size_t remaining = (size_t)count - 1;',
                      new='size_t remaining = (size_t)count;'),
               action('finish', summary='echo every received byte', claims=[{
                   'id': 'REQ-ECHO-001', 'status': 'implemented', 'reason': 'send length now covers all received bytes',
                   'code_refs': ['server.c:26']}])]
    provider = ScriptedProvider(config, actions)
    row = driver.run_private_repair(store, providers={'qwen': provider})
    assert row['status'] is True and row['protocol_generation'] is False
    assert [r[2]['passed'] for r in observations] == [False, False, True, True]
    assert len({r[0] for r in observations}) == 1
    before, after = store.root / 'evidence/private-repair-before', store.root / 'evidence/private-repair-after'
    for variant in ('release', 'san'):
        def sends(path):
            return [r['data_hex'] for r in map(json.loads, path.read_text().splitlines()) if r['event'] == 'send']
        assert sends(before / (variant + '.jsonl')) == sends(after / (variant + '.jsonl'))
    wire = json.dumps([p.wire for p in provider.prepared])
    assert 'expected_length' in wire and 'actual_length' in wire
    assert 'oracle.py' not in wire and 'data_hex' not in wire and observations[0][0] not in wire
    assert len(provider.prepared) == 3 and store.run['phase_cost_cny']['public_tools'] > 0


def test_archive_complete_delivery_and_public_report(tmp_path, monkeypatch):
    config = qwen_config()
    store = RunStore.initialize(tmp_path / 'runs', driver.FIXTURES / 'spec.json', driver.FIXTURES / 'target.json',
                                driver.FIXTURES / 'acceptance.json', config)
    delivery = store.root / 'delivery'
    delivery.mkdir()
    for name in ('Makefile', 'README.md', 'main.c', 'build.mk', 'tables.inc', 'messages.def'):
        (delivery / name).write_text('public build dependency')
    (delivery / 'alias.inc').symlink_to('tables.inc')
    store.run['delivery'] = {'path': 'delivery', 'files': driver.tree_hashes(delivery), 'checkpoint': store.run['accepted_checkpoint']}
    store.run.update(status='success', exit_code=0)  # synthetic archive input, not generation evidence
    store.save()
    # Actual Report5 publisher/projection; no alternative counter-only report.
    from nepa.report import publish_report, public_report
    host = publish_report(store)
    row = {'status': True, 'run_id': store.run_id, 'protocol': 'http', 'candidate_sha256': 'a' * 64,
           'builds': {'passed': True}, 'checks': {'passed': True, 'variants': []}}
    monkeypatch.setattr(driver, 'CAMPAIGN', tmp_path / 'runs')
    output = tmp_path / 'public.tar.gz'
    driver.archive({'stages': {'http': row}}, 'http', output)
    with tarfile.open(output) as bundle:
        names = bundle.getnames()
        assert {'project/build.mk', 'project/tables.inc', 'project/messages.def', 'project/alias.inc',
                'inputs/spec.json', 'inputs/target.json', 'inputs/index.json', 'report.json',
                'independent-audit.json', 'candidate.json'} <= set(names)
        assert not any('private/' in name or 'evidence/' in name or 'acceptance.json' in name for name in names)
        public = json.loads(bundle.extractfile('report.json').read())
        assert public == public_report(host)
        assert public['schema_version'] == '5.0' and public['requirements']
        assert 'budget' in public and 'private_suite_sha256' in public
    (delivery / 'escape.inc').symlink_to(store.root / 'private/acceptance.json')
    store.run['delivery']['files'] = driver.tree_hashes(delivery)
    store.save()
    with pytest.raises(ValueError, match='escapes'):
        driver.archive({'stages': {'http': row}}, 'http', tmp_path / 'must-not-exist.tar.gz')
    assert not (tmp_path / 'must-not-exist.tar.gz').exists()


def test_capability_length_is_truncation_and_studies_remain_truthful(tmp_path, monkeypatch):
    config = qwen_config()
    config.coder.action_format = 'tool_calls'
    class TruncatingProvider(ScriptedProvider):
        def send(self, prepared):
            response = super().send(prepared)
            response.provider_metadata['finish_reason'] = 'length'
            return response
    actual_client = driver.LLMClient
    def offline_client(cfg):
        return actual_client(cfg, {'qwen': TruncatingProvider(cfg, [action('read_file', path='main.c')])})
    monkeypatch.setattr(driver, 'LLMClient', offline_client)
    monkeypatch.setattr(driver, 'CAMPAIGN', tmp_path / 'runs')
    record = {'runs': [], 'samples': []}
    for model in driver.MODELS:
        for mode in driver.MODES:
            cfg = driver.experiment_config(config, 'capability', model=model, mode=mode)
            for index in range(2):
                request = driver.probe_request(cfg, index)
                record['samples'].append({'model': model, 'mode': mode, 'index': index,
                    'request': request.model_dump(mode='json'),
                    'wire_sha256': hashlib.sha256(actual_client(cfg).prepare(request).body).hexdigest()})
    batch = tmp_path / 'batch'
    batch.mkdir()
    result = driver.capability(batch, record, config)
    assert result['status'] is False and len(result['rows']) == 4
    for row in result['rows']:
        assert len(row['samples']) == 2
        assert all(r['failure_class'] == 'truncated' and r['capability_unsupported'] is False for r in row['samples'])
        store = RunStore.open(tmp_path / 'runs', row['run_id'])
        assert store.run['status'] == 'study_complete'
        assert all(t['status'] == 'pending' for t in store.run['tasks'].values())


def test_missing_paid_opt_in_does_not_create_study_store(tmp_path, monkeypatch):
    config = qwen_config()
    admission = {'config': driver.public_config_snapshot(config)}
    batch = tmp_path / 'batch'
    batch.mkdir()
    driver.atomic_json(batch / 'experiment.json', {'admission': admission, 'config_path': 'unused.yaml', 'stages': {}})
    monkeypatch.setattr(driver, 'scoped_manifest', lambda *a: admission)
    monkeypatch.setattr(driver, 'new_store', lambda *a: pytest.fail('paid store created without opt-in'))
    monkeypatch.setattr('sys.argv', ['driver.py', 'capability', '--batch', str(batch)])
    with pytest.raises(ValueError, match='explicit --paid'):
        driver.main()
    assert driver.read(batch / 'experiment.json')['stages'] == {}
