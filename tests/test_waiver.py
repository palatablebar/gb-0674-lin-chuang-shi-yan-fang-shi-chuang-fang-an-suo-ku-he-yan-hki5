"""中心豁免裁决专项测试。

覆盖：授权范围、审批时点、重试幂等、跨模块同一结论、可追溯、重算失败保留旧结论。
"""
from copy import deepcopy
import pytest
from app.engine import evaluate
from app.errors import BusinessError
from app.modules.deviation.rules import adjudicate, _normalize_waivers
from app.modules.locking.rules import decide
from app import store
from app.archive import serialize
import zipfile
import io
import json


def _issue(subject='00001', visit='V1', code='WINDOW_LATE'):
    return {'subject': subject, 'visit': visit, 'id': f'{subject}-{visit}', 'line': 2, 'day': 11, 'code': code}


def _records(subject='00001', visit='V1', at='2026-10-12T08:00:00+08:00'):
    return [{'subject': subject, 'visit': visit, 'kind': 'PRIMARY', 'at': at}]


def test_visit_scope_and_timely_approval_grants(clean):
    ctx = deepcopy(clean['context'])
    ctx['waivers'] = [{'waiver_id': 'W1', 'scope': 'VISIT', 'subject': '00001', 'visit': 'V1',
                       'approved_at': '2026-10-10T09:00:00+08:00'}]
    decisions, reviews, granted = adjudicate(_records(), clean['windows'], [_issue()], [], ctx)
    d = next(x for x in decisions if x['subject'] == '00001')
    assert d['decision'] == '豁免放行' and d['reason_code'] == 'WAIVER_GRANTED'
    assert d['blocks_lock'] is False and granted == 1
    assert reviews[0]['code'] == 'WAIVER_REGISTERED'


def test_approval_on_visit_day_is_timely(clean):
    # 审批时刻不晚于访视即有效（含同一日早于采样时刻）
    ctx = deepcopy(clean['context'])
    ctx['waivers'] = [{'waiver_id': 'W1', 'scope': 'VISIT', 'subject': '00001', 'visit': 'V1',
                       'approved_at': '2026-10-12T07:00:00+08:00'}]
    decisions, _, granted = adjudicate(_records(), clean['windows'], [_issue()], [], ctx)
    assert granted == 1
    assert next(x for x in decisions if x['subject'] == '00001')['reason_code'] == 'WAIVER_GRANTED'


def test_subject_only_scope_does_not_release(clean):
    ctx = deepcopy(clean['context'])
    ctx['waivers'] = [{'waiver_id': 'W2', 'scope': 'SUBJECT', 'subject': '00001', 'visit': None,
                       'approved_at': '2026-10-10T09:00:00+08:00'}]
    decisions, _, granted = adjudicate(_records(), clean['windows'], [_issue()], [], ctx)
    d = next(x for x in decisions if x['subject'] == '00001')
    assert d['decision'] == '阻断' and d['reason_code'] == 'WAIVER_SCOPE_SUBJECT_ONLY'
    assert d['blocks_lock'] is True and granted == 0


def test_approval_later_than_visit_does_not_release(clean):
    ctx = deepcopy(clean['context'])
    ctx['waivers'] = [{'waiver_id': 'W3', 'scope': 'VISIT', 'subject': '00001', 'visit': 'V1',
                       'approved_at': '2026-10-13T09:00:00+08:00'}]
    decisions, _, granted = adjudicate(_records(), clean['windows'], [_issue()], [], ctx)
    d = next(x for x in decisions if x['subject'] == '00001')
    assert d['reason_code'] == 'WAIVER_APPROVED_LATE' and d['blocks_lock'] is True and granted == 0


