# 救灾物资发放核验台

离线记录救援物资到点实数、家庭资格和现场签收，判定发放完成与缺口追索。三个独立入口为 `arrival-import`、`household-import`、`delivery-import`；各自保存原始证据、业务实体、状态和跨链决定。所有数据为离线合成 fixture，不访问网络。

安装：`python -m pip install . --no-deps --no-build-isolation`。演示：`python -m reliefhandoff demo`。测试：`python -m pytest -q`。各入口接受 `--db` 和 `--file`；`report --db` 输出逐实体状态与来源。

现场只以实际可发件数决定交付。破封包件先隔离，`arrival-reconcile` 要求两个不同见证人与实物照片摘要才能释放核点件数；少件记录保留。家庭资格按灾区、人数和凭证时效计算，`eligibility-review` 可凭独立审核证据改变待补证家庭的状态。签收量与应领额度、现场剩余数同时核对；部分签收形成原因明确的缺口，分为现场供应不足和家庭部分领取，后续补货签收才能销案。重复收据异值拒绝，冲突原文留存。`demo-decisions` 演示部分交付→破封复核→资格补证→补发销案。

## 可复用净水设备

水等消耗品仍按件核销。明确标记为可复用的净水设备单独建账：设备本体和可拆滤芯各有序列号，入口为 `equipment-arrival`、`equipment-authority`、`equipment-issue`、`equipment-return`、`equipment-inspect`、`equipment-merge`。

- `equipment-arrival` 只从已核点可用的到站件建立资产，序列清单必须与 `arrival_count.released_count` 完全相符；破封未复核（`status!='usable'`）时拒绝建档。
- `equipment-authority` 记录负责人预先签发的处分材料：签发者、相对方、`loan`/`transfer` 范围、有效期和文件摘要。
- `equipment-issue` 的每张领用单写明 `mode`（限时借用 `loan` 或产权转让 `transfer`）、签发者及其处分权、接手保管人、生效时间，并绑定授权材料。借用期间站点保留产权但不能重复出借；产权转让后设备即使暂放站点，站点也不能把保管当所有权。不成立的申请记为 `refused`（附原因），不产生交接。
- `equipment-return` 核对本体、滤芯、见证人和状态。缺件或损坏进入 `pending_inspection`，滤芯缺失不能被一张交接单洗白；只有 `equipment-merge` 记录真实新滤芯序列、再经 `equipment-inspect` 见证检查后才可能恢复。产权转让后的归还只转移保管，不转移产权。
- 迟到单据与相互矛盾的产权主张写入 `equipment_dispute`（保留原始 `source_failure` 摘要），冻结该设备的后续处分，且不覆盖已发生的实物交接。

`report` 新增 `equipment_units` 与 `lendable_equipment`：逐台列出产权人、实际保管人、借用是否结清、滤芯状态、下一次能否出借及原始到站来源。`demo-equipment --mode loan|transfer` 用 P1+F1 演示两种处分下 B 避难点能否借到同一台设备。
