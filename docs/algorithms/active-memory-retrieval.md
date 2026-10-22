# Active memory retrieval

MemoryLayer 在 PMD 原有检索评分之前建立 ACTIVE 且未过期的候选集，
再选择 cosine 排名前 `2*k` 个种子。祖先扩展仍使用 PMD 的 lineage 遍历，
但仅保留有效节点进入最终候选。拓扑不剪裁，历史节点仍可连接有效祖先。

评分公式保持 `alpha*cosine + beta*proximity + gamma*importance`，
proximity 沿用 PMD 图距离计算。相同分数保留候选插入顺序。
这个修改修复生命周期过滤，不引入新的学习算法或评测结论。

回归验证见 `tests/test_memory_correctness.py`：无效节点不能挤占有效结果，
lineage 扩展不能返回已删除或已过期祖先，零学习率阻止 successor 创建。
