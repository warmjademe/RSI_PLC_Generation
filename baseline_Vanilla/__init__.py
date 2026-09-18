"""不读取跨任务记忆；保留当前任务的真实验证反馈与有限重试。"""
from .workflow import run

__all__ = ["run"]
