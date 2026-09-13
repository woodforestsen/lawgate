-- ============================================================================
-- 溯源与质量扩展表（不属于手册 schema 主体，单独文件，便于"手册 schema 原样"
-- 与"工程增量"分离）。ingest_provenance 记录每个法源的获取方式与可信状态，
-- 是 data/kb/qa_report.md 与图注自动生成的依据。
-- ============================================================================

CREATE TABLE IF NOT EXISTS ingest_provenance (
    law_short        TEXT NOT NULL,
    version          TEXT NOT NULL DEFAULT '',
    -- 原始来源：FLK_DOCX / FLK_WEB / OFFICIAL_WEB / MANUAL_TRANSCRIPT / SEED
    source_kind      TEXT NOT NULL,
    source_url       TEXT,
    retrieval_date   TEXT,
    -- 人工/自动核验状态：VERIFIED / PENDING_FLK_VERIFICATION / UNVERIFIED
    verification     TEXT NOT NULL DEFAULT 'UNVERIFIED',
    verified_by      TEXT,
    verified_date    TEXT,
    n_provisions     INTEGER,
    continuity_gaps  TEXT,      -- JSON 数组字符串，如 "[667, 668]"
    note             TEXT,
    PRIMARY KEY (law_short, version)
);

-- 解析抽验记录（手册 S2.4 第 2、3 步：10% 人工抽验 ≥95%）
CREATE TABLE IF NOT EXISTS parse_audit (
    audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    law_short    TEXT NOT NULL,
    article_no   INTEGER NOT NULL,
    paragraph_no INTEGER,
    field        TEXT,          -- article_no / paragraph_split / text_match
    verdict      TEXT NOT NULL CHECK(verdict IN ('ok','mismatch')),
    auditor      TEXT,
    audit_date   TEXT,
    note         TEXT
);

-- 案号核验样本（用于 E6 与人工测试记录）
CREATE TABLE IF NOT EXISTS case_verify_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    case_no     TEXT NOT NULL,
    claimed_cause TEXT,
    expected    TEXT,
    got_level   TEXT,
    got_passed  INTEGER,
    run_date    TEXT
);
