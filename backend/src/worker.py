import json
import logging
import os
from pathlib import Path

import redis

from .db import Session
from .models import Production
from .forecast import forecast_week
from .evaluation_service import run_evaluation, active_method

r = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
ARTIFACT_DIR = Path(os.environ.get('ARTIFACT_DIR', '/artifacts'))
try:
    ARTIFACT_DIR.mkdir(exist_ok=True)
except PermissionError:
    import tempfile
    ARTIFACT_DIR = Path(tempfile.gettempdir()) / 'theatre-artifacts'
    ARTIFACT_DIR.mkdir(exist_ok=True)
FORECAST_QUEUE = 'theatre-forecast'
EVAL_QUEUE = 'theatre-evaluation'


def handle_forecast(raw):
    # 旧消息只有剧目 id：沿用当时的现用方案（无显式入队方案时按当前启用方案）。
    if raw.lstrip().startswith('{'):
        payload = json.loads(raw)
        pid = int(payload['production_id'])
        method_key = payload.get('method_key')
    else:
        pid = int(raw)
        method_key = None
    with Session() as s:
        p = s.get(Production, pid)
        if not p or p.forecast_state != 'calculating':
            return
        try:
            p.forecast_evidence = forecast_week(s, p, method_key or active_method(s, pid))
            p.forecast_state = 'ready'
            Path(ARTIFACT_DIR / f'production-{p.id}.json').write_text(
                json.dumps(p.forecast_evidence, ensure_ascii=False))
            s.commit()
        except Exception:
            s.rollback()
            p = s.get(Production, pid)
            p.forecast_state = 'failed'
            p.forecast_evidence = {'error': '预测失败，请修正后重试'}
            s.commit()
            logging.exception('forecast failed')


def handle_evaluation(raw):
    evaluation_id = int(raw)
    with Session() as s:
        try:
            run_evaluation(s, evaluation_id)
        except Exception:
            logging.exception('evaluation failed')


def main():
    # 评估走独立队列与独立写表，不阻塞、不干扰正常预测。
    while True:
        Path('/tmp/worker-heartbeat').touch()
        entry = r.blpop([FORECAST_QUEUE, EVAL_QUEUE], timeout=2)
        if not entry:
            continue
        queue, raw = entry
        try:
            if queue == EVAL_QUEUE:
                handle_evaluation(raw)
            else:
                handle_forecast(raw)
        except Exception:
            logging.exception('worker job failed: %s', queue)


if __name__ == '__main__':
    main()
