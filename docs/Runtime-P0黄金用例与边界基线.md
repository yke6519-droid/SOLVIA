---
type: project
domain:
  - agent
  - runtime
  - charting
status: baseline
created: "2026-07-28"
updated: "2026-07-28"
---
# Runtime P0：黄金用例与边界基线

## 1. P0 的目标

P0 不改预测、查询、图表、导出和前端业务逻辑，只完成重构前的基线冻结：

```text
明确职责边界
  → 固定可重复的黄金用例
  → 手动确认当前真实行为
  → 保存工具、事件、产物和错误证据
  → 为后续自动回放建立断言标准
```

黄金用例的作用不是增加一套业务功能，而是回答一个问题：

> Runtime 改造之后，相同输入是否仍然产生正确的业务结果、事件顺序和权限边界？

## 2. 需要每次手动测试吗？

不需要。

P0 采用“两步法”：

```text
第一次：人工执行，确认预期和真实系统一致
后续：自动回放，检查行为是否发生回归
```

### 2.1 第一次为什么需要人工执行

当前项目还没有统一的 Runtime 测试回放器，也没有完整的黄金用例测试目录。因此第一次需要人工操作，原因是要确认：

- Agent 实际选择了什么工具；
- SSE 实际发送了哪些事件；
- 数据制品和图表快照是否真实生成；
- 用户确认、取消和异常是否按照当前设计工作；
- 当前日志中是否存在计划外重试。

人工执行不是最终方案，只负责建立“可信的基准答案”。

### 2.2 后续如何自动化

后续可以把每条用例转换为回放测试：

```text
固定用户输入
  → 调用测试会话接口
  → 收集 SSE 事件
  → 收集工具调用记录
  → 查询产物和数据库快照
  → 与预期断言比较
  → 输出通过/失败和差异
```

自动测试不要求 LLM 每次输出逐字相同，只检查稳定的结构化事实，例如工具名称、事件类型、产物类型、错误码和完成证据。

## 3. 一条黄金用例应该记录什么

每条用例使用下面的记录结构：

```yaml
case_id: chart_actual_predicted_single_station
title: 单站点实际与预测逐小时对比
user_input: 对比英杰站2026-06-10的真实发电和预测发电情况，并画图
preconditions:
  - 英杰站点存在
  - actual 数据可查询
  - predicted 数据已缓存或允许生成
expected_tools:
  - get_actual_power 或 get_power_dataset
  - get_predicted_power 或 predict_power
  - create_power_chart（未来高层入口）
expected_artifacts:
  - actual DatasetArtifact
  - predicted DatasetArtifact
expected_events:
  - task_started
  - tool_started
  - tool_finished
  - chart_spec
expected_evidence:
  - 合法 ChartSpec
  - 24 个时间点
  - 两条序列
forbidden:
  - 使用不匹配的 active_dataset
  - Agent 自行编造数据数组
  - 返回 Markdown 假图并声称成功
  - 无依据地重复尝试图表能力
```

字段含义：

| 字段 | 作用 |
|---|---|
| `preconditions` | 测试前必须准备的数据和状态 |
| `expected_tools` | 允许或期望出现的工具调用 |
| `expected_artifacts` | 必须生成或复用的数据制品 |
| `expected_events` | 流式执行过程必须出现的事件 |
| `expected_evidence` | 判断任务真正完成的结构化证据 |
| `forbidden` | 明确禁止的错误行为 |

## 4. 第一批黄金用例

### 4.1 预测与确认类

| 编号 | 场景 | 重点检查 |
|---|---|---|
| PRED-01 | 预测缓存命中 | 不重复询问，不重复计算 |
| PRED-02 | 预测缓存未命中并确认 | 进入等待，确认后继续同一任务 |
| PRED-03 | 预测缓存未命中并取消 | 返回取消状态，不加载模型 |
| PRED-04 | 预测参数存在歧义 | 只询问一次，记住已选站点 |

### 4.2 错误与数据边界类

| 编号 | 场景 | 重点检查 |
|---|---|---|
| ERR-01 | 查询日期无数据 | 返回可理解的无数据结果 |
| ERR-02 | 站点不存在 | 返回业务错误，不伪造站点 |
| ERR-03 | 同一用户跨会话制品隔离 | 当前系统不开放跨用户制品访问入口 |
| ERR-04 | 工具参数非法 | 可恢复错误，不产生脏数据 |
| ERR-05 | 外部服务超时 | 有明确错误码和重试边界 |

### 4.3 图表类

| 编号 | 场景 | 重点检查 |
|---|---|---|
| CHART-01 | 单站点实际逐小时折线图 | 单序列、24 个小时点 |
| CHART-02 | 单站点预测逐小时折线图 | 预测制品和实际制品不混用 |
| CHART-03 | 实际与预测逐小时对比 | 两个制品可组合成 ChartDataView |
| CHART-04 | 多站点单日逐小时对比 | 站点作为序列，不要求用户逐个选择 |
| CHART-05 | 多站点日总发电量柱状图 | 每个站点一条稳定序列 |
| CHART-06 | 单站点周期日总量趋势 | 日期为横轴，日总量为数值 |
| CHART-07 | active_dataset 不匹配 | 不得直接拿 active_dataset 画错图 |
| CHART-08 | 历史图表恢复 | 读取 ChartSpec 快照，不重新调用模型 |

