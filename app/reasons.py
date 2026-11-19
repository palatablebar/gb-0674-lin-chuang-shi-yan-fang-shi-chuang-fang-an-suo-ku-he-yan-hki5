REASONS = {
    'COLUMN_COUNT':'列数错误', 'MISSING_ID':'编号缺失', 'DUPLICATE_CONFLICT':'重复编号冲突',
    'MISSING_VALUE':'数值缺失', 'BAD_NUMBER':'数值格式错误', 'BAD_TIME':'时间格式错误',
    'BAD_UNIT':'单位不受支持', 'UNKNOWN_REFERENCE':'引用对象不存在', 'INVALID_RANGE':'范围错误',
    'VERSION_CONFLICT':'版本冲突', 'EMPTY_FILE':'空文件', 'BLANK_FILE':'全空行',
    'HEADER_ONLY':'仅有表头', 'STALE_VERSION':'复核版本已更新', 'NOT_FOUND':'批次不存在',
    'WAIVER_GRANTED':'豁免有效放行', 'WAIVER_SCOPE_SUBJECT_ONLY':'豁免仅覆盖受试者',
    'WAIVER_APPROVED_LATE':'审批晚于访视', 'WAIVER_NOT_FOR_VISIT':'豁免访视不匹配',
    'WAIVER_MALFORMED':'豁免结构无效', 'WAIVER_DUPLICATE':'豁免重复登记',
    'WAIVER_NOT_APPLICABLE':'豁免不适用该偏离', 'WAIVER_MISSING':'无豁免依据',
    'WAIVER_REGISTERED':'豁免已登记',
}
def reject(state, record, code, detail):
    state['quarantine'].append({'line':record.get('_line',0),'id':record.get('id',''),
       'code':code,'detail':detail,'raw':{k:v for k,v in record.items() if not k.startswith('_')}})
