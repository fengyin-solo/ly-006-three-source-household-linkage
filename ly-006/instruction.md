# 三份登记表的住户核对与覆盖口径

季度复核前，区级服务评估组收到窗口接案、电话回访和住户回访三批匿名材料。个体行来自
部分成员，其他成员留在按户汇总的年龄、人数和地址摘要里。评估组要先恢复住户、户内成员槽
和记录归属，再判断窗口接案与电话回访覆盖了多少人。

## 调用与材料

入口是 `/app/linkage_ledger.py`，调用形式为 `python3 /app/linkage_ledger.py RUN_JSON RESULT_JSON`。
程序使用镜像自带的标准库，判分不访问外部服务。成功时写出结果并返回 0。

运行文件的 `run_id` 是本次运行号，`source_a`、`source_b`、`source_c` 是三份来源，`model`
给出地址、姓名和性别的匹配参数以及户、记录成本。每份来源含 `households` 和 `records`。
户摘要含 `address`、`ages`、`household_no`、`size`，其中 `size` 等于年龄槽数；记录含
`address`、`age`、`given`、`sex`、`source_no`、`surname`。`given`、`sex`、`surname`
可以为 `null`，`source_no` 和 `household_no` 在各自来源内唯一。

## 支持度与住户

姓名和地址先做 Unicode NFKC、大小写折叠，再去掉空白与连字符。两个文本的相似度是
`1 - 编辑距离 / 较长字符串长度`。相似度达到对应 `threshold` 时同意，否则不同意。
任一侧为空时不贡献。人员字段同意贡献 `ln(m/u)`，不同意贡献 `ln((1-m)/(1-u))`。

两户只有年龄数组逐位相等，且地址支持减去 `household_cost` 后大于 0，才是候选户对。
以 `source_a` 为锚点，分别对 B、C 求至多一对一的最大支持对齐；没有候选的户保留单来源状态。
户唯一性差距是逐个禁用已选户对后重新求最优总和所得差值的最小值，低于 `uniqueness_margin`
时拒绝。记录进入成员槽必须年龄相等，且记录地址对该户的调整支持大于 0；每条记录进入恰好
一个槽，每个槽在同一来源至多一条记录，全来源分配使地址支持总和最大。

同户同岁的多个槽先使跨来源人员支持总和最大；仍并列时，按每槽的
`(source_a记录号, source_b记录号, source_c记录号)` 做空值优先、非空字典序比较，取整户
序列字典序最小者。成员年龄升序排列，同岁槽沿用前述规范次序。一个槽三侧都没有记录时是
未观测成员；来源未观测数是该来源户摘要人数减记录数。

## 覆盖估计

`n_a`、`n_b` 分别是 A、B 户摘要人数总和；`matched` 是同时具有 A、B 户摘要的成员槽数。
A 无 B 对应户时该户成员计为 A 侧未匹配，B 侧同理。`matched` 为 0 时拒绝。
否则 `chapman_estimate = ((n_a + 1) * (n_b + 1) / (matched + 1)) - 1`，
`variance = ((n_a + 1) * (n_b + 1) * (n_a - matched) * (n_b - matched)) /
((matched + 1) ** 2 * (matched + 2))`。

估计值按 half-up 保留 3 位小数，方差按 half-up 保留 6 位小数。区间取 `z=1.96`，
用未舍入估计和方差计算；下界向下取整到 3 位且不低于 `max(n_a, n_b)`，上界向上取整到 3 位。

## 结果与失败

结果含 `run_id`、`households`、`population`、`diagnostics`。每个户给出规范地址、年龄、
从 `H0001` 起的 `household_key`、三侧户号、成员、已观测数和未观测数；每个成员给出年龄、
三侧记录号和该槽已观测记录两两人员支持之和。`diagnostics` 给出 `uniqueness_gap`。
`population` 给出 `n_a`、`n_b`、`matched`、
`unmatched_a`、`unmatched_b`、三侧未观测数、全局未观测成员数、Chapman 估计、方差和区间。

户按规范地址排序，年龄数组和三侧户号作为次级键。户内成员按规范年龄序排列。户对唯一性不足、记录
无法注入年龄槽或 A/B 没有对齐户时，程序返回非零状态且不创建结果文件。

镜像内 `environment/app/data/current_run.json` 是一份示例材料。每份来源最多 8 户、16 条记录。
超过任一项上限时返回非零状态。
单次从读取到写完结果在 5 秒内完成。
