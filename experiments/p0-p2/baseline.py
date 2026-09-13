"""Read-only P0 collector. Never opens a historical run through current RunStore."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execution_seconds(value):
    """Outer execution includes its nested worker; do not count both."""
    if isinstance(value, dict):
        if isinstance(value.get('duration_ms'), (int, float)):
            return value['duration_ms'] / 1000
        if isinstance(value.get('execution'), dict):
            return execution_seconds(value['execution'])
        return sum(execution_seconds(v) for v in value.values())
    if isinstance(value, list):
        return sum(execution_seconds(v) for v in value)
    return 0.0


def action_errors(response, wire, schema):
    if response.get('provider_metadata', {}).get('finish_reason') in ('length', 'content_filter'):
        return 'truncated'
    native = 'tools' in wire
    try:
        if native:
            calls = response.get('tool_calls', [])
            if len(calls) != 1:
                return 'zero_calls' if not calls else 'multiple_calls'
            call = calls[0]
            if not call.get('id') or call.get('type') != 'function':
                return 'invalid_envelope'
            value = {'tool': call['function']['name'], 'arguments': json.loads(call['function']['arguments'])}
        else:
            raw = response.get('text', '')
            if not raw.strip():
                return 'empty'
            if raw.lstrip().startswith('<') or 'DSML' in raw:
                return 'xml_dsml'
            value = json.loads(raw)
        return 'schema' if list(Draft202012Validator(schema).iter_errors(value)) else None
    except (ValueError, KeyError, TypeError):
        return 'invalid_arguments' if native else 'json_syntax_or_prose'


def collect(run_dir: Path, *, name=None):
    run_dir = run_dir.resolve()
    state = read(run_dir / 'run.json')
    schema_path = ROOT / 'nepa/schemas/agent-action.schema.json'
    schema = read(schema_path)
    currency = 'CNY' if 'cost_cny' in state['budget'] else 'USD'
    cost_key = 'cost_' + currency.lower()
    settled_key = 'settled_' + currency.lower()
    models, tasks, tools = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    totals, errors, invalid = Counter(), Counter(), Counter()
    hashes = {}

    def evidence(path):
        hashes[path.relative_to(run_dir).as_posix()] = sha(path)
        return read(path)

    calls, spans = [], []
    requests = sorted((run_dir / 'evidence/calls').glob('*.request.json'))
    for path in requests:
        request = evidence(path)
        prefix = path.name.removesuffix('.request.json')
        response_path = path.with_name(prefix + '.response.json')
        error_path = path.with_name(prefix + '.error.json')
        result = evidence(response_path) if response_path.exists() else {}
        failure = evidence(error_path) if error_path.exists() else {}
        response, wire = result.get('response', {}), request['wire']
        model, task = wire['model'], request['task_id']
        # Settlements and unknown failed attempts have distinct counters.
        delta = Counter(calls=1, successful_calls=int(bool(response)), error_calls=int(bool(failure)),
                        llm_wait_s=result.get('elapsed_s', 0) + failure.get('elapsed_s', 0),
                        input_tokens=response.get('tokens_in', 0), output_tokens=response.get('tokens_out', 0))
        delta[settled_key] = response.get(cost_key, 0)
        category = action_errors(response, wire, schema) if response else None
        if category:
            invalid[category] += 1
            delta.update(format_failures=1, format_failure_wait_s=result.get('elapsed_s', 0))
            delta['format_failure_' + cost_key] = response.get(cost_key, 0)
        if failure:
            errors[failure['error']] += 1
        for bucket in (totals, models[model], tasks[task]):
            bucket.update(delta)
        start = request.get('started_at')
        elapsed = result.get('elapsed_s', failure.get('elapsed_s'))
        end = start + elapsed if start is not None and elapsed is not None else None
        if end is not None:
            spans.append((start, end))
        metadata = response.get('provider_metadata', {})
        progress = {}
        for message in reversed(wire.get('messages', [])):
            content = message.get('content') or ''
            if '\nDecision budget:' in content:
                progress = json.loads(content.rsplit('\nDecision budget:', 1)[1])
                break
        calls.append({'id': prefix, 'task_id': task, 'requested_model': model,
                      'session': progress.get('session'), 'decisions_left': progress.get('decisions_left'),
                      'model_route': progress.get('model_route'),
                      'returned_model': metadata.get('returned_model_identity'),
                      'normalized_model': response.get('model'), 'started_at': start,
                      'end_from_start_plus_elapsed': end, 'elapsed_s': elapsed,
                      'usage': metadata.get('usage'), 'pricing': response.get('pricing'),
                      'format_failure': category, 'error': failure or None,
                      'reserved': request.get('reserved_' + currency.lower()), **dict(delta)})
    for path in sorted((run_dir / 'evidence/actions').glob('*.json')):
        row = evidence(path)
        task = row['action']['task_id']
        tool = row['action']['action']['tool']
        result = row['result']
        delta = Counter()
        if tool == 'finish':
            delta['host_build_s'] = execution_seconds(result.get('build'))
            delta['host_check_s'] = execution_seconds(result.get('verification'))
            delta['host_build_and_check_s'] = delta['host_build_s'] + delta['host_check_s']
        else:
            delta['agent_tool_s'] = execution_seconds(result)
        if result.get('error') or result.get('returncode', 0) != 0 or result.get('accepted') is False:
            delta['failed_tool_or_finish'] = 1
        for bucket in (totals, tasks[task], tools[tool]):
            bucket.update(delta)
        tools[tool]['actions'] += 1
    for task, value in state['tasks'].items():
        tasks[task].update({k: value[k] for k in ('sessions', 'decisions')})
        tasks[task]['additional_sessions'] = max(0, value['sessions'] - 1)
        tasks[task]['claims'] = len(value.get('claims', []))
    final = state.get('final_checks', {}).get('result', {})
    export_build = execution_seconds(final.get('build'))
    export_check = execution_seconds(final.get('verification'))
    wall = state['updated_at'] - state['created_at']
    known = totals['llm_wait_s'] + totals['agent_tool_s'] + totals['host_build_and_check_s'] + export_build + export_check
    for path in (run_dir / 'run.json', run_dir / 'report.json'):
        if path.exists():
            evidence(path)
    for ref in {**state['inputs'], **state.get('private_inputs', {})}.values():
        path = run_dir / ref['path']
        hashes[ref['path']] = sha(path)
        if hashes[ref['path']] != ref['sha256']:
            raise ValueError('historical input digest mismatch: ' + ref['path'])
    occupied = state['budget'][cost_key]
    gaps = [{'after_call_end': left[1], 'before_next_call_start': right[0],
             'seconds': right[0] - left[1], 'meaning': 'non-LLM interval, includes tools and unmeasured overhead'}
            for left, right in zip(sorted(spans), sorted(spans)[1:]) if right[0] > left[1]]
    return {'schema_version': 'profile/1', 'name': name or state['run_id'], 'run_directory': str(run_dir),
            'status': state['status'] == 'success', 'source_status': state['status'],
            'exposure': 'legacy_readable' if state['schema_version'] != '7.0' else 'private_isolated',
            'currency': currency, 'total_wall_s': wall, 'totals': dict(totals),
            'billing_recalculation': ('Original USD settlement only; missing request starts/cache detail prevents exact repricing.'
                                     if currency == 'USD' else 'Original CNY request-start period and usage settlement retained.'),
            'models': dict(models), 'tasks': dict(tasks), 'tools': dict(tools), 'calls': calls,
            'transport_errors': dict(errors), 'format_failures': dict(invalid),
            'final_export_build_s': export_build, 'final_export_check_s': export_check,
            'final_export_build_and_check_s': export_build + export_check,
            'cost': {'settled': totals[settled_key], 'occupied': occupied,
                     'outstanding_reservations': occupied - totals[settled_key]},
            'budget': state['budget'], 'inputs': state['inputs'],
            'runtime_sha256': state['runtime']['package_sha256'], 'config_sha256': state['config_sha256'],
            'sandbox_image': state.get('sandbox_image'), 'known_duration_s': known,
            'unattributed_wall_residual_s': wall - known,
            'non_llm_intervals': gaps, 'calls_without_start_time': len(requests) - len(spans),
            'unmeasured': ['Python file tools', 'context preparation', 'checkpoint/publication',
                           'separate retry backoff', 'pause/resume duration', 'independent audit time'],
            'method': 'Persisted request starts, response/error elapsed_s, outer ExecResult.duration_ms; never filesystem mtime. '
                      'Residual is not active execution or measured pause. Format failures are a subset of calls, not extra time. '
                      'Host checks on legacy runs are readable-oracle checks, not private proof.',
            'action_schema_sha256': sha(schema_path), 'evidence_hashes': hashes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, help='one read-only source; otherwise collect the three inventory entries')
    parser.add_argument('--output', type=Path, required=True, help='new output directory; existing paths are refused')
    args = parser.parse_args()
    sources = [{'run_directory': str(args.run), 'name': args.run.name}] if args.run else [
        read(ROOT / 'runs/p0-baseline-inventory' / (name + '.json'))
        for name in ('deepseek_first', 'deepseek_mqtt_expanded', 'deepseek_http')]
    if not sources:
        parser.error('no baseline sources')
    output = args.output.resolve()
    if output.is_relative_to(ROOT / 'runs/p0-baseline-inventory') or any(
        output.is_relative_to(Path(s['run_directory']).resolve()) for s in sources
    ):
        parser.error('output must not be inside historical sources')
    rows = [collect(Path(s['run_directory']), name=s['name']) for s in sources]
    output.mkdir(parents=True, exist_ok=False)
    for row in rows:
        (output / (row['name'] + '.json')).write_text(json.dumps(row, indent=2) + '\n')
    print(json.dumps({'collected': len(rows), 'source_statuses': [r['status'] for r in rows],
                      'p0_p2_complete': False, 'output': str(output)}))


if __name__ == '__main__':
    main()
