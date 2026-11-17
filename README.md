# 救灾物资发放核验台

离线记录救援物资到点实数、家庭资格和现场签收，判定发放完成与缺口追索。三个独立入口为 `arrival-import`、`household-import`、`delivery-import`；各自保存原始证据、业务实体、状态和跨链决定。所有数据为离线合成 fixture，不访问网络。

安装：`python -m pip install . --no-deps --no-build-isolation`。演示：`python -m reliefhandoff demo`。测试：`python -m pytest -q`。各入口接受 `--db` 和 `--file`；`report --db` 输出逐实体状态与来源。

现场只以实际可发件数决定交付。破封包件先隔离，`arrival-reconcile` 要求两个不同见证人与实物照片摘要才能释放核点件数；少件记录保留。家庭资格按灾区、人数和凭证时效计算，`eligibility-review` 可凭独立审核证据改变待补证家庭的状态。签收量与应领额度、现场剩余数同时核对；部分签收形成原因明确的缺口，分为现场供应不足和家庭部分领取，后续补货签收才能销案。重复收据异值拒绝，冲突原文留存。`demo-decisions` 演示部分交付→破封复核→资格补证→补发销案。
