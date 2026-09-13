"""Bounded, explicit P2 commands. No paid operation runs without --paid.

Uses the production RunStore, LLMClient, CodingSession and orchestrator. This is
experiment evidence and gating, not an alternative coding loop or billing ledger.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import hashlib
import io
import json
import os
import re
import secrets
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

from nepa.application import build_orchestrator
from nepa.config import ResolvedConfig, load_config, public_config_snapshot
from nepa.llm.client import LLMClient, LLMRequest, decode_action
from nepa.report import public_report
from nepa.run_store import RunStore, atomic_json, file_lock, runtime_fingerprint, tree_hashes
from nepa.schemas import load_schema
from nepa.speclib.lint import digest
from nepa.speclib.plan import compile_plan, validate_claims
from nepa.tools.build import BuildRunner
from nepa.tools.sandbox import SandboxExecutor
from nepa.tools.verification import VerificationRunner, safe_feedback

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'tests/fixtures/public_tools'
PRIVATE_REPAIR = ROOT / 'tests/fixtures/private_repair'
CAMPAIGN = ROOT / 'runs/qwen-e2e'
PLUS = 'qwen3.7-plus-2026-05-26'
FLASH = 'qwen3.7-flash-2026-07-15'
MODELS = (FLASH, PLUS)
MODES = ('json_object', 'tool_calls')
STAGES = ('capability', 'public-tools', 'private-repair', 'freeze', 'mqtt-first', 'mqtt-repeat-1', 'mqtt-repeat-2', 'http')


def read(path):
    return json.loads(path.read_bytes())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_hashes():
    return {name: value for name, value in tree_hashes(FIXTURES).items()
            if name != 'frozen.json' and '__pycache__' not in Path(name).parts}


def system_prompt(mode):
    instruction = ('Use exactly ONE native function call per decision with the supplied tool schemas.'
                   if mode == 'tool_calls' else
                   'Return exactly ONE complete JSON action containing only tool and arguments; do not copy schema metadata such as $schema. Example: {"tool":"read_file","arguments":{"path":"main.c"}}. No Markdown or prose.\nAction schema:\n' +
                   json.dumps(load_schema('agent-action.schema.json'), separators=(',', ':')))
    return (FIXTURES / 'system.md').read_text().replace('{{action_instructions}}', instruction)


def experiment_config(base, phase, *, model=None, mode=None):
    values = public_config_snapshot(base)
    values['campaign'].update(phase=phase,
                              phase_max_cost_cny={'capability': 5.0, 'public_tools': 5.0})
    if phase != 'generation':
        values['coder'].update(model=model, fast_model=None, action_format=mode)
        values['budgets'].update(max_cost_cny=5, decisions_per_session=12, sessions_per_task=1,
                                 followups=0, final_repairs=0)
        if phase == 'capability':
            values['coder']['max_tokens'] = 1024
    config = ResolvedConfig.model_validate(values)
    require(config.coder.provider == 'qwen', 'Qwen-only campaign')
    require(config.coder.model in MODELS and config.coder.fast_model in (*MODELS, None), 'exact snapshot IDs required')
    require(config.budgets.campaign_max_cost_cny <= 300 and config.budgets.max_cost_cny <= 20
            and config.budgets.wall_clock_hours <= 4, 'campaign/generation limits exceeded')
    return config


def scoped_manifest(config, config_path=None):
    """Content identity intentionally excludes Git status and unrelated documents."""
    files = {f'nepa/{name}': value for name, value in runtime_fingerprint()['files'].items()}
    if config_path is not None:
        path = Path(config_path).resolve()
        files[path.relative_to(ROOT).as_posix()] = sha(path)
    audit = ROOT / 'project_docs/research/qwen-capability-audit.md'
    if audit.exists():
        files[audit.relative_to(ROOT).as_posix()] = sha(audit)
    for path in (ROOT / 'pyproject.toml', ROOT / 'uv.lock', ROOT / 'requirements.lock'):
        if path.exists():
            files[path.relative_to(ROOT).as_posix()] = sha(path)
    for name in ('baseline.py', 'driver.py', 'README.md'):
        path = ROOT / 'experiments/p0-p2' / name
        files[path.relative_to(ROOT).as_posix()] = sha(path)
    files['tests/test_p0_p2_experiments.py'] = sha(ROOT / 'tests/test_p0_p2_experiments.py')
    for subdir in ('tests/fixtures/public_tools', 'tests/fixtures/private_repair', 'gold_file/mqtt', 'gold_file/http'):
        files.update({f'{subdir}/{name}': value for name, value in tree_hashes(ROOT / subdir).items()
                      if '__pycache__' not in Path(name).parts and not name.endswith('.pyc')})
    image = subprocess.check_output(['docker', 'image', 'inspect', config.sandbox.image,
                                     '--format', '{{.Id}}'], text=True).strip()
    inputs = {}
    for protocol in ('mqtt', 'http'):
        source = ROOT / 'gold_file' / protocol
        spec, target = read(source / 'specIR.json'), read(source / 'target.json')
        inputs[protocol] = {'plan': digest(compile_plan(spec, target)), 'spec': digest(spec),
                            'target': digest(target), 'acceptance': digest(read(source / 'acceptance.json'))}
    return {'schema_version': 'candidate/1', 'files': files, 'config': public_config_snapshot(config),
            'image': image, 'protocol_inputs': inputs}


def assert_original_coverage():
    """Original requirement/check mappings cannot silently shrink during migration."""
    for protocol, inventory in (('mqtt', 'deepseek_mqtt_expanded'), ('http', 'deepseek_http')):
        old = Path(read(ROOT / 'runs/p0-baseline-inventory' / (inventory + '.json'))['run_directory'])
        source = ROOT / 'gold_file' / protocol
        require(read(old / 'inputs/spec.json')['requirements'] == read(source / 'specIR.json')['requirements'],
                protocol + ' original requirements changed')
        old_checks = read(old / 'inputs/acceptance.json')['checks']
        new_checks = read(source / 'acceptance.json')['checks']
        require(old_checks == new_checks, protocol + ' original check contracts changed; review a new cohort')


def probe_request(config, index):
    return LLMRequest(role='coder', system=system_prompt(config.coder.action_format),
                      user=('Call read_file now with path main.c. The file exists in the current project; '
                            'no preliminary discovery action is needed.' if index == 0 else
                            'Call list_files now for the current project directory.'),
                      model=config.coder.model, action_format=config.coder.action_format,
                      json_schema=load_schema('agent-action.schema.json'), temperature=config.coder.temperature,
                      max_tokens=config.coder.max_tokens)


def prepare(batch, config_path):
    require(read(FIXTURES / 'frozen.json')['files'] == fixture_hashes(), 'public fixture freeze changed')
    require('study_complete' in load_schema('run.schema.json')['properties']['status']['enum'],
            'Run7 study_complete owner interface must be installed before experiments')
    config_path = config_path.resolve()
    config_bytes = config_path.read_bytes()
    base = experiment_config(load_config(config_path), 'generation')
    require(base.coder.action_format == 'tool_calls', 'reviewed Qwen candidate requires native tool_calls; no fallback')
    audit_path = ROOT / 'project_docs/research/qwen-capability-audit.md'
    audit_bytes = audit_path.read_bytes()
    dates = re.findall(r'\d{4}-\d{2}-\d{2}', audit_bytes.decode())
    require(dates, 'capability audit must contain a source date')
    samples = []
    for model in MODELS:
        for mode in MODES:
            config = experiment_config(base, 'capability', model=model, mode=mode)
            for index in range(2):
                request = probe_request(config, index)
                prepared = LLMClient(config).prepare(request)  # serialization only: no network or reservation
                samples.append({'model': model, 'mode': mode, 'index': index,
                                'config': public_config_snapshot(config), 'request': request.model_dump(mode='json'),
                                'wire': prepared.wire, 'wire_sha256': hashlib.sha256(prepared.body).hexdigest()})
    frozen = scoped_manifest(base, config_path)
    assert_original_coverage()
    record = {'schema_version': 'p0-p2-experiment/1', 'status': False, 'paid_executed': False,
              'campaign_root': str(CAMPAIGN), 'created_at': time.time(),
              'config_path': str(config_path),
              'source_provenance': {'config_yaml': {'path': config_path.relative_to(ROOT).as_posix(), 'sha256': sha(config_path)},
                                    'capability_audit': {'path': audit_path.relative_to(ROOT).as_posix(),
                                                         'sha256': sha(audit_path), 'recorded_date': dates[0],
                                                         'source_dates': sorted(set(dates))}},
              'generation_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'admission': frozen, 'admission_sha256': digest(frozen), 'samples': samples,
              'public_session_configs': {model: public_config_snapshot(experiment_config(base, 'public_tools',
                                           model=model, mode='tool_calls')) for model in MODELS},
              'stages': {}, 'runs': [],
              'scope': 'public fixtures do not establish private isolation or real protocol generation'}
    require(config_path.read_bytes() == config_bytes and audit_path.read_bytes() == audit_bytes,
            'source config/audit changed while preparing; retry prepare after owner edits complete')
    batch.mkdir(parents=True, exist_ok=False)
    (batch / 'config-source.yaml').write_bytes(config_bytes)
    (batch / 'qwen-capability-audit.md').write_bytes(audit_bytes)
    atomic_json(batch / 'before-paid.json', record)
    atomic_json(batch / 'experiment.json', record)
    return record


def call_audit(store, *, only_model=None):
    rows = []
    for path in sorted((store.root / 'evidence/calls').glob('*.response.json')):
        response = read(path)['response']
        request_path = path.with_name(path.name.replace('.response.json', '.request.json'))
        request = read(request_path)
        model = request['wire']['model']
        metadata = response['provider_metadata']
        require(model in MODELS and (only_model is None or model == only_model), 'unexpected requested model')
        require(response['model'] == metadata.get('returned_model_identity') == model
                and metadata.get('returned_model_identity_observed') is True
                and metadata.get('requested_model_identity') == model
                and metadata.get('provider') == 'qwen', 'provider requested/returned identity mismatch')
        require(response['cached'] is False, 'response cache/import is forbidden')
        usage = metadata.get('usage', {})
        require(type(usage.get('prompt_tokens')) is int and type(usage.get('completion_tokens')) is int,
                'missing measured provider usage')
        require(response.get('pricing', {}).get('currency') == 'CNY', 'missing CNY settlement')
        require(request['phase'] == store.config.campaign.phase, 'wrong RunStore phase accounting')
        rows.append({'request': request_path.name, 'response': path.name, 'model': model,
                     'request_sha256': sha(request_path), 'response_sha256': sha(path)})
    require(rows, 'no actual paid responses; collection is not success')
    return rows


def new_store(config, source=FIXTURES):
    spec_name = 'spec.json' if source in (FIXTURES, PRIVATE_REPAIR) else 'specIR.json'
    return RunStore.initialize(CAMPAIGN, source / spec_name, source / 'target.json', source / 'acceptance.json', config)


def capability(batch, record, base):
    rows = []
    atomic_json(batch / 'capability-results.json', rows)
    for model in MODELS:
        for mode in MODES:
            config = experiment_config(base, 'capability', model=model, mode=mode)
            store = new_store(config)
            record['runs'].append(store.run_id)
            atomic_json(batch / 'experiment.json', record)
            row = {'model': model, 'mode': mode, 'run_id': store.run_id, 'status': False, 'samples': []}
            rows.append(row)
            try:
                with store.lock(), store.deadline():
                    for sample in [s for s in record['samples'] if s['model'] == model and s['mode'] == mode]:
                        request = LLMRequest.model_validate(sample['request'])
                        client = LLMClient(config)
                        require(hashlib.sha256(client.prepare(request).body).hexdigest() == sample['wire_sha256'],
                                'probe payload drift from before-paid freeze')
                        response = client.complete(request, store=store, task_id='bootstrap')
                        action, errors = decode_action(response, mode, load_schema('agent-action.schema.json'))
                        expected_tool = 'read_file' if sample['index'] == 0 else 'list_files'
                        failure_class = ('truncated' if response.provider_metadata.get('finish_reason') == 'length' else
                                         'content_filter' if response.provider_metadata.get('finish_reason') == 'content_filter' else
                                         'invalid_action' if errors else 'unexpected_action' if action['tool'] != expected_tool else None)
                        row['samples'].append({'index': sample['index'], 'status': failure_class is None,
                                               'failure_class': failure_class, 'finish_reason': response.provider_metadata.get('finish_reason'),
                                               'capability_unsupported': False,
                                               'action': action, 'errors': errors,
                                               'response_ref': store.run['last_response'], 'cost_cny': response.cost_cny})
                        atomic_json(batch / 'capability-results.json', rows)
                    row['calls'] = call_audit(store, only_model=model)
                    row['status'] = len(row['samples']) == 2 and all(s['status'] for s in row['samples'])
            except Exception as exc:
                row['error'] = f'{type(exc).__name__}: {exc}'
                row['failure_class'] = getattr(exc, 'failure_class', type(exc).__name__)
            finally:
                row['budget'] = store.run['budget']
                store.run.update(status='study_complete', exit_code=0 if row['status'] else 2,
                                 reason='Study stage ended; full generation not attempted. Capability outcome is in experiment.json.')
                store.save()
                atomic_json(batch / 'capability-results.json', rows)
    return {'status': len(rows) == 4 and all(r['status'] for r in rows), 'rows': rows}


def check_fixture_trace(fixture, trace, store, executions):
    model_rows = [r for r in trace if r['actor'] == 'model']
    names = [r['action']['tool'] for r in model_rows]
    cursor = iter(names)
    sequence = all(any(name == expected for name in cursor) for expected in fixture['sequence'])
    finishes = [r for r in model_rows if r['action']['tool'] == 'finish' and r['result'].get('accepted') is True]
    require(sequence and finishes, 'missing executed tool sequence or accepted finish')
    require(finishes[-1]['result']['build']['passed'] is True, 'finish lacked real host build')
    require({r['variant'] for r in finishes[-1]['result']['build']['builds']} >= {'release', 'san'}, 'both builds required')
    require(len(executions) == 2 and all(e['returncode'] == fixture['exit_code'] and e['stdout'] == fixture['stdout']
            and not e['timed_out'] and not e['stderr'] for e in executions), 'stdout/exit/termination contract failed')
    for name in ['Makefile', *fixture.get('unchanged', [])]:
        expected = (FIXTURES / name).read_text() if name == 'Makefile' else fixture['files'][name]
        require((store.project / name).read_text() == expected, 'fixture immutable source changed: ' + name)
    for name in fixture.get('changed', []):
        require((store.project / name).read_text() != fixture['files'][name], 'cross-file repair missing: ' + name)
    if fixture.get('exact_replace'):
        replacement = fixture['exact_replace']
        require(any(r['action'] == {'tool': 'replace_text', 'arguments': replacement} and 'written' in r['result']
                    for r in model_rows), 'exact replacement was not executed')
        require(not any(r['action']['tool'] == 'write_file' for r in model_rows), 'write_file bypassed replace fixture')
    if fixture.get('compile_failure_first'):
        first = model_rows[0]
        require(first['action'] == {'tool': 'run_command', 'arguments': {'argv': ['make', 'release']}}
                and first['result']['returncode'] not in (0, None) and 'error:' in first['result']['stderr'],
                'first model action must produce a genuine compiler failure')
        read_names = {r['action']['arguments']['path'] for r in model_rows if r['action']['tool'] == 'read_file'}
        require(set(fixture['files']) <= read_names, 'cross-file diagnostic repair requires source reads')
        requests = [read(p) for p in (store.root / 'evidence/calls').glob('*.request.json')]
        require(any('error:' in json.dumps(r['wire']['messages']) for r in requests), 'compiler diagnostic did not reach a request')
    claims = finishes[-1]['action']['arguments']['claims']
    require([c['id'] for c in claims] == fixture['requirement_ids'], 'missing original requirement claim')
    if fixture['id'] == 'finish_claim':
        require(claims[0]['status'] == 'already_present', 'finish fixture must acknowledge existing implementation')


def run_fixture(store, fixture, *, providers=None):
    require(store.config.coder.fast_model is None and store.config.budgets.sessions_per_task == 1
            and store.config.budgets.decisions_per_session <= 12, 'single-model, single-session bounds required')
    session = build_orchestrator(store, providers).session
    # Explicit test boundary: replace the complete production system prompt.
    session.system = system_prompt(store.config.coder.action_format)
    trace = []
    original_finish = store.finish_action
    actor = 'host_fixture_setup'

    def observed_finish(identifier, result):
        action = copy.deepcopy(store.run['pending_action']['action'])
        ref = original_finish(identifier, result)
        row = {'sequence': len(trace), 'actor': actor, 'call_counter': store.run['call_counter'],
               'action': action, 'result': result, 'result_ref': ref}
        trace.append(row)
        store.evidence(f'experiments/tool-sequence/{len(trace):04d}.json', row)
        return ref

    store.finish_action = observed_finish  # observe the existing executor; no second action loop
    try:
        for path, content in {'Makefile': (FIXTURES / 'Makefile').read_text(), **fixture['files']}.items():
            action = {'tool': 'write_file', 'arguments': {'path': path, 'content': content}}
            identifier = store.start_action('requirements:001', action)
            store.finish_action(identifier, session.tools.execute(action['tool'], action['arguments']))
        task = copy.deepcopy(next(t for t in store.plan()['tasks'] if t['id'] == 'requirements:001'))
        task.update(goal=fixture['goal'], context={'fixture': fixture['id'], 'stdout': fixture['stdout'],
                    'exit_code': 0, 'timeout_s': 2, 'requirements': store.inputs()[0]['requirements']})
        if fixture.get('exact_replace'):
            task['goal'] += (' Use these exact replace_text arguments, including the literal quote characters: ' +
                             json.dumps(fixture['exact_replace']))
        if fixture.get('compile_failure_first'):
            task['goal'] += (' Your very first action must be run_command with argv ["make", "release"], '
                             'before listing or reading any files. After that failure, read the source files '
                             'to diagnose and repair it.')
        store.evidence('experiments/public-fixture.json', {'fixture': fixture, 'task': task,
                       'system': session.system, 'seeded_project': tree_hashes(store.project), 'generation_evidence': False})
        actor = 'model'
        passed = session.run(task)
        require(passed, 'fixture session exhausted or failed')
        actor = 'host_output_audit'
        executions = []
        for build in store.inputs()[1]['builds']:
            action = {'tool': 'run_command', 'arguments': {'argv': ['./' + build['artifact']]}}
            identifier = store.start_action(task['id'], action)
            result = asdict(session.tools.executor.exec(action['arguments']['argv'], str(store.project), fixture['timeout_s']))
            store.finish_action(identifier, result)
            executions.append(result)
        check_fixture_trace(fixture, trace, store, executions)
        return {'status': True, 'fixture': fixture['id'], 'run_id': store.run_id, 'execution': executions,
                'trace': store.evidence('experiments/complete-tool-sequence.json', trace),
                'decisions': store.run['tasks'][task['id']]['decisions'], 'protocol_generation': False}
    finally:
        store.finish_action = original_finish


def public_tools(batch, record, base):
    require(base.coder.action_format == 'tool_calls', 'public sessions must prove the frozen native profile; no fallback')
    rows = []
    for model in MODELS:
        for fixture in read(FIXTURES / 'suite.json')['fixtures']:
            config = experiment_config(base, 'public_tools', model=model, mode=base.coder.action_format)
            store = new_store(config)
            record['runs'].append(store.run_id)
            atomic_json(batch / 'experiment.json', record)
            row = {'status': False, 'model': model, 'fixture': fixture['id'], 'run_id': store.run_id}
            try:
                with store.lock(), store.deadline():
                    row.update(run_fixture(store, fixture))
                    row['calls'] = call_audit(store, only_model=model)
            except Exception as exc:
                row.update(status=False, error=f'{type(exc).__name__}: {exc}')
            finally:
                row['budget'] = store.run['budget']
                store.run.update(status='study_complete', exit_code=0 if row['status'] else 2,
                                 reason='Study stage ended; full generation not attempted. Public fixture outcome is in experiment.json.')
                store.save()
                rows.append(row)
                atomic_json(batch / 'public-tool-results.json', rows)
            require(row['status'], f'public fixture failed: {model}/{fixture["id"]}; diagnose before a new attempt')
    return {'status': len(rows) == 8 and all(r['status'] for r in rows), 'rows': rows}


def run_private_repair(store, *, providers=None):
    require(store.config.campaign.phase == 'public_tools' and store.config.coder.model == PLUS
            and store.config.coder.fast_model is None and store.config.coder.action_format == 'tool_calls',
            'controlled repair requires the frozen Plus-only native profile and public_tools accounting')
    session = build_orchestrator(store, providers).session
    session.system = (PRIVATE_REPAIR / 'system.md').read_text().replace(
        '{{action_instructions}}', 'Use exactly ONE native function call per decision with the supplied tool schemas.')
    spec, target, acceptance = store.inputs()
    task = copy.deepcopy(next(t for t in store.plan()['tasks'] if t['id'] == 'requirements:001'))
    task.update(goal='Repair the complete binary echo requirement using only current public source and the published diagnostic.',
                context={'requirements': spec['requirements'], 'scope': 'host-seeded controlled private-feedback repair'})
    for path in sorted((PRIVATE_REPAIR / 'project').rglob('*')):
        if path.is_file():
            action = {'tool': 'write_file', 'arguments': {'path': path.relative_to(PRIVATE_REPAIR / 'project').as_posix(),
                                                         'content': path.read_text()}}
            identifier = store.start_action(task['id'], action)
            store.finish_action(identifier, session.tools.execute(action['tool'], action['arguments']))
    initial = tree_hashes(store.project)
    # Record host build as one host action; only CodingSession may accept the task.
    host_action = {'tool': 'finish', 'arguments': {'summary': 'Host seeded-fixture build; not task acceptance', 'claims': []}}
    identifier = store.start_action(task['id'], host_action)
    builds = session.builder.run(target, store.project, clean=True)
    store.finish_action(identifier, {'build': builds, 'accepted': False, 'actor': 'host_fixture_setup'})
    require(builds['passed'], 'seeded controlled fixture must compile before private behavioral failure')
    seed = secrets.token_hex(32)
    private_hashes = copy.deepcopy(store.run['private_inputs'])
    store.evidence('experiments/private-repair-before.json', {'seed': seed, 'project': initial,
                   'private_inputs': private_hashes, 'task': task, 'system': session.system, 'protocol_generation': False})
    before = session.verifier.run(target, acceptance, store.project, store.private_checks,
                                  store.root / 'evidence/private-repair-before', seed=seed)
    before_ref = store.evidence('experiments/private-repair-failure.json', before)
    require(before['passed'] is False and len(before['variants']) == 2, 'private oracle must actually fail before repair')
    for variant in before['variants']:
        checks = variant['detail']['checks']
        require(len(checks) == 1 and checks[0]['id'] == 'echo-complete' and checks[0]['passed'] is False
                and checks[0]['category'] == 'echo_mismatch'
                and checks[0]['observation']['expected_length'] > checks[0]['observation']['actual_length'],
                'controlled failure must be actual short echo, not an infrastructure failure')
    feedback = session.publish_feedback('experiments/private-repair-safe.json', before)
    feedback['model_route'] = {'reason': 'private_acceptance_repair'}
    actions_before = set((store.root / 'evidence/actions').glob('*.json'))
    require(session.run(task, repair=True, feedback=feedback), 'bounded Plus repair session failed')
    model_actions = sorted(set((store.root / 'evidence/actions').glob('*.json')) - actions_before)
    action_rows = [read(p) for p in model_actions]
    require(any(r['action']['action']['tool'] in ('write_file', 'replace_text') for r in action_rows),
            'private repair requires an executed model edit')
    require(any(r['action']['action']['tool'] == 'finish' and r['result'].get('accepted') is True for r in action_rows),
            'private repair requires accepted finish and actual host builds')
    require(store.run['private_inputs'] == private_hashes, 'private assertion changed during repair')
    store.inputs()  # also checks actual private bytes, not only refs
    after = session.verifier.run(target, acceptance, store.project, store.private_checks,
                                 store.root / 'evidence/private-repair-after', seed=seed)
    after_ref = store.evidence('experiments/private-repair-retest.json', after)
    require(after['passed'] is True and len(after['variants']) == 2, 'same private assertion must pass after model repair')
    for variant in after['variants']:
        checks = variant['detail']['checks']
        require(len(checks) == 1 and checks[0]['id'] == 'echo-complete' and checks[0]['passed'] is True,
                'retest omitted original private assertion')
    require((store.project / 'server.c').read_bytes() != (PRIVATE_REPAIR / 'project/server.c').read_bytes(),
            'source was not repaired by session')
    require((store.project / 'Makefile').read_bytes() == (PRIVATE_REPAIR / 'project/Makefile').read_bytes(),
            'repair changed the controlled build contract')
    calls = call_audit(store, only_model=PLUS)
    for path in (store.root / 'evidence/calls').glob('*.request.json'):
        wire = json.dumps(read(path)['wire'])
        require(seed not in wire and 'oracle.py' not in wire and str(store.private_checks) not in wire,
                'raw private data entered model context')
    return {'status': True, 'run_id': store.run_id, 'protocol_generation': False,
            'before': before_ref, 'after': after_ref, 'same_seed': True, 'private_assertion_sha256': digest(private_hashes),
            'safe_feedback': feedback['evidence_ref'], 'model_actions': {p.name: sha(p) for p in model_actions},
            'calls': calls, 'budget': store.run['budget']}


def private_repair(batch, record, base):
    config = experiment_config(base, 'public_tools', model=PLUS, mode='tool_calls')
    store = new_store(config, PRIVATE_REPAIR)
    record['runs'].append(store.run_id)
    record['stages']['private-repair']['run_id'] = store.run_id
    atomic_json(batch / 'experiment.json', record)
    row = {'status': False, 'run_id': store.run_id, 'protocol_generation': False}
    try:
        with store.lock(), store.deadline():
            row.update(run_private_repair(store))
    except Exception as exc:
        row.update(status=False, error=f'{type(exc).__name__}: {exc}')
    finally:
        row['budget'] = store.run['budget']
        store.run.update(status='study_complete', exit_code=0 if row['status'] else 2,
                         reason='Controlled private repair study ended; not fresh protocol generation.')
        store.save()
        atomic_json(batch / 'private-repair-results.json', row)
    return row


def empty_initial(store):
    require(not tree_hashes(store.project), 'generation must start with empty project')
    require(store.run['call_counter'] == 0 and store.run['budget']['calls'] == 0
            and not store.run['pending_calls'] and 'last_response' not in store.run, 'prior responses/imports found')
    require(not list((store.root / 'evidence/calls').glob('*')), 'pre-existing calls/cache found')
    tree = subprocess.check_output(['git', '--git-dir', str(store.root / 'checkpoints.git'), 'ls-tree', '-r',
                                    '--name-only', store.run['accepted_checkpoint']], text=True)
    require(not tree.strip(), 'initial checkpoint is not empty')
    return {'status': True, 'project': {}, 'calls': 0, 'checkpoint': store.run['accepted_checkpoint'],
            'run_state_sha256': sha(store.root / 'run.json'), 'response_cache': False, 'imported_solution': False}


def verify_generation(store, frozen, destination, protocol):
    state, report = store.run, read(store.root / 'report.json')
    require(state['status'] == report['status'] == 'success', 'generation/report did not succeed')
    require(state['schema_version'] == '7.0' and report['schema_version'] == '5.0', 'Run7/Report5 required')
    require(state['config_sha256'] == digest(frozen['config']) and state['sandbox_image'] == frozen['image'], 'run config/image drift')
    expected_runtime = {k.removeprefix('nepa/'): v for k, v in frozen['files'].items() if k.startswith('nepa/')}
    require(state['runtime']['files'] == expected_runtime, 'run runtime drift')
    require(not any(h['kind'] == 'configuration_change' for h in state['history']), 'reconfigured run cannot count')
    require(state['verification_policy']['exposure'] == 'private_isolated', 'private isolation metadata missing')
    spec, target, acceptance = store.inputs()
    for key, ref in {**state['inputs'], **state['private_inputs']}.items():
        if key == 'index':
            continue
        source = {'spec': 'specIR.json', 'target': 'target.json', 'acceptance': 'acceptance.json'}.get(
            key, key.removeprefix('check:'))
        require(ref['sha256'] == frozen['files'][f'gold_file/{protocol}/{source}'], 'run input differs from frozen candidate: ' + key)
    require(digest(compile_plan(spec, target)) == frozen['protocol_inputs'][protocol]['plan'], 'initial plan differs from candidate')
    ids = [r['id'] for r in spec['requirements']]
    require([r['id'] for r in report['requirements']] == ids, 'report omitted/reordered original requirements')
    claims = [c for t in state['tasks'].values() for c in t['claims']]
    require(len(claims) == len(ids) and {c['id'] for c in claims} == set(ids), 'claims do not cover every original requirement')
    for task in store.plan()['tasks']:
        require(state['tasks'][task['id']]['status'] == 'passed', 'unfinished task')
        validate_claims(task, state['tasks'][task['id']]['claims'], store.project)
    required_ids = {r for c in acceptance['checks'] if c['required'] for r in c['req_ids']}
    require(all(r['agent_claim'] and (r['id'] not in required_ids or r['verification']['status'] == 'scenarios_passed')
                for r in report['requirements']), 'claim/coverage report incomplete')
    require(report['cache_hits'] == 0, 'cached generation cannot count')
    calls = call_audit(store)
    delivery = store.root / state['delivery']['path']
    require(tree_hashes(delivery) == state['delivery']['files'], 'delivery drift')
    require(tree_hashes(store.project) == state['working_hashes'], 'unrecorded generated-project changes')
    destination.mkdir(parents=True, exist_ok=False)
    copied = destination / 'project'
    shutil.copytree(delivery, copied, symlinks=True)
    executor = SandboxExecutor(store.config.sandbox.image, store.config.sandbox.cpu, store.config.sandbox.mem_gb)
    started = time.time()
    builds = BuildRunner(executor, store.config.sandbox.build_timeout_s).run(target, copied, clean=True)
    checks = VerificationRunner(executor).run(target, acceptance, copied, store.private_checks,
                destination / 'private-verification') if builds['passed'] else None
    row = {'status': bool(builds['passed'] and checks and checks['passed']), 'builds': builds, 'checks': checks,
           'calls': calls, 'run_id': store.run_id, 'budget': state['budget'],
           'independent_audit_started_at': started, 'independent_audit_finished_at': time.time(),
           'original_requirement_count': len(ids), 'scenario_mapped_requirements': len(required_ids),
           'remaining_coverage_gaps': [r for r in ids if r not in required_ids]}
    atomic_json(destination / 'result.json', row)
    require(tree_hashes(delivery) == state['delivery']['files'], 'audit changed source delivery')
    require(row['status'], 'copied export clean release/san/full private verification failed')
    return row


def generation(stage, batch, record, base):
    protocol = 'http' if stage == 'http' else 'mqtt'
    frozen = record['stages']['freeze']['candidate']
    require(scoped_manifest(base, record['config_path']) == frozen, 'candidate drift: start a new three-MQTT cohort')
    assert_original_coverage()
    store = new_store(base, ROOT / 'gold_file' / protocol)
    record['runs'].append(store.run_id)
    record['stages'][stage]['run_id'] = store.run_id
    atomic_json(batch / 'experiment.json', record)
    initial = empty_initial(store)
    atomic_json(batch / (stage + '-initial.json'), initial)
    store.evidence('experiments/initial-empty.json', initial)
    # Production orchestration owns serial tasks, repair bounds, checkpoints, billing and deadline.
    code = build_orchestrator(store).run(store)
    require(code == 0, f'{stage} failed (run {store.run_id}, exit {code}); no repeats launched')
    row = verify_generation(store, frozen, batch / (stage + '-independent-audit'), protocol)
    require(scoped_manifest(base, record['config_path']) == frozen, 'candidate changed during run: sample does not count')
    row.update(protocol=protocol, candidate_sha256=digest(frozen), initial=initial)
    return row


def gate(record, stage):
    require(stage not in record['stages'], 'stage already attempted; preserve it and prepare a new batch for diagnosis/retry')
    predecessors = {'capability': (), 'public-tools': ('capability',), 'private-repair': ('public-tools',),
                    'freeze': ('capability', 'public-tools', 'private-repair'),
                    'mqtt-first': ('freeze',), 'mqtt-repeat-1': ('mqtt-first',),
                    'mqtt-repeat-2': ('mqtt-first', 'mqtt-repeat-1'),
                    'http': ('mqtt-first', 'mqtt-repeat-1', 'mqtt-repeat-2')}
    require(all(record['stages'].get(s, {}).get('status') is True for s in predecessors[stage]),
            'predecessor gate incomplete or failed: ' + stage)


def summary(record):
    stages = {stage: record['stages'].get(stage, {}).get('status') is True for stage in STAGES}
    return {'status': all(stages.values()),
            'stages': stages, 'private_feedback_repair': stages['private-repair'],
            'paid_executed': record['paid_executed'], 'run_ids': record['runs'],
            'scope': 'three fresh MQTT plus fresh HTTP under one candidate; mapped scenarios only',
            'limitation': 'Controlled fixture repair is not fresh MQTT generation or full protocol conformance.'}


def archive(record, stage, output):
    row = record['stages'].get(stage, {})
    require(stage in ('mqtt-first', 'mqtt-repeat-1', 'mqtt-repeat-2', 'http') and row.get('status') is True,
            'only independently verified successful public deliveries may be archived')
    store = RunStore.open(CAMPAIGN, row['run_id'])
    delivery = store.root / store.run['delivery']['path']
    require(store.run['status'] == 'success', 'study or incomplete run cannot be a production archive')
    require(not output.resolve().is_relative_to(delivery.resolve()), 'archive output cannot modify source delivery')
    require(tree_hashes(delivery) == store.run['delivery']['files'], 'delivery changed')
    public = public_report(read(store.root / 'report.json'))
    store.inputs()  # verify all frozen public/private input digests before copying public inputs
    paths = sorted(delivery.rglob('*'))
    for path in paths:
        require(path.resolve().is_relative_to(delivery.resolve()), 'delivery symlink escapes to non-public source')
        require(path.is_symlink() or path.is_file() or path.is_dir(), 'unsupported delivery member')

    def public_link(info):
        if info.issym():
            source = delivery / Path(info.name).relative_to('project')
            info.linkname = os.path.relpath(source.resolve(), source.parent)
        return info

    with tarfile.open(output, 'x:gz') as bundle:
        for path in paths:
            name = path.relative_to(delivery).as_posix()
            bundle.add(path, arcname='project/' + name, recursive=False, filter=public_link)
        for key in ('spec', 'target', 'index'):
            ref = store.run['inputs'][key]
            bundle.add(store.root / ref['path'], arcname='inputs/' + key + '.json', recursive=False)
        public_files = {'report.json': public,
                        'independent-audit.json': safe_feedback({'build': row['builds'], 'verification': row['checks']}),
                        'candidate.json': {'candidate_sha256': row['candidate_sha256'], 'run_id': row['run_id'],
                                           'protocol': row['protocol'], 'independent_audit_passed': row['status']}}
        for name, value in public_files.items():
            data = (json.dumps(value, indent=2) + '\n').encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', *STAGES, 'summary', 'archive'))
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--paid', action='store_true')
    parser.add_argument('--stage', choices=('mqtt-first', 'mqtt-repeat-1', 'mqtt-repeat-2', 'http'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    batch = args.batch.resolve()
    if args.command == 'prepare':
        require(args.config, 'prepare requires --config with owner-reviewed Qwen Config3')
        result = prepare(batch, args.config)
        print(json.dumps({'status': False, 'prepared': True, 'paid_executed': False, 'batch': str(batch)}))
        return
    with file_lock(batch / '.lock'):
        record = read(batch / 'experiment.json')
        base = ResolvedConfig.model_validate(record['admission']['config'])
        if args.command == 'summary':
            print(json.dumps(summary(record), indent=2))
            return
        if args.command == 'archive':
            require(args.stage and args.output, 'archive requires --stage and a new --output')
            archive(record, args.stage, args.output)
            print(json.dumps({'status': True, 'archive': str(args.output)}))
            return
        gate(record, args.command)
        require(scoped_manifest(base, record['config_path']) == record['admission'], 'scoped candidate drift; prepare a new batch')
        if args.command != 'freeze':
            require(args.paid, 'paid commands require explicit --paid; no API called')
        record['stages'][args.command] = {'status': False, 'state': 'running', 'started_at': time.time()}
        atomic_json(batch / 'experiment.json', record)
        try:
            if args.command == 'freeze':
                assert_original_coverage()
                result = {'status': True, 'candidate': scoped_manifest(base, record['config_path']), 'cohort_size': 3}
            else:
                record['paid_executed'] = True
                atomic_json(batch / 'experiment.json', record)
                function = {'capability': capability, 'public-tools': public_tools, 'private-repair': private_repair}.get(args.command)
                result = function(batch, record, base) if function else generation(args.command, batch, record, base)
            record['stages'][args.command].update(result, state='complete')
        except Exception as exc:
            record['stages'][args.command].update(status=False, state='failed', error=f'{type(exc).__name__}: {exc}')
        finally:
            record['stages'][args.command]['finished_at'] = time.time()
            record['status'] = summary(record)['status']
            atomic_json(batch / 'experiment.json', record)
        print(json.dumps(record['stages'][args.command], indent=2))
        if not record['stages'][args.command]['status']:
            raise SystemExit(2)


if __name__ == '__main__':
    main()
