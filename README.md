# 🍳 AI私厨管家（AI Private Chef Butler）

> 拍一张冰箱或厨房的照片，AI 自动识别食材、联网搜索真实菜谱，再按「营养价值」与「制作难度」排序推荐给你。

一个 **多模态大模型 + 联网搜索 + 持久化记忆** 的完整应用示例，带 Streamlit Web 界面。

---

## ✨ 功能特性

| 功能 | 说明 |
| --- | --- |
| 📷 **图片识别食材** | 上传 / 拍照 / 剪贴板读图 / 粘贴图片网址，四条路都能进；识别不到食材会**直说**，绝不编造 |
| 🖼️ **多图暂存** | 可以一次攒多张图再一起发送，每张图都有**独立删除按钮**，发错了不用全部重来 |
| 🔍 **联网搜真实菜谱** | 用 Tavily 搜索下厨房等中文菜谱站，**带真实来源链接**，不靠模型记忆编菜谱 |
| 📊 **智能排序** | 按营养评级（A/B/C）+ 难度星级（1~5）+ 食材齐全度算综合分排序 |
| 💡 **创意建议兜底** | 搜不到合适菜谱时，改搜"食材搭配灵感"，基于现有食材给创意做法 |
| 💬 **对话式交互** | 支持多轮追问（"那我再加点土豆呢？"），带上下文记忆 |
| 🗂️ **聊天记录侧边栏** | 会话列表：新建 / 切换 / 重命名 / 删除，**关掉浏览器再打开还能接着聊** |
| 🤖 **模型随时切** | 界面上两个下拉框切换文字模型与视觉模型，改代码清单界面自动同步 |
| ⬇️ **滚动到最新** | 向上翻历史时右下角浮现「↓」按钮，带未读数，点一下回到最新消息 |
| 🛡️ **稳健降级** | 数据库没启动、搜索通道抽风、模型不可用——都会明确提示并降级，不白屏 |

---

## 🏗️ 架构

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            用户（浏览器）                                  │
│                    http://localhost:8501  (Streamlit)                     │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        app.py · Web UI 层                                 │
│  • 会话侧边栏（列表/新建/切换/重命名/删除）   • 模型切换下拉框              │
│  • 四条图片入口（上传/摄像头/剪贴板/图片网址） • 菜谱卡片渲染              │
│  • 多图暂存 + 单图删除                        • 配色集中在 THEME 字典       │
└───────┬──────────────────────────────────────────────────────┬───────────┘
        │                                                      │
        ▼                                                      ▼
┌───────────────────────────────┐            ┌──────────────────────────────┐
│   utils/ · 工具层              │            │   core/history.py            │
│  • image_handler  图片→Base64  │            │  会话元信息 CRUD              │
│    （魔数识别、SSRF 防护）      │            │  （标题/置顶/软删除）          │
│  • image_input    剪贴板探测   │            └──────────────┬───────────────┘
│    URL 识别、下载缓存          │                           │
│  • recipe_parser  回答→卡片    │                           │
│  • img_codec      零依赖编解码 │                           │
│  • scroll_button  「↓」悬浮按钮│                           │
└───────────────┬───────────────┘                           │
                │                                           │
                ▼                                           │
┌───────────────────────────────────────────────────────────┴──────────────┐
│                       core/ · 核心层                                      │
│                                                                          │
│  ┌────────────────────┐   ┌──────────────────────┐   ┌────────────────┐ │
│  │ model_settings.py  │──▶│ llm_models.py        │   │ agent.py       │ │
│  │ ★ 模型清单（唯一）  │   │ 两条通道 + 真实探测   │──▶│ create_agent   │ │
│  │ 换模型只改这里      │   │ + 自动降级 + 密钥脱敏 │   │ + 系统提示词    │ │
│  └────────────────────┘   └──────────┬───────────┘   └───────┬────────┘ │
│                                      │                       │          │
│                                      ▼                       ▼          │
│                       ┌──────────────────────┐   ┌────────────────────┐ │
│                       │ 阿里云百炼（OpenAI   │   │ memory.py          │ │
│                       │ 协议"瞒天过海"）      │   │ PostgreSQL 记忆     │ │
│                       │ DeepSeek 官方（原生） │   │ + 优雅降级          │ │
│                       └──────────────────────┘   └─────────┬──────────┘ │
└────────────────────────────────────────────────────────────┼────────────┘
                                                             │
                        ┌────────────────────────────────────┘
                        ▼
        ┌──────────────────────────────────────────────────────┐
        │  tools/search_tool.py · 联网搜索                      │
        │  首选 langchain-tavily（TavilySearch）                │
        │  失败自动降级到标准库 HTTPS（环境问题时的兜底）         │
        │  限定中文菜谱站；没命中自动放宽全网                    │
        └──────────────────────┬───────────────────────────────┘
                               ▼
                    ┌──────────────────────┐
                    │   Tavily Search API  │
                    └──────────────────────┘

        ┌──────────────────────────────────────────────────────┐
        │  schemas/food_schemas.py · 数据层                     │
        │  Pydantic 模型（食材/食谱/推荐清单）+ 脏数据清洗       │
        │  + 排序算法 + 假链接拦截                              │
        └──────────────────────────────────────────────────────┘
