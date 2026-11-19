from collections import defaultdict
from app.coerce import instant
from app.errors import BusinessError

# 豁免裁决原因码（与 app/reasons.py 的 REASONS 对应）
# WAIVER_GRANTED            豁免有效，窗外偏离放行（偏离仍留痕，不洗白）
# WAIVER_SCOPE_SUBJECT_ONLY 豁免只覆盖受试者、未指定该次访视，不能放行具体窗外偏离
# WAIVER_APPROVED_LATE      审批晚于访视，不能追溯授权
# WAIVER_NOT_FOR_VISIT      豁免的访视/受试者与偏离不匹配
# WAIVER_MALFORMED          豁免结构或时间无效，不具授权效力
# WAIVER_DUPLICATE          同一豁免重复登记，重试不新增放行
# WAIVER_NOT_APPLICABLE     非窗外偏离（漏访/重复首次等）不在豁免受理范围
# WAIVER_MISSING            该窗外偏离没有任何豁免依据


def detect(windows, context):
    primary = defaultdict(list)
    for row in windows:
        if row['kind'] == 'PRIMARY':
            primary[(row['subject'], row['visit'])].append(row)
    issues = []
    completed = []
    for subject in sorted(context['subjects']):
        for visit in context['required_visits']:
            items = primary.get((subject, visit), [])
            if not items:
                issues.append({'subject': subject, 'visit': visit, 'id': '', 'code': 'MISSING_PRIMARY'})
                continue
            if len(items) > 1:
                issues.append({'subject': subject, 'visit': visit, 'id': ','.join(r['id'] for r in items), 'code': 'DUPLICATE_PRIMARY'})
                continue
            row = items[0]
            if row['code'] != 'IN_WINDOW':
                issues.append({**row, 'code': 'WINDOW_' + row['code']})
            else:
                completed.append(row)
    return issues, completed


def _normalize_waivers(context):
    """校验并按豁免编号去重；同一豁免重复登记只计一次，重试不新增放行。"""
    raw = context.get('waivers', [])
    if not isinstance(raw, list):
        raise BusinessError('BAD_CONTEXT', '豁免清单须为数组')
    seen = {}
    reviews = []
    for item in raw:
        if not isinstance(item, dict):
            raise BusinessError('BAD_CONTEXT', '豁免条目须为对象')
        wid = str(item.get('waiver_id', '')).strip()
        if not wid:
            raise BusinessError('BAD_CONTEXT', '豁免编号不能为空')
        if wid in seen:
            reviews.append({'waiver_id': wid, 'code': 'WAIVER_DUPLICATE',
                            'detail': '同一豁免重复登记，按首次登记处理，不新增放行',
                            'subject': str(item.get('subject', '')), 'visit': str(item.get('visit') or '')})
            continue
        problem = None
        scope = str(item.get('scope', '')).strip()
        if scope not in {'VISIT', 'SUBJECT'}:
            problem = ('WAIVER_MALFORMED', '豁免范围须为 VISIT 或 SUBJECT')
        elif not str(item.get('subject', '')).strip():
            problem = ('WAIVER_MALFORMED', '豁免缺少受试者')
        elif scope == 'VISIT' and not str(item.get('visit', '') or '').strip():
            problem = ('WAIVER_MALFORMED', '访视级豁免缺少访视编号')
        else:
            try:
                instant(item['approved_at'])
            except (BusinessError, KeyError, TypeError, AttributeError):
                problem = ('WAIVER_MALFORMED', '审批时刻无效或缺少时区偏移')
        reviews.append({'waiver_id': wid, 'scope': scope,
                        'subject': str(item.get('subject', '')),
                        'visit': str(item.get('visit') or ''),
                        'approved_at': str(item.get('approved_at', '')),
                        'code': 'WAIVER_MALFORMED' if problem else 'WAIVER_REGISTERED',
                        'detail': problem[1] if problem else '豁免登记有效，等待与偏离匹配'})
        # 结构无效的豁免不进入可授权清单，不能产生任何放行。
        if problem is None:
            seen[wid] = item
    return list(seen.values()), reviews


