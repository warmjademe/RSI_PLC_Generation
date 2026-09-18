"""检索原始训练轨迹，保留候选、反馈与实际终局状态，不先提炼规则或技能。"""
from .workflow import run

__all__ = ["run"]