## 5. 第一次人工测试怎么做

### 5.1 测试前准备

建议使用单独的测试账号和测试会话，避免污染正常会话。每条用例开始前记录：

```text
测试时间
用户 ID
会话 ID
代码版本或 Git commit
数据库状态
是否已有缓存
```

### 5.2 执行时观察三处

#### A. 前端对话窗口

观察：

- 是否显示正在处理、调用工具、等待确认和完成；
- 是否出现重复工具步骤；
- 是否真正出现图表卡片；
- 是否显示错误而不是空白页面。

#### B. 浏览器 Network

观察：

- `/api/chat/stream` 是否持续返回 SSE；
- 事件顺序是否合理；
- 是否重复发起同一个请求；
- 是否收到 `chart_spec`、`user_input_required` 或错误事件。

#### C. 后端日志和数据库

记录：

- 工具名称和参数；
- 工具调用次数；
- `artifact_id`；
- `chart_id` 或图表快照；
- 错误码；
- 开始和结束时间。

### 5.3 手动测试记录示例

```text
case_id: CHART-03
run_id: 待填写
session_id: 待填写
结果: PASS / FAIL

实际工具调用：
1. get_actual_power
2. get_predicted_power
3. create_chart_plan

实际产物：
- artifact_actual: artifact_xxx
- artifact_predicted: artifact_xxx
- chart_snapshot: chart_xxx

实际事件：
task_started → tool_started → tool_finished → chart_spec → task_finished

异常：无
备注：两条序列均为24个小时点，时间戳完成对齐
```

## 6. 自动回放应该检查什么

自动化测试不比较整段自然语言，而比较结构化断言：

```text
断言 1：是否出现预期工具
断言 2：是否出现禁止工具或重复调用
断言 3：是否生成正确类型的 DatasetArtifact
断言 4：是否生成合法 ChartSpec
断言 5：事件顺序是否正确
断言 6：错误是否属于预期错误码
断言 7：是否跨用户访问数据
断言 8：是否产生真实完成证据
```

例如 CHART-03 的自动断言：

```text
必须：
- actual 制品存在
- predicted 制品存在
- ChartDataView 包含两个序列
- ChartSpec.chart_type == line
- ChartSpec.series 数量 == 2
- x_axis 数据点数量 == 24

禁止：
- 只使用 active_dataset
- series 数量 == 1
- 生成文本图但没有 chart_spec
- 重试超过约定次数
```

## 7. 基线指标

第一轮只记录真实值，不提前设定虚假的提升百分比：

| 指标 | 含义 |
|---|---|
| 工具调用次数 | 一次任务实际调用了多少个工具 |
| 重复调用次数 | 相同工具和相同参数重复调用次数 |
| SSE 事件数量 | 执行过程产生的结构化事件数 |
| P50/P95 耗时 | 典型和较慢请求的执行时间 |
| ChartSpec 成功率 | 真实生成合法图表的比例 |
| 错误可解释率 | 错误是否有稳定错误码和用户可理解原因 |
| 产物追溯完整率 | 是否能找到来源、会话、用户和查询条件 |
| 历史恢复成功率 | 重新打开会话后图表是否能正常恢复 |

## 8. P0 完成条件

满足以下条件后，P0 才算完成：

- 10 条以上黄金用例已经建立；
- 至少手动执行一遍关键用例；
- 每条用例都有输入、预期、实际结果和证据；
- 明确了 DataArtifact、active_dataset、ChartDataView、ChartSpec 的边界；
- 没有因为建立基线而修改现有业务逻辑；
- 记录了当前工具调用次数、重复尝试和失败原因；
- 已确定后续自动回放需要检查的断言。

## 9. P0 完成后下一阶段

P0 完成后进入 R1：Runtime 基础数据模型。

R1 只做旁路记录，不改变工具业务行为：

```text
ToolInvocation
ToolSpec
ToolResult
PolicyDecision
AgentRunState
RuntimeContext
AgentEvent
```

第一批建议只记录：

```text
run_id
call_id
工具名
调用参数摘要
开始时间
结束时间
成功/业务失败/严重异常
```

完成 R1 后，再进入 R2 的 `PolicyAwareTool`，不要在 R1 同时迁移预测确认和图表链路。

## 10. 当前实测记录

### PRED-03：预测缓存未命中并取消

**测试输入：**

```text
预测英杰站点今天的发电情况
```

**预期行为：**

```text
预测缓存未命中
  → 等待用户确认
  → 用户回复取消
  → 返回 CANCELLED
  → 不继续执行预测
  → 不加载预测模型
  → 不返回“任务执行失败”
```

**实际工具调用：**

```text
1. get_station_info
2. predict_power
```

**实际执行过程：**

```text
get_station_info
  → predict_power
  → 等待用户确认
  → 用户回复取消
  → 前端显示“用户取消了本次预测”
  → 最终又显示“任务执行失败”
```

