-- ============================================
-- Agent 记忆模块建表脚本
-- ============================================
-- 执行方式: mysql -u root -p solar_agent < memory_schema.sql
-- 或在 MySQL 客户端中直接执行

-- 表1: 全量消息存储
-- SQLChatMessageHistory 也会自动建表,但不会加 created_at 列和索引
-- 这里手动建,为定时清理提供时间列和索引支撑
CREATE TABLE IF NOT EXISTS message_store (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    session_id  VARCHAR(255) NOT NULL,
    message     TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session_created (session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 表2: 对话摘要 (每 session 一行, UPSERT 更新)
CREATE TABLE IF NOT EXISTS agent_summary_store (
    session_id  VARCHAR(255) PRIMARY KEY,
    summary     TEXT NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- MySQL Event: 定时清理旧消息
-- ============================================

-- 开启 event_scheduler (需要 SUPER 权限, root 默认有)
SET GLOBAL event_scheduler = ON;

-- ===== 测试版 =====
-- 每 10 秒执行一次, 删除 10 秒前的消息
-- 测试完毕后用生产版替换
DROP EVENT IF EXISTS clean_old_messages_test;

CREATE EVENT clean_old_messages_test
ON SCHEDULE EVERY 10 SECOND
DO
    DELETE FROM message_store
    WHERE created_at < DATE_SUB(NOW(), INTERVAL 10 SECOND);

-- ===== 生产版 (测试完毕后取消注释, 并注释掉上面的测试版) =====
-- DROP EVENT IF EXISTS clean_old_messages_test;
-- DROP EVENT IF EXISTS clean_old_messages;
-- CREATE EVENT clean_old_messages
-- ON SCHEDULE EVERY 1 DAY STARTS TIMESTAMP(CURRENT_DATE + INTERVAL 1 DAY, '03:00:00')
-- DO
--     DELETE FROM message_store
--     WHERE created_at < DATE_SUB(NOW(), INTERVAL 30 DAY);
