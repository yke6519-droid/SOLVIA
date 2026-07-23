-- 对话附件元数据。文件本体由 FILE_DIR/uploads 存储，Agent 只使用 attachment_id。
CREATE TABLE IF NOT EXISTS file_attachment (
    attachment_id VARCHAR(64) NOT NULL,
    user_id       BIGINT NOT NULL,
    session_id    VARCHAR(255) NOT NULL,
    original_name VARCHAR(255) NOT NULL,
    storage_uri   VARCHAR(1024) NOT NULL,
    content_type  VARCHAR(255) NULL,
    size_bytes    BIGINT NOT NULL,
    sha256        CHAR(64) NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'uploaded',
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at    DATETIME NULL,
    PRIMARY KEY (attachment_id),
    KEY idx_attachment_user_session_created (user_id, session_id, created_at),
    KEY idx_attachment_status_expires (status, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话附件元数据';