**后端日志证据：**

- `get_station_info` 成功返回英杰站点信息；
- `predict_power` 进入预测确认流程；
- `/api/chat/{session_id}/reply` 返回 HTTP 200；
- 用户回复后没有继续出现气象查询、模型加载或预测缓存写入日志；
- 本轮最终被前端归类为“任务执行失败”。

**基线判定：**

```text
状态：PARTIAL_FAIL
```

前端展示层表现为“取消”，但任务协议层没有稳定产出 `CANCELLED` 结果，最终被通用异常路径覆盖为“任务执行失败”。因此该用例暂时不能算完整通过。

**当前问题记录：**

```text
问题编号：PRED-03-001
问题类型：取消结果未被统一识别
影响范围：预测确认取消、Runtime 错误分级、前端最终状态展示
当前阶段：P0 只记录，不修复
后续处理阶段：R2/R3，统一 ToolResult 与 Runtime Policy 后处理
```

**额外观察：**

本次任务中，Agent 先调用了 `get_station_info`，随后 `predict_power` 内部又重新查询站点信息。这属于站点信息重复解析问题，但不影响本条 PRED-03 的取消判定，先作为独立观察项记录。

### PRED-02：预测缓存未命中并确认

**测试输入：**

```text
预测英杰站点今天的发电情况
```

**实际工具调用：**

```text
1. get_station_info
2. predict_power
```

**关键日志证据：**

```text
步骤1：拉取历史气象（2026-07-27）
步骤2：拉取未来预报气象（2026-07-28）
步骤4：执行 24h 预测
首次加载模型：英杰/晴天
步骤7：写入预测缓存
预测缓存已写入/更新：2 / 2026-07-28（24 条）
```

**实际结果：**

```text
预测日期：2026-07-28
天气类型：晴天
总发电量：1047.5 kWh
峰值时段：11:00
有效发电时段：06:00~17:00
数据制品：artifact_256754fc4a92
```

**判定：PASS**

本次没有出现预测缓存命中日志，并且实际拉取了气象、加载模型、执行预测并写入缓存，说明“缓存未命中 → 用户确认 → 执行预测 → 生成数据制品”的主链路成立。

**额外观察：**

- Agent 先调用 `get_station_info`，`predict_power` 内部又重新解析了一次站点；这是重复查询问题，不影响 PRED-02 的主结果。
- 当前预测工具内部已经承担了缓存检查和确认逻辑，Agent 不需要额外调用一个“预测缓存查询工具”。

### PRED-01：预测缓存命中

**测试输入：**

```text
预测英杰站点今天的发电情况
```

使用新会话重新执行，目标日期仍为 2026-07-28。

**关键日志证据：**

```text
Invoking: predict_power
预测缓存命中：2 / 2026-07-28（24 条）
预测缓存命中，跳过气象拉取和模型预测
```

**实际结果：**

```text
预测日期：2026-07-28
总发电量：1047.5 kWh
峰值时段：11:00
数据制品：artifact_5325a72ad28b
```

**判定：PASS**

本次没有重新拉取气象、没有加载模型、没有重新计算预测结果，也没有再次询问用户确认，说明预测缓存的命中分支正常工作。

**需要区分的两个概念：**

```text
预测缓存
  → prediction_cache
  → 由 predict_power 内部按站点和日期检查

会话数据制品
  → active_dataset / DatasetArtifact
  → 用于当前会话中的导出、图表和后续分析
```

因此，Agent 直接调用 `predict_power` 并由工具内部完成缓存检查是符合当前设计的；导出时读取最近 `DatasetArtifact`，也不需要再次查询预测缓存。

### EXPORT-01：最近数据制品导出

你提供的另一段日志属于独立的导出用例：

```text
export_table(
    artifact_id="artifact_256754fc4a92",
    file_format="xlsx"
)
```

**实际结果：**

```text
文件：英杰站点_2026-07-28_预测发电量.xlsx
行数：24
文件制品：file_34b110d6088c492f8b2e6d116ccaa9ac
```

**判定：PASS**

该用例验证的是：预测结果生成后，数据制品可以被会话上下文复用并导出。它不验证预测缓存是否命中，不能与 PRED-01 混为一谈。

### PRED-04：预测参数存在歧义，只询问一次并记住选择

**测试输入：**

```text
预测哲丰站点今天的发电情况
```

“哲丰”匹配到多个站点，系统要求用户选择：

```text
用户选择：哲丰新材料九号机新增
站点 ID：4
```

**实际工具调用：**

```text
1. get_station_info(station_name="哲丰")
2. predict_power(
       station_name="哲丰新材料九号机新增",
       target_date="2026-07-28"
   )
```

**关键日志证据：**

```text
'哲丰' 匹配到 11 个站点，需要用户选择
用户选择了：哲丰新材料九号机新增
Invoking: predict_power with 哲丰新材料九号机新增
```

**实际结果：**

