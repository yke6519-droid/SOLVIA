-- SolarAgent 对话记忆表（生产安全版）
-- 仅负责建表，不在应用启动时修改 MySQL 全局配置。

CREATE TABLE IF NOT EXISTS chat_session (
    session_id      VARCHAR(255) NOT NULL,
    user_id         BIGINT       NOT NULL,
    title           VARCHAR(10)  NOT NULL DEFAULT '新会话',
    title_source    VARCHAR(20)  NOT NULL DEFAULT 'auto',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_message_at DATETIME     NULL,
    context_json    JSON         NULL,
    PRIMARY KEY (session_id),
    KEY idx_chat_session_user_updated (user_id, updated_at),
    KEY idx_chat_session_user_last_message (user_id, last_message_at, session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话元数据';
CREATE TABLE IF NOT EXISTS message_store (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    session_id  VARCHAR(255) NOT NULL,
    user_id     BIGINT NULL,
    message     TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session_created (session_id, created_at),
    INDEX idx_message_store_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS agent_summary_store (
    session_id  VARCHAR(255) NOT NULL,
    user_id     BIGINT NULL,
    summary     TEXT NOT NULL,
    summary_until_message_id INT NULL,
    summary_version INT NOT NULL DEFAULT 1,
    source_message_count INT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id),
    INDEX idx_summary_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 旧环境升级请执行 sql/migrations/001_phase1_memory_schema.sql。
-- 不在此处创建测试清理 Event，避免对话记录被自动删除。

-- Structured chart snapshots for restoring ECharts in historical conversations.
CREATE TABLE IF NOT EXISTS chart_snapshot_store (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id  VARCHAR(255) NOT NULL,
    user_id     BIGINT NOT NULL,
    message_id  INT NULL,
    chart_data  JSON NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_chart_session_created (session_id, user_id, created_at),
    INDEX idx_chart_message (message_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Revocable, rotating refresh sessions. The raw refresh token is never stored.
CREATE TABLE IF NOT EXISTS auth_refresh_session (
    id                   BIGINT AUTO_INCREMENT PRIMARY KEY,
    token_id             CHAR(32)     NOT NULL,
    token_hash           CHAR(64)     NOT NULL,
    user_id              BIGINT       NOT NULL,
    expires_at           DATETIME     NOT NULL,
    revoked_at           DATETIME     NULL,
    replaced_by_token_id CHAR(32)     NULL,
    last_used_at         DATETIME     NULL,
    created_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_refresh_token_id (token_id),
    UNIQUE KEY uk_refresh_token_hash (token_hash),
    KEY idx_refresh_user_active (user_id, revoked_at, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