def _match_waiver(issue, at_by_key, waivers):
    """对单条窗外偏离做豁免匹配，返回 (是否放行, 豁免编号或'', 原因码, 说明)。"""
    key = (issue['subject'], issue['visit'])
    visit_iso = at_by_key.get(key)
    visit_at = instant(visit_iso) if visit_iso else None
    candidates = [w for w in waivers if str(w.get('subject', '')).strip() == issue['subject']]
    if not candidates:
        return False, '', 'WAIVER_MISSING', '无覆盖该受试者的中心豁免'
    # 优先访视级，其次受试者级；同优先级按编号稳定排序，保证结论可复现。
    candidates.sort(key=lambda w: (0 if w.get('scope') == 'VISIT' else 1, str(w.get('waiver_id'))))
    for w in candidates:
        wid = str(w['waiver_id']).strip()
        if w.get('scope') == 'VISIT' and str(w.get('visit') or '').strip() != issue['visit']:
            code, detail = 'WAIVER_NOT_FOR_VISIT', '豁免指定的访视与偏离访视不一致'
        elif w.get('scope') == 'SUBJECT':
            code, detail = 'WAIVER_SCOPE_SUBJECT_ONLY', '豁免只覆盖受试者、未指定该次访视，不能授权具体窗外偏离'
        elif visit_at is not None and instant(w['approved_at']) > visit_at:
            code, detail = 'WAIVER_APPROVED_LATE', '审批晚于访视，不能追溯授权'
        else:
            return True, wid, 'WAIVER_GRANTED', '豁免覆盖该受试者该次访视且审批不晚于访视，窗外偏离放行但留痕'
        last = (wid, code, detail)
    return False, last[0], last[1], last[2]


def adjudicate(records, windows, issues, completed, context):
    """对每条访视给出统一结论：访视、偏离、锁库、归档共用本结果。"""
    waivers, reviews = _normalize_waivers(context)
    windows_by_key = {(r['subject'], r['visit']): r for r in windows if r['kind'] == 'PRIMARY'}
    # 排窗结果不带采样时刻，裁决审批时点须回到方案模块产出的规范化记录。
    at_by_key = {(r['subject'], r['visit']): r['at'] for r in records if r['kind'] == 'PRIMARY'}
    issue_by_key = {(i['subject'], i['visit']): i for i in issues}
    decisions = []
    granted = 0
    for subject in sorted(context['subjects']):
        for visit in context['required_visits']:
            key = (subject, visit)
            win = windows_by_key.get(key)
            issue = issue_by_key.get(key)
            if issue is None:
                decisions.append({
                    'subject': subject, 'visit': visit,
                    'record_id': win['id'] if win else '', 'line': win['line'] if win else 0,
                    'day': win['day'] if win else '', 'window_code': 'IN_WINDOW',
                    'deviation_code': '', 'waiver_id': '', 'decision': '通过',
                    'reason_code': 'IN_WINDOW', 'reason': '访视在半开窗内，无需豁免',
                    'blocks_lock': False})
                continue
            if issue['code'].startswith('WINDOW_'):
                released, wid, code, detail = _match_waiver(issue, at_by_key, waivers)
                blocks = not released
                granted += int(released)
                decisions.append({
                    'subject': subject, 'visit': visit,
                    'record_id': issue.get('id', ''), 'line': issue.get('line', 0),
                    'day': issue.get('day', ''), 'window_code': issue['code'].replace('WINDOW_', '', 1),
                    'deviation_code': issue['code'], 'waiver_id': wid,
                    'decision': '豁免放行' if released else '阻断',
                    'reason_code': code, 'reason': detail, 'blocks_lock': blocks})
            else:
                # 漏访、重复首次等非窗外偏离，豁免无权处理。
                decisions.append({
                    'subject': subject, 'visit': visit,
                    'record_id': issue.get('id', ''), 'line': issue.get('line', 0),
                    'day': issue.get('day', ''), 'window_code': '',
                    'deviation_code': issue['code'], 'waiver_id': '',
                    'decision': '阻断', 'reason_code': 'WAIVER_NOT_APPLICABLE',
                    'reason': {'MISSING_PRIMARY': '缺首次访视，豁免不受理，复检亦不能伪填',
                               'DUPLICATE_PRIMARY': '重复首次访视，豁免不受理'}[issue['code']],
                    'blocks_lock': True})
    return decisions, reviews, granted


def apply(state):
    issues, completed = detect(state['windows'], state['context'])
    decisions, waiver_reviews, granted = adjudicate(state['records'], state['windows'], issues, completed, state['context'])
    state.update(deviations=issues, completed=completed,
                 visit_decisions=decisions, waiver_reviews=waiver_reviews)
    blocking_deviations = sum(1 for d in decisions if d['blocks_lock'])
    state['views']['deviation'] = {
        'title': '偏离复核与豁免裁决', 'rows': decisions,
        'waiver_rows': waiver_reviews,
        'metrics': {'完成访视': len(completed), '缺失或偏离': len(issues),
                    '豁免放行': granted, '仍阻断偏离': blocking_deviations,
                    '缺首次访视': sum(x['code'] == 'MISSING_PRIMARY' for x in issues)}}
