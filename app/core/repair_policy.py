"""修复策略表：Finding -> 当前是否存在可执行的修复通道。

这张表是"返工路由"的唯一真相来源，也是交付门禁的判断依据：
表里没有的 (rule_id, field) 组合意味着当前版本无法修复该缺陷。
对 BLOCKER 级发现，"无法修复"即等于"不予交付"——而且不该浪费返工额度，
因为重跑注定得到逐字相同的裁决（已由运行时实测证明：prompt 步骤只执行 1 次
而 review 执行 3 次，三轮裁决签名完全一致）。

本阶段只登记已具备真实修复通道的组合；
后续会补 ("R6","narration") -> sanitize_narration 与 prompt 侧的 regenerate。
"""
from __future__ import annotations

# (rule_id, field) -> 修复动作名。field 为 "*" 表示通配。
REPAIR_ACTIONS: dict[tuple[str, str], str] = {
    ("R3", "*"): "regenerate_generation",   # 素材缺失/损坏：重跑生成即可
    ("R4", "*"): "regenerate_generation",   # 时长越界：上游已钳制，重跑兜底
}


def has_repair(rule_id: str, field: str) -> bool:
    """该 (规则, 字段) 组合当前是否有修复通道。"""
    return (rule_id, field) in REPAIR_ACTIONS or (rule_id, "*") in REPAIR_ACTIONS
