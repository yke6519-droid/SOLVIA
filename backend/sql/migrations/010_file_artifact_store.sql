-- Agent 生成文件的持久化元数据；文件本体第一阶段保存在 FILE_DIR。
CREATE TABLE IF NOT EXISTS file_artifact (
    file_id       VARCHAR(64) NOT NULL,
    user_id       BIGINT NOT NULL,
    session_id    VARCHAR(255) NOT NULL,
    original_name VARCHAR(255) NOT NULL,
    storage_uri   VARCHAR(1024) NOT NULL,
    content_type  VARCHAR(255) NULL,
    size_bytes    BIGINT NOT NULL,
    sha256        CHAR(64) NOT NULL,
    source_tool   VARCHAR(64) NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'ready',
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at    DATETIME NULL,
    PRIMARY KEY (file_id),
    KEY idx_file_artifact_user_session_created (user_id, session_id, created_at),
    KEY idx_file_artifact_status_expires (status, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 生成文件产物元数据';
