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