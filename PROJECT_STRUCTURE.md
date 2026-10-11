# 📂 AI私厨管家 · 项目结构说明

> 这份文档的目的：让你（或接手代码的人）**快速知道每个文件是干什么的**，
> 特别是**想换大模型时该改哪个文件**。

---

## ⭐ 最常改的三个文件

| 我想做的事 | 改这个文件 | 改哪里 |
| --- | --- | --- |
| **换大模型**（文字 / 看图） | `core/model_settings.py` | 顶部 `MODEL_CATALOG` |
| **改管家的性格与输出格式** | `core/agent.py` | 顶部 `SYSTEM_PROMPT` |
| **改排序口味**（更看重营养 / 更好做） | `schemas/food_schemas.py` | `NUTRITION_SCORES` / `DIFFICULTY_SCORES` |

### 🔴 `core/model_settings.py` —— 模型清单（唯一来源）

全项目**只有一个地方**定义"有哪些模型可用"，界面下拉框和模型创建都读它。
所以**改这份清单，界面下拉框自动跟着变**，不需要动任何界面代码。

```python
MODEL_CATALOG = (
    ModelSpec("qwen3.8-flash",  ALIYUN,   True,  "Qwen3.8-Flash", "实测视觉通过·速度快"),
    ModelSpec("deepseek-chat",  DEEPSEEK, False, "DeepSeek-Chat（官方）", "官方通道·仅文本"),
    #                ↑ 通道      ↑ 能否看图（实测）  ↑ 界面显示名
)
```

三个字段的含义：

- **模型名**：传给 API 的精确名字，一个字不能改
- **通道**：`ALIYUN` = 阿里云百炼（兼容 OpenAI 协议）；`DEEPSEEK` = DeepSeek 官方（LangChain 原生，**不需要 base_url**）
- **vision**：是否支持图像输入。填 `False` 的模型**绝不会出现在「看图模型」下拉框里**

### 两条通道的差别（务必理解）

| | 阿里云百炼通道 | DeepSeek 官方通道 |
| --- | --- | --- |
| LangChain 原生支持 | ❌ 不支持，要借用 OpenAI 协议 | ✅ 原生支持 |
| 怎么接 | `model_provider="openai"` + `base_url` | 直接给 `api_key`，**不要 base_url** |
| 需要哪些环境变量 | `DASHSCOPE_BASE_URL` + `DASHSCOPE_API_KEY` | 只要 `DEEPSEEK_API_KEY` |
| 代码里的类 | `ChatOpenAI` | `ChatDeepSeek` |

⚠️ 容易踩的坑：把 `deepseek-chat` 发到**阿里云端点**上会得到 404「Model not exist」，
从而误以为"这个模型不存在"。其实只是**发错了端点**——它属于 DeepSeek 官方通道。

---

## 📁 目录结构

```
.
├── app.py                     # 【Web UI 入口】白底黑字、会话侧边栏、模型切换、四条图片输入
├── core/                      # ── 核心层 ──
│   ├── model_settings.py      # ★ 模型清单（换模型只改这里）
│   ├── llm_models.py          # 模型工厂：两条通道、真实探测、自动降级、密钥脱敏
│   ├── agent.py               # 主 Agent：系统提示词 + 绑定工具 + 绑定记忆
│   ├── memory.py              # PostgreSQL 持久化记忆（含优雅降级）
│   └── history.py             # 会话管理：列表 / 新建 / 重命名 / 删除
├── tools/
│   └── search_tool.py         # 联网搜菜谱：首选 TavilySearch，失败降级标准库
├── schemas/
│   └── food_schemas.py        # Pydantic 模型 + 脏数据清洗 + 排序算法
├── utils/
│   ├── image_handler.py       # 图片 → Base64（本地/URL/剪贴板，含魔数校验与 SSRF 防护）
│   ├── image_input.py         # 界面图片输入：剪贴板探测、URL 识别、下载缓存
│   ├── img_codec.py           # 零依赖 PNG/BMP 编解码（不依赖 Pillow）
│   └── recipe_parser.py       # 把模型 Markdown 回答解析成菜谱卡片
├── .streamlit/
│   ├── config.toml            # 主题锁定（白底 + 深色文字，防"白底白字"）
│   └── credentials.toml       # 声明不提供邮箱，跳过 Streamlit 首次引导
├── schema.sql                 # 数据库结构（用户表 + LangGraph 检查点表）
├── seed_demo.sql              # 纯虚构演示数据
├── .env.example               # 环境变量模板（复制为 .env 后填 Key）
├── requirements.txt           # 依赖清单
├── README.md                  # 项目说明（架构图、快速开始、FAQ）
├── PROJECT_STRUCTURE.md       # 本文件
└── LICENSE                    # MIT
```

---

## 🔍 各模块职责

### 入口层

**`app.py`** — Streamlit Web UI

- 侧边栏：新建对话、会话列表（可重命名/删除）、四条图片输入、模型切换、生成设置
- 主区：欢迎引导 → 对话消息流 → 输入框
- 配色集中在一个 `THEME` 字典里，改一处全局生效
- 关键设计：**图片只在当前这一轮发给模型**（一张 1024×740 的照片 Base64 后约 112 万字符），
  随后仅把"识别出的食材"记进对话记忆，避免多轮重复计费

### 核心层 `core/`

**`model_settings.py`** — 模型清单（见上文）

**`llm_models.py`** — 模型工厂

