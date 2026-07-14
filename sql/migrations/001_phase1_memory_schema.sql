-- 阶段一迁移：补充用户隔离字段并删除危险测试 Event。
DROP EVENT IF EXISTS clean_old_messages_test;

ALTER TABLE message_store
    ADD COLUMN IF NOT EXISTS user_id BIGINT NULL,
    ADD INDEX idx_user_session_created (user_id, session_id, created_at);

ALTER TABLE agent_summary_store
    ADD COLUMN IF NOT EXISTS user_id BIGINT NULL;

-- 历史摘要无法安全判断归属时保持 NULL，由管理员完成归属后再启用严格约束。