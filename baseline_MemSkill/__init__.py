"""保留原实现的记忆操作库、执行器、PPO 控制器、训练内开发划分和操作设计反馈；只训练小型控制器，不更新 PLC 大模型权重。"""
from .workflow import run

__all__ = ["run"]
