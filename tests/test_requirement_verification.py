from copy import deepcopy
from nepa.report import requirement_verification

TARGET = {'builds': [{'id': 'release'}, {'id': 'san'}]}
ACCEPTANCE = {'checks': [{'id': 'case', 'req_ids': ['R'], 'required': True}]}


def final():
    return {'evidence': {'path': 'export.json', 'sha256': '0' * 64}, 'result': {'verification': {'variants': [
        {'variant': variant, 'execution': {'returncode': 0, 'timed_out': False}, 'detail': {'passed': True, 'server_returncode': 0, 'early_exit': None, 'stop_timeout': False,
         'sanitizer_error': False, 'checks': [{'id': 'case', 'returncode': 0, 'passed': True}]}}
        for variant in ('release', 'san')]}}}


def test_scope_and_missing_current_evidence():
    assert requirement_verification('unmapped', TARGET, ACCEPTANCE, final())['status'] == 'unverified'
    assert requirement_verification('R', TARGET, ACCEPTANCE, None)['status'] == 'incomplete'
    assert requirement_verification('R', TARGET, ACCEPTANCE, final())['status'] == 'scenarios_passed'
    a = deepcopy(ACCEPTANCE); a['checks'][0]['required'] = False
    v = requirement_verification('R', TARGET, a, final())
    assert v['status'] == 'unverified' and all(r['status'] == 'passed' for r in v['scenarios'])


def test_missing_variant_or_check_and_timeout_never_pass():
    for missing in ('variant', 'check', 'timeout', 'reference', 'supervisor'):
        f = final()
        if missing == 'variant':
            f['result']['verification']['variants'].pop()
        if missing == 'check':
            f['result']['verification']['variants'][1]['detail']['checks'] = []
        if missing == 'timeout':
            f['result']['verification']['variants'][1]['detail']['checks'][0]['returncode'] = None
        if missing == 'reference':
            f.pop('evidence')
        if missing == 'supervisor':
            f['result']['verification']['variants'][1]['execution'].update(returncode=None, timed_out=True)
        assert requirement_verification('R', TARGET, ACCEPTANCE, f)['status'] == 'incomplete'


def test_variant_failure_and_sanitizer_override_claims_and_old_success():
    for fault in ('case', 'sanitizer', 'early_exit'):
        old, current = final(), final()
        d = current['result']['verification']['variants'][1]['detail']
        if fault == 'case':
            d['checks'][0].update(returncode=1, passed=False)
        if fault == 'sanitizer':
            d['sanitizer_error'] = True
        if fault == 'early_exit':
            d['early_exit'] = 0
        current['previous_attempt'] = old
        assert requirement_verification('R', TARGET, ACCEPTANCE, current)['status'] == 'failed'
