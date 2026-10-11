-- =============================================================================
--  AI私厨管家 · 演示数据（**全部为虚构假数据，可放心执行**）
-- =============================================================================
--  用法：
--      psql "postgresql://postgres:postgres@localhost:15432/postgres" -f seed_demo.sql
--
--  ⚠️ 重要说明：
--    1. 本文件里的姓名（张三、李四…）、手机号（13800138000 等）、
--       会话内容全部是**编造的示例数据**，与任何真实用户无关。
--    2. 只写入 chef_sessions（会话元信息表）与一张演示用的食材表。
--       **不会**伪造 LangGraph 检查点数据——那部分是框架私有二进制格式，
--       手写进去只会让程序读崩。想看到真实对话，跑起来自然就有了。
--    3. 所有插入都是幂等的（ON CONFLICT DO NOTHING），可重复执行。
-- =============================================================================

BEGIN;


-- =============================================================================
--  一、会话元信息（chef_sessions）—— 对应界面左侧「最近对话」列表
-- =============================================================================
--  说明：thread_id 必须与 LangGraph 检查点里的 thread_id 一致，程序才能点开读到内容。
--        这里仅作列表展示的演示；点开这些会话会显示为空对话（因为没有对应的检查点数据）。

INSERT INTO chef_sessions (thread_id, title, message_count, pinned, deleted, created_at, updated_at)
VALUES
    ('demo-thread-0001', '冰箱里有西红柿和鸡蛋，能做点什么',   6, TRUE,  FALSE, now() - INTERVAL '2 days',  now() - INTERVAL '2 days'),
    ('demo-thread-0002', '只有半颗白菜和一把粉丝，想吃清淡的', 4, FALSE, FALSE, now() - INTERVAL '1 day',   now() - INTERVAL '20 hours'),
    ('demo-thread-0003', '张三说家里孩子吃不了辣，求不辣的菜', 8, FALSE, FALSE, now() - INTERVAL '5 hours', now() - INTERVAL '5 hours'),
    ('demo-thread-0004', '李四问减脂期适合吃哪些家常菜',       3, FALSE, FALSE, now() - INTERVAL '2 hours', now() - INTERVAL '2 hours'),
    ('demo-thread-0005', '用土豆和牛肉能做什么',               5, FALSE, FALSE, now() - INTERVAL '1 hour',  now() - INTERVAL '1 hour'),
    -- 下面这条演示"已删除"的会话：deleted = TRUE，界面列表里不会出现
    ('demo-thread-0006', '（已删除的演示会话）',               2, FALSE, TRUE,  now() - INTERVAL '3 days',  now() - INTERVAL '3 days')
ON CONFLICT (thread_id) DO NOTHING;


-- =============================================================================
--  二、演示用食材表（chef_demo_ingredients）
-- =============================================================================
--  说明：主程序并不依赖这张表（识别出的食材是随对话走的，不落库）。
--        建它纯粹是为了演示"数据库里长什么样"，以及给面试官看你懂数据建模。
--        如果你不需要，直接删掉这一整段即可。

