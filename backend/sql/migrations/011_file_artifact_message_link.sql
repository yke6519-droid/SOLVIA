-- 将 Agent 生成的文件产物绑定到具体助手消息，历史会话可恢复文件卡片。
-- 迁移可重复执行。
USE solar_agent;

SET @has_file_message_id = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'file_artifact'
      AND column_name = 'message_id'
);
SET @sql = IF(
    @has_file_message_id = 0,
    'ALTER TABLE `file_artifact` ADD COLUMN `message_id` BIGINT NULL AFTER `session_id`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_file_message_index = (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'file_artifact'
      AND index_name = 'idx_file_artifact_message'
);
SET @sql = IF(
    @has_file_message_index = 0,
    'ALTER TABLE `file_artifact` ADD KEY `idx_file_artifact_message` (`session_id`, `user_id`, `message_id`)',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
