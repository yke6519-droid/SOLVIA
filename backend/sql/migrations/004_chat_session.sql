-- SolarAgent 会话元数据：每个 session_id 只保存一条会话记录。
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
    KEY idx_chat_session_user_updated (user_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话元数据';
