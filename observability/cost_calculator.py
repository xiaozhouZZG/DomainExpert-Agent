"""Token 成本核算"""


class CostCalculator:
    """Token 成本计算器"""

    # 价格表（每百万token美元）
    PRICES = {
        "gpt-4o-mini": {"input": 0.15, "output": 0.6},
        "gpt-4o": {"input": 2.5, "output": 10},
    }

    @staticmethod
    def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
        """计算成本"""
        prices = CostCalculator.PRICES.get(model, {"input": 0, "output": 0})

        input_cost = (input_tokens / 1_000_000) * prices["input"]
        output_cost = (output_tokens / 1_000_000) * prices["output"]

        return input_cost + output_cost


# 全局累计成本
_total_cost = 0.0


def get_total_cost() -> float:
    """获取累计总成本"""
    return _total_cost


def add_cost(cost: float):
    """添加成本"""
    global _total_cost
    _total_cost += cost