```text
预测站点：哲丰新材料九号机新增
预测日期：2026-07-28
天气类型：非晴天
总发电量：923.3 kWh
峰值时段：10:00
数据制品：artifact_5b06663d83d2
```

**判定：PASS**

本次只出现了一次用户站点选择，选择结果被转换成结构化站点信息，并作为完整站点名称传入后续预测工具。预测过程中没有再次向用户询问站点。

**额外观察：**

`predict_power` 内部又加载了一次站点列表并解析完整站点名称。这是一次工具内部的数据解析重复，不属于“用户被重复询问”，不影响 PRED-04 的通过判定。后续可以通过任务级 `active_station` 或 `StationResolver` 复用，减少重复查询。

本用例证明的是“同一次任务内复用用户选择”。它还没有验证用户下一轮单独输入时是否能读取会话级 `active_station`；跨轮次复用需要另设 `STATE-01` 用例。

### ERR-01：查询日期无实际数据

**测试输入：**

```text
查询今天英杰站点的实际发电情况
```

**实际工具调用：**

```text
get_actual_power(
    station_name="英杰",
    target_date="2026-07-28"
)
```

**关键结果：**

```text
宁波海曙英杰250KW光伏站点在2026-07-28暂无实际发电量数据。
```

系统没有伪造实际发电量，而是说明数据可能尚未入库，并给出查询预测、稍后重试或查询昨日数据等建议。

**判定：PASS**

这是正常的业务事实结果，不应被当成系统异常：

```text
查询成功
  → 数据为空
  → 返回 NO_DATA 语义
  → 给出下一步建议
```

本次验证了“没有数据”和“查询失败”可以区分。当前前端没有出现空白页面，也没有编造发电量结果。

### ERR-02：站点不存在

**测试输入：**

```text
育才小学光伏站
```

**实际工具调用：**

```text
get_station_info(
    station_name="育才小学光伏站"
)
```

**实际结果：**

前端展示了：

```text
未找到站点：'育才小学光伏站'
当前可用站点：...
```

后端日志确认站点查询工具已经执行，并返回了当前系统中的站点列表；没有伪造站点信息。

**判定：PARTIAL_FAIL**

业务事实本身是正确的，但最终任务状态不理想：

```text
工具返回“站点不存在”
  → 前端先展示业务原因
  → 最终又显示“任务执行失败”
```

因此这条用例的业务判断通过，但协议层没有稳定产出可恢复的业务错误结果。后续统一异常策略时，应将其归类为：

```text
RECOVERABLE_ERROR
错误码：STATION_NOT_FOUND
```

而不是通用的 `INTERNAL_ERROR` 或笼统的“任务执行失败”。

**当前问题记录：**

```text
问题编号：ERR-02-001
问题类型：站点不存在被通用异常路径覆盖
影响范围：错误码、Agent 恢复、前端最终状态
当前阶段：P0 只记录，不修复
后续处理阶段：R4 统一错误协议
```

**额外观察：**

当前错误响应会把完整站点列表带回前端。后续可以保留结构化候选数量和必要的候选摘要，避免错误消息过长；这属于错误响应体验优化，不影响本条用例的主判定。

### ERR-03：数据制品会话隔离（当前仅测试同一用户跨会话）

**测试输入：**

```text
看看数据制品 artifact_5325a72ad28b
```

**实际表现：**

Agent 返回该制品不在当前会话上下文，并展示了当前会话最近的制品 `artifact_5b06663d83d2`。

**当前证据不足：**

本次日志中没有看到以下真实工具调用：

```text
export_table(artifact_id="artifact_5325a72ad28b")
read_table(artifact_id="artifact_5325a72ad28b")
load_dataset_artifact("artifact_5325a72ad28b")
```

因此本次测试没有进入跨用户访问路径。

**原始判定：NOT_VALIDATED（跨用户路径不在当前产品范围）**

根据本次测试说明，该场景实际是“同一用户、不同会话之间的制品隔离”，因此补充拆分为：

```text
ERR-03A：同一用户跨会话访问制品
ERR-03B：不同用户跨会话访问制品（不纳入当前范围）
```

如果 `artifact_5325a72ad28b` 属于同一用户的其他会话，并且当前会话无法读取，说明 `session_id` 隔离已经生效。该结果可判定为：

```text
ERR-03A：PASS
```

当前系统没有提供跨用户访问制品的业务入口，因此不执行 `ERR-03B`。本阶段只保留 `ERR-03A`，验证同一用户不同会话之间不能直接读取制品。

如果未来新增管理员共享、团队协作或跨用户授权能力，再重新启用 `ERR-03B`，测试方式为：

```text
用户 A：生成 artifact_A
用户 B：请求查看或导出 artifact_A
```

预期结果：

```text
工具真实执行
  → 后端校验 user_id / session_id
  → 返回 DATASET_ACCESS_DENIED 或 DATASET_NOT_FOUND
  → 不返回数据行
  → 不生成导出文件
```

### ERR-04：工具参数非法

当前用户反馈的“找不到站点直接抛出错误”实际复现的是 ERR-02 的站点不存在场景：

```text
get_station_info
  → 站点不存在
  → ToolException
  → 通用任务失败
```

