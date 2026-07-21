-- Refresh Token rotation and revocation support.
-- Run once against the existing SolarAgent database.
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
