# 网文拆解系统 (Novel Decomposition System)

> AI 驱动的网文拆解工具：从几百万字的小说中自动提取完整大纲、角色档案、势力地图、地点图鉴、修炼体系。

---

## 目录

1. [系统架构](#系统架构)
2. [环境准备](#环境准备)
3. [配置 API Key 和模型](#配置-api-key-和模型)
4. [快速开始](#快速开始)
5. [命令详解](#命令详解)
6. [输出产物说明](#输出产物说明)
7. [核心机制](#核心机制)
8. [支持的小说格式](#支持的小说格式)
9. [常见问题](#常见问题)

---

## 系统架构

```
┌────────────────────────────────────────────────────────────┐
│                    你的小说.txt (几百万字)                    │
└────────────────────────┬───────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 1  预处理 (本地运行，不调用 API，秒级完成)                │
│                                                             │
│  ├─ 正则匹配 "第X章" 识别所有章节边界                          │
│  ├─ 清洗正文（去除广告、作者求票、多余空行）                     │
│  ├─ 检测缺失章节、异常长短章                                   │
│  └─ 按 ~20 章/批 打包（控制每批 token 数不超限）               │
│                                                             │
│  输入: 原始 .txt          输出: 1924 个 RawChapter + 97 个批次 │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 2  章节分析 (调用 AI，最耗时，~30-50 分钟)               │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ 滚动上下文机制 (Rolling Context)                       │    │
│  │                                                      │    │
│  │ Batch 1: [无前文] → AI分析 → 输出摘要A + 实体快照A      │    │
│  │ Batch 2: [摘要A + 实体快照A] → AI分析 → 输出B          │    │
│  │ Batch 3: [摘要A+B + 实体快照AB] → AI分析 → 输出C       │    │
│  │ ...                                                  │    │
│  │ 每 10 批压缩一次旧摘要，上下文始终 < 1500 tokens        │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  每批提取:                                                   │
│  ├─ 每章摘要（100-200字）                                    │
│  ├─ 关键事件（登场/退场/战斗/转折/修炼...）                    │
│  ├─ 角色（ID、别名、性格、能力、关系网）                       │
│  ├─ 势力（宗门/家族/国家、首领、成员）                         │
│  ├─ 地点（类型、所属区域、重要性）                             │
│  ├─ 功法（类别、等级、使用者、限制）                           │
│  ├─ 伏笔（描述 + 推测回收章节）                               │
│  └─ 矛盾标记（与前面章节不一致的地方）                         │
│                                                             │
│  输入: 97 个批次           输出: 97 个 batch_NNNN.json        │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 3  实体聚合 (本地运算 + 少量 AI 调用，~1 分钟)           │
│                                                             │
│  ├─ 汇总 97 个批次的全部实体数据                              │
│  ├─ 实体消歧（3 级匹配）:                                    │
│  │   ① 精确 ID 匹配         → "char_chenling" = "char_chenling" │
│  │   ② 名称/别名重叠匹配      → "陈伶" = "红衣" = "戏子"       │
│  │   ③ 二元组相似度检测       → 标记疑似的供人工审查            │
│  ├─ 矛盾检测: 角色死而复生？势力覆灭又出现？时间线混乱？         │
│  └─ 角色缺口分析: 哪些角色超过 50 章没出现？                   │
│                                                             │
│  输入: 97 个 batch JSON    输出: layer3_resolved.json         │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 4  全局合成 (本地运算，秒级完成)                         │
│                                                             │
│  ├─ 全书大纲: 章节摘要 → 卷级分段 → 层级大纲树                 │
│  ├─ 角色档案: 消歧后的实体 → 叙事性角色卡                      │
│  ├─ 势力档案: 势力时间线 + 兴衰史                             │
│  ├─ 地点图志: 地点层级 + 特征                                 │
│  ├─ 修炼体系: 功法/境界/道具分类                               │
│  └─ 剧情弧: 从章节标签聚类识别故事弧线                          │
│                                                             │
│  输入: Layer 3 消歧结果  输出: 7 份 Markdown + 1 份 JSON      │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                     最终产出物                                │
│                                                             │
│  data/output/reports/                                       │
│  ├── 00_全书概览.md        ← 完整大纲，按卷组织                │
│  ├── 01_角色大全.md        ← 所有角色档案 + 关系网             │
│  ├── 02_势力格局.md        ← 宗门/家族/国家的兴衰              │
│  ├── 03_地理图志.md        ← 世界观地图                       │
│  ├── 04_修炼体系.md        ← 境界/功法/法宝全录                │
│  ├── 05_剧情线分析.md      ← 故事弧线的起承转合                │
│  └── 06_矛盾与缺口.md      ← 检测到的吃书/角色缺席             │
│                                                             │
│  data/export/                                               │
│  └── human_review_sample_20chapters.md  ← 人工验证样本        │
└─────────────────────────────────────────────────────────────┘
```

---

## 环境准备

### 1. 安装 Python 依赖

```bash
# 进入项目目录
cd novel-decomp

# 以开发模式安装（修改代码后无需重新安装）
pip install -e .
```

### 2. 安装后的目录结构

```
novel-decomp/
├── CONFIG.md                 ← 📘 配置填写指南（推荐先看这个）
├── README.md                 ← 📘 本文件
├── pyproject.toml            ← 项目依赖声明
├── .env.example              ← 环境变量模板
├── .env                      ← 🔑 你的 API Key（需要自行创建）
│
├── novel_decomp/             ← 源代码
│   ├── config.py             ← 全局配置（提供商、模型、价格）
│   ├── anthropic_client.py   ← AI API 调用封装（重试、缓存、计价）
│   ├── models/               ← 数据模型（章节、角色、势力...）
│   │   ├── chapter.py
│   │   ├── entities.py
│   │   ├── plot.py
│   │   └── pipeline.py
│   ├── layer1/               ← 预处理：章节切分 + 批次打包
│   │   ├── extractor.py      ← 从 txt 中识别所有章节
│   │   └── batcher.py        ← 自适应批次构建
│   ├── layer2/               ← AI 分析：滚动上下文 + 工具调用
│   │   ├── prompt.py         ← 系统提示词 + 滚动上下文构建
│   │   ├── analyzer.py       ← 单批次 API 调用 + 结果解析
│   │   └── runner.py         ← 异步流水线运行器
│   ├── layer3/               ← 聚合：消歧 + 矛盾检测
│   │   ├── collator.py       ← 批次结果汇总
│   │   ├── resolver.py       ← 实体消歧（3 级匹配）
│   │   └── detector.py       ← 吃书检测 + 角色缺口分析
│   ├── layer4/               ← 合成：出最终产物
│   │   ├── outline.py        ← 全书大纲构建
│   │   ├── profiles.py       ← 角色/势力/地点/功法档案
│   │   └── arcs.py           ← 剧情弧识别
│   ├── export/               ← 导出
│   │   ├── markdown.py       ← Markdown 报告生成
│   │   └── sampling.py       ← 人工验证随机抽样
│   ├── pipeline/             ← 编排
│   │   ├── orchestrator.py   ← 4 层全流程调度
│   │   └── checkpoint.py     ← 断点续跑
│   ├── cache/
│   │   └── disk_cache.py     ← API 响应磁盘缓存
│   └── scripts/
│       └── cli.py            ← 命令行入口（run/estimate/resume/export/status）
│
└── data/                     ← 运行产物
    ├── cache/                ← API 响应缓存（避免重复调用）
    ├── checkpoint/           ← 断点续跑状态
    ├── output/               ← 最终输出
    │   ├── layer2/           ← 每批次分析结果 JSON
    │   ├── layer3_resolved.json
    │   ├── layer4_synthesis.json
    │   └── reports/          ← 可读的 Markdown 报告
    └── export/               ← 人工验证样本
```

---

## 配置 API Key 和模型

### 方式一：使用 Anthropic 官方 API（推荐，质量最稳定）

```bash
# 1. 进入项目目录
cd novel-decomp

# 2. 复制环境变量模板
cp .env.example .env

# 3. 编辑 .env 文件，填入以下内容：
```

```bash
# .env 文件内容：
# ============================================
# 提供商选择：anthropic
# ============================================
NOVEL_DECOMP_PROVIDER=anthropic

# 你的 Anthropic API Key
# 申请地址：https://console.anthropic.com/
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 主分析模型（用于章节分析）
NOVEL_DECOMP_MODEL=claude-sonnet-4-6-20250514

# 廉价模型（用于次要任务，如摘要压缩）
NOVEL_DECOMP_CHEAP_MODEL=claude-haiku-4-5-20251001
```

### 方式二：使用 DeepSeek API（性价比最高，便宜 8-10 倍）

```bash
# .env 文件内容：
# ============================================
# 提供商选择：deepseek
# ============================================
NOVEL_DECOMP_PROVIDER=deepseek

# 你的 DeepSeek API Key
# 申请地址：https://platform.deepseek.com/
ANTHROPIC_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# DeepSeek 的 Anthropic 兼容端点（必填）
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic

# 主分析模型
NOVEL_DECOMP_MODEL=deepseek-v4-pro

# 廉价模型
NOVEL_DECOMP_CHEAP_MODEL=deepseek-v4-flash
```

> 📘 更详细的配置说明见 **[CONFIG.md](CONFIG.md)**

### 模型组合推荐

| 预算 | 提供商 | 主模型 | 廉价模型 | 预估成本(300万字) |
|------|--------|--------|----------|-------------------|
| 💰 极致省钱 | DeepSeek | `deepseek-v4-flash` | `deepseek-v4-flash` | ~$0.50 |
| ⭐ 性价比 | DeepSeek | `deepseek-v4-pro` | `deepseek-v4-flash` | ~$1.50 |
| ✅ 推荐 | Anthropic | `claude-sonnet-4-6` | `claude-haiku-4-5` | ~$13 |
| 🏆 顶级质量 | Anthropic | `claude-opus-4-8` | `claude-sonnet-4-6` | ~$75 |

---

## 快速开始

### 第一步：估算成本（不花钱，先看要多少钱）

```bash
novel-decomp estimate -n "你的小说.txt"
```

输出示例：
```
=== 成本估算 ===
  提供商: deepseek
  模型: deepseek-v4-pro (廉价: deepseek-v4-flash)
  总章数: 1924 章, 97 批次
  预估总输入 tokens: 2,394,479
  预估总输出 tokens: 194,000

  Layer 2 (章节分析):        $1.34      ← 占成本大头
  Layer 3+4 (聚合+合成):     ~$0.22     ← 很便宜
  合计估算:              $1.56          ← 一本 300 万字小说
```

### 第二步：试跑（可选，先跑几章看看质量）

```bash
# 用 --dry-run 确认参数无误
novel-decomp run -n "你的小说.txt" --dry-run
```

### 第三步：正式运行

```bash
# 完整拆解一本小说
novel-decomp run -n "你的小说.txt" --sample-size 20
```

运行过程中终端会显示实时进度：
```
── Layer 1: 预处理 ──
  ✓ 1924 章
  ✓ 97 个批次

── Layer 2: 章节分析 ──
  Batch 1/97 [Ch 1-20] ... ✓
  Batch 2/97 [Ch 21-40] ... ✓
  ...

── Layer 3: 实体聚合 ──
  ✓ 角色消歧: 5234 → 847（合并了 4387 次）
  ✓ 检测到 3 处矛盾，12 个角色长期缺席

── Layer 4: 全局合成 ──
  ✓ 大纲: 19 卷 | 角色: 847 | 势力: 43 | 地点: 128 | 剧情弧: 24

── Export ──
  ✓ 7 份报告已生成
  ✓ 人工验证样本: 20 章
```

### 如果中途中断

```bash
# 按 Ctrl+C 中断后，直接 resume 继续（不会重复已完成的批次）
novel-decomp resume
```

### 查看结果

```bash
# 导出所有 Markdown 报告
novel-decomp export

# 导出人工验证样本（抽查 AI 分析质量）
novel-decomp export --human-review --sample-size 20

# 查看流水线状态
novel-decomp status
```

---

## 命令详解

### `novel-decomp run` — 运行完整拆解

```bash
novel-decomp run -n 小说.txt [选项]
```

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `-n, --novel` | (必填) | 小说 .txt 文件路径 |
| `-o, --output` | `data/output` | 输出目录：所有分析结果放哪 |
| `-b, --batch-size` | `20` | 每批处理多少章：越大越快但可能遗漏细节 |
| `-m, --model` | `.env` 里的值 | 覆盖主分析模型 |
| `--cheap-model` | `.env` 里的值 | 覆盖廉价模型（用于摘要压缩等轻量任务） |
| `-c, --concurrency` | `3` | 最大并发 API 调用数：越大越快但可能触发限流 |
| `-s, --sample-size` | `10` | 人工验证随机抽样章数 |
| `--dry-run` | `false` | 只估算不实际运行：看看要花多少钱 |
| `-v, --verbose` | `false` | 详细输出：出错时显示完整堆栈 |

### `novel-decomp estimate` — 只看成本，不调 API

```bash
novel-decomp estimate -n 小说.txt [-b 20] [-m 模型名]
```

根据章节数和模型价格估算 token 消耗和费用，**不产生任何 API 调用**。

### `novel-decomp resume` — 从中断处继续

```bash
novel-decomp resume [--checkpoint 检查点目录] [-n 小说路径] [-m 模型]
```

上次运行中断后（Ctrl+C、网络错误、API 限流等），从检查点恢复。已完成的分析批次会从缓存中直接读取，**不会重复调用 API 浪费钱**。

### `novel-decomp export` — 导出结果

```bash
# 导出全部 Markdown 报告
novel-decomp export

# 只导出人工验证样本
novel-decomp export --human-review -s 20
```

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `-o, --output` | `data/output` | 从哪里读取分析结果 |
| `-f, --format` | `both` | 输出格式：`markdown` / `json` / `both` |
| `-s, --sample-size` | `20` | 人工验证抽样章数 |
| `--human-review` | `false` | 是否导出人工验证样本 |

### `novel-decomp status` — 查看当前进度

```bash
novel-decomp status
```

输出当前流水线的运行状态：每个 Layer 完成到哪了、消耗了多少 token、花了多少钱。

---

## 输出产物说明

运行完成后，所有产物在 `data/output/` 下：

### `reports/` — Markdown 报告（推荐阅读）

| 文件 | 内容 | 用途 |
|------|------|------|
| `00_全书概览.md` | 卷级大纲 + 前 50 章逐章摘要 | 快速了解全书剧情走向 |
| `01_角色大全.md` | 所有角色档案（名字、别名、性格、关系网、出场章节、角色弧光） | 写同人 / 设定集 |
| `02_势力格局.md` | 所有势力的类型、首领、成员、同盟/敌对、重大事件时间线 | 理解世界观权力结构 |
| `03_地理图志.md` | 所有地点的类型、所属区域、关联势力、出场次数 | 画地图 / 世界观考据 |
| `04_修炼体系.md` | 所有功法/境界/能力的分类、等级、使用者、限制 | 理解战力系统 |
| `05_剧情线分析.md` | 按故事弧分段：每段的起止章、类型、高潮章、关键节拍 | 理解叙事结构 |
| `06_矛盾与缺口.md` | AI 检测到的作者"吃书" + 角色长期缺席 | 找 bug / 写作参考 |

### `layer4_synthesis.json` — 完整结构化数据

如果你需要二次开发，这个文件包含所有分析结果的机器可读格式。

### `export/human_review_sample_20chapters.md` — 人工验证

随机抽取 20 章，左边原文、右边 AI 分析，附带评分表。

---

## 核心机制

### 1. 滚动上下文 (Rolling Context)

```
原因：全书 200 万 token，远超 AI 单次上下文上限（20 万 token）
解决：分批处理 + 滚动传递摘要

实现：
  Batch 1    → AI 分析 → 输出 400 字摘要 + 实体快照
  Batch 2    → 输入 = 摘要1 + 实体快照1 + 第21-40章原文 → 输出
  Batch 3    → 输入 = 摘要1+2 + 实体快照汇总 + 第41-60章原文 → 输出
  ...
  Batch 10   → 触发压缩：旧摘要合并为"弧摘要"
  Batch 11   → 输入 = 弧摘要 + 最近3批摘要 + 实体快照 + 原文

上下文始终控制在 ~1500 tokens。
```

### 2. 实体消歧 (Entity Resolution)

```
问题：网文中同一个角色可能有 3-5 个称呼
  "陈伶" = "红衣" = "戏子" = "伶哥" = "那个疯子"

3 级消歧策略：
  ① 精确匹配    ID 相同 → 自动合并
  ② 别名匹配    一个的名字出现在另一个的 aliases 里 → 合并
  ③ 相似度检测   中文二元组 Jaccard 相似度 > 60% → 标记供人工审查
```

### 3. 断点续跑 + 缓存

```
每完成一个批次：
  ├─ 保存 batch_NNNN.json（分析结果）
  ├─ 更新 layer2_checkpoint.json（滚动上下文 + 实体快照）
  └─ SHA256 缓存 API 响应（相同输入不重复调用）

中断后 resume：
  ├─ 加载检查点 → 恢复滚动上下文
  ├─ 跳过已完成批次（从缓存读取）
  └─ 从断点继续调用 API
```

### 4. 结构化输出 (Tool Use)

```
不用自由文本，用 Anthropic 的 tool_use 强制 LLM 输出 JSON：
  ├─ 消除 JSON 解析错误
  ├─ 字段类型校验（Pydantic）
  └─ 缺失字段自动填默认值
```

---

## 支持的小说格式

### 目前支持

- 纯文本 `.txt` 文件（UTF-8 编码）
- 章节格式：`第N章 标题`

### 会自动处理

- 去除广告行（"求推荐"、"求收藏" 等）
- 去除作者 PS
- 识别完本感言/后记（标记为 afterword）
- 异常短章/长章告警
- 缺失章节检测

### 局限性

- 目前仅支持 `第X章` 格式。`Chapter X` 或 `第X卷 第Y章` 需要修改正则
- 提示词面向中文网文优化。英文小说能跑但提取质量可能下降
- 没有显式 "第X卷" 标记时，卷边界从内容自动推断（准确率约 70%）
- 单章超长（>20,000 字）会被单独放入一个批次

---

## 常见问题

### Q: 跑一半网络断了怎么办？
```bash
novel-decomp resume  # 从断点继续，已完成的不会重新调用 API
```

### Q: 想换模型重新跑怎么办？
修改 `.env` 后删除缓存：
```bash
rm -rf data/cache/*
novel-decomp run -n 小说.txt
```

### Q: DeepSeek 支持 tool-use 吗？
支持。DeepSeek 的 Anthropic 兼容端点 (`/anthropic`) 完整支持 tool_use。实测 `deepseek-v4-pro` 表现良好。

### Q: 分析质量怎么样？
建议跑完后用人工验证样本抽查：`novel-decomp export --human-review -s 20`

### Q: 能只跑其中一层吗？
可以，直接调用 Python 模块：
```bash
python -c "from novel_decomp.layer1.extractor import extract_chapters; ..."
```

### Q: 如何只分析新更新的章节（追更场景）？
目前需要重新跑完整流程。增量更新功能在计划中。

### Q: Claude Code 里能用吗？
可以。项目代码在本地，Claude Code 可以直接读取和修改。但运行建议在终端执行 `novel-decomp run`。