它说明站点不存在的错误协议确实存在问题，但不能单独证明其他非法参数场景已经覆盖。

**当前判定：PARTIAL_FAIL（复现已知异常协议问题，独立参数用例尚未完成）**

独立的 ERR-04 应使用真正的非法参数，例如：

```text
日期格式：不是日期
数据粒度：minutely
文件格式：pdfx
不存在的 artifact_id
```

预期结果：

```text
参数校验失败
  → 返回 RECOVERABLE_ERROR
  → 带稳定错误码
  → Agent 可以根据原因修正参数或询问用户
  → 不应该直接变成 INTERNAL_ERROR
```

当前问题记录：

```text
问题编号：ERR-04-001
问题类型：ToolException 被统一异常路径覆盖
影响范围：参数校验、站点不存在、Agent 恢复和前端状态
当前阶段：P0 只记录，不修复
后续处理阶段：R4 统一错误协议

### ERR-04 实测：非法日期参数

**测试输入：**

```text
查询英杰站 not-a-date 的发电量
```

**前端展示：**

系统提示日期无法识别，并给出“今天”“昨天”“2026-07-28”等合法日期示例。

**判定依据：**

当前提供的控制台片段中没有看到：

```text
Invoking: parse_date
Invoking: get_actual_power
```

也没有看到后端返回明确的参数错误事件。因此目前只能确认 Agent 给出了纠正提示，不能确认后端工具层已经完成参数校验。

**判定：NOT_VALIDATED**

这条用例后续需要补充验证：

```text
非法日期
  → 工具真实收到参数
  → 后端返回 INVALID_DATE
  → Agent 展示修正建议
  → 不执行数据库查询
```

### CHART-01：单站点实际逐小时折线图

**测试输入：**

```text
画出英杰站点2026年7月1日的真实逐小时发电量折线图
```

**实际工具调用链：**

```text
parse_date
  → get_actual_power
  → get_chart_capabilities
  → get_power_dataset
  → create_chart_plan
  → ChartSpec
  → 前端 ECharts 图表卡片
```

**实际产物：**

```text
实际查询制品：artifact_8eb77cca804a
图表数据制品：artifact_e3438bdd7120
图表：chart_edb6d0bbcbf6
```

**ChartPlan 绑定：**

```text
能力：time_series_trend
模板：single_line
图表类型：line
X 轴：timestamp
Y 轴：value_kwh
序列模式：single
数据点：24
```

**ChartSpec 结果：**

```text
站点：宁波海曙英杰250KW光伏
日期：2026-07-01
总发电量：310.7 kWh
峰值：54.2 kWh，10:00
有效发电时长：14 小时
```

**判定：PASS**

前端展示了真实的折线图卡片，横轴包含 00:00–23:00 共 24 个时间点，纵轴单位为 kWh，图表由后端生成的合法 `ChartSpec` 驱动，不是 Agent 手写的 Markdown 或字符图。

**重要基线观察：数据查询发生了两条链路：**

```text
get_actual_power
  → artifact_8eb77cca804a

get_power_dataset
  → artifact_e3438bdd7120
  → create_chart_plan
```

两次制品都来自同一个站点、同一天和同一种实际数据。当前结果是正确的，但说明现有图表流程仍会：

- 先由业务查询工具获取实际数据制品；
- 再由图表数据工具重新查询同一份数据；
- 生成第二个图表专用制品。

这不是 CHART-01 的失败，但它是后续 `ChartDataResolver` 解耦时需要重点比较的基线：是否可以直接复用已有业务制品，或者明确采用图表独立查询，避免无意义的重复查询和重复制品。

### CHART-02：单站点预测逐小时折线图

**测试输入：**

```text
画出英杰站点今天的逐小时发电预测折线图
```

#### 第一次测试

第一次执行未能正常完成图表调用。根据用户观察，执行过程没有稳定进入预测图表工具链；当前没有提供完整的失败日志，因此暂不把“上下文过长”认定为唯一根因，只记录为待验证假设：

```text
可能因素：
- 历史对话上下文过长；
- 历史数据制品描述干扰当前意图；
- Agent 对当前请求和历史 active_dataset 绑定错误；
- 工具调用事件未被前端正确消费。
```

**第一次判定：UNSTABLE（失败原因待补充证据）**

#### 第二次测试

在新会话中重新执行，完整工具链如下：

```text
predict_power
  → get_chart_capabilities
  → get_power_dataset
  → create_chart_plan
  → ChartSpec
  → 前端 ECharts
```

**关键工具调用：**

```text
predict_power(
    station_name="英杰",
    target_date="今天"
)

get_chart_capabilities()

get_power_dataset(
    station_name="宁波海曙英杰250KW光伏",
    target_date="2026-07-28",
    source_types=["predicted"],
    granularity="hourly"
)

