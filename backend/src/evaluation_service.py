"""候选方案评估服务。

口径：
- 用最近三个完整周（周一至周日、7 天均已验票）逐周回测；每周只用该周以前
  的验票训练，误差为未封顶原始需求的逐日绝对误差均值。
- 评估单固定创建时刻的验票快照与当时现用方案；之后验票更正不改旧结论，
  只在启用时按指纹拒绝过期评估。
- worker 按周写入 EvaluationWeek，已完成周重试跳过，中断后可恢复。
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import select

from .models import Production, Performance
from .methods import METHODS, DEFAULT_METHOD, MIN_TRAIN_DAYS, predict, method_label
from .evaluation_models import MethodSetting, MethodEvaluation, EvaluationWeek

WEEKS_PER_EVALUATION = 3


def active_method(s, production_id):
    setting = s.scalar(select(MethodSetting).where(MethodSetting.production_id == production_id))
    return setting.method_key if setting else DEFAULT_METHOD


def set_active_method(s, production_id, key):
    setting = s.scalar(select(MethodSetting).where(MethodSetting.production_id == production_id))
    if not setting:
        setting = MethodSetting(production_id=production_id, method_key=key)
        s.add(setting)
    else:
        setting.method_key = key
    return setting


def arrival_fingerprint(rows):
    """对 (日期, 验票人数) 序列取指纹，用于启用时识别验票是否已变化。"""
    payload = '|'.join(f'{r[0]}:{r[1]}' for r in rows)
    return sha256(payload.encode()).hexdigest()


def latest_complete_weeks(rows, count=WEEKS_PER_EVALUATION):
    """从连续验票序列取最近 count 个周一至周日完整周，返回 (周一, 周日) 列表，时间升序。"""
    if not rows:
        return []
    weeks = {}
    for d, _ in rows:
        monday = d - timedelta(days=d.weekday())
        weeks.setdefault(monday, set()).add(d)
    complete = sorted(m for m, days in weeks.items() if len(days) == 7)
    return [(m, m + timedelta(days=6)) for m in complete[-count:]]


def uncapped_mae(actual, predicted):
    """未封顶误差：预测只做非负截断，不做座位封顶。"""
    errs = [abs(float(a) - float(p)) for a, p in zip(actual, predicted)]
    return round(sum(errs) / len(errs), 3)


def start_evaluation(s, production, candidate_key, user, enqueue):
    if candidate_key not in METHODS:
        raise HTTPException(409, f'未知候选方案：{candidate_key}')
    observed = s.scalars(
        select(Performance).where(
            Performance.production_id == production.id,
            Performance.arrivals.is_not(None),
        ).order_by(Performance.date)
    ).all()
    if len(observed) < MIN_TRAIN_DAYS:
        raise HTTPException(409, f'至少需要 {MIN_TRAIN_DAYS} 天完整验票记录才能评估')
    for a, b in zip(observed, observed[1:]):
        if b.date - a.date != timedelta(days=1):
            raise HTTPException(409, f'验票日历缺口：{(a.date + timedelta(days=1)).isoformat()}；请先补齐')

    windows = latest_complete_weeks([(r.date, r.arrivals) for r in observed])
    if len(windows) < WEEKS_PER_EVALUATION:
        raise HTTPException(409, '完整周不足三个，无法发起三周回测评估')
    first_monday = windows[0][0]
    train_count = sum(1 for r in observed if r.date < first_monday)
    if train_count < MIN_TRAIN_DAYS:
        raise HTTPException(409, f'首个评估周之前仅 {train_count} 天验票，不足 {MIN_TRAIN_DAYS} 天训练窗口')

    baseline_key = active_method(s, production.id)
    # 先锁始终存在的剧目行：即使该剧目还没有任何评估单，两个经理的并发发起
    # 也会在此串行，保证同剧目同时只有一张在途评估；与预测、启用共用同一把行锁。
    s.query(Production).filter_by(id=production.id).with_for_update().one()
    # 已结束的历史评估（含同候选的旧一代）全部保留可查，各自带快照与过期标记，
    # 不被新评估覆盖。
    locked = s.query(MethodEvaluation).filter_by(production_id=production.id).with_for_update().all()
    if any(e.state in ('queued', 'running') for e in locked):
        raise HTTPException(409, '该剧目已有评估进行中，请等待完成或恢复原评估')

    snapshot = {r.date.isoformat(): int(r.arrivals) for r in observed}
    fingerprint = arrival_fingerprint(sorted(snapshot.items()))
    now = datetime.now(timezone.utc).isoformat()

    evaluation = MethodEvaluation(
        production_id=production.id,
        candidate_key=candidate_key,
        baseline_key=baseline_key,
        state='queued',
        arrival_snapshot=snapshot,
        history_fingerprint=fingerprint,
        total_weeks=WEEKS_PER_EVALUATION,
        created_by=user.email,
        created_at=now,
    )
    s.add(evaluation)
    s.flush()
    for idx, (monday, sunday) in enumerate(windows):
        s.add(EvaluationWeek(
            evaluation_id=evaluation.id,
            week_index=idx,
            week_start=monday,
            week_end=sunday,
            train_end=monday - timedelta(days=1),
            state='pending',
        ))
    s.commit()
    try:
        enqueue(evaluation.id)
    except Exception:
        s.rollback()
        obj = s.get(MethodEvaluation, evaluation.id)
        if obj:
            obj.state = 'failed'
            obj.error_message = '评估队列暂不可用，请重试恢复'
            s.commit()
        raise HTTPException(503, '评估队列暂不可用，请稍后重试')
    return evaluation


def run_evaluation(s, evaluation_id):
    """worker 入口：按周计算，已 done 的周跳过，任一周失败则整单转 failed 可恢复。"""
    evaluation = s.get(MethodEvaluation, evaluation_id)
    if not evaluation:
        return
    if evaluation.state == 'completed':
        return
    evaluation.state = 'running'
    evaluation.error_message = None
    s.commit()

    snapshot = {datetime.fromisoformat(k).date(): v for k, v in evaluation.arrival_snapshot.items()}
    ordered = sorted(snapshot.items())
    weeks = s.scalars(
        select(EvaluationWeek).where(EvaluationWeek.evaluation_id == evaluation_id).order_by(EvaluationWeek.week_index)
    ).all()
    try:
        for week in weeks:
            if week.state == 'done':
                continue
            train = [(d, v) for d, v in ordered if d <= week.train_end]
            holdout = [(d, v) for d, v in ordered if week.week_start <= d <= week.week_end]
            if len(holdout) != 7 or len(train) < MIN_TRAIN_DAYS:
                raise ValueError(f'第 {week.week_index + 1} 周快照不完整，无法回测')
            train_values = [v for _, v in train]
            actual = [v for _, v in holdout]
            cand_pred = predict(evaluation.candidate_key, train_values, 7)
            base_pred = predict(evaluation.baseline_key, train_values, 7)
            cand_mae = uncapped_mae(actual, cand_pred)
            base_mae = uncapped_mae(actual, base_pred)
            week.candidate_mae = cand_mae
            week.baseline_mae = base_mae
            week.state = 'done'
            week.error_message = None
            week.detail = {
                'days': [
                    {
                        'date': d.isoformat(),
                        'actual': int(a),
                        'candidate_predicted': round(float(cp), 2),
                        'baseline_predicted': round(float(bp), 2),
                    }
                    for (d, a), cp, bp in zip(holdout, cand_pred, base_pred)
                ],
                'candidate_method': method_label(evaluation.candidate_key),
                'baseline_method': method_label(evaluation.baseline_key),
                'train_days': len(train),
            }
            s.commit()
        evaluation.state = 'completed'
        s.commit()
    except Exception as exc:
        s.rollback()
        obj = s.get(MethodEvaluation, evaluation_id)
        obj.state = 'failed'
        obj.error_message = str(exc)
        w = s.scalar(select(EvaluationWeek).where(
            EvaluationWeek.evaluation_id == evaluation_id,
            EvaluationWeek.state != 'done',
        ).order_by(EvaluationWeek.week_index))
        if w:
            w.state = 'failed'
            w.error_message = str(exc)
        s.commit()


def resume_evaluation(s, evaluation_id, enqueue):
    evaluation = s.get(MethodEvaluation, evaluation_id)
    if not evaluation:
        raise HTTPException(404, '评估单不存在')
    if evaluation.state == 'completed':
        raise HTTPException(409, '评估已完成，无需恢复')
    if evaluation.state in ('queued', 'running'):
        raise HTTPException(409, '评估仍在进行中')
    evaluation.state = 'queued'
    evaluation.error_message = None
    for week in s.scalars(select(EvaluationWeek).where(EvaluationWeek.evaluation_id == evaluation_id)).all():
        if week.state != 'done':
            week.state = 'pending'
            week.error_message = None
    s.commit()
    try:
        enqueue(evaluation.id)
    except Exception:
        s.rollback()
        obj = s.get(MethodEvaluation, evaluation_id)
        obj.state = 'failed'
        obj.error_message = '评估队列暂不可用，请重试恢复'
        s.commit()
        raise HTTPException(503, '评估队列暂不可用，请稍后重试')
    return evaluation


def activate_evaluation(s, evaluation_id, user, audit):
    evaluation = s.query(MethodEvaluation).filter_by(id=evaluation_id).with_for_update().one_or_none()
    if not evaluation:
        raise HTTPException(404, '评估单不存在')
    if evaluation.state != 'completed':
        raise HTTPException(409, '三周回测全部完成后才可启用候选方案')
    # 锁剧目行使两个经理的并发启用在此串行：持锁后重读当前方案，只有基线仍
    # 等于当前现用方案、且验票指纹一致的一份被接纳，另一份按过期拒绝。
    production = s.query(Production).filter_by(id=evaluation.production_id).with_for_update().one()

    current_key = active_method(s, evaluation.production_id)
    if evaluation.baseline_key != current_key:
        raise HTTPException(409, '现用方案已变化，本评估基于旧方案比较，结论过期，拒绝启用')

    observed = s.scalars(select(Performance).where(
        Performance.production_id == evaluation.production_id,
        Performance.arrivals.is_not(None),
    ).order_by(Performance.date)).all()
    live_fp = arrival_fingerprint([(r.date, r.arrivals) for r in observed])
    if live_fp != evaluation.history_fingerprint:
        raise HTTPException(409, '评估后验票已更正，旧结论与当前数据不符，拒绝启用过期评估')

    set_active_method(s, evaluation.production_id, evaluation.candidate_key)
    audit(s, user, f"启用预测方案 {evaluation.candidate_key}（评估单 #{evaluation.id}，剧目 {production.code}）")
    s.commit()
    return evaluation


def evaluation_view(s, evaluation):
    weeks = s.scalars(
        select(EvaluationWeek).where(EvaluationWeek.evaluation_id == evaluation.id).order_by(EvaluationWeek.week_index)
    ).all()
    done = [w for w in weeks if w.state == 'done']
    current_key = active_method(s, evaluation.production_id)
    observed = s.scalars(select(Performance).where(
        Performance.production_id == evaluation.production_id,
        Performance.arrivals.is_not(None),
    ).order_by(Performance.date)).all()
    live_fp = arrival_fingerprint([(r.date, r.arrivals) for r in observed])
    stale_reasons = []
    if evaluation.baseline_key != current_key:
        stale_reasons.append('baseline-method-changed')
    if live_fp != evaluation.history_fingerprint:
        stale_reasons.append('arrivals-changed')
    candidate_mae = round(sum(w.candidate_mae for w in done) / len(done), 3) if done else None
    baseline_mae = round(sum(w.baseline_mae for w in done) / len(done), 3) if done else None
    return {
        'id': evaluation.id,
        'production_id': evaluation.production_id,
        'candidate_key': evaluation.candidate_key,
        'candidate_label': method_label(evaluation.candidate_key),
        'baseline_key': evaluation.baseline_key,
        'baseline_label': method_label(evaluation.baseline_key),
        'state': evaluation.state,
        'total_weeks': evaluation.total_weeks,
        'completed_weeks': len(done),
        'candidate_mae': candidate_mae,
        'baseline_mae': baseline_mae,
        'error_message': evaluation.error_message,
        'created_by': evaluation.created_by,
        'created_at': evaluation.created_at,
        'stale': bool(stale_reasons),
        'stale_reasons': stale_reasons,
        'activatable': evaluation.state == 'completed' and not stale_reasons,
        'weeks': [
            {
                'week_index': w.week_index,
                'week_start': w.week_start.isoformat(),
                'week_end': w.week_end.isoformat(),
                'train_end': w.train_end.isoformat(),
                'state': w.state,
                'candidate_mae': w.candidate_mae,
                'baseline_mae': w.baseline_mae,
                'detail': w.detail,
                'error_message': w.error_message,
            }
            for w in weeks
        ],
    }
