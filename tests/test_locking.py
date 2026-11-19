from app.modules.locking.rules import decide
from copy import deepcopy

def test_clean_lockable(clean):
    assert clean['balance']['status']=='可锁库'
def test_dirty_blocks(sample):
    # 4 条偏离中仅 1 条获有效豁免放行；仍阻断 = 3 偏离 + 2 复检问题 + 3 隔离
    assert sample['balance']['blocked']==8;assert sample['balance']['status']=='待处理'
    assert sample['balance']['waiver_granted']==1
    assert sample['balance']['blocking_deviations']==3
def test_input_conservation(sample):
    assert sample['balance']['input']==51;assert sample['balance']['accepted']==45;assert sample['balance']['quarantined']==3;assert sample['balance']['row_balanced']
def test_completed_expected(clean):
    assert clean['balance']['completed']==clean['balance']['expected']==40
def test_isolation_blocks(clean):
    s=deepcopy(clean);s['quarantine']=[{'id':'x'}];assert decide(s)['status']=='待处理'
def test_count_mismatch_blocks(clean):
    s=deepcopy(clean);s['counts']['input']+=1;assert decide(s)['status']=='待处理';assert decide(s)['row_balanced'] is False
def test_missing_visit_not_replaced(sample):
    # 00002-V2、00003-V2 改为窗外后，完成访视由 40 减 4（另含 00001-V1、00020-V2）
    assert sample['balance']['completed']==36;assert sample['balance']['expected']==40
def test_waiver_grant_removes_block_but_keeps_deviation(sample):
    # 豁免改变锁库后果，但不洗白偏离：deviations 仍为 4，放行项 blocks_lock=False
    assert sample['balance']['deviations']==4
    granted=[d for d in sample['visit_decisions'] if d['reason_code']=='WAIVER_GRANTED']
    assert len(granted)==1
    g=granted[0]
    assert (g['subject'],g['visit'])==('00001','V1') and g['blocks_lock'] is False
    assert g['deviation_code']=='WINDOW_LATE' and g['waiver_id']=='CW-2026-001'
def test_subject_only_waiver_cannot_release(sample):
    d=next(d for d in sample['visit_decisions'] if d['subject']=='00002' and d['visit']=='V2')
    assert d['decision']=='阻断' and d['reason_code']=='WAIVER_SCOPE_SUBJECT_ONLY' and d['blocks_lock'] is True
def test_late_approval_cannot_release(sample):
    d=next(d for d in sample['visit_decisions'] if d['subject']=='00003' and d['visit']=='V2')
    assert d['decision']=='阻断' and d['reason_code']=='WAIVER_APPROVED_LATE' and d['blocks_lock'] is True
def test_missing_primary_not_waiverable(sample):
    d=next(d for d in sample['visit_decisions'] if d['subject']=='00020' and d['visit']=='V2')
    assert d['reason_code']=='WAIVER_NOT_APPLICABLE' and d['blocks_lock'] is True