def test_waiver_for_other_visit_does_not_release(clean):
    ctx = deepcopy(clean['context'])
    ctx['waivers'] = [{'waiver_id': 'W4', 'scope': 'VISIT', 'subject': '00001', 'visit': 'V2',
                       'approved_at': '2026-10-10T09:00:00+08:00'},
                      {'waiver_id': 'W5', 'scope': 'SUBJECT', 'subject': '00001', 'visit': None,
                       'approved_at': '2026-10-10T09:00:00+08:00'}]
    decisions, _, granted = adjudicate(_records(), clean['windows'], [_issue()], [], ctx)
    d = next(x for x in decisions if x['subject'] == '00001')
    # 访视级指向 V2 不匹配，受试者级不覆盖具体偏离，最终阻断
    assert d['blocks_lock'] is True and d['reason_code'] == 'WAIVER_SCOPE_SUBJECT_ONLY' and granted == 0


def test_missing_waiver_blocks(clean):
    ctx = deepcopy(clean['context'])
    ctx['waivers'] = []
    decisions, _, granted = adjudicate(_records(), clean['windows'], [_issue()], [], ctx)
    d = next(x for x in decisions if x['subject'] == '00001')
    assert d['reason_code'] == 'WAIVER_MISSING' and d['blocks_lock'] is True and granted == 0


def test_missing_primary_not_waiverable_even_with_visit_waiver(clean):
    ctx = deepcopy(clean['context'])
    ctx['waivers'] = [{'waiver_id': 'W6', 'scope': 'VISIT', 'subject': '00020', 'visit': 'V2',
                       'approved_at': '2026-10-10T09:00:00+08:00'}]
    issue = {'subject': '00020', 'visit': 'V2', 'id': '', 'code': 'MISSING_PRIMARY'}
    # 漏访：移除该次 PRIMARY 的排窗与记录，豁免亦不能使其成立
    windows = [r for r in clean['windows'] if not (r['subject'] == '00020' and r['visit'] == 'V2')]
    decisions, _, granted = adjudicate([], windows, [issue], [], ctx)
    d = next(x for x in decisions if x['subject'] == '00020' and x['visit'] == 'V2')
    assert d['reason_code'] == 'WAIVER_NOT_APPLICABLE' and d['blocks_lock'] is True and granted == 0


def test_malformed_waiver_is_invalid(clean):
    ctx = deepcopy(clean['context'])
    ctx['waivers'] = [{'waiver_id': 'W7', 'scope': 'VISIT', 'subject': '00001', 'visit': 'V1',
                       'approved_at': 'not-a-time'}]
    decisions, reviews, granted = adjudicate(_records(), clean['windows'], [_issue()], [], ctx)
    assert reviews[0]['code'] == 'WAIVER_MALFORMED'
    assert next(x for x in decisions if x['subject'] == '00001')['reason_code'] == 'WAIVER_MISSING'


def test_duplicate_waiver_retry_adds_no_release(clean):
    # 同一豁免编号重复登记（重试场景）：去重后只放行一次，重复条目标记 WAIVER_DUPLICATE
    ctx = deepcopy(clean['context'])
    w = {'waiver_id': 'WDUP', 'scope': 'VISIT', 'subject': '00001', 'visit': 'V1',
         'approved_at': '2026-10-10T09:00:00+08:00'}
    ctx['waivers'] = [deepcopy(w), deepcopy(w), deepcopy(w)]
    decisions, reviews, granted = adjudicate(_records(), clean['windows'], [_issue()], [], ctx)
    assert granted == 1
    assert sum(r['code'] == 'WAIVER_DUPLICATE' for r in reviews) == 2
    assert sum(r['code'] == 'WAIVER_REGISTERED' for r in reviews) == 1
    d = next(x for x in decisions if x['subject'] == '00001')
    assert d['waiver_id'] == 'WDUP' and d['blocks_lock'] is False


def test_rerun_same_input_same_conclusion_and_run_id():
    # 同输入（含豁免）重算批号与结论一致，重试不新增放行
    first = evaluate()
    second = evaluate()
    assert first['manifest']['run_id'] == second['manifest']['run_id']
    assert first['visit_decisions'] == second['visit_decisions']
    assert first['balance'] == second['balance']


