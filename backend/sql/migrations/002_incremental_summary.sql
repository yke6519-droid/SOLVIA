-- SolarAgent incremental-summary state
-- Adds a durable cursor and optimistic version; no message rows are deleted.
USE solar_agent;

SET @has_summary_cursor = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'agent_summary_store'
      AND column_name = 'summary_until_message_id'
);
SET @sql = IF(
    @has_summary_cursor = 0,
    'ALTER TABLE `agent_summary_store` ADD COLUMN `summary_until_message_id` INT NULL AFTER `summary`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_summary_version = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'agent_summary_store'
      AND column_name = 'summary_version'
);
SET @sql = IF(
    @has_summary_version = 0,
    'ALTER TABLE `agent_summary_store` ADD COLUMN `summary_version` INT NOT NULL DEFAULT 1 AFTER `summary_until_message_id`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_source_count = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'agent_summary_store'
      AND column_name = 'source_message_count'
);
SET @sql = IF(
    @has_source_count = 0,
    'ALTER TABLE `agent_summary_store` ADD COLUMN `source_message_count` INT NOT NULL DEFAULT 0 AFTER `summary_version`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
