# 📂 AI私厨管家 · 项目结构说明书

> 这份文档的唯一目的：让你**快速知道每个文件是干什么的**，
> 特别是**想换大模型时该改哪个文件**。

> 💡 **关于「在哪一行」**：本文件**刻意不写死行号**。
> 代码一改，行号就失效，反而误导人。凡是需要定位的地方，本文件一律给出
> **可直接搜索的符号名**（如 `THEME`、`SYSTEM_PROMPT`、`MODEL_CATALOG`），
> 在编辑器里按 `Ctrl+F` 搜这个名字，永远找得到。

---

## 🚀 30 秒速查表

| 我想做的事 | 改这个文件 | 具体位置 |
| --- | --- | --- |
| **换大模型**（文字 / 看图） | `core/model_settings.py` | `MODEL_CATALOG` |
| **改管家的性格、语气、输出格式** | `core/agent.py` | `SYSTEM_PROMPT` 常量 |
| **改排序口味**（更重营养 / 更重好做） | `schemas/food_schemas.py` | `NUTRITION_SCORES` / `DIFFICULTY_SCORES` |
| **改配色** | `app.py` | `THEME` 字典 |
| **改界面结构** | `app.py` | `_render_sidebar()` / `_render_welcome()` / `_render_answer()` |
| **加一个新工具给 Agent 用** | `tools/search_tool.py` | 新写一个函数 + 在 `get_search_tools()` 里注册 |
| **改数据库连接方式** | `core/memory.py` | `resolve_dsn()` |
| **改菜谱卡片外观** | `utils/recipe_parser.py` | `render_recipe_card_html()` |
| **换 .env 文件位置** | `core/llm_models.py` | `ENV_PATH` 常量 |

---

## 1. ⭐ 最先要理解的三件事

### 1.1 🔴 换模型：只改 `core/model_settings.py`

**全项目只有一个地方定义「有哪些模型可用」**：`core/model_settings.py` 的
`MODEL_CATALOG`。界面的两个下拉框、模型创建、候选降级**全部读它**——
改这份清单，界面下拉框**自动跟着变**，不需要动任何界面代码。

```python
MODEL_CATALOG = (
    ModelSpec("qwen3.8-flash", ALIYUN, True, "Qwen3.8-Flash", "实测视觉通过·速度快"),
    #                ↑ 模型名   ↑ 通道  ↑ 能否看图   ↑ 界面显示名    ↑ 备注
)
```

三个关键字段：

- **模型名**：传给 API 的精确名字，一个字都不能改
- **通道**：`ALIYUN` = 阿里云百炼（兼容 OpenAI 协议）；`DEEPSEEK` = DeepSeek 官方（LangChain 原生，**不需要 base_url**）
- **vision**：是否支持图像输入。填 `False` 的模型**绝不会出现在「看图模型」下拉框里**

