-- =============================================================================
--  AI私厨管家 · 数据库结构（PostgreSQL）
-- =============================================================================
--  用途：一条命令建好全部表，克隆项目后即可运行。
--
--  用法：
--      psql "postgresql://postgres:postgres@localhost:15432/postgres" -f schema.sql
--
--  包含两部分：
--      第一部分  chef_sessions      —— 本项目自建表（会话元信息：标题、置顶等）
--      第二部分  checkpoints 等 4 张 —— LangGraph 官方检查点表（对话内容持久化）
--
--  ⚠️ 关于第二部分：
--      这 4 张表由 langgraph-checkpoint-postgres 的 PostgresSaver.setup() 自动创建，
--      程序首次运行时会自己建好。这里显式写出来是为了：
--        ① 让你能看懂数据存在哪里；
--        ② 便于 DBA 预先建表（例如生产环境不给应用建表权限时）。
--      表的字段与索引与官方实现保持一致（PostgreSQL 16 上实测生成）。
--
--  说明：所有语句都是幂等的（IF NOT EXISTS），可重复执行。
-- =============================================================================


-- =============================================================================
--  第一部分：本项目自建表
-- =============================================================================

-- 会话元信息表
--   为什么单独一张表：对话内容由 LangGraph 检查点表托管（框架私有格式），
--   但"标题、是否置顶、是否删除"这些是业务概念，框架表里没有，所以自己存。
CREATE TABLE IF NOT EXISTS chef_sessions (
    thread_id       TEXT        PRIMARY KEY,                    -- 会话 ID（与检查点的 thread_id 一致）
    title           TEXT        NOT NULL DEFAULT '',            -- 会话标题（取自首条有意义的用户消息，可重命名）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),         -- 创建时间
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),         -- 最后更新时间（列表按它倒序）
    message_count   INTEGER     NOT NULL DEFAULT 0,             -- 消息条数（用于列表展示）
    pinned          BOOLEAN     NOT NULL DEFAULT FALSE,         -- 是否置顶
    deleted         BOOLEAN     NOT NULL DEFAULT FALSE          -- 软删除：列表里隐藏，数据保留
);

-- 会话列表按更新时间倒序查询，加个索引
CREATE INDEX IF NOT EXISTS idx_chef_sessions_updated
    ON chef_sessions (updated_at DESC);


-- =============================================================================
--  第二部分：LangGraph 官方检查点表（由 PostgresSaver.setup() 创建）
-- =============================================================================

-- 检查点主表：每个会话每一步的状态快照
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id               TEXT   NOT NULL,
    checkpoint_ns           TEXT   NOT NULL DEFAULT '',
    checkpoint_id           TEXT   NOT NULL,
    parent_checkpoint_id    TEXT,
    type                    TEXT,
    checkpoint              JSONB  NOT NULL,
    metadata                JSONB  NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx
    ON checkpoints (thread_id);


-- 检查点二进制大对象表：把状态里的大字段（消息内容等）单独存，避免主表膨胀
CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id       TEXT   NOT NULL,
    checkpoint_ns   TEXT   NOT NULL DEFAULT '',
    channel         TEXT   NOT NULL,
    version         TEXT   NOT NULL,
    type            TEXT   NOT NULL,
    blob            BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx
    ON checkpoint_blobs (thread_id);


-- 中间写入表：记录每个任务节点的写入内容（用于断点续跑与审计）
CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id       TEXT    NOT NULL,
    checkpoint_ns   TEXT    NOT NULL DEFAULT '',
    checkpoint_id   TEXT    NOT NULL,
    task_id         TEXT    NOT NULL,
    idx             INTEGER NOT NULL,
    channel         TEXT    NOT NULL,
    type            TEXT,
    blob            BYTEA   NOT NULL,
    task_path       TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx
    ON checkpoint_writes (thread_id);


-- 迁移版本表：框架用它判断是否需要升级表结构
CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);

-- 标记 LangGraph 表结构已是最新版本（避免框架再跑一遍迁移）
INSERT INTO checkpoint_migrations (v) VALUES (10)
    ON CONFLICT (v) DO NOTHING;


-- =============================================================================
--  完成
-- =============================================================================
--  验证：
--      \dt                       -- 应看到 5 张表
--      SELECT count(*) FROM chef_sessions;
--
--  想灌一份演示数据：psql <连接串> -f seed_demo.sql
-- =============================================================================
