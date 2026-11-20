# 救灾物资发放核验台

离线记录救援物资到点实数、家庭资格和现场签收，判定发放完成与缺口追索，并冻结每张收据的批次来源以支持离线召回核对。独立入口为 `arrival-import`、`arrival-reconcile`、`household-import`、`eligibility-review`、`picking-import`、`delivery-import`、`recall-import`；各自保存原始证据、业务实体、状态和跨链决定。所有数据为离线合成 fixture，不访问网络。

安装：`python -m pip install . --no-deps --no-build-isolation`。演示：`python -m reliefhandoff demo`。测试：`python -m pytest -q`。各入口接受 `--db` 和 `--file`；`report --db` 输出逐实体状态、逐收据来源、逐批剩余可发数与召回联系名单。

## 拣货合同与来源冻结

站点必须在发放前通过 `picking-import` 为同站点、同品类选定一种现场拣货合同：`fifo`（按可用时刻先进先发）或 `lifo`（按可用时刻后进先发）。未选定合同的收据记为 `picking_contract_not_selected` 挂起，不占用任何件数；已开始发放后不得改选合同，同值重导幂等。

批次可用时刻：核点完整的包件在核点时（`counted_at`）可用；破封包件在 `arrival-reconcile` 见证复核完成时（`reconciled_at`）才按复核件数可用。晚于收据时刻才可用的批次不能分给该收据，事后复核、补货或重复导入都不会回填旧收据的来源。

`delivery-import` 在同一事务中冻结收据、逐批次来源（`delivery_source`）、分配数量、选定合同与原始证据摘要（来源 blob 的 SHA-256）。一张收据可跨多个批次，逐批次件数之和等于已交付数量，同一物理件经批次剩余量约束不会分配两次。资格、签名与额度门禁保持原规则不变。

## 离线召回核对

`recall-import` 登记批次召回：未发出的该批件数立即隔离（批次状态置 `recalled`，退出可发池），已发出的来源份额经冻结的 `delivery_source` 列出对应家庭、收据与件数；其它批次的物资不受影响。召回后新到补货可继续发放，旧收据来源不变。

没有来源登记的旧库收据只列入 `unknown_source_receipts` 与召回报告的 `indeterminate_legacy_receipts`，系统不会为让召回报告完整而倒推分配；其件数在隔离计算中保守扣减。`report` 新增：`receipt_sources`（每张收据的来源件数与证据摘要）、`batch_availability`（每批已发、剩余可发数与可用时刻）、`recall_contacts`（召回联系家庭、已发份额、隔离件数与无法确定的旧记录）。

## 既有业务规则

现场只以实际可发件数决定交付。破封包件先隔离，`arrival-reconcile` 要求两个不同见证人与实物照片摘要才能释放核点件数；少件记录保留。家庭资格按灾区、人数和凭证时效计算，`eligibility-review` 可凭独立审核证据改变待补证家庭的状态。签收量与应领额度、现场剩余数同时核对；部分签收形成原因明确的缺口，分为现场供应不足和家庭部分领取，后续补货签收才能销案。重复收据异值拒绝，冲突原文留存。`demo-decisions` 演示部分交付→破封复核→资格补证→补发销案。旧版本数据库打开时自动补齐新增列与新表，旧收据保留为来源未知。
