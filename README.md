<p align="center">
  <img src="logo-large.png" alt="Virtual Life Logo" width="180" />
</p>

<h1 align="center">虚拟人生（Virtual Life）</h1>

<p align="center">为机器人维护连续的虚拟人生：长期时间表、每日日程、穿搭、当前状态与主动消息。</p>

> 喜欢本插件的朋友可以点一个 `Star⭐`，也欢迎在 Issues 里提出建议或反馈问题。也欢迎给插件提交 PR，帮助完善功能或修复问题。

> 推荐可联动插件 [Personal Network](https://cloud.astrbot.app/plugin/FloranceYeh/astrbot_plugin_personal_network)

> 欢迎加入群聊（1094990582）一起讨论插件使用、功能建议和问题反馈。也欢迎各方大佬交流学习LLM、Prompt、AstrBot开发。

## 功能

- **人格共享生活状态**：按 `persona_id` 管理数据，同一人格在不同私聊和群聊中共享连续一致的生活进度。
- **大时间表**：维护学期、假期、考试周、项目周期、冲刺和发布工期等长期阶段，支持固定安排、特殊日期、特殊时期、里程碑和重叠优先级；阶段通过管理员草稿批准后生效，并可自动续期。
- **每日日程与穿搭**：结合人格、大时间表、节假日、历史日程和临时要求生成 24 小时活动时间线、结构化穿搭、心情、可打扰度、主动分享窗口与消息预算，支持按部分重写。
- **主动消息**：只向已订阅的私聊或群聊发送消息，根据日程窗口、当前状态、可打扰度和每日预算决定发送或延后；连续无人回应会暂停，用户再次发言后恢复。
- **回复延迟与消息合并**：普通聊天按消息到达时的可打扰度公式计算等待时间，在框架请求 LLM 前合并同一会话的后续消息；时段结束、管理员手动结算或关闭功能时立即结算。
- **智能状态注入**：通过本地关键词按需向普通对话补充当前活动、地点、穿搭、日程和长期阶段摘要，不额外调用 LLM；模型也可通过 Tools 查询完整日程和大时间表。
- **日程要求**：管理员可通过命令或 LLM Tool 为未来单日或日期区间添加一次性生成要求，成功生成后按日期消费。
- **可视化查询**：日程时间轴、穿搭、大时间表草稿、阶段列表和详情均可渲染为图片，渲染失败时自动回退为文字。
- **安全边界**：主动消息使用独立的私聊、群聊 UMO 白名单；回复延迟公开提示不暴露具体活动和地点，内部状态只临时提供给 LLM。

- **与 Personal Network 联动**：推荐配合 [astrbot_plugin_personal_network](https://github.com/FloranceYeh/astrbot_plugin_personal_network) 使用。Virtual Life 负责让人格拥有连续的日程、状态和主动行为，Personal Network 负责保存这些生活中出现的人物、长期关系和共同经历；组合后，虚拟日程不再只围绕抽象活动生成，而可以延续人格已有的人际关系。

## 安装

### AstrBot 插件市场（推荐）

1. 打开 AstrBot 管理面板，进入插件市场。
2. 搜索“虚拟人生”。
3. 点击安装，完成后重载插件。
4. 在插件配置中选择日程模型，并按需配置主动消息会话白名单。

<details>
<summary><strong>手动安装（备用）</strong></summary>

可选择以下任一方式：

1. **控制面板安装**：在 AstrBot 插件管理页面选择通过仓库链接安装，输入 `https://github.com/FloranceYeh/astrbot_plugin_virtual_life`。
2. **Git 克隆**：在 AstrBot 的 `data/plugins` 目录执行：

   ```bash
   git clone https://github.com/FloranceYeh/astrbot_plugin_virtual_life.git
   ```

3. **下载插件**：下载仓库 ZIP 并解压到 `data/plugins/astrbot_plugin_virtual_life`。

完成后安装 `requirements.txt` 中的依赖，然后重载插件或重启 AstrBot。

</details>

完整版本历史见 `CHANGELOG.md`。

### 图片预览脚本

无需启动 AstrBot，可使用内置示例数据直接生成 PNG，脚本会优先使用本机 Chrome、Edge 或 Chromium：

```bash
python scripts/render_image_preview.py --view timeline
python scripts/render_image_preview.py --view outfit stage-list stage-detail --theme light
python scripts/render_image_preview.py --view all --output-dir preview_output
```

脚本依赖 `jinja2` 与 `playwright`；可通过 `--browser` 指定浏览器可执行文件，通过 `--width`、`--theme` 和 `--font` 调整预览样式。

## 配置

主动消息只对以下配置中的 UMO 生效：

- `friend_settings.session_list`
- `group_settings.session_list`

管理员可在目标会话直接使用 `/虚拟人生 订阅会话`，插件会自动识别私聊或群聊、启用对应主动消息类型并将当前 UMO 写入白名单。日程查询和普通聊天状态注入不要求会话进入主动消息白名单。

关键默认值（格式：字段名（配置界面名称）——说明）：

- `schedule_settings.generate_time`（生成时间）：每日生成时间，默认 `07:00`
- `schedule_settings.schedule_llm_provider`（日程模型）：通过 AstrBot 内置 Provider 选择器指定日程生成模型，留空使用当前供应商
- `schedule_settings.proactive_llm_provider`（主动消息模型）：通过 AstrBot 内置 Provider 选择器单独指定主动消息模型，留空使用当前供应商
- `schedule_settings.reference_history_days`（历史参考天数）：生成日程时参考的近期自然日天数，默认 `3`，设置为 `0` 可关闭
- `schedule_settings.history_days`（历史保留天数）：本地保留的日程历史天数，默认 `3`
- `schedule_settings.generation_retries`（生成重试次数）：生成结果校验失败后的额外重试次数，默认 `2`；每次重试只参考最近一次失败输出
- `prompt_settings`（提示词与模板）：集中管理所有生成提示词与模板，配置界面默认折叠
- `prompt_settings.generation_retry_prompt_template`（生成重试纠错模板）：生成纠错提示模板，可使用 `{mode}`、`{attempt}`、`{error}`、`{previous_output}`；上次输出最多注入 `12000` 个字符
- `smart_context_injection.enable`（启用智能注入）：普通聊天智能状态注入总开关，默认启用；不额外调用 LLM
- `smart_context_injection.base_module_enable`（注入基础状态）：是否始终注入当前基础状态，默认启用；关闭后仅在关键词命中时注入对应模块
- `smart_context_injection.max_chars`（注入长度上限）：注入内容总字符上限，默认 `1600`，可设置 `400-8000`
- `smart_context_injection.long_term_milestone_days`（里程碑窗口）：注入近期里程碑的未来天数，默认 `7`，可设置 `0-90`
- `smart_context_injection`（智能注入）直接读取配置中的 `outfit_keywords`（穿搭关键词）、`underwear_keywords`（内衣关键词）、`schedule_keywords`（日程关键词）、`long_term_keywords`（大时间表关键词）、`full_schedule_keywords`（完整日程关键词）和 `full_long_term_keywords`（完整大时间表关键词）；列表为空时对应模块不会触发
- `personal_network_integration.enable`（启用人际网络集成）：默认关闭
- `personal_network_integration.new_character_probability`（新人物生成概率）：每次生成今日日程时触发新虚构人物生成并写入关系网的概率，默认 `0`，可设置 `0-1`；人物生成后作为日程要求注入当日时间线
- `personal_network_integration.inject_current_participants`（注入当前时段参与人物）：当前日程时段含 `participant_ids` 时向智能注入查询并注入对应人物信息，默认启用
- `reply_delay_settings.enable`（启用回复延迟）：按消息到达时段的可打扰度延迟普通聊天，默认关闭；等待结束后才请求 LLM
- `reply_delay_settings.notify_user`（发送等待提示）：每个有延迟的消息批次发送一次公开等待提示，默认启用
- `reply_delay_settings.active_conversation_seconds`（连续对话免延迟窗口）：普通 LLM 回复成功发送后的免延迟窗口，默认 `300` 秒；设为 `0` 可关闭
- `reply_delay_settings.max_delay_seconds`（最长回复延迟）：全局最长回复延迟，默认 `1800` 秒；实际延迟不会超过当前日程时段结尾
- `reply_delay_settings.delay_formulas`（各可打扰度延迟公式）：分别配置 `blocked/low/normal/high` 的延迟秒数公式
- `prompt_settings.schedule_generation_system_prompt`（日程生成系统提示词）与 `prompt_settings.outfit_generation_system_prompt`（穿搭生成系统提示词）控制结构约束；完整日程使用 `prompt_settings.complete_generation_prompt_template`（完整生成提示模板），局部重写使用对应的 `schedule_prompt_template`（重写日程提示模板）或 `outfit_prompt_template`（重写穿搭提示模板）
- `friend_settings.daily_budget_min` / `daily_budget_max`（私聊日预算下限/上限）：随机预算 `1-3`，`friend_settings.llm_bonus_max`（LLM 额外预算）最多增加 `2`，`friend_settings.daily_hard_max`（日硬上限）为 `5`
- `group_settings.daily_budget_min` / `daily_budget_max`（群聊日预算下限/上限）：随机预算 `0-1`，`group_settings.llm_bonus_max`（LLM 额外预算）最多增加 `1`，`group_settings.daily_hard_max`（日硬上限）为 `2`
- `delivery_settings.max_unanswered`（未回复阈值）：连续未回复暂停阈值，默认 `3`
- `delivery_settings.sleep_exception_probability`（睡眠异常概率）：默认 `0.08`
- `delivery_settings.proactive_window_jitter_minutes`（主动窗口随机偏移）：日程主动窗口随机偏移 `15` 分钟，可设置为 `0-60`，`0` 表示关闭
- `delivery_settings.availability_probabilities`（可打扰程度触发概率）：普通主动消息按 `blocked/low/normal/high = 0%/25%/70%/100%` 的默认概率触发；窗口未命中时延迟到下一可打扰时段，并在意图中注明原定日程已结束
- `delivery_settings.segmented_reply_settings`（主动消息分段）：默认使用关键词模式，原文不超过 `words_count_threshold`（不分段字数阈值，默认 `150`）字符时按中英文句末标点与换行拆分，超过阈值时整条发送
- `delivery_settings.segmented_reply_settings.split_mode`（分段模式）：可切换为 `regex`
- `delivery_settings.segmented_reply_settings.enable_content_cleanup`（启用内容清理）拥有独立开关，`interval_method`（间隔计算方法）支持 `log` 和 `random`，默认按中英文字数使用 `log`
- `long_term_settings.renewal_retry_minutes`（续期重试间隔）：大时间表自动续期失败后默认每 `60` 分钟重试，`long_term_settings.renewal_max_attempts`（续期最大重试）最多 `6` 次
- `image_settings.image_render_enabled`（启用图片渲染）默认启用，`image_settings.image_theme`（图片主题）为 `dark`，`image_settings.image_width`（图片宽度）为 `1200`；可通过 `image_settings` 调整主题、宽度和字体

## 命令

### 虚拟人生

- `/虚拟人生 订阅会话`（管理员）：订阅当前私聊或群聊会话，保存白名单并立即安排今日日程中的主动消息

### 回复延迟

- `/回复延迟 开启`（管理员）：启用普通聊天回复延迟并保存配置
- `/回复延迟 关闭`（管理员）：关闭回复延迟、保存配置并立即结算所有会话中的等待批次
- `/回复延迟 结算`（管理员）：立即结算当前会话中的等待批次，不修改开关配置

### 每日日程

- `/虚拟日程 查看`：展示今日主题、心情、当前大时间段、今日特殊时间段与节日，并渲染高亮当前活动的 24 小时时间轴
- `/虚拟日程 穿搭`：渲染今日穿搭风格、主题、心情、造型概述和单品明细
- `/虚拟日程 要求 <日期或日期区间> <要求>`（管理员）：为当前人格未来日期的首次日程生成追加要求
- `/虚拟日程 重写 [补充要求]`（管理员）：重新生成主题、心情、穿搭和时间日程
- `/虚拟日程 重写日程 [补充要求]`（管理员）：保留主题、心情和穿搭，仅重写时间线、主动窗口与消息预算
- `/虚拟日程 重写穿搭 [补充要求]`（管理员）：保留主题、心情和时间日程，仅重写穿搭；补充要求中可指定风格池里的风格

### 大时间表

- `/大时间表 生成 [自然语言要求]`：结合人格与前一阶段生成追加草稿
- `/大时间表 导入 <JSON>`：将结构化阶段保存为替换全部阶段的草稿
- `/大时间表 草稿`：按阶段渲染当前人格待批准草稿
- `/大时间表 批准`：批准草稿、记录通知会话并重生成今日日程
- `/大时间表 拒绝 [修改意见]`：删除草稿；提供意见时自动重新生成草稿
- `/大时间表 列表`：渲染当前人格已批准阶段总览
- `/大时间表 查看 [阶段ID或名称]`：省略参数时查看当前或最近阶段，也支持唯一的部分 ID、部分名称匹配
- `/大时间表 重生成 [要求]`：生成替换全部阶段的草稿

### 主动消息

- `/主动消息 状态`
- `/主动消息 立即`（管理员）
- `/主动消息 执行时间`：列出当前会话全部日程窗口、延迟窗口、沉默主动和睡眠异常的具体执行时间

## LLM Tools

- `get_virtual_daily_schedule`
- `get_long_term_timeline`
- `add_schedule_requirement`

日程要求按当前人格共享，只允许管理员通过命令或 LLM Tool 添加。命令日期支持 `YYYY-MM-DD`、`MM-DD` 和 `DD`，也可用 `..` 表示首尾均包含的区间，例如 `/虚拟日程 要求 7-29..8-2 减少远途活动`。省略年份时使用今年，省略月份时使用本月；右端省略部分且早于左端时自动推导到下一月或下一年，例如 `29..2` 表示本月 29 日至下月 2 日。只接受今天之后的日期，区间最长 366 天；每条最多 500 字，同一人格的任一日期最多叠加 10 条。

区间内每个日期首次成功生成并保存日程后，只消费该日期对应的一次使用机会；生成失败时保留，成功后的当天重写不会再次应用。整个区间过期后，未消费的要求会自动清理。`add_schedule_requirement` 仅可在当前管理员明确提出要求时调用，参数为 `start_date`、`end_date` 和 `requirement`。

## 普通回复延迟

启用 `reply_delay_settings` 后，普通私聊和群聊会按第一条消息到达时生效的日程项计算回复延迟。排队和等待发生在 AstrBot 构建请求及获取会话锁之前，延迟结束后才会构建并请求 LLM。命令、主动消息、日程生成以及其他插件显式构造的 LLM 请求不参与延迟。当天没有内存中的有效日程时直接放行，避免为了计算延迟而提前请求日程生成 LLM。

每个 UMO 独立维护消息队列和固定截止时间。第一条消息创建批次并计算一次延迟；截止时间前到达的后续消息只进入同一队列，不重新计算或延长延迟。截止时刻原子地摘取整批消息并合并为一个用户轮次，只发起一次 LLM 请求。群聊按整个群合并，并保留成员和消息时间；图片与音频附件也会并入最终请求。截止时刻之后到达的消息进入新批次，同一会话的 LLM 请求串行执行。

四档默认公式的结果单位均为秒：

```text
high:    0
normal:  random(5, 30)
low:     random(60, 300)
blocked: probability(0.2, random(300, 1200), remaining)
```

公式字段：

| 字段 | 内容 |
| --- | --- |
| `remaining` | 第一条消息到达时，距离当前日程时段结尾的完整剩余秒数 |
| `message_length` | 第一条消息的纯文本字符数，最小为 `0` |

公式函数：

| 函数 | 内容 |
| --- | --- |
| `random(low, high)` | 在 `low` 到 `high` 之间均匀随机取值，`high` 不得小于 `low` |
| `probability(p, hit_value[, miss_value])` | 以 `0-1` 的概率 `p` 返回 `hit_value`；未命中返回 `miss_value`，省略时返回 `0`。例如 `probability(0.3, random(30, 90), remaining)` 表示 30% 概率延迟 30-90 秒，70% 概率等待到当前时段结束；最终仍受 `remaining` 和 `max_delay_seconds` 截断 |
| `min(a, ...)` / `max(a, ...)` | 返回参数中的最小值或最大值，至少需要一个参数 |
| `round(value[, digits])` | 按 Python `round` 规则舍入，正好位于中间时取最近偶数；`digits` 省略时为 `0` |
| `ceil(value)` / `floor(value)` | 向上取整或向下取整 |

运算符支持 `+ - * /`、一元正负号和括号。表达式由受限解析器计算，不支持属性访问、关键字参数或执行任意代码；缺失或非法公式按 `0` 处理，不使用代码内置回退值。公式结果先限制为非负数并向上取整，再按 `min(公式结果, remaining, max_delay_seconds)` 截断，因此时段结束时立即结算。最终延迟为 `0` 时直接放行，不创建延迟批次、不输出回复延迟日志，也不向 LLM 注入回复延迟上下文。

有实际延迟时，插件按 `notification_template` 和 `public_reasons` 发送一次不经过 LLM 的提示；公开模板只能引用 `{delay_seconds}`、`{public_reason}` 和 `{availability}`，不会自动暴露具体活动或地点。结算时会把消息到达时与当前的具体日程状态、计划及实际等待时间作为临时上下文提供给 LLM，并要求模型保持角色一致且不主动披露内部细节。

普通 LLM 回复成功发送后，会开启默认 `300` 秒的连续对话窗口；窗口内的新批次延迟为 `0`，每次成功回复后重新计时，沉寂超时后恢复公式延迟。等待批次和连续对话窗口只保存在内存中，插件重启后不会恢复。

管理员可使用 `/回复延迟 开启|关闭|结算` 控制运行状态。关闭会立即结算全部会话中已经等待的批次；结算只立即处理命令所在会话的当前批次，不会延长截止时间，也不会修改配置。

## 与 Personal Network 联动

推荐配合 [astrbot_plugin_personal_network](https://cloud.astrbot.app/plugin/FloranceYeh/astrbot_plugin_personal_network) 使用。Virtual Life 负责让人格拥有连续的日程、状态和主动行为，Personal Network 负责保存这些生活中出现的人物、长期关系和共同经历；组合后，虚拟日程不再只围绕抽象活动生成，而可以延续人格已有的人际关系。

在配置中展开“人际网络集成”并开启“启用人际网络集成”后：

- 每日虚拟日程生成会读取当前人格的关系网，并要求模型在明确涉及已知人物时写入稳定的 `participant_ids`。
- 主动消息生成会读取相同的关系上下文，避免脱离已有关系和近期共同经历。
- 已经结束且带有明确参与人物的日程项会幂等回写为 Personal Network 人生经历。

### 新人物自动生成

配置 `new_character_probability`（默认 `0`，范围 `0-1`）后，每次生成今日日程都会以该概率触发一次新虚构人物生成：插件调用 LLM 生成人物姓名、简介、性格、喜好、长期事实以及与人格的关系类型和关系简述，通过 `upsert_batch_for_plugin` 将人物和一段强度为 `30` 的 active 关系写入 Personal Network，并把“今天应与该人物相遇”的会面要求作为日程要求注入本次生成，使其自然出现在当天时间线中，同时写入对应的 `participant_ids`。人物生成失败、写入被拒绝或兼容插件不可用时会安全跳过，不影响日程生成。

### 当前时段参与人物注入

`inject_current_participants`（默认 `true`）开启后，智能状态注入会在当前日程时段含有 `participant_ids` 时，自动查询这些人物并注入姓名、简介和性格信息，帮助模型在聊天中持续感知正在互动的对象；关闭后仅注入其他状态模块。

该功能默认关闭。Virtual Life 不会静态导入 Personal Network；未安装、未启用或版本不兼容时会记录一次提示并安全跳过，其他日程、主动消息和回复延迟功能保持正常。

## 智能状态注入

启用 `smart_context_injection` 后，插件根据本地关键词注入需要的摘要，不额外请求 LLM。默认始终注入基础状态（当前时间、活动、地点、状态和可打扰程度）；关闭 `base_module_enable` 后仅在关键词命中时注入对应模块。启用 Personal Network 集成时，若当前日程时段含 `participant_ids`，还会注入当前时段的参与人物信息：

- 穿搭词追加外显穿搭；只有明确内衣、内裤、打底、贴身等词才追加内衣、内裤与打底信息。
- 日程词追加当前时间、活动、地点、状态、可打扰程度、主题、心情、当前时段和下一项活动。
- 上课、考试、项目、工期等词追加当前阶段、固定事件、特殊时期、约束和近期里程碑。
- 完整日程、校历、工期表等请求会提示模型优先调用 `get_virtual_daily_schedule` 或 `get_long_term_timeline`，而不是根据摘要虚构完整内容。

## 大时间表结构示例

以下 JSON 可通过 `/大时间表 导入 <JSON>` 导入为草稿。所有输入来源都必须执行 `/大时间表 批准` 后才会生效。

```json
{
  "stages": [
    {
      "id": "semester-2026-fall",
      "name": "2026 秋季学期",
      "kind": "academic",
      "start_date": "2026-09-01",
      "end_date": "2027-01-20",
      "priority": 10,
      "summary": "正常上课并准备期末考试",
      "weekly_rules": [
        {
          "weekdays": [1, 3],
          "start": "08:00",
          "end": "10:00",
          "title": "专业课",
          "location": "教学楼",
          "participants": ["同学"],
          "required": true
        }
      ],
      "special_dates": [
        {
          "date": "2026-09-01",
          "start": "08:00",
          "end": "11:00",
          "title": "开学典礼",
          "location": "礼堂",
          "participants": ["全体新生"],
          "required": true
        }
      ],
      "special_periods": [
        {
          "name": "期末周",
          "start_date": "2027-01-10",
          "end_date": "2027-01-20",
          "constraints": ["减少娱乐活动", "优先复习和考试"]
        }
      ],
      "milestones": [
        {"date": "2027-01-15", "title": "专业课期末考试", "lead_days": 7}
      ],
      "constraints": ["工作日保持学生作息"]
    }
  ]
}
```

上班族工期使用相同结构，将 `kind` 设置为 `project`，并用 `special_periods` 表示冲刺期、联调期或发布期，用 `milestones` 表示交付和上线日期。

## 数据

插件数据目录包含：

- `plans.json`：人格日程及按人格保存的待消费日程要求
- `sessions.json`：会话预算、未回复与睡眠抽签状态
- `long_term_timelines.json`：按人格保存已批准阶段、待批准草稿和管理员通知会话

文件使用异步锁和临时文件替换写入。日程生成不读取任何具体会话历史；只有为目标会话生成最终主动消息时才读取该会话最近记录。主动消息成功发送后会以系统触发说明和 assistant 正文写入当前 AstrBot 对话历史，使后续对话能够感知已发送内容；没有当前对话时会自动创建。

生成新日程时会按 `schedule_settings.reference_history_days` 读取同一人格之前若干自然日的有效日程，帮助模型保持生活连续性并减少主题、穿搭和活动组合重复。不同人格的历史不会互相引用。

大时间表生成只读取当前人格设定、前一阶段和管理员要求，不读取任何具体会话历史。特殊日期会覆盖同时间的周期安排；重叠阶段按优先级、范围长度和录入顺序选择；无法消解的固定事件冲突会阻止日程生成。 中国节日日历在运行时按阶段日期范围自动计算，不写入或修改已批准阶段 JSON。

## Token 预算

> **单次请求估算（样本）**：普通聊天的智能注入会增加约 `64-542` Token 上下文；完整日程首轮生成约 `2,200-2,500` Token（启用人际网络集成时）；局部重写约 `1,050-1,700` Token；每次大时间表生成约 `900-1,400` Token；命中新人物生成概率时额外增加约 `550` Token。

| 操作 | 单次请求估算区间 | 说明 |
| --- | ---: | --- |
| 普通聊天智能注入 | 64-542 输入 Token | 基础状态默认始终注入；关闭 `base_module_enable` 且未命中关键词时为 0。 |
| 完整虚拟日程首轮生成 | 2,200-2,500 Token | 约 1,730-1,900 输入 + 约 450-600 输出；输入已含启用人际网络时的关系上下文。 |
| 新人物自动生成（附加） | 约 550 Token | 命中 `new_character_probability` 时单独调用一次 LLM，约 406 输入 + 约 148 输出。 |
| 重写日程 | 1,300-1,700 Token | 约 1,200 输入 + 约 200 输出；旧日程、穿搭和长期约束越详细，输入越高。 |
| 重写穿搭 | 1,050-1,250 Token | 约 860 输入 + 约 200 输出；保留的时间线和穿搭越详细，输入越高。 |
| 大时间表生成 | 900-1,400 Token | 约 690 输入 + 约 230-670 输出（1-3 个阶段）。 |

以下数据使用 `tiktoken` `0.13.0` 的 `cl100k_base` 编码器，于 `2026-07-31` 按当前默认提示词测得。日程样本使用一个简短学生人格、三天历史日程，以及一个包含固定事件、特殊时期和里程碑的当前阶段；历史中最近一天保留完整时间线，较早两天使用摘要；输出样本含 6 个时间线项目、1 个主动窗口和 6 个穿搭单品；参与人物注入样本为 2 人。它们用于容量规划，不是账单承诺：实际用量会随 Provider 使用的 tokenizer、人格长度、管理员要求、关系网规模、日程内容及模型输出而变化。

### 智能状态注入

智能注入不额外调用 LLM；这些 Token 会作为普通聊天上下文的一部分。基础模块默认开启，此时至少注入 `64` Token；关闭 `base_module_enable` 且未命中关键词时增加 `0` Token。下表按开启基础模块计算；命中日程时基础状态不会重复注入。样本基础状态为 `64` Token，全部模块同时命中时为 `452` Token，命中当前时段参与人物时最高约 `542` Token，并受 `smart_context_injection.max_chars` 限制。

| 命中内容 | 总输入 Token | 相对基础状态增加 |
| --- | ---: | ---: |
| 仅基础状态 | 64 | 0 |
| 外显穿搭 | 173 | 109 |
| 明确询问内衣或打底 | 128 | 64 |
| 今日日程摘要 | 127 | 63 |
| 当前大时间表摘要 | 189 | 125 |
| 请求完整日程并提示调用 Tool | 163 | 99 |
| 请求完整大时间表并提示调用 Tool | 100 | 36 |
| 当前时段参与人物（2 人） | 154 | 90 |
| 全部模块同时命中（不含参与人物） | 452 | 388 |
| 全部模块 + 当前时段参与人物 | 542 | 478 |

完整日程和完整大时间表请求只注入 Tool 调用约束，不会把完整 JSON 直接塞进上下文；模型分别通过 `get_virtual_daily_schedule` 与 `get_long_term_timeline` 查询。启用 `inject_current_participants` 且当前时段含参与人物时，会额外注入这些人物每人一行（姓名、简介、性格），字符数随人数增长并受 `smart_context_injection.max_chars` 总上限约束。

### 每日日程生成

完整日程会合并日程和穿搭约束，并通过单一模板发送一次共享上下文。默认样本中，系统提示约 `971` Token，用户提示总计约 `762` Token，其中三天历史约 `347` Token、当前大时间表上下文约 `212` Token；典型结构化日程输出约 `500-600` Token。因此可按下式估算：

```text
日程输入 ≈ 系统提示 971 + 用户基础/人格/创意约束 203
         + 历史日程（样本 3 天为 347）
         + 大时间表上下文（样本为 212）
         + 关系上下文（启用人际网络集成时约 166）
         + 管理员补充要求
日程输出 ≈ 500-600（随时间线项目、主动窗口和穿搭条目数量变化）
```

### 新人物自动生成

命中 `new_character_probability` 时，日程生成之外还会单独调用一次 LLM 生成新虚构人物。默认样本中，系统提示约 `273` Token、用户提示约 `133` Token，合计输入约 `406` Token；典型人物 JSON 输出约 `148` Token。该请求只使用日程模型，且只在启用 Personal Network 集成、概率命中且人物写入成功时发生。

### 大时间表生成

默认样本中，系统提示约 `523` Token，用户提示（人格、前一阶段和管理员要求）约 `169` Token；生成一个包含周期事件、特殊日期、特殊时期和里程碑的阶段约 `228` Token，三个同等复杂度阶段约 `670` Token。因此可按下式估算：

```text
大时间表输入 ≈ 系统提示 523 + 人格/前一阶段/管理员要求 169
大时间表输出 ≈ 228 × 阶段数量（事件、特殊时期与里程碑会额外增加）
```

以上日程估算仅覆盖首轮请求。默认最多额外重试 `2` 次；每次重试都会重新发送基础提示，并额外附带上一轮无效输出和纠错提示，因此应按实际重试次数单独累加。Provider 的实际计费 Token 以其响应中的 usage 为准；不同 tokenizer 下，中文、JSON 标点和字段名的分词结果都可能与本表不同。