> ⚠️ 仓库默认清单里的模型名是针对作者本人账号实测过的。**你自己部署时，
> 请先跑一遍模型自检**（见 [1.3](#13-模型是真实探测--自动降级的)），确认哪些模型你的 Key 能调通，
> 再按需精简 `MODEL_CATALOG`。

### 1.2 两条模型的通道（务必理解）

| | 阿里云通道 | DeepSeek 官方通道 |
| --- | --- | --- |
| LangChain 原生支持？ | ❌ 不支持（假装成 OpenAI） | ✅ 原生支持 |
| 怎么接 | `model_provider="openai"` + `base_url` | 直接给 `api_key`，**不要 base_url** |
| 需要哪些环境变量 | `DASHSCOPE_BASE_URL` + `DASHSCOPE_API_KEY` | 只要 `DEEPSEEK_API_KEY` |
| 依赖的包 | `langchain-openai` | `langchain-deepseek` |

⚠️ **踩过的坑**：把 DeepSeek 官方通道的模型名发到**阿里云端点**上会得到
`404 Model not exist`，从而误以为「这个模型不存在」。
其实只是**发错了端点**——它属于 DeepSeek 官方通道。

### 1.3 模型是「真实探测 + 自动降级」的

候选模型**不是按名单信任，而是真的发一次请求去试**：

- 文本模型：发一句话，看有没有正常回复
- 视觉模型：发一张 **64×64 的纯蓝色图**（`VISION_PING_COLOR = (40, 90, 220)`），
  **必须答出「蓝」才算通过**——仅仅「没报错」不算，否则等于没测出多模态能力

第一个通过的真实生效，失败的自动跳到下一个，并在界面给出提示。

> 💡 想验证某个模型能不能用，最直接的办法是点界面侧边栏的「模型状态自检」，
> 或看 `core/llm_models.py` 里 `LLMModels.self_check()` 的输出。
> **填 `vision=True` 前务必实测**——很多模型名字看起来像多模态，实际并不支持。

---

## 2. 📁 完整目录结构（逐文件说明）

```
.
├── app.py                                 # 【Web UI 入口】唯一需要 streamlit run 的文件
├── requirements.txt                       # 【依赖清单】实测版本号 + 国内镜像安装命令
├── README.md                              # 【上手文档】功能、环境要求、如何运行
├── PROJECT_STRUCTURE.md                   # 【本文件】逐文件作用说明
├── 食材标签异常态视觉规范.md                  # 【前端规范】食材标签「脏数据」的判定与渲染规则
├── LICENSE                                # MIT 许可证
├── .gitignore                             # 【安全】确保 .env 永不入库
├── .env.example                           # 【配置模板】复制成 .env 后填入你自己的 Key
├── schema.sql                             # 【建表脚本】会话表 + LangGraph 检查点表
├── seed_demo.sql                          # 【演示数据】全是虚构数据，可放心执行
├── .streamlit/
│   ├── config.toml                        # 【主题锁定】白底 + 深色文字，防「白底白字」
│   └── credentials.toml                   # 【防卡死】声明不提供邮箱，避免首启交互提示
│
├── core/                                  # ── 核心业务逻辑 ──
│   ├── __init__.py                        # 包声明与模块导航注释
│   ├── model_settings.py                  # ★★★【模型名单】换模型只改这里（界面自动同步）
│   ├── llm_models.py                      # 【模型工厂】两条通道、真实探测、自动降级、密钥脱敏
│   ├── agent.py                           # 【主 Agent】系统提示词 + 绑定工具 + 绑定记忆
│   ├── memory.py                          # 【持久化记忆】PostgreSQL 检查点，记住多轮对话
│   └── history.py                         # 【会话管理】聊天记录列表：新建/切换/重命名/删除
│
├── tools/                                 # ── 自定义工具（给 Agent 调用）──
│   ├── __init__.py                        # 包声明
│   └── search_tool.py                     # 【联网搜菜谱】Tavily 封装：限定中文菜谱站 + 搜不到自动放宽
│
├── schemas/                               # ── 数据模型 ──
│   ├── __init__.py                        # 包声明
│   └── food_schemas.py                    # 【结构化输出】食材/食谱 Pydantic 模型 + 排序算法
│
└── utils/                                 # ── 通用工具 ──
    ├── __init__.py                        # 包声明
    ├── image_handler.py                   # 【图片处理】本地/URL/剪贴板 → Base64（含格式校验）
    ├── image_input.py                     # 【界面图片输入】剪贴板探测、图片网址识别、下载缓存
    ├── img_codec.py                       # 【零依赖图片编解码】PNG/BMP 读写
    ├── recipe_parser.py                   # 【菜谱解析】把模型 Markdown 回答解析成卡片数据
    └── scroll_button.py                   # 【界面增强】「↓ 滚动到最新」悬浮按钮
```

---

## 3. 🔍 每个文件的详细职责

### 入口层

**`app.py`**（998 行）— Streamlit Web UI 入口，启动：

```bash
pip install -r requirements.txt
streamlit run app.py
```

- **左栏**：新建对话、会话列表（可切换/重命名/删除）、文字模型与看图模型两个下拉框、
  上传照片 / 摄像头拍照（折叠）/ 剪贴板读图 / 粘贴图片网址、菜谱条数、排序偏好、清空、重连模型
- **主区**：欢迎引导 → 对话消息流 → 底部输入框 → 「↓ 滚动到最新」悬浮按钮
- **多图暂存**：可以一次攒**多张**图再一起发送，每张图**右上角有独立删除按钮**
  （`_stage_image` / `_remove_staged_image` / `_clear_staged_images` / `_render_staged_images`）
- **菜谱卡片渲染失败时自动退回 Markdown 原文**，**绝不白屏**
- 颜色全部写死在 `THEME` 字典，改一处即可换肤

> ⚠️ **图片会跟随后续对话**（重要，容易误解）：
> 图片会以 Base64 形式**存进对话记忆**，并且 `app.py` 会保留
> 「最近一张图」（`st.session_state.last_image`），
> **在后续每一轮纯文字提问时重新发给模型**（见 `_handle_input()`）。
> 这是**有意的便利设计**：用户追问「这个菜辣不辣」时不必重新上传照片。
> 代价是**一张照片会被按轮次重复计费**，只有点「🧹 全部清空」才停止。
> 若想改成「只发一次」，需改 `_handle_input()` 中的 `images_for_turn` 一行。

### 核心层 `core/`

**`model_settings.py`**（238 行）— ★ 模型名单

- 全项目**唯一**的模型清单来源（详见 [1.1](#11--换模型只改-coremodel_settingspy)）
- `list_models(role=...)`：按「文本 / 视觉」过滤，界面下拉框直接用它
- `spec_for(name)`：查某个模型属于哪条通道
- `set_override()` / `get_override()` / `clear_overrides()`：界面切模型时记录临时选择
- `default_text_model()` / `default_vision_model()`：候选列表的**第一名**（降级就从它开始试）
- 环境变量 `AI_CHEF_TEXT_MODEL` / `AI_CHEF_VISION_MODEL` 可临时覆盖

**`llm_models.py`**（800 行）— 模型工厂

- `build_model(name)`：**全项目唯一的模型构造入口**，自动判断走阿里云还是 DeepSeek 官方通道
- `LLMModels.text_model` / `.vision_model`：按候选顺序**真实探测**，第一个可用的生效
- `_text_candidates()` / `_vision_candidates()`：候选列表**从 `MODEL_CATALOG` 动态生成**
  （默认模型排第一，其余按清单顺序）——这正是「换模型只改清单」能成立的原因
- `capabilities()` / `safe_repr()` / `mask_secret()`：输出脱敏信息
  （如 `已配置（长度 116，sk-w…Fkjw）`），可安全展示在网页与日志里
- **`ENV_PATH` 是全项目唯一的 `.env` 路径定义**（相对**项目根目录**，克隆下来即可用），
  只有这个文件调用 `load_dotenv`，其它模块禁止重复加载配置
- 找不到 `.env` 时会**向上级目录继续查找**（方便从子目录跑脚本）

**`agent.py`**（412 行）— 主 Agent

- `SYSTEM_PROMPT`：管家的完整工作流程与输出格式（看图 → 搜菜谱 → 排序 → 卡片式输出 → 搜不到给创意建议）
- `ChefAgent`：封装 `create_agent`，对外只暴露 `chat()` 和 `stream_chat()`
- `get_chef_agent()`：带缓存的取用入口（`_CHEF` 单例），界面走这个
- `_coerce_messages()`：把「字符串 / 图片 / 字典 / 消息对象」统一成 LangChain 消息
- `_collect_tool_calls()`：整理本轮工具调用，供界面显示「联网搜索几次」
- `checkpointer` 是注入口：接 PostgreSQL 时只传一个参数，本文件逻辑无需改动
- ⚠️ **本文件没有任何历史裁剪逻辑**：每轮都会把**完整历史**交给模型

**`memory.py`**（482 行）— 持久化记忆

- 后端开关 `AI_CHEF_MEMORY_BACKEND`，取值 `auto` / `postgres` / `sqlite` / `memory`，
  其中 `postgres` 是默认值（`DEFAULT_BACKEND`）
- `resolve_dsn()`：**全项目唯一的连接串解析入口**，别处禁止自己读 `DB_URI`
- `get_memory_or_fallback()` 给界面用：数据库连不上时退回内存并提示，页面不崩
- `redact_uri()`：**唯一允许查看 DB_URI 的方式**，只输出 `postgresql://***:***@主机:端口/库名`；
  ⚠️ 格式不合法时**宁可说「已隐藏」也不回显原文**（否则一次误传就把密码写进日志）
- ⚠️ **降级结果绝不缓存**（`_MEMORY` 保持 `None`）：否则启动瞬间的失败会被永久记住

**`history.py`**（481 行）— 聊天记录 / 会话管理

- 会话元信息（标题、时间、消息数）存在自建小表 `chef_sessions`
- 聊天内容**不另存一份**，直接复用框架的 checkpointer 读取——
  读法是 `get_tuple(config).checkpoint["channel_values"]["messages"]`
  （注意：checkpointer **没有** `get_state()` 方法，那是编译后图的接口）
- 删除是**软删除**：列表里消失，数据仍保留在数据库
- `is_meaningful_title()`：过滤「1」「2」这类无意义标题，避免侧边栏看起来像坏了
- `purge_orphan_sessions()`：清理「元信息在、对话内容不在」的空壳会话

### 工具层 `tools/`

**`search_tool.py`**（496 行）— Tavily 联网搜索

- `search_recipes()`：按食材搜中文菜谱，默认限定
  `xiachufang.com`、`meishij.net`、`douguo.com`、`xiangha.com`、`msdsp.com`
  （实测：不加限制会混入百度百科和 YouTube，对做菜没用）
- **硬过滤兜底**：白名单万一没命中，自动放宽为全网搜（开关 `AI_CHEF_TAVILY_SITE_FALLBACK`），不让用户看到空白
- `search_creative_pairings()`：搜不到菜谱时的创意搭配兜底
- **双通道**：优先走 LangChain 的 `TavilySearch`，失败则用标准库
  `http.client`/`ssl` 直连 `https://api.tavily.com/search`（对付代理抖动）
- `_looks_like_transport_failure()`：**区分「网络断了」与「真没搜到」**——
  传输失败时**不做**站点放宽重试（否则会连续重试、拖慢响应）
- 工具入参用 Pydantic 约束（`max_results` 限定 1~10，防止模型一次要 100 条）
- 未配置 `TAVILY_API_KEY` 时返回空工具列表 + 明确警告，界面仍可纯对话

### 数据层 `schemas/`

**`food_schemas.py`**（495 行）— Pydantic 模型与排序

- `Ingredient`：食材名/份量/新鲜度/是否需要另买（`label` 输出界面显示文本）
- `Recipe`：菜名/营养评级(A,B,C)/难度(1~5星)/耗时/食材/步骤/来源链接
- `RecipeRecommendation`：一次推荐的完整结果（含创意建议兜底分支）
- **脏数据清洗函数**：`clean_nutrition_grade()` / `clean_difficulty()` / `parse_json_object()`
  ——`⭐⭐⭐`→3、`三星`→3、`b级`→`B`、`约 15 分钟`→15，模型输出不规范也能吃下
- **安全校验**：星级越界收敛到 1~5；`_drop_fake_url()` 把**编造的假链接
  （非 http 开头）自动清空**，避免误导用户点击
- 打分量表集中在 `NUTRITION_SCORES` / `DIFFICULTY_SCORES`（改口味就改这两个字典）
- 排序：`recommend_recipes(data, preference=...)` 支持「综合 / 营养优先 / 简单优先」；
  底层是 `sort_recipes()`，另有 `recommendation_json_schema()` 供结构化输出用

### 工具函数层 `utils/`

**`image_handler.py`**（807 行）— 图片处理

- 五种来源统一转换：本地文件 / 网络 URL / **剪贴板**（Windows 原生 API）/ 原始字节 / 上传对象
- **按文件头魔数判断格式**，不靠后缀名（把 PNG 改名成 .jpg 也能正确识别）
- `ImagePayload.data_url` 生成 `data:image/jpeg;base64,...`，即发给模型的格式
- `prefer_base64=False` 时改为**直传 URL**（省流量，但要求图片可公网访问）；界面默认走 Base64
- 内置 SSRF 防护：默认拒绝内网/回环地址
- 默认单图上限 15 MB（`AI_CHEF_MAX_IMAGE_MB` 可调），中文报错可直接展示给用户
- ⚠️ **不做任何压缩、不限制分辨率**：一张 4032×3024 手机照会原样发出

**`image_input.py`**（335 行）— 界面图片输入

- `probe_clipboard_image()`：探测剪贴板有没有图，并给一个**指纹**用于「自动发现新图」；
  只读图片，不会碰剪贴板里的文字/密码
- `detect_image_url()`：识别用户粘贴的图片地址。有扩展名 / 图床域名 → 直接认；
  短链 → **用一次 HEAD 请求看 Content-Type 或文件头**，避免把网页链接当图片下载
- `cached_image_path()`：缓存文件名 = **URL 的 MD5 摘要** + `.img`（URL 太长不能直接做文件名）
- `download_image_cached()`：下载后写入 `.data/image_cache/`，第二次直接命中缓存
- `load_cached_image()`：缓存内容**按魔数严格校验**，坏缓存**自动删除并重新下载**

**`img_codec.py`**（321 行）— **零依赖**的最小 PNG/BMP 编解码器

- 能力：`encode_png` / `make_solid_png` / `make_gradient_png` / `read_png_size` /
  `decode_png_rgb`（含 Paeth 反滤波）/ `png_to_view_dib` / `png_to_simple_bmp`
- 用途：构造合规测试图片与界面占位图
- 保证「不装 Pillow 也能跑通识别链路」——这是刻意设计的零依赖底线

**`recipe_parser.py`**（593 行）— 把模型 Markdown 回答解析成菜谱卡片

- 最难点是区分「菜谱小标题」与「做法步骤」：`### 1. 番茄炒蛋` 是菜谱，
  而 `1. 鸡蛋打散炒至凝固后盛出备用。` 是步骤。靠标题标记、缩进、动词、句末标点四个特征区分
- **食材标签清洗**（最曲折的一块）：模型输出的食材标签常带脏字符，三类要分别处理

  | 模型原始输出 | 应该变成 | 规则 |
  | --- | --- | --- |
  | 番茄 + 空括号 | `番茄` | 空括号直接清掉 |
  | 排骨 + 份量括号 | 原样保留 | 括号里是有效份量，**不能**当脏数据删掉 |
  | 只写了半个括号的残片 | 丢弃 | 括号没闭合，是模型截断的产物 |

  实现见 `_split_with_status()` / `_tidy_bracket()` / `_is_renderable_ingredient()`，
  前端规范见 `食材标签异常态视觉规范.md`
- 解析不出来就返回空列表，由界面退回 Markdown 显示

**`scroll_button.py`**（230 行）— 「↓ 滚动到最新」悬浮按钮

- 通过 `st.html(..., unsafe_allow_javascript=True)` 注入（**不是 iframe**，脚本会真实执行）
- 滚动容器是 Streamlit 的 `section[data-testid="stAppScrollToBottomContainer"]`，
  **不是** `stAppViewContainer`（后者 `overflow:hidden`，永远不滚，是个坑）；
  找不到时退化为「遍历所有元素找可滚动的那一个」
- 只在用户向上翻历史时浮现；不在底部时显示未读条数；点击平滑滚到底部后自动隐藏
- 注入失败**静默降级**，绝不影响聊天
- 内部只用两种注入方式：`st.html`（首选）；老版本 Streamlit 没有该参数时
  **仅当抛 `TypeError`** 才退回 `components.html`

### 数据库脚本

**`schema.sql`**（117 行）— 建表脚本

- 业务表：会话元信息等
- LangGraph 检查点表：`checkpoints` / `checkpoint_blobs` / `checkpoint_writes` / `checkpoint_migrations`
  （这些是框架要求的表结构）
- 用法：`psql "<你的 DB_URI>" -f schema.sql`

**`seed_demo.sql`**（120 行）— 演示数据

- 全部是**虚构数据**（假姓名、`13800138000` 这类占位手机号），可放心执行
- 用法：`psql "<你的 DB_URI>" -f seed_demo.sql`

---

## 4. 🔐 环境与安全约定（红线）

| 约定 | 说明 |
| --- | --- |
| 环境变量 | 复制 `.env.example` 为**项目根目录**的 `.env`，填入你自己的 Key |
| `.env` 不入库 | `.gitignore` 已忽略 `.env` / `.env.*`（但保留 `.env.example`） |
| 🚨 红线 | **绝不把真实 Key、密码、连接串写进代码**；仓库内零硬编码凭据 |
| 加载方式 | 只由 `core/llm_models.py` 用 `load_dotenv()` 读取，其它模块禁止重复加载 |
| 日志安全 | 密钥只输出长度与前 4/后 4 位；数据库连接串只输出 `***:***@主机:端口/库名` |
| ⚠️ `.env` 优先级 | 因为 `load_dotenv(override=True)`，**环境变量无法覆盖 `.env`**。要模拟配置故障得改代码里的解析入口 |
| 国内镜像 | pypi.org 在国内常解析失败，可用 `https://mirrors.aliyun.com/pypi/simple/` |

**最小启动步骤：**

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置密钥
cp .env.example .env        # Windows: Copy-Item .env.example .env
# 然后编辑 .env，填入 DASHSCOPE_API_KEY / DEEPSEEK_API_KEY / TAVILY_API_KEY / DB_URI

# 3. （可选）如果要用 PostgreSQL 记忆
psql "<你的 DB_URI>" -f schema.sql

# 4. 启动
streamlit run app.py
```

---

## 5. 🗺️ 数据流全景（一次完整的「拍照做菜」）

```
用户上传照片
   │
   ▼
utils/image_handler.prepare_image()              ← 校验格式/大小，转 Base64 DataURL
   │
   ▼
app.py 存入 st.session_state（可攒多张，逐个删除）
   │
   ▼
core/agent.ChefAgent.chat([图片, "能做什么菜？"])
   │
   ├─► 视觉模型看到图片 ──► 列出识别到的食材（识别不到就直说，绝不编造）
   │
   ├─► 模型决定调用工具 search_recipes("西红柿、鸡蛋、小葱")
   │        └─► tools/search_tool ──► Tavily API ──► 下厨房等站点的真实菜谱
   │
   ├─► 模型按「营养价值 + 制作难度」排序，输出 Markdown（含来源链接）
   │
   └─► core/memory (PostgreSQL) 自动保存这一轮对话（含图片 Base64）
   │
   ▼
utils/recipe_parser.parse_recipes_from_text()    ← 解析成菜谱卡片数据
   │
   ▼
app.py 渲染菜谱卡片（营养等级 / 难度星级 / 耗时 / 食材 / 来源链接）
   │
   ▼
utils/scroll_button.render_scroll_to_latest()    ← 页面滚动时浮现「↓」按钮
```

需要**结构化数据**（而不是对话文本）时，走另一条路：

```
core/llm_models.build_structuring_model(RecipeRecommendation)
   → schemas/food_schemas.Recipe 对象
   → recommend_recipes(preference="营养优先") 排序
```

---

## 6. 已知问题与改进建议

以下是已知的真实问题（尚未修改，供参考）：

| # | 问题 | 影响 | 建议 |
| --- | --- | --- | --- |
| 1 | **图片不压缩、不限制分辨率**：`image_handler.py` 只校验 15 MB 上限 | 一张 4032×3024 手机照会消耗大量视觉 Token；而视觉 Token 由**像素尺寸**决定，**压 JPEG 质量省不了 Token，必须缩像素** | 发图前把长边缩到 1024~768，可显著降低 Token |
| 2 | **图片在后续每轮重复发送**：`app.py` 的 `last_image` 会被反复塞进请求，且被 checkpointer 永久保存 | 多轮追问时 Token 线性增长 | 视需求决定是否改成「只发一次」 |
| 3 | **没有历史裁剪**：`core/` 内没有任何 `trim`/`prune`/`window`/`history_limit` 逻辑 | 长对话线性增长，最终可能超出上下文窗口 | 只带最近 N 轮 |
| 4 | **`MODEL_CATALOG` 的模型名针对作者账号实测** | 你的 Key 可能调不通其中一部分 | 部署后先跑一次模型自检，按需精简清单 |

---

## 7. 📌 本文件与其它文档的分工

| 文档 | 职责 |
| --- | --- |
| **`PROJECT_STRUCTURE.md`**（本文件） | 「**哪个文件管什么**」——代码地图 |
| `README.md` | 「**怎么装、怎么跑、怎么用**」——操作手册 |
| `食材标签异常态视觉规范.md` | 食材标签脏数据的**判定规则与渲染规范** |
| `.env.example` | **环境变量模板**（不含任何真实密钥） |