CREATE TABLE IF NOT EXISTS chef_demo_ingredients (
    id              SERIAL      PRIMARY KEY,
    owner_name      TEXT        NOT NULL,                       -- 谁家的冰箱（假名字）
    owner_phone     TEXT,                                       -- 联系电话（假号码）
    name            TEXT        NOT NULL,                       -- 食材名称
    quantity        TEXT,                                       -- 大致份量
    freshness       TEXT,                                       -- 新鲜度
    nutrition_grade CHAR(1),                                    -- 营养评级 A/B/C
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  chef_demo_ingredients IS '演示用食材表（数据全部虚构）';
COMMENT ON COLUMN chef_demo_ingredients.owner_phone IS '示例手机号，均为虚构（13800138000 为常见占位号段）';

INSERT INTO chef_demo_ingredients (owner_name, owner_phone, name, quantity, freshness, nutrition_grade)
VALUES
    ('张三', '13800138000', '西红柿',   '2 个',   '新鲜',       'A'),
    ('张三', '13800138000', '鸡蛋',     '3 个',   '新鲜',       'A'),
    ('张三', '13800138000', '小葱',     '一把',   '一般',       'B'),
    ('张三', '13800138000', '豆腐',     '半盒',   '需尽快食用', 'A'),
    ('李四', '13900139000', '白菜',     '半颗',   '新鲜',       'A'),
    ('李四', '13900139000', '粉丝',     '一把',   '未知',       'B'),
    ('李四', '13900139000', '土豆',     '4 个',   '一般',       'B'),
    ('李四', '13900139000', '牛腩',     '500 克', '新鲜',       'A'),
    ('王五', '13700137000', '青椒',     '3 个',   '新鲜',       'A'),
    ('王五', '13700137000', '鸡蛋',     '6 个',   '新鲜',       'A'),
    ('王五', '13700137000', '胡萝卜',   '2 根',   '一般',       'A'),
    ('王五', '13700137000', '香菇',     '6 朵',   '新鲜',       'A')
ON CONFLICT DO NOTHING;


-- =============================================================================
--  三、演示用食谱表（chef_demo_recipes）
-- =============================================================================
--  说明：同样只是演示数据建模。真实运行时菜谱来自 Tavily 联网搜索，不落库。
--        如果你想做"收藏菜谱"这类功能，可以以这张表为起点扩展。

CREATE TABLE IF NOT EXISTS chef_demo_recipes (
    id              SERIAL      PRIMARY KEY,
    name            TEXT        NOT NULL,                       -- 菜名
    nutrition_grade CHAR(1)     NOT NULL DEFAULT 'B',           -- 营养评级 A/B/C
    difficulty      SMALLINT    NOT NULL DEFAULT 3,             -- 难度星级 1~5
    time_minutes    INTEGER,                                    -- 耗时（分钟）
    ingredients     TEXT[]      NOT NULL DEFAULT '{}',          -- 需要的食材
    source_url      TEXT,                                       -- 来源链接（示例用假链接）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_difficulty CHECK (difficulty BETWEEN 1 AND 5),
    CONSTRAINT chk_grade      CHECK (nutrition_grade IN ('A','B','C'))
);

COMMENT ON TABLE chef_demo_recipes IS '演示用食谱表（数据全部虚构）';

INSERT INTO chef_demo_recipes (name, nutrition_grade, difficulty, time_minutes, ingredients, source_url)
VALUES
    ('西红柿炒鸡蛋', 'B', 1, 10, ARRAY['西红柿','鸡蛋','小葱'],       'https://example.com/recipe/tomato-egg'),
    ('番茄鸡蛋汤',   'A', 1, 10, ARRAY['西红柿','鸡蛋','小葱'],       'https://example.com/recipe/tomato-egg-soup'),
    ('醋溜白菜',     'A', 2, 15, ARRAY['白菜','干辣椒','醋'],         'https://example.com/recipe/vinegar-cabbage'),
    ('白菜粉丝煲',   'B', 2, 20, ARRAY['白菜','粉丝','虾皮'],         'https://example.com/recipe/cabbage-vermicelli'),
    ('土豆炖牛腩',   'A', 4, 90, ARRAY['土豆','牛腩','胡萝卜','姜'],   'https://example.com/recipe/beef-potato-stew'),
    ('青椒炒鸡蛋',   'A', 1, 10, ARRAY['青椒','鸡蛋'],               'https://example.com/recipe/pepper-egg'),
    ('香菇滑鸡',     'A', 3, 30, ARRAY['香菇','鸡腿肉','姜'],         'https://example.com/recipe/mushroom-chicken')
ON CONFLICT DO NOTHING;

COMMIT;


-- =============================================================================
--  完成。验证：
--      SELECT count(*) FROM chef_sessions;              -- 6（其中 1 条已删除）
--      SELECT count(*) FROM chef_sessions WHERE deleted = FALSE;  -- 5
--      SELECT name, nutrition_grade, difficulty FROM chef_demo_recipes ORDER BY difficulty;
--
--  清理演示数据（想恢复干净状态时执行）：
--      DELETE FROM chef_sessions WHERE thread_id LIKE 'demo-thread-%';
--      DROP TABLE IF EXISTS chef_demo_ingredients;
--      DROP TABLE IF EXISTS chef_demo_recipes;
-- =============================================================================
