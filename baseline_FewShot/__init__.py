"""按固定种子为每个目标型号选取成功程序，所有查询使用同一组示例，不根据测试题选例。"""
from .workflow import run

__all__ = ["run"]
