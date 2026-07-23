-- 统一数据制品持久化：实际、预测、天气和其他结构化查询共用。
CREATE TABLE IF NOT EXISTS dataset_artifact_store (
    artifact_id   VARCHAR(64) NOT NULL,
    user_id       BIGINT NOT NULL,
    session_id    VARCHAR(255) NOT NULL,
    artifact_type VARCHAR(64) NOT NULL,
    source_tool   VARCHAR(128) NULL,
    schema_json   LONGTEXT NOT NULL,
    rows_json     LONGTEXT NOT NULL,
    metadata_json LONGTEXT NOT NULL,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (artifact_id),
    KEY idx_dataset_artifact_user_session_created (user_id, session_id, created_at),
    KEY idx_dataset_artifact_source (source_tool, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 统一结构化数据制品';