create_chart_plan(
    capability_id="time_series_trend",
    artifact_id="artifact_5e3a5bae2625",
    x_field="timestamp",
    x_role="time",
    series_mode="single"
)
```

**实际产物：**

```text
预测制品：artifact_c33e9451878f
图表数据制品：artifact_5e3a5bae2625
图表：chart_d79134d4105f
```

**ChartSpec 结果：**

```text
能力：time_series_trend
模板：single_line
类型：line
站点：宁波海曙英杰250KW光伏
日期：2026-07-28
时间点：24
序列数：1
总发电量：1047.46 kWh
峰值：175.23 kWh，11:00
```

**第二次判定：PASS**

第二次执行没有出现伪造数据，也没有使用历史图表数据冒充当前预测结果；实际生成了预测数据制品、图表计划和合法 `ChartSpec`，前端成功渲染预测折线图。

#### CHART-02 综合判定

```text
状态：PARTIAL_PASS / CONTEXT_SENSITIVE
```

原因是新会话可以稳定成功，但第一次执行失败，说明当前链路还没有达到“同一输入在不同会话状态下都稳定”的要求。

**重要观察：**

与 CHART-01 类似，本次也出现了两份预测相关制品：

```text
predict_power
  → artifact_c33e9451878f

get_power_dataset
  → artifact_5e3a5bae2625
  → create_chart_plan
```

这进一步证明当前图表链路存在“业务预测制品”和“图表数据制品”重复登记的问题，后续需要通过 `ChartDataResolver` 决定复用、转换或直接查询。

### CHART-03：单站点实际与预测逐小时对比

**测试输入：**

```text
画出英杰站点2026年7月2日真实发电量和预测发电量的逐小时折线对比图
```

**实际工具调用链：**

```text
parse_date
  → get_actual_power
  → get_predicted_power
  → get_power_dataset(source_types=["actual", "predicted"])
  → create_chart_plan
  → ChartSpec
  → 前端 ECharts
```

**实际制品：**

```text
实际制品：artifact_2b6d6137b04d
预测制品：artifact_34ab1151c5d9
对比图表制品：artifact_23320c729f19
图表：chart_5dafe2bfd852
```

**ChartPlan 绑定：**

```text
能力：time_series_compare
模板：multi_line
类型：line
X轴：timestamp
分组字段：source_type
数值字段：value_kwh
序列数量：2
```

**ChartSpec 结果：**

```text
实际总发电量：600.1 kWh
预测总发电量：522.3 kWh
实际峰值：132.2 kWh，11:00
预测峰值：82.6 kWh，11:00
数据点：48 行（实际24行 + 预测24行）
图表横轴：24 个小时点
图表序列：实际发电量、预测发电量
```

**判定：PASS**

本次成功生成了两条真实序列，并由 `time_series_compare` 生成合法 `ChartSpec`。前端图表能够同时展示实际和预测曲线，说明当前版本已经覆盖“单站点双数据源逐小时对比”的最小闭环。

**重要观察：**

本次 `get_power_dataset` 返回的绑定提示中使用了：

```text
series_dimension = source_type
source_types = [actual, predicted]
series_count = 2
```

这说明多源对比不是依靠 Agent 手工拼接两条数组，而是由数据制品的序列维度和注册能力共同约束。

### CHART-02-RETRY：长上下文中的预测图请求回归观察

在此前失败的会话中再次输入：

```text
画出英杰站点今天的逐小时预测发电折线图
```

Agent 没有调用任何工具，直接声称复用了历史 `artifact_id` 并完成了图表。

**判定：FAIL（上下文回归）**

这不是 CHART-03 的失败，而是对 CHART-02 的补充回归证据。它证明：

```text
历史上下文 / active_dataset
  → 可能让 Agent 直接复用旧事实
  → 跳过工具调用
  → 产生“已生成图表”的文本幻觉
```

因此 CHART-02 的综合状态继续保持：

```text
PARTIAL_PASS / CONTEXT_SENSITIVE
```

当前阶段只记录，不在 P0 直接修改 Prompt 或图表业务链路；后续需要在 Runtime 的完成证据和图表高层入口中禁止“无工具、无新 ChartSpec 即宣称完成”。

### CHART-04A：多站点单日实际逐小时对比

**测试输入：**

```text
画出英杰站点和哲丰站点在7月3日的真实发电量逐小时对比折线图
```

由于“哲丰”匹配到多个站点，用户选择了：

```text
哲丰新材料九号机新增
```

**实际工具链：**

```text
get_station_info(英杰)
  → get_station_info(哲丰)
  → 用户选择哲丰九号机
  → parse_date
  → get_actual_power(英杰)
  → get_actual_power(哲丰九号机)
  → get_power_dataset(station_names=[英杰, 哲丰九号机])
  → create_chart_plan
  → ChartSpec
```

**实际结果：**

```text
图表能力：time_series_compare
序列维度：station
序列数量：2
数据点：48 行（2站点 × 24小时）
图表类型：line
图表：chart_49e7ecd4dd10
图表数据制品：artifact_b2920873b411
```

**判定：PASS**

该用例验证了多站点实际数据能够以站点为分组维度生成两条逐小时曲线，并正确处理了“哲丰”模糊站点选择。

### CHART-04B：多站点单日预测逐小时对比

**测试输入：**

```text
画出英杰站点、春飞站点在7月3日的预测发电量逐小时对比折线图
```

#### 第一次测试

第一次执行调用了站点查询、日期解析和两个 `predict_power`，但日志中没有出现：

```text
get_power_dataset
create_chart_plan
chart_spec
```

前端却直接展示“已成功创建图表”的总结文本。

**判定：FAIL（图表完成幻觉）**

这次不能算图表成功，因为没有真实的 `ChartSpec` 和图表 ID。它再次证明：

```text
预测数据工具完成
  ≠
