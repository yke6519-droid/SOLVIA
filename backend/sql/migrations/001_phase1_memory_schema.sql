-- SolarAgent Phase 1 memory schema migration
-- 目标：将已有环境升级到当前可运行的消息/摘要表结构。
-- 数据库：MySQL 8.0+
-- 说明：本迁移不删除消息，不修改历史会话归属，也不强制重建主键。

USE solar_agent;

-- 空库兼容：如果表不存在，按当前运行环境的结构创建。
CREATE TABLE IF NOT EXISTS `message_store`
(
    `id`         INT AUTO_INCREMENT PRIMARY KEY,
    `session_id` VARCHAR(255) NOT NULL,
    `user_id`    BIGINT       NULL,
    `message`    TEXT         NOT NULL,
    `created_at` TIMESTAMP    NULL DEFAULT CURRENT_TIMESTAMP,
    KEY `idx_session_created` (`session_id`, `created_at`),
    KEY `idx_message_store_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `agent_summary_store`
(
    `session_id` VARCHAR(255) NOT NULL,
    `user_id`    BIGINT       NULL,
    `summary`    TEXT         NOT NULL,
    `updated_at` TIMESTAMP    NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`session_id`),
    KEY `idx_summary_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 兼容更早的 message_store：缺列时才补充，不影响已有消息。
SET @has_message_user_id = (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'message_store'
      AND column_name = 'user_id'
);
SET @sql = IF(
    @has_message_user_id = 0,
    'ALTER TABLE `message_store` ADD COLUMN `user_id` BIGINT NULL AFTER `session_id`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 兼容更早的 agent_summary_store：缺列时才补充。
SET @has_summary_user_id = (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'agent_summary_store'
      AND column_name = 'user_id'
);
SET @sql = IF(
    @has_summary_user_id = 0,
    'ALTER TABLE `agent_summary_store` ADD COLUMN `user_id` BIGINT NULL AFTER `session_id`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 当前运行库需要的用户索引，存在时跳过。
SET @has_message_user_index = (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'message_store'
      AND index_name = 'idx_message_store_user_id'
);
SET @sql = IF(
    @has_message_user_index = 0,
    'ALTER TABLE `message_store` ADD INDEX `idx_message_store_user_id` (`user_id`)',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_summary_user_index = (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'agent_summary_store'
      AND index_name = 'idx_summary_user'
);
SET @sql = IF(
    @has_summary_user_index = 0,
    'ALTER TABLE `agent_summary_store` ADD INDEX `idx_summary_user` (`user_id`)',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

