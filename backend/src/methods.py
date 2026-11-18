"""预测方法注册表。

现用方案与候选方案共用同一 fit/forecast 接口，所有方法均不封顶（原始需求
max(0, pred)），误差比较发生在封顶之前，与座位容量无关。新增方法只需在
METHODS 注册一个固定键，评估与正式预测即可选择。
"""
import numpy as np
from statsmodels.tsa.holtwinters import ExponentialSmoothing

MIN_TRAIN_DAYS = 28


def _arr(values):
    return np.array(values, dtype=float)


def _clip(seq):
    return [max(0.0, float(v)) for v in seq]


def hw_additive(values, horizon):
    """现用方案：Holt-Winters 加法趋势 + 加法周季节（7）。"""
    model = ExponentialSmoothing(
        _arr(values), trend='add', seasonal='add',
        seasonal_periods=7, initialization_method='estimated'
    ).fit(optimized=True)
    return _clip(model.forecast(horizon))


def hw_damped(values, horizon):
    """候选：在现用方案上对趋势做阻尼，抑制连续外推的过冲。"""
    model = ExponentialSmoothing(
        _arr(values), trend='add', seasonal='add', damped_trend=True,
        seasonal_periods=7, initialization_method='estimated'
    ).fit(optimized=True)
    return _clip(model.forecast(horizon))


def seasonal_naive(values, horizon):
    """候选：季节朴素法，直接复用 7 天前的验票水平。"""
    if len(values) < 7:
        raise ValueError('季节朴素法至少需要 7 天历史')
    base = list(values)
    return [max(0.0, float(base[(len(base) - 7) + (i % 7)])) for i in range(horizon)]


def moving_average(values, horizon):
    """候选：7 日滚动均值，平滑掉周末脉冲。"""
    if len(values) < 7:
        raise ValueError('7 日滚动均值至少需要 7 天历史')
    window = [float(v) for v in values[-7:]]
    out = []
    for _ in range(horizon):
        nxt = max(0.0, float(np.mean(window)))
        out.append(nxt)
        window = window[1:] + [nxt]
    return out


# 固定键即方案身份：评估快照与启用记录均保存键，不随展示文案变化。
METHODS = {
    'holt_winters_additive': {
        'label': 'Holt-Winters 加法趋势 + 加法周季节',
        'fn': hw_additive,
    },
    'holt_winters_damped': {
        'label': 'Holt-Winters 阻尼趋势 + 加法周季节',
        'fn': hw_damped,
    },
    'seasonal_naive': {
        'label': '季节朴素法（复用 7 天前水平）',
        'fn': seasonal_naive,
    },
    'moving_average_7d': {
        'label': '7 日滚动均值',
        'fn': moving_average,
    },
}
DEFAULT_METHOD = 'holt_winters_additive'


def method_keys():
    return tuple(METHODS.keys())


def predict(key, values, horizon):
    if key not in METHODS:
        raise ValueError(f'未知预测方案：{key}')
    if len(values) < MIN_TRAIN_DAYS:
        raise ValueError(f'至少需要 {MIN_TRAIN_DAYS} 天连续验票记录')
    return METHODS[key]['fn'](list(values), horizon)


def method_label(key):
    return METHODS[key]['label'] if key in METHODS else key
