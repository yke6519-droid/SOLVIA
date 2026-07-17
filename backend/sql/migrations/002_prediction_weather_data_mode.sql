-- SolarAgent prediction cache migration: weather data mode
-- 数据库：MySQL 8.0+
-- 目标：支持历史实况回测与未来预报两种预测缓存，并允许同一时段分别保存。
-- 本脚本可在已经完成迁移的数据库上重复执行，不删除预测数据。

USE solar_agent;

-- 空库兼容：如果表不存在，按当前运行环境的结构创建。
CREATE TABLE IF NOT EXISTS `prediction_cache`
(
    `id`               BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
    `station_id`       BIGINT       NOT NULL COMMENT '站点ID',
    `record_time`      DATETIME     NOT NULL COMMENT '预测的整点时段',
    `power_kwh`        DECIMAL(10, 2) NOT NULL COMMENT '预测发电量(kWh)',
    `weather_type`     VARCHAR(16)  DEFAULT NULL COMMENT '天气类型(晴天/非晴天)',
    `predict_date`     DATE         NOT NULL COMMENT '预测的目标日期',
    `predicted_at`     DATETIME     DEFAULT CURRENT_TIMESTAMP COMMENT '预测执行时间',
    `status`           TINYINT      DEFAULT 1 COMMENT '状态：1有效 0已失效',
    `created_at`       DATETIME     DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
    `weather_data_mode` VARCHAR(32) NOT NULL DEFAULT 'forecast' COMMENT '气象数据模式：historical_actual/forecast',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_station_time_mode` (`station_id`, `record_time`, `weather_data_mode`),
    KEY `idx_station_date` (`station_id`, `predict_date`),
    KEY `idx_status` (`status`),
    KEY `idx_prediction_mode_date` (`station_id`, `predict_date`, `weather_data_mode`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='预测发电量缓存表';

-- 兼容旧表：先增加模式列，旧记录统一视为未来预报缓存。
SET @has_weather_mode = (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'prediction_cache'
      AND column_name = 'weather_data_mode'
);
SET @sql = IF(
    @has_weather_mode = 0,
    'ALTER TABLE `prediction_cache` ADD COLUMN `weather_data_mode` VARCHAR(32) NOT NULL DEFAULT ''forecast'' COMMENT ''气象数据模式：historical_actual/forecast'' AFTER `created_at`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE `prediction_cache`
SET `weather_data_mode` = 'forecast'
WHERE `weather_data_mode` IS NULL OR TRIM(`weather_data_mode`) = '';

ALTER TABLE `prediction_cache`
    MODIFY COLUMN `weather_data_mode` VARCHAR(32) NOT NULL DEFAULT 'forecast'
        COMMENT '气象数据模式：historical_actual/forecast';

-- 旧版本唯一键为 station_id + record_time，会阻止同一时段保存两种模式。
SET @has_old_unique = (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'prediction_cache'
      AND index_name = 'uk_station_time'
);
SET @sql = IF(
    @has_old_unique > 0,
    'ALTER TABLE `prediction_cache` DROP INDEX `uk_station_time`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_new_unique = (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'prediction_cache'
      AND index_name = 'uk_station_time_mode'
);
SET @sql = IF(
    @has_new_unique = 0,
    'ALTER TABLE `prediction_cache` ADD UNIQUE INDEX `uk_station_time_mode` (`station_id`, `record_time`, `weather_data_mode`)',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_mode_date_index = (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'prediction_cache'
      AND index_name = 'idx_prediction_mode_date'
);
SET @sql = IF(
    @has_mode_date_index = 0,
    'ALTER TABLE `prediction_cache` ADD INDEX `idx_prediction_mode_date` (`station_id`, `predict_date`, `weather_data_mode`, `status`)',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

