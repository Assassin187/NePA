"""Pre-registered paired action-format study; charged to the new CNY MQTT campaign."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import httpx

from nepa.application import build_orchestrator
from nepa.config import load_config, public_config_snapshot
from nepa.llm.client import LLMClient, LLMRequest, decode_action
from nepa.llm.providers.openai_compat import OpenAICompatibleProvider
from nepa.llm.telemetry import calculate_cost, price_usage
from nepa.report import publish_report
from nepa.run_store import RunStore, atomic_json, runtime_fingerprint, campaign_cost_cny
from nepa.schemas import load_schema
from nepa.speclib.lint import digest
from nepa.speclib.plan import compile_plan

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL = ROOT / 'runs/_refactor/worktree/runs/e2e'
CAMPAIGN = ROOT / 'runs/mqtt-e2e'
# New authorized CNY campaign: fixed sublimit, never replenish it on retry.
STUDY_CAMPAIGN_CEILING = 10
MODES = ('json_object', 'tool_calls')
SCHEMA = load_schema('agent-action.schema.json')
MAKEFILE = ('release:\n\tmkdir -p build/release\n\tgcc -std=c99 -Wall -Wextra -Werror main.c -o build/release/protocol-server\n'
            'san:\n\tmkdir -p build/san\n\tgcc -std=c99 -Wall -Wextra -Werror -fsanitize=address,undefined -fno-pie -no-pie main.c -o build/san/protocol-server\n'
            'clean:\n\trm -rf build\n')


def campaign_cost():
    return campaign_cost_cny(CAMPAIGN)

def samples():
    """Historical facts are diagnostic input only; never imported into a live project."""
    historical = HISTORICAL / '20260912T135449Z-4c036768/evidence/calls'
    requests = [json.loads(p.read_bytes()) for p in sorted(historical.glob('*.request.json'))]
    result = []
    http_spec = json.loads((ROOT / 'gold_file/http/specIR.json').read_bytes())
    target = json.loads((ROOT / 'gold_file/http/target.json').read_bytes())
    http_tasks = compile_plan(http_spec, target)['tasks']
    for model, count in [('deepseek-flash', 12), ('deepseek-v4-pro', 4)]:
        pool = [r for r in requests if r['wire']['model'] == model and len(json.dumps(r['wire'], ensure_ascii=False).encode()) < 145000]
        assert len(pool) >= count
        for i in range(count):
            historical_request = pool[int((i + .5) * len(pool) / count)]
            content = historical_request['wire']['messages'][1]['content'].split('\nDecision budget:')[0]
            facts = json.loads(content)
            result.append({'id': f'mqtt-{model}-{i:02d}', 'model': model, 'protocol': 'mqtt',
                           'facts': facts, 'historical_request_sha256': digest(historical_request)})
            task = http_tasks[(i + 1) % len(http_tasks)]
            facts = {'task': task, 'target': target, 'spec_index': {'protocol': http_spec['protocol'], 'input_path': 'inputs/spec.json'},
                     'pipeline': [{'id': t['id'], 'kind': t['kind'], 'primary_requirement_count': len(t['requirement_ids'])} for t in http_tasks],
                     'current_observations': [], 'latest_observed_diagnostic': None,
                     'initial_feedback': 'This is a format-only request. Select one next action for the current task; actual source inspection would occur through the tools.'}
            result.append({'id': f'http-{model}-{i:02d}', 'model': model, 'protocol': 'http', 'facts': facts})
    assert len(result) == 32
    return result


def invalid_category(response, mode, errors):
    if not errors:
        return None
    if response.provider_metadata.get('finish_reason') in ('length', 'content_filter'):
        return 'truncated'
    if mode == 'tool_calls':
        if not response.tool_calls:
            return 'zero_calls'
        if len(response.tool_calls) > 1:
            return 'multiple_calls'
        call = response.tool_calls[0]
        names = {entry['properties']['tool']['const'] for entry in SCHEMA['oneOf']}
        if call.get('function', {}).get('name') not in names:
            return 'unknown_tool'
        return 'invalid_arguments_or_envelope'
    if not response.text.strip():
        return 'empty'
    if response.text.lstrip().startswith('<') or 'DSML' in response.text:
        return 'xml_dsml'
    try:
        json.loads(response.text)
    except ValueError:
        return 'json_syntax_or_prose'
    return 'schema'


def metrics(rows):
    valid = sum(r['valid'] for r in rows)
    return {'samples': len(rows), 'invalid': len(rows) - valid,
            'invalid_rate': (len(rows) - valid) / len(rows) if rows else None,
            'invalid_api_s': sum(r['elapsed_s'] for r in rows if not r['valid']),
            'seconds_per_valid': sum(r['elapsed_s'] for r in rows) / valid if valid else None,
            'cny_per_valid': sum(r['cost_cny'] for r in rows) / valid if valid else None}


def assess(rows, sessions, complete):
    results = {mode: {'all': metrics([r for r in rows if r['mode'] == mode]),
                     **{model: metrics([r for r in rows if r['mode'] == mode and r['model'] == model])
                        for model in ('deepseek-flash', 'deepseek-v4-pro')}} for mode in MODES}
    control, native = results['json_object'], results['tool_calls']
    gates = {'complete': complete,
             'flash_rate': complete and native['deepseek-flash']['invalid_rate'] <= .05,
             'flash_reduction': complete and control['deepseek-flash']['invalid'] > 0 and native['deepseek-flash']['invalid'] <= .5 * control['deepseek-flash']['invalid'],
             'pro_no_regression': complete and native['deepseek-v4-pro']['invalid'] <= control['deepseek-v4-pro']['invalid'],
             'short_sessions': complete and all(s['passed'] for s in sessions),
             'time': complete and all(r['all']['seconds_per_valid'] is not None for r in results.values()) and native['all']['seconds_per_valid'] <= 1.1 * control['all']['seconds_per_valid'],
             'cost': complete and all(r['all']['cny_per_valid'] is not None for r in results.values()) and native['all']['cny_per_valid'] <= 1.1 * control['all']['cny_per_valid']}
    return {'metrics': results, 'gates': gates, 'promote_native': all(gates.values()),
            'scope': 'small paired format study and tool fixtures, not protocol-generation or statistical stability evidence'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--paid', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    base_config = load_config(ROOT / 'configs/default.yaml')
    starting_cost = campaign_cost()
    ceiling = STUDY_CAMPAIGN_CEILING
    data = samples()
    prereg = {'samples': data, 'sample_counts_per_mode': {'deepseek-flash': 24, 'deepseek-v4-pro': 8},
              'short_sessions_per_mode': ['read_write', 'replace', 'compiler_repair', 'finish'],
              'runtime': runtime_fingerprint(), 'config': public_config_snapshot(base_config),
              'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'study_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'starting_campaign_cost_cny': starting_cost, 'study_cap_cny': 10, 'campaign_ceiling_cny': ceiling,
              'thresholds': {'flash_invalid_rate_max': .05, 'flash_relative_reduction_min': .5,
                             'pro_invalid_increase_max': 0, 'time_and_cost_ratio_max': 1.1, 'all_short_sessions_pass': True}}
    atomic_json(output / 'preregistration.json', prereg)
    if not args.paid:
        print(json.dumps({'offline_preregistration': str(output / 'preregistration.json')}))
        return
    assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip(), 'commit before paid comparison'
    stores, clients, systems = {}, {}, {}
    all_stores = []
    rows, sessions = [], []
    error = None
    def new_store(mode):
        config = load_config(ROOT / 'configs/default.yaml', overrides={'coder': {'action_format': mode},
                             'budgets': {'max_cost_cny': 10, 'campaign_max_cost_cny': ceiling}})
        store = RunStore.initialize(CAMPAIGN, ROOT / 'gold_file/mqtt/specIR.json', ROOT / 'gold_file/mqtt/target.json', ROOT / 'gold_file/mqtt/acceptance.json', config)
        all_stores.append(store)
        return store
    try:
        assert starting_cost < ceiling, "Authorized study allocation is exhausted"
        for mode in MODES:
            store = new_store(mode)
            stores[mode] = store
            orchestrator = build_orchestrator(store)
            clients[mode] = orchestrator.session.client
            systems[mode] = orchestrator.session.system
        # Freeze/check actual request sizes for every pair before the first paid call.
        for sample in data:
            for mode in MODES:
                request = LLMRequest(role='coder', system=systems[mode], user=json.dumps(sample['facts'], ensure_ascii=False),
                                     messages=[{'role': 'user', 'content': json.dumps(sample['facts'], ensure_ascii=False)}],
                                     action_format=mode, json_schema=SCHEMA, model=sample['model'],
                                     temperature=base_config.coder.temperature, max_tokens=base_config.coder.max_tokens)
                wire = OpenAICompatibleProvider._payload(request, sample['model'], False)
                assert len(json.dumps(wire, ensure_ascii=False).encode()) <= base_config.coder.context_max_bytes
        for index, sample in enumerate(data):
            # Alternate request order within each pair to reduce temporal ordering effects.
            for mode in MODES if index % 2 == 0 else tuple(reversed(MODES)):
                request = LLMRequest(role='coder', system=systems[mode], user=json.dumps(sample['facts'], ensure_ascii=False),
                                     messages=[{'role': 'user', 'content': json.dumps(sample['facts'], ensure_ascii=False)}],
                                     action_format=mode, json_schema=SCHEMA, model=sample['model'],
                                     temperature=base_config.coder.temperature, max_tokens=base_config.coder.max_tokens)
                store = stores[mode]
                result = clients[mode].complete(request, store=store, task_id='bootstrap')
                action, errors = decode_action(result, mode, SCHEMA)
                evidence = store.read_ref(store.run['last_response'])
                rows.append({'sample': sample['id'], 'mode': mode, 'model': sample['model'], 'protocol': sample['protocol'],
                             'valid': not errors, 'invalid_category': invalid_category(result, mode, errors), 'errors': errors, 'tool': action.get('tool') if isinstance(action, dict) else None,
                             'elapsed_s': evidence['elapsed_s'], 'cost_cny': result.cost_cny, 'pricing': result.pricing,
                             'run_id': store.run_id, 'call': store.run['call_counter'], 'response_ref': store.run['last_response']})
                atomic_json(output / 'format-results.json', rows)
            print(f'Format pair {index + 1}/32 completed; study cost=CNY{campaign_cost() - starting_cost:.4f}', flush=True)
        fixtures = [
            ('read_write', '#include "answer.h"\nint main(void){return ANSWER == 42 ? 0 : 1;}\n', 'Read main.c and create answer.h defining ANSWER as 42; do not change main.c. Then finish.'),
            ('replace', 'int main(void){return 7;}\n', 'Read main.c and use replace_text to replace the return value 7 with 0. Then finish.'),
            ('compiler_repair', 'int main(void){return 0}\n', 'Use the actual compiler diagnostic to fix main.c. Then finish.'),
            ('finish', 'int main(void){return 0;}\n', 'This fixture is complete. Request finish with empty claims to obtain host build verification.'),
        ]
        for name, source, goal in fixtures:
            for mode in MODES:
                store = new_store(mode)
                session = build_orchestrator(store).session
                # Host-created test fixture is explicitly recorded and is never live-generation evidence.
                for path, content in [('Makefile', MAKEFILE), ('main.c', source), ('README.md', 'Action-study fixture; not a protocol implementation.')]:
                    action = {'tool': 'write_file', 'arguments': {'path': path, 'content': content}}
                    identifier = store.start_action('bootstrap', action)
                    store.finish_action(identifier, session.tools.execute(action['tool'], action['arguments']))
                feedback = None
                if name == 'compiler_repair':
                    action = {'tool': 'run_command', 'arguments': {'argv': ['make', 'release']}}
                    identifier = store.start_action('bootstrap', action)
                    feedback = session.tools.execute(action['tool'], action['arguments'])
                    store.finish_action(identifier, feedback)
                    assert feedback['returncode'] != 0
                task = copy.deepcopy(store.plan()['tasks'][0])
                task.update(goal='FORMAT STUDY ONLY: ' + goal, context={'fixture': name})
                passed = session.run(task, feedback=feedback)
                execution = session.tools.execute('run_command', {'argv': ['./build/release/protocol-server']}) if passed else None
                passed = bool(passed and execution['returncode'] == 0)
                sessions.append({'fixture': name, 'mode': mode, 'passed': passed, 'run_id': store.run_id,
                                 'budget': store.run['budget'], 'execution': execution})
                atomic_json(output / 'session-results.json', sessions)
                print(f'Short session {name}/{mode}: {passed}; study cost=CNY{campaign_cost() - starting_cost:.4f}', flush=True)
        # Strict Beta probe submits the unchanged complete action parameter schemas.
        # Rejection is capability evidence; no local validator is relaxed to make it pass.
        store = new_store('tool_calls')
        request = LLMRequest(role='coder', system=systems['tool_calls'], user='Call finish with summary probe and empty claims.',
                             action_format='tool_calls', json_schema=SCHEMA, temperature=0, max_tokens=16000,
                             model='deepseek-flash')
        wire = OpenAICompatibleProvider._payload(request, request.model, False)
        for tool in wire['tools']:
            tool['function']['strict'] = True
        price = store.config.pricing['deepseek/deepseek-flash']
        reservation = calculate_cost(price, len(json.dumps(wire, ensure_ascii=False).encode()) + 64, request.max_tokens)
        sequence = store.reserve_call('bootstrap', reservation, wire)
        started = time.monotonic()
        provider_config = store.config.providers['deepseek']
        probe = {'endpoint': provider_config.base_url.rstrip('/') + '/beta/chat/completions', 'run_id': store.run_id,
                 'unchanged_local_schemas': True}
        try:
            with httpx.Client(timeout=120) as client:
                result = client.post(probe['endpoint'], headers={'Authorization': 'Bearer ' + os.environ[provider_config.api_key_env]}, json=wire)
            probe.update(status_code=result.status_code, elapsed_s=time.monotonic() - started, response=result.text)
            usage = None
            for line in result.text.splitlines():
                if line.startswith('data: ') and line[6:] != '[DONE]':
                    event = json.loads(line[6:])
                    if event.get('usage'):
                        usage = event['usage']
            if result.is_success and usage:
                store.settle_call(sequence, {'model': 'deepseek-flash', 'text': result.text, 'tokens_in': usage['prompt_tokens'],
                                  'tokens_out': usage['completion_tokens'], 'cost_cny': (pricing := price_usage(price, usage['prompt_tokens'], usage['completion_tokens'],
                                      started_at=store.run['pending_calls'][str(sequence)]['started_at'],
                                      cache_hit_tokens=usage.get('prompt_cache_hit_tokens')))['cost_cny'], 'pricing': pricing,
                                  'cached': False, 'provider_metadata': {'provider': 'deepseek', 'strict_probe': True}}, elapsed_s=probe['elapsed_s'])
            else:
                store.fail_call(sequence, RuntimeError(f'strict probe HTTP {result.status_code}; usage unknown'), elapsed_s=probe['elapsed_s'])
        except Exception as exc:
            probe.update(error=f'{type(exc).__name__}: {exc}')
            store.fail_call(sequence, exc, elapsed_s=time.monotonic() - started)
        atomic_json(output / 'strict-probe.json', probe)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    finally:
        for store in all_stores:
            store.run.update(status='failed', exit_code=2, reason='Action-interface study only; no protocol generation attempted.')
            store.save()
            publish_report(store)
        complete = len(rows) == 64 and len(sessions) == 8 and error is None
        summary = {**assess(rows, sessions, complete), 'error': error, 'complete': complete,
                   'preregistration_sha256': hashlib.sha256((output / 'preregistration.json').read_bytes()).hexdigest(),
                   'study_cost_cny': campaign_cost() - starting_cost,
                   'run_ids': [s.run_id for s in all_stores], 'no_generated_protocol_claim': True}
        atomic_json(output / 'summary.json', summary)
        print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