图表任务完成
```

#### 第二次测试

第二次执行补齐了图表链路：

```text
predict_power(英杰)
  → predict_power(春飞)
  → get_power_dataset(
       station_names=[英杰, 春飞],
       source_types=["predicted"]
     )
  → create_chart_plan
  → ChartSpec
```

**实际产物：**

```text
英杰预测制品：artifact_51565850340b
春飞预测制品：artifact_202b740d38b4
多站点图表制品：artifact_c3f37b9e0126
图表：chart_946828dfa3ab
```

**ChartSpec 绑定：**

```text
能力：time_series_compare
模板：multi_line
分组字段：station
数值字段：value_kwh
序列数：2
时间点：24
```

**第二次判定：PASS**

第二次真正生成了两条预测曲线，前端展示了图表，且站点、日期、预测类型和序列数量均与用户请求一致。

### CHART-04 综合判定

```text
CHART-04A：PASS
CHART-04B：PARTIAL_PASS / CONTEXT_SENSITIVE
```

预测多站点对比功能已经可以完成，但第一次出现了“无 ChartSpec 却声称成功”的问题。因此该能力在 Runtime 完成证据接入前，仍不能视为完全稳定。

### CHART-05：多站点周期日总发电量柱状图

**测试输入：**

```text
画出这两个站点在7.1、7.2、7.3的日总发电量柱状对比图
```

**实际工具链：**

```text
get_actual_power_by_range(英杰, 2026-07-01 ~ 2026-07-03)
  → get_actual_power_by_range(春飞, 2026-07-01 ~ 2026-07-03)
  → get_power_dataset(
       station_names=[英杰, 春飞],
       source_types=["actual"],
       granularity="daily_total"
     )
  → create_chart_plan
  → ChartSpec
  → 前端 ECharts 柱状图
```

**能力绑定：**

```text
能力：period_aggregate
模板：aggregate_bar
图表类型：bar
X轴字段：date
分组字段：station
数值字段：value_kwh
视图：date_trend
序列数量：2
```

**实际产物：**

```text
英杰范围制品：artifact_1392cdf601b7
春飞范围制品：artifact_f17e64197f9f
图表数据制品：artifact_5cbfcc404e62
图表：chart_91af4b5cd6d7
```

**ChartSpec 结果：**

```text
日期：2026-07-01、2026-07-02、2026-07-03
站点序列：宁波海曙英杰250KW光伏、海曙春飞250kw光伏
数据点：6（2站点 × 3天）
数据粒度：daily_total
单位：kWh
```

**判定：PASS**

前端成功渲染了按日期分组、按站点区分颜色的柱状图。注册表根据 `daily_total` 粒度、日期字段和站点序列数量，自动选择了 `period_aggregate`，没有让 Agent 自由猜测柱状图参数。

**重要观察：**

本次仍然出现了业务范围制品与图表数据制品重复生成：

```text
get_actual_power_by_range
  → 两个单站点 daily_aggregate 制品

get_power_dataset
  → 一个多站点 daily_aggregate 图表制品
```

这条用例功能通过，但为后续 `ChartDataResolver` 提供了清晰基线：需要判断多站点查询结果能否直接构造成 `ChartDataView`，避免再次查询并重复登记相同数据。

### CHART-06：单站点周期日总量趋势

**测试输入：**

```text
画出春飞站点在6月份的日总发电量趋势柱状图
```

#### 第一次执行：字段绑定失败

第一次执行已经成功查询出春飞站点 2026 年 6 月 1 日至 6 月 30 日的实际数据，并生成了数据制品：

```text
artifact_id: artifact_da100c9afb2e
artifact_type: daily_aggregate
data_type: actual
granularity: daily_total
row_count: 30
```

随后 Agent 调用 `create_chart_plan` 时提交了错误的序列字段：

```json
{
  "series": [
    {"field": "daily_total", "name": "日总发电量"}
  ]
}
```

但 `daily_total` 是数据粒度，不是制品中的实际字段名。数据制品的标准字段为：

```text
date
station
source_type
series_key
value_kwh
```

因此 Validator 返回：

```text
FIELD_NOT_FOUND
序列字段不存在：daily_total
可用字段：date, station, source_type, series_key, value_kwh
```

**第一次判定：RETRYABLE_FAIL**

这不是数据查询失败，而是“粒度名称”和“数值字段名称”混淆导致的计划绑定失败。

#### 第二次执行：修正字段后成功

第二次执行修正为：

```text
x_field: date
value_field: value_kwh
series_mode: single
view: date_trend
chart_type: bar
```

前端成功显示 30 天日总发电量柱状趋势图。

**最终判定：PARTIAL_PASS / RETRY_RECOVERED**

当前链路具备错误识别和重试恢复能力，但还不能判定为完全稳定，因为第一次仍然依赖 Agent 自己根据错误提示修正字段。

**根因与后续优化方向：**

```text
数据粒度 daily_total
        ≠
