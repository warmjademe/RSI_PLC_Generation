"""保留原实现的 state/action/reward 案例记忆、冻结编码器余弦检索和 Top-4 选择，同时呈现成功与失败案例。"""
from .workflow import run

__all__ = ["run"]
