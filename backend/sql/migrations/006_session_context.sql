-- Compact durable context for explicit station selections.
-- Existing sessions keep NULL and are treated as an empty context.
USE solar_agent;

SET @has_session_context = (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'chat_session'
      AND column_name = 'context_json'
);
SET @sql = IF(
    @has_session_context = 0,
    'ALTER TABLE `chat_session` ADD COLUMN `context_json` JSON NULL',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