- `build_model(name)`：**全项目唯一的模型构造入口**，自动判断走哪条通道
- 候选列表按优先级依次**真实探测**（视觉模型必须答对颜色才算可用），第一个可用的生效
- `capabilities()` / `safe_repr()` 输出脱敏信息（如 `已配置（长度 116，sk-w…Fkjw）`）
- 只有这个文件调用 `load_dotenv`，其它模块禁止重复加载配置

**`agent.py`** — 主 Agent

- `SYSTEM_PROMPT`：完整工作流程（看图 → 搜菜谱 → 排序 → 卡片式输出 → 搜不到给创意建议）
- `ChefAgent`：封装 `create_agent`，对外只暴露 `chat()` / `stream_chat()`
- `checkpointer` 是注入口：接数据库时只传一个参数，本文件逻辑无需改动

**`memory.py`** — 持久化记忆

- 四种后端，开关 `AI_CHEF_MEMORY_BACKEND`：`postgres`（默认）/ `auto` / `sqlite` / `memory`
- 连不上数据库时退回内存记忆并明确提示，页面不崩
- `redact_uri()`：**唯一允许查看连接串的方式**，只输出 `postgresql://***:***@主机:端口/库名`

**`history.py`** — 会话管理

- 会话元信息（标题/置顶/软删除）存在自建表 `chef_sessions`
- 聊天内容**不另存一份**，直接复用 LangGraph 检查点读取
- 删除是**软删除**：列表里消失，数据仍保留

### 工具层

**`search_tool.py`** — 联网搜索

- `search_recipes()`：按食材搜中文菜谱，默认限定下厨房/美食杰/豆果/香哈等站点
- **首选 `langchain-tavily` 的 `TavilySearch`**；失败时自动降级到标准库 HTTPS 实现
  （部分代理软件的 TUN 模式会让 httpx 的 TLS 握手失败，标准库不受影响）
- **错误不再被吞掉**：通道故障会如实上报，界面提示"搜索不可用"而不是"没搜到菜谱"
- 工具入参用 Pydantic 约束（`max_results` 限定 1~10）

### 数据层

**`food_schemas.py`** — Pydantic 模型与排序

- `Ingredient` / `Recipe` / `RecipeRecommendation`
- **脏数据清洗**：`⭐⭐⭐`→3、`三星`→3、`b级`→`B`、`约 15 分钟`→15
- **安全校验**：星级越界收敛到 1~5；**编造的假链接（非 http 开头）自动清空**
- 排序：`recommend_recipes(data, preference="综合"/"营养优先"/"简单优先")`

### 工具函数层

**`image_handler.py`** — 图片处理

- 三种来源：本地文件 / 网络 URL / **剪贴板**（Windows 原生 API，不装 Pillow 也能用）
- **按文件头魔数判断格式**，不靠后缀名
- 内置 SSRF 防护：默认拒绝内网/回环地址
- 中文报错可直接展示给用户

**`image_input.py`** — 界面图片输入

- `probe_clipboard_image()`：探测剪贴板是否有图并给指纹（用于"自动发现新图"）
- `detect_image_url()`：识别图片地址；短链用一次 HEAD 请求看 Content-Type，避免把网页当图片
- `download_image_cached()`：下载后写入 `.data/image_cache/`，二次访问直接命中缓存

**`recipe_parser.py`** — 把回答解析成卡片

- 最难点是区分「菜谱小标题」与「做法步骤」：靠标题标记、缩进、动词、句末标点四个特征
- 解析不出来就返回空列表，由界面退回 Markdown 显示（**绝不白屏**）

---

## 🔐 安全约定

| 约定 | 说明 |
| --- | --- |
| 密钥 | 只存在于项目根目录 `.env`（已 gitignore），代码中零硬编码 |
| 日志 | 密钥只输出长度与首尾各 4 位；连接串只输出 `***:***@主机:端口/库名` |
| 演示数据 | `seed_demo.sql` 中的姓名、手机号、会话内容**全部虚构** |
| 图片输入 | 默认拒绝请求内网/回环地址（防 SSRF） |

---

## 🗺️ 一次完整的「拍照做菜」数据流

```
用户上传照片
   │
   ▼
utils/image_handler.prepare_image_from_uploaded_file()   ← 校验格式/大小，转 Base64
   │
   ▼
core/agent.ChefAgent.chat([图片, "能做什么菜？"])
   │
   ├─▶ 视觉模型看图 ──▶ 列出识别到的食材（识别不到就直说，绝不编造）
   │
   ├─▶ 模型决定调用工具 search_recipes("西红柿、鸡蛋、小葱")
   │        └─▶ tools/search_tool ──▶ Tavily API ──▶ 下厨房等站点的真实菜谱
   │
   ├─▶ 模型按「营养价值 + 制作难度」排序，输出 Markdown（含来源链接）
   │
   └─▶ core/memory (PostgreSQL) 保存这一轮对话
   │
   ▼
utils/recipe_parser.parse_recipes_from_text()             ← 解析成菜谱卡片数据
   │
   ▼
app.py 渲染菜谱卡片（营养等级 / 难度星级 / 耗时 / 食材 / 来源链接）
```

需要结构化数据（而不是对话文本）时，走另一条路：
`core/llm_models.build_structuring_model(RecipeRecommendation)` →
得到 `Recipe` 对象 → `recommend_recipes(preference="营养优先")` 排序。