def test_granted_deviation_still_recorded_not_washed(sample):
    # 放行不等于洗白：偏离仍在 deviations，统一结论里保留原因码与豁免依据
    assert any(i['code'] == 'WINDOW_LATE' and i['subject'] == '00001' for i in sample['deviations'])
    d = next(x for x in sample['visit_decisions'] if x['subject'] == '00001' and x['visit'] == 'V1')
    assert d['deviation_code'] == 'WINDOW_LATE' and d['decision'] == '豁免放行'


def test_locking_and_archive_share_one_conclusion(sample):
    # 锁库阻断数与统一结论一致；归档封存状态与锁库一致
    blocking = sum(1 for d in sample['visit_decisions'] if d['blocks_lock'])
    assert sample['balance']['blocking_deviations'] == blocking
    assert sample['balance']['status'] == '待处理'
    assert sample['archive']['status'] == '待处理'
    assert sample['archive']['waiver_granted'] == sample['balance']['waiver_granted'] == 1


def test_traceability_fields_present(sample):
    # 每条结论可追溯到原始访视行号与豁免审批依据
    d = next(x for x in sample['visit_decisions'] if x['waiver_id'])
    assert d['line'] == 2 and d['record_id'] == '00001-V1'
    review = next(r for r in sample['waiver_reviews'] if r['waiver_id'] == d['waiver_id'])
    assert review['approved_at'] and review['scope'] == 'VISIT'


def test_archive_bundle_contains_waiver_evidence(sample):
    z = zipfile.ZipFile(io.BytesIO(serialize(sample)))
    doc = json.loads(z.read('waivers.json'))
    assert len(doc['waivers']) == 3
    assert len(doc['visit_decisions']) == 40
    granted = [x for x in doc['visit_decisions'] if x['reason_code'] == 'WAIVER_GRANTED']
    assert len(granted) == 1 and granted[0]['waiver_id'] == 'CW-2026-001'


def test_recompute_failure_keeps_stored_conclusion(tmp_path):
    # 重算在写入前抛错时，库内旧批次结论必须原样保留
    path = str(tmp_path / 'keep.sqlite')
    saved = store.save(path, evaluate())
    rid = saved['manifest']['run_id']
    before = store.get(path, rid)
    with pytest.raises(BusinessError):
        # 豁免结构非法，引擎在持久化前失败
        evaluate({'overrides': {'waivers': [{'waiver_id': ''}]}})
    after = store.get(path, rid)
    assert after['manifest']['sha256'] == before['manifest']['sha256']
    assert after['visit_decisions'] == before['visit_decisions']
    assert after['balance']['status'] == before['balance']['status']


def test_bad_waiver_shape_raises_not_silent(clean):
    with pytest.raises(BusinessError) as e:
        _normalize_waivers({'waivers': 'not-a-list'})
    assert e.value.code == 'BAD_CONTEXT'


def test_waiver_override_changes_lock_without_erasing(sample, clean):
    # 通过 overrides 给原本被阻断的 00002-V2 补一份合规访视级豁免，可改变锁库后果
    override = {'waivers': deepcopy(sample['context']['waivers']) + [
        {'waiver_id': 'CW-2026-004', 'scope': 'VISIT', 'subject': '00002', 'visit': 'V2',
         'approved_at': '2026-10-12T09:00:00+08:00'}]}
    s = evaluate({'overrides': override})
    d = next(x for x in s['visit_decisions'] if x['subject'] == '00002' and x['visit'] == 'V2')
    assert d['reason_code'] == 'WAIVER_GRANTED' and d['blocks_lock'] is False
    # 偏离仍在
    assert any(i['subject'] == '00002' and i['code'] == 'WINDOW_LATE' for i in s['deviations'])
