from datetime import date as DateValue
from sqlalchemy import String, Integer, Float, ForeignKey, Date, JSON, UniqueConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

EVALUATION_STATES = ('queued', 'running', 'completed', 'failed')
EVALUATION_WEEK_STATES = ('pending', 'done', 'failed')


class MethodSetting(Base):
    """每剧目当前启用的预测方案。缺省回落到方法注册表的 DEFAULT_METHOD。"""
    __tablename__ = 'method_settings'
    id: Mapped[int] = mapped_column(primary_key=True)
    production_id: Mapped[int] = mapped_column(ForeignKey('productions.id'), unique=True)
    method_key: Mapped[str] = mapped_column(String(40))


class MethodEvaluation(Base):
    """候选方案评估单：固定创建时刻的验票快照、现用方案与候选方案。

    同剧目同候选可有多代评估（验票更正后重新发起），每代独立保存快照与
    过期标记，旧结论不被覆盖，启用时逐单校验是否仍有效。
    """
    __tablename__ = 'method_evaluations'
    id: Mapped[int] = mapped_column(primary_key=True)
    production_id: Mapped[int] = mapped_column(ForeignKey('productions.id'), index=True)
    candidate_key: Mapped[str] = mapped_column(String(40), index=True)
    baseline_key: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(20), default='queued')
    # 创建时刻的全部验票（date -> arrivals），后续更正不影响本单。
    arrival_snapshot: Mapped[dict] = mapped_column(JSON)
    history_fingerprint: Mapped[str] = mapped_column(String(64))
    total_weeks: Mapped[int] = mapped_column(Integer, default=3)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[str] = mapped_column(String(40))


class EvaluationWeek(Base):
    """评估单内逐周回测结果。已 done 的周重试时跳过，实现中断恢复不重复计算。"""
    __tablename__ = 'evaluation_weeks'
    __table_args__ = (UniqueConstraint('evaluation_id', 'week_index'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey('method_evaluations.id'))
    week_index: Mapped[int] = mapped_column(Integer)
    week_start: Mapped[DateValue] = mapped_column(Date)
    week_end: Mapped[DateValue] = mapped_column(Date)
    train_end: Mapped[DateValue] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(20), default='pending')
    candidate_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
