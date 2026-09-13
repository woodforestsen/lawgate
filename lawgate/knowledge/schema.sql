-- ============================================================================
-- CA-LegalGate 结构化事实库 schema（手册 S2.1）
-- 执行一次：sqlite3 data/kb/legal_facts.db < lawgate/knowledge/schema.sql
--
-- 与手册的偏差（均为必要修正，已在 docs/deviations.md 记录）：
--   D1. provision_fts 增加 provision_id 列。手册用 (law_short, article_no) 回连
--       legal_provisions，在"一条多款多项"时会乘出重复行；改用主键精确回连。
--   D2. item_no 默认 '' 而非 NULL。SQLite 的 UNIQUE 约束中 NULL 互不相等，
--       手册写法无法阻止 (条, 款, NULL) 重复插入。
-- ============================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS legal_provisions (
    provision_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    law_name        TEXT NOT NULL,      -- 《中华人民共和国民法典》
    law_short       TEXT NOT NULL,      -- 民法典
    law_level       TEXT,               -- 法律/行政法规/司法解释
    book            TEXT, chapter TEXT, section TEXT,
    article_no      INTEGER NOT NULL,   -- 数字条号
    article_label   TEXT NOT NULL,      -- '第六百六十七条'
    paragraph_no    INTEGER DEFAULT 1,
    item_no         TEXT DEFAULT '',    -- '(一)'（D2：不用 NULL）
    text            TEXT NOT NULL,
    validity_status TEXT NOT NULL CHECK(validity_status IN
        ('现行有效','已修订','已废止','尚未生效','部分失效')),
    effective_date  TEXT, publish_date TEXT, version TEXT,
    superseded_by   TEXT,               -- '民法典#667'
    amendment_note  TEXT,
    source_db       TEXT DEFAULT 'FLK',
    source_url      TEXT NOT NULL,
    retrieval_date  TEXT NOT NULL,
    UNIQUE(law_short, version, article_no, paragraph_no, item_no)
);
CREATE INDEX IF NOT EXISTS idx_lp_query  ON legal_provisions(law_short, article_no);
CREATE INDEX IF NOT EXISTS idx_lp_status ON legal_provisions(validity_status);
CREATE INDEX IF NOT EXISTS idx_lp_ver    ON legal_provisions(law_short, version, article_no);

CREATE TABLE IF NOT EXISTS provision_keywords (
    keyword TEXT NOT NULL, law_short TEXT NOT NULL, article_no INTEGER NOT NULL,
    weight REAL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_pk_kw ON provision_keywords(keyword);

-- 全文检索：D1 使用 provision_id 精确回连
CREATE VIRTUAL TABLE IF NOT EXISTS provision_fts USING fts5(
    text, law_short, article_no, provision_id UNINDEXED,
    tokenize='unicode61');

CREATE TABLE IF NOT EXISTS case_registry (
    case_no       TEXT PRIMARY KEY,     -- '（2022）沪01民终12345号'
    year INTEGER, court_code TEXT, court_name TEXT,
    case_type TEXT, seq_no INTEGER,
    cause_action TEXT,                  -- 案由
    judgment_date TEXT,
    exists_in_db INTEGER NOT NULL DEFAULT 1,
    source_url TEXT, doc_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_cr_cause ON case_registry(cause_action);
CREATE INDEX IF NOT EXISTS idx_cr_court ON case_registry(court_code);

CREATE TABLE IF NOT EXISTS judgments (
    judgment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_no TEXT NOT NULL REFERENCES case_registry(case_no),
    court_name TEXT, cause_action TEXT, full_text TEXT, chunks TEXT
);
CREATE INDEX IF NOT EXISTS idx_j_case ON judgments(case_no);

CREATE TABLE IF NOT EXISTS law_lifecycle (
    from_law TEXT NOT NULL, to_law TEXT,
    relation TEXT NOT NULL CHECK(relation IN
        ('废止','修订','替代','修正','新法优于旧法')),
    effective_date TEXT NOT NULL, authority TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS idx_ll_from ON law_lifecycle(from_law);

-- 同义词/别名表：意图识别的确定性词典（从代码下沉到库，便于法学生维护）
CREATE TABLE IF NOT EXISTS law_alias (
    alias TEXT PRIMARY KEY,
    law_short TEXT NOT NULL
);

-- 主题词表：topic -> keyword（供 FTS 与桶划分共用）
CREATE TABLE IF NOT EXISTS topic_keyword (
    topic TEXT NOT NULL,
    keyword TEXT NOT NULL,
    PRIMARY KEY (topic, keyword)
);
