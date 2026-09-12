import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('action_study', ROOT / 'experiments/protocol-expansion/action_study.py')
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def rows():
    return [{'mode': mode, 'model': model, 'valid': i != 0 or mode != 'json_object', 'elapsed_s': 1, 'cost_cny': .01}
            for mode in study.MODES for model, count in [('deepseek-flash', 24), ('deepseek-v4-pro', 8)] for i in range(count)]


def test_promotion_requires_all_preregistered_gates():
    data = rows()
    sessions = [{'passed': True}] * 8
    assert study.assess(data, sessions, True)['promote_native']
    assert not study.assess(data, sessions, False)['promote_native']
    assert not study.assess(data, [{'passed': False}], True)['promote_native']
    for row in data:
        if row['mode'] == 'tool_calls':
            row['elapsed_s'] = 2
    assert not study.assess(data, sessions, True)['promote_native']


def test_zero_error_control_does_not_establish_a_reduction():
    data = rows()
    for row in data:
        row['valid'] = True
    assert not study.assess(data, [{'passed': True}] * 8, True)['promote_native']
