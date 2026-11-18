import os

import redis
from fastapi import APIRouter, Depends
from sqlalchemy import select

from .db import db_session
from .auth import current, admin
from .models import Production
from .methods import METHODS, DEFAULT_METHOD
from .evaluation_models import MethodEvaluation
from .evaluation_schemas import EvaluationInput
from .evaluation_service import (
    active_method, start_evaluation, resume_evaluation,
    activate_evaluation, evaluation_view,
)
from .services import get, audit

router = APIRouter()
_r = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
EVAL_QUEUE = 'theatre-evaluation'


def _enqueue(evaluation_id):
    _r.rpush(EVAL_QUEUE, evaluation_id)


@router.get('/forecast-methods')
def list_methods(s=Depends(db_session), u=Depends(current)):
    return {
        'methods': [{'key': k, 'label': v['label']} for k, v in METHODS.items()],
        'default_key': DEFAULT_METHOD,
    }


@router.get('/productions/{pid}/method-evaluations')
def list_evaluations(pid: int, s=Depends(db_session), u=Depends(current)):
    get(s, Production, pid)
    rows = s.scalars(
        select(MethodEvaluation).where(MethodEvaluation.production_id == pid).order_by(MethodEvaluation.id.desc())
    ).all()
    return {
        'active_method_key': active_method(s, pid),
        'evaluations': [evaluation_view(s, e) for e in rows],
    }


@router.post('/productions/{pid}/method-evaluations')
def create_evaluation(pid: int, payload: EvaluationInput, s=Depends(db_session), u=Depends(admin)):
    production = get(s, Production, pid)
    evaluation = start_evaluation(s, production, payload.candidate_key, u, _enqueue)
    audit(s, u, f'发起候选方案评估 #{evaluation.id}：{evaluation.candidate_key}（剧目 {production.code}）')
    s.commit()
    return evaluation_view(s, evaluation)


@router.post('/productions/{pid}/method-evaluations/resume/{eid}')
def resume(pid: int, eid: int, s=Depends(db_session), u=Depends(admin)):
    get(s, Production, pid)
    evaluation = resume_evaluation(s, eid, _enqueue)
    audit(s, u, f'恢复候选方案评估 #{eid}')
    s.commit()
    return evaluation_view(s, evaluation)


@router.post('/productions/{pid}/method-evaluations/{eid}/activate')
def activate(pid: int, eid: int, s=Depends(db_session), u=Depends(admin)):
    get(s, Production, pid)
    evaluation = activate_evaluation(s, eid, u, audit)
    return evaluation_view(s, evaluation)
