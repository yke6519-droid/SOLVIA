-- Session-list keyset pagination support.
-- Backfill missing timestamps, then add the index used by the list query.
USE solar_agent;

UPDATE chat_session
SET last_message_at = COALESCE(last_message_at, updated_at)
WHERE last_message_at IS NULL;

SET @has_session_cursor_index = (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'chat_session'
      AND index_name = 'idx_chat_session_user_last_message'
);
SET @sql = IF(
    @has_session_cursor_index = 0,
    'ALTER TABLE `chat_session` ADD INDEX `idx_chat_session_user_last_message` (`user_id`, `last_message_at`, `session_id`)',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