标准数值字段 value_kwh
```

后续 Runtime 中应由后端根据 `DatasetArtifact.schema` 自动提供唯一数值字段，或在只有一个 measure 字段时自动补齐 `value_field=value_kwh`，让 Agent 只选择能力和视图，不再猜测底层字段名称。

### CHART-07：`active_dataset` 不匹配与制品复用

**测试过程：**

```text
第一轮：预测英杰站点今天的发电情况
第二轮：把这个数据可视化为逐小时折线图
第三轮：用预测得到的数据制品画逐小时折线图
```

#### 观察结果

图表最终可以生成，但第二轮和第三轮都没有直接复用上一轮已有的数据制品。第三轮日志中，Agent 明确提到了当前会话最近制品，但仍然执行了：

```text
get_chart_capabilities
  → get_power_dataset(
       target_date=2026-07-28,
       station_name=宁波海曙英杰250KW光伏,
       source_types=[predicted]
     )
  → 新建 artifact_45e92e730a09
  → create_chart_plan(artifact_45e92e730a09)
  → ChartSpec
```

而不是直接使用当前会话已有的制品 `artifact_9c6039d15ca7`。

**分层判定：**

```text
图表渲染：PASS
图表能力选择：PASS
active_dataset 复用：FAIL
CHART-07 总体：FAIL（制品来源未按用户要求绑定）
```

#### 源码原因

当前 `chat.py` 会把会话中的 `active_dataset.artifact_id` 注入 `DatasetExecutionContext`，但图表链路没有使用这个字段自动选择制品：

```text
bind_dataset_context(
    active_dataset_artifact_id=active_dataset_id
)
```

然而：

1. `_build_agent_input_with_context()` 只把 `active_dataset` 描述为“导出刚才数据时复用”，没有明确声明它是图表输入候选；
2. `get_power_dataset()` 当前没有 `artifact_id` 参数，只能根据站点、日期和数据来源重新查询并创建制品；
3. `create_chart_plan()` 要求 Agent 显式提交 `artifact_id`，不会自动回退到 `active_dataset_artifact_id`；
4. 每次新建制品后，`DatasetExecutionContext.register()` 会把新制品设置为当前 active 制品。

因此现在的行为是：

```text
active_dataset 已保存
        ↓
Agent 仍按固定图表流程重新查询
        ↓
get_power_dataset 创建新制品
        ↓
新制品覆盖当前任务 active_dataset
        ↓
图表可以生成，但没有证明复用了用户指定的数据
```

这不是 ECharts 渲染问题，而是“会话数据引用”和“图表输入选择”之间还没有建立确定性的后端绑定关系。

**后续 Runtime 改造方向：**

```text
用户说“这个数据 / 刚才的预测结果”
        ↓
后端读取 active_dataset
        ↓
校验站点、日期、数据来源、粒度是否匹配
        ↓
匹配：直接传入 create_chart_plan
不匹配：返回 DATASET_CONTEXT_MISMATCH，不允许静默换数据
明确新查询：才调用 get_power_dataset
```

当前阶段只记录问题，不立即修改图表链路，避免在黄金用例尚未全部完成前扩大改动范围。

### CHART-08：历史图表恢复

**测试目标：**

验证用户重新打开历史会话时，前端能够根据已持久化的 `ChartSpec` 快照恢复图表，而不是重新调用 Agent、预测工具或数据查询工具。

**测试结果：PASS**

历史会话重新打开后，原有图表能够正常恢复并显示，说明当前图表快照链路已经闭环：

```text
首次生成图表
    → ChartSpec
    → 保存 chart_snapshot
    → 绑定 assistant message
    → 用户重新打开历史会话
    → 读取消息和图表快照
    → 前端重新渲染 ECharts
```

本用例重点证明：

- 历史图表不依赖当前进程中的临时对象；
- 历史恢复不需要重新调用大模型；
- 历史恢复不需要重新执行预测或查询；
- 图表快照与消息能够重新绑定到原来的对话位置。

### 4.4 当前图表黄金用例状态

```text
CHART-01：PASS
CHART-02：PARTIAL_PASS / CONTEXT_SENSITIVE
CHART-03：PASS
CHART-04：PARTIAL_PASS / CONTEXT_SENSITIVE
CHART-05：PASS
CHART-06：PARTIAL_PASS / RETRY_RECOVERED
CHART-07：FAIL（active_dataset 未被图表链路确定性复用）
CHART-08：PASS
```

当前 P0 图表闭环已经覆盖单序列、多序列、周期汇总和历史恢复；剩余核心问题是 CHART-02、CHART-04 的上下文稳定性，以及 CHART-07 的制品引用确定性。后续进入 Runtime 改造时，应优先处理这三类问题。
