import hashlib
import json


def freeze(state):
    # 封存依据纳入统一访视结论与豁免裁决，保证锁库与归档共用同一结论、可追溯。
    basis = {'selected': state['selected_results'], 'windows': state['windows'],
             'deviations': state['deviations'], 'visit_decisions': state.get('visit_decisions', []),
             'waiver_reviews': state.get('waiver_reviews', [])}
    return {'status': '可冻结' if state['balance']['status'] == '可锁库' else '待处理',
            'seal': hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'subjects': len(state['context']['subjects']),
            'results': len(state['selected_results']),
            'waiver_granted': state['balance'].get('waiver_granted', 0)}


def apply(state):
    state['archive'] = freeze(state)
    state['views']['archive'] = {
        'title': '冻结清单', 'rows': state['selected_results'],
        'decision_rows': state.get('visit_decisions', []),
        'waiver_rows': state.get('waiver_reviews', []),
        'metrics': {'受试者': state['archive']['subjects'], '结果': state['archive']['results'],
                    '豁免放行': state['archive']['waiver_granted'], '状态': state['archive']['status']}}