```

### 两条模型通道（关键设计）

| | 阿里云百炼通道 | DeepSeek 官方通道 |
| --- | --- | --- |
| LangChain 原生支持 | ❌ 不支持 | ✅ 原生支持 |
| 接入方式 | 借用 OpenAI 协议：`provider="openai"` + `base_url` | 只给 `api_key`，**不需要 base_url** |
| 环境变量 | `DASHSCOPE_BASE_URL` + `DASHSCOPE_API_KEY` | 只要 `DEEPSEEK_API_KEY` |
| 依赖的包 | `langchain-openai` | `langchain-deepseek` |

候选模型会**真实探测**（发一次请求确认能不能用；视觉模型必须真的答对颜色才算通过），
第一个可用的自动生效，不可用就降级到下一个并在界面说明原因。

---

## 🚀 快速开始

### 1. 准备环境

需要 Python 3.11+。

```bash
git clone <你的仓库地址>
cd <仓库目录>

# 建议用虚拟环境（Windows）
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
# python3 -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt
```

> 国内网络如果 `pypi.org` 解析失败，用镜像：
> `pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/`

### 2. 配置环境变量

```bash
# Windows PowerShell
Copy-Item .env.example .env

# macOS / Linux
cp .env.example .env
```

然后编辑**项目根目录**的 `.env`，把占位符换成你自己的 Key：

```ini
DASHSCOPE_API_KEY=sk-你的阿里云百炼Key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DEEPSEEK_API_KEY=sk-你的DeepSeekKey
TAVILY_API_KEY=tvly-你的TavilyKey
DB_URI=postgresql://postgres:postgres@localhost:15432/postgres?sslmode=disable
```

各 Key 的申请入口：

| 变量 | 申请地址 | 备注 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | https://bailian.console.aliyun.com/ | Qwen / Kimi / GLM 等模型，新用户有免费额度 |
| `DEEPSEEK_API_KEY` | https://platform.deepseek.com/api_keys | 可选，不用 DeepSeek 官方通道可不填 |
| `TAVILY_API_KEY` | https://app.tavily.com/home | 联网搜菜谱用，有免费额度 |

> 🔒 `.env` 已被 `.gitignore` 忽略，**不会**被提交到仓库。仓库里只有一个 `.env.example` 模板。

### 3. 启动数据库（可选，但推荐）

不启动也能跑，只是**没有长期记忆**（关掉页面历史就没了），界面会明确提示。

```bash
# 一行起一个 PostgreSQL
docker run -d --name langgraph-postgres \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=postgres \
  -p 15432:5432 -v pgdata:/var/lib/postgresql/data postgres:16

# 建表（会话表 + LangGraph 检查点表）
psql "postgresql://postgres:postgres@localhost:15432/postgres" -f schema.sql

