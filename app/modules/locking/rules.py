def decide(state):
    c = state['counts']
    explained = len(state['records']) + len(state['quarantine']) + c['blank'] + c['duplicate'] + c['repeated_header']
    # 阻断项以统一访视结论为准：豁免放行的窗外偏离不再阻断锁库，但偏离本身仍留痕。
    blocking_deviations = sum(1 for d in state.get('visit_decisions', []) if d['blocks_lock'])
    waiver_granted = sum(1 for d in state.get('visit_decisions', []) if d['reason_code'] == 'WAIVER_GRANTED')
    blocked = blocking_deviations + len(state['retest_issues']) + len(state['quarantine'])
    return {
        'input': c['input'], 'accepted': len(state['records']), 'quarantined': len(state['quarantine']),
        'row_balanced': c['input'] == explained,
        'completed': len(state['completed']),
        'expected': len(state['context']['subjects']) * len(state['context']['required_visits']),
        'deviations': len(state['deviations']),
        'waiver_granted': waiver_granted,
        'blocking_deviations': blocking_deviations,
        'retest_issues': len(state['retest_issues']),
        'blocked': blocked,
        'status': '可锁库' if not blocked and c['input'] == explained else '待处理'}


def apply(state):
    state['balance'] = decide(state)
    state['views']['locking'] = {
        'title': '锁库闸门', 'rows': [state['balance']],
        'decision_rows': state.get('visit_decisions', []),
        'metrics': {'应访视': state['balance']['expected'], '已完成': state['balance']['completed'],
                    '豁免放行': state['balance']['waiver_granted'], '阻断项': state['balance']['blocked']}}
