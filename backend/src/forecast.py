from datetime import timedelta
from math import ceil

import numpy as np
from sqlalchemy import select

from .services import history, validate_history
from .models import Performance
from .methods import predict, method_label


def _backtest_mae(method_key, train, test):
    back = predict(method_key, train, 7)
    mae = float(np.mean(np.abs(np.array(test) - np.array(back))))
    return round(mae, 3), back


def forecast_week(s, production, method_key=None):
    if method_key is None:
        from .evaluation_service import active_method
        method_key = active_method(s, production.id)
    rows = history(s, production.id)
    validate_history(rows)
    y = [r.arrivals for r in rows]
    train = y[:-7]
    test = y[-7:]
    mae, back = _backtest_mae(method_key, train, test)
    pred = predict(method_key, y, 7)
    output = []
    for i, value in enumerate(pred):
        day = rows[-1].date + timedelta(days=i + 1)
        perf = s.scalar(select(Performance).where(Performance.production_id == production.id, Performance.date == day))
        if not perf:
            perf = Performance(production_id=production.id, date=day, curtain='19:30', status='scheduled')
            s.add(perf)
        if perf.arrivals is not None or perf.status == 'crew_confirmed':
            raise ValueError('未来场次已有验票或确认点名，不得覆盖')
        raw = max(0, float(value))
        perf.raw_forecast = round(raw, 2)
        perf.forecast = min(production.seats, ceil(raw))
        perf.status = 'forecasted'
        output.append({'date': day.isoformat(), 'raw': round(raw, 2), 'capped': perf.forecast,
                       'required_ushers': max(1, ceil(perf.forecast / 40))})
    return {
        'backtest_mae': mae, 'train_days': len(train), 'holdout_days': 7,
        'history_days': len(y), 'holdout_start': rows[-7].date.isoformat(),
        'history_end': rows[-1].date.isoformat(),
        'backtest': [{'date': r.date.isoformat(), 'actual': r.arrivals, 'predicted': round(float(v), 2)}
                     for r, v in zip(rows[-7:], back)],
        'future': output,
        'method': method_label(method_key),
        'method_key': method_key,
        'seat_capacity': production.seats,
    }