# 可选：灌一份纯虚构的演示数据
psql "postgresql://postgres:postgres@localhost:15432/postgres" -f seed_demo.sql
```

> 端口用 `15432` 是为了避开 Windows 的保留端口段，可按需改成 `5432`，记得同步改 `.env`。
> 上面这些 `postgres:postgres` 是**本地开发用的默认口令**，不是任何真实环境的凭据。

### 4. 启动应用

```bash
streamlit run app.py
```

浏览器打开 <http://localhost:8501> 即可。

### 5. 试试这些

- 上传一张冰箱 / 案板照片，问"看看能做什么菜"
- 不传图，直接说"我有西红柿和鸡蛋，能做什么"
- 点侧边栏「📋 剪贴板」，可以读入你刚截的图
- 直接把一个图片网址粘进输入框
- 换个模型：侧边栏「🤖 模型设置」里切换文字 / 看图模型
- 发够多条消息后向上翻，右下角会出现「↓」，点它回到最新

---

## 🔧 换模型怎么改

**只改一个文件**：`core/model_settings.py` 里的 `MODEL_CATALOG`。

```python
MODEL_CATALOG = (
    ModelSpec("qwen3.8-flash",  ALIYUN,   True,  "Qwen3.8-Flash", "实测视觉通过·速度快"),
    ModelSpec("deepseek-chat",  DEEPSEEK, False, "DeepSeek-Chat（官方）", "官方通道·仅文本"),
    #                ↑ 通道      ↑ 能否看图（实测结论）  ↑ 界面显示名
)
```

**界面上的两个模型下拉框会自动同步这份清单**，不用改任何界面代码。
`vision=False` 的模型绝不会出现在「看图模型」下拉框里。

也可以不改代码，用环境变量临时覆盖：`AI_CHEF_TEXT_MODEL` / `AI_CHEF_VISION_MODEL`。

模型名、可用性、是否支持视觉都随账号与额度变化。
**不确定某个模型能不能用时，先手动发一次请求验证**，再写进清单——
本项目就踩过"用 2×2 的小图探测，被服务端以尺寸不合规拒绝，
从而把能看图的模型误判成不支持视觉"的坑。

---

## 🗄️ 数据存放位置

| 数据 | 存在哪 | 说明 |
| --- | --- | --- |
| 对话内容 | PostgreSQL `checkpoints` 等 4 张表 | LangGraph 官方格式，由框架读写 |
| 会话元信息（标题/置顶/软删除） | PostgreSQL `chef_sessions` | 本项目自建表 |
| 图片缓存 | `.data/image_cache/`（已 gitignore） | 下载过的网络图片，二次访问免流量 |
| 密钥 | 项目根目录 `.env`（已 gitignore） | **绝不入库** |

---

## ⚠️ 开发者注意事项（改这些文件前先读）

| 坑 | 说明 |
| --- | --- |
| **连接串只有一个入口** | 一律用 `core.memory.resolve_dsn()`，**不要**自己读 `os.getenv("DB_URI")`。曾经 `SessionStore` 自己读，导致配置注入不进去、界面给出错误结论 |
| **降级结果绝不能缓存** | 曾经把"内存降级句柄"缓存起来，于是启动瞬间的失败被永久记住，数据库恢复了也一直没记忆 → 表现为「历史记录打不开」 |
| **`.env` 优先级高于环境变量** | 因为 `load_dotenv(override=True)`，在环境变量里改 `DB_URI` 是无效的。要模拟故障得替换 `resolve_dsn` |
| **checkpointer 没有 `get_state()`** | 读对话用 `get_tuple(config).checkpoint["channel_values"]["messages"]`（`get_state` 是编译后图的接口） |
| **会话标题要有意义才用** | 测试时常发「1」「2」，直接当标题会让侧边栏看起来像坏了。见 `core/history.py` 的 `is_meaningful_title` |
| **空壳会话** | 元信息在、对话内容不在的会话可用 `SessionStore.purge_orphan_sessions()` 清理 |
| **滚动按钮的滚动容器** | Streamlit 的主滚动区是 `stAppScrollToBottomContainer`，**不是** `stAppViewContainer`（后者 `overflow:hidden`，永远不滚） |

---

## ❓ 常见问题

**Q：界面提示"长期记忆未启用"？**
数据库没连上。检查 Docker 容器是否在跑、`.env` 里的 `DB_URI` 端口是否与容器映射一致。
应用会退回内存记忆继续可用，数据库恢复后**自动重连**（不会一直卡在降级状态）。

**Q：搜菜谱返回"联网搜索通道本次不可用"？**
搜索请求失败了，最典型的原因是**代理软件的 TUN 模式**干扰 TLS 握手
（表现为 `SSL: UNEXPECTED_EOF_WHILE_READING`）。
本项目的搜索工具内置了标准库降级通道，会自动绕过 `httpx` 的问题；
如果两边都失败，界面会明确告诉你"通道不可用"，而不是假装"没搜到菜谱"。

**Q：识别不出食材 / 说"这不是食材照片"？**
这是**预期行为**。合成图、图标、插画都会被如实指出，因为提示词明确要求
"只说你真正看到的，不要凭空猜测"——宁可让你重拍一张，也不编造食材让你买到错的菜。

**Q：手机拍的 HEIC 传不上去？**
HEIC 不在支持范围内（模型侧普遍不支持），请先导出为 JPG / PNG。

**Q：换模型后界面下拉框没变？**
下拉框的选项在每次页面渲染时从 `core/model_settings.py` 读取，
改完代码**刷新页面**即可，不需要重启服务。

**Q：界面没出现「↓」按钮？**
它只在**你向上滚动、不在最底部**时出现。消息太少没产生滚动条时本来就不显示。

**Q：图片太大会怎样？**
默认单张上限 15 MB，可用环境变量 `AI_CHEF_MAX_IMAGE_MB` 调整。
注意：**本程序不会自动压缩图片**，手机原图会原样发给模型，Token 消耗较高，建议先压一下。

---

## 📄 许可

[MIT](LICENSE) —— 可自由使用、修改、商用，保留版权声明即可。

菜谱内容来自第三方网站（下厨房、美食杰等），版权归原作者所有；
本项目仅做搜索与展示，请以原始页面为准。
