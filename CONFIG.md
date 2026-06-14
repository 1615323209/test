# 网文拆解系统 — 配置指南

> 请填写下方的 API Key 和模型选择，然后保存为 `.env` 文件即可使用。

---

## 方式一：使用 Anthropic 官方 API（推荐，质量最稳定）

```bash
# ============================================
# 提供商选择：anthropic
# ============================================
NOVEL_DECOMP_PROVIDER=anthropic

# 你的 Anthropic API Key
# 申请地址：https://console.anthropic.com/
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 主分析模型（用于章节分析和最终合成）
NOVEL_DECOMP_MODEL=claude-sonnet-4-6-20250514

# 廉价模型（用于次要任务）
NOVEL_DECOMP_CHEAP_MODEL=claude-haiku-4-5-20251001
```

### Anthropic 推荐模型组合

| 预算 | 主模型 | 廉价模型 | 预估总成本(300万字) |
|------|--------|----------|---------------------|
| 💰 经济 | `claude-haiku-4-5-20251001` | `claude-haiku-4-5-20251001` | ~$3 |
| ⭐ 推荐 | `claude-sonnet-4-6-20250514` | `claude-haiku-4-5-20251001` | ~$13 |
| 🏆 顶级 | `claude-opus-4-8-20250514` | `claude-sonnet-4-6-20250514` | ~$75 |

---

## 方式二：使用 DeepSeek API（性价比最高，便宜 8-10 倍）

> DeepSeek 使用 OpenAI SDK 通过原生端点 `https://api.deepseek.com` 调用。
> **注意：不是 `/anthropic` 端点。**

```bash
# ============================================
# 提供商选择：deepseek
# ============================================
NOVEL_DECOMP_PROVIDER=deepseek

# 你的 DeepSeek API Key
# 申请地址：https://platform.deepseek.com/
ANTHROPIC_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# DeepSeek 原生端点（OpenAI 格式）
ANTHROPIC_BASE_URL=https://api.deepseek.com

# 主分析模型
NOVEL_DECOMP_MODEL=deepseek-v4-pro

# 廉价模型
NOVEL_DECOMP_CHEAP_MODEL=deepseek-v4-flash
```

### DeepSeek 推荐模型组合

| 预算 | 主模型 | 廉价模型 | 预估总成本(300万字) |
|------|--------|----------|---------------------|
| 💰 极致省钱 | `deepseek-v4-flash` | `deepseek-v4-flash` | ~$0.50 |
| ⭐ 推荐 | `deepseek-v4-pro` | `deepseek-v4-flash` | ~$1.50 |
| 🚀 极速 | `deepseek-v4-flash` | `deepseek-v4-flash` | ~$0.50 |

---

## 填写完成后

将上方你选择的配置复制到 `.env` 文件中：

```bash
# 1. 复制模板
cp .env.example .env

# 2. 用编辑器打开 .env，粘贴你选择的配置
# 3. 验证
novel-decomp estimate -n 你的小说.txt
```

---

## 模型价格参考 (每百万 tokens)

### Anthropic
| 模型 | 输入价格 | 输出价格 |
|------|---------|---------|
| claude-opus-4-8 | $15.00 | $75.00 |
| claude-sonnet-4-6 | $3.00 | $15.00 |
| claude-haiku-4-5 | $0.80 | $4.00 |

### DeepSeek (OpenAI 格式端点)
| 模型 | 输入价格 | 输出价格 |
|------|---------|---------|
| deepseek-v4-pro | $0.47 | $1.10 |
| deepseek-v4-flash | $0.10 | $0.30 |
| deepseek-chat | $0.47 | $1.10 |

---

## 常见问题

**Q: 之前用 `/anthropic` 端点报 403 怎么办？**
A: 已修复。新版本对 DeepSeek 使用 OpenAI SDK 通过 `https://api.deepseek.com` 原生端点。请确保 `.env` 中：
- `NOVEL_DECOMP_PROVIDER=deepseek`
- `ANTHROPIC_BASE_URL=https://api.deepseek.com`（不是 `/anthropic`）

**Q: 可以混用吗？**
A: 每个 pipeline 运行使用单一提供商。切换需要修改 `.env` 中的 `NOVEL_DECOMP_PROVIDER`。

**Q: DeepSeek 支持结构化输出（function calling）吗？**
A: 支持。系统会自动将 Anthropic 格式的 tool schema 转换为 OpenAI function calling 格式，实现同样的结构化 JSON 输出。
