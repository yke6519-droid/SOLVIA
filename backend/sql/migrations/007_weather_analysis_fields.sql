-- 为历史/未来气象缓存增加天气分析字段。
--
-- 说明：这些字段只用于天气查询和分析，不会进入 pv_predictor 的原有特征白名单。
-- 迁移可重复执行，已存在的列不会重复添加，也不会删除已有气象缓存数据。
USE solar_agent;

SET @has_forecast_precipitation = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'weather_forecast_cache'
      AND column_name = 'precipitation'
);
SET @sql = IF(
    @has_forecast_precipitation = 0,
    'ALTER TABLE `weather_forecast_cache` ADD COLUMN `precipitation` DECIMAL(10, 2) NULL COMMENT ''总降水量(mm)''',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_forecast_sunshine = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'weather_forecast_cache'
      AND column_name = 'sunshine_duration'
);
SET @sql = IF(
    @has_forecast_sunshine = 0,
    'ALTER TABLE `weather_forecast_cache` ADD COLUMN `sunshine_duration` DECIMAL(12, 2) NULL COMMENT ''有效日照时长(秒)''',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_archive_precipitation = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'weather_archive_cache'
      AND column_name = 'precipitation'
);
SET @sql = IF(
    @has_archive_precipitation = 0,
    'ALTER TABLE `weather_archive_cache` ADD COLUMN `precipitation` DECIMAL(10, 2) NULL COMMENT ''总降水量(mm)''',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_archive_sunshine = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'weather_archive_cache'
      AND column_name = 'sunshine_duration'
);
SET @sql = IF(
    @has_archive_sunshine = 0,
    'ALTER TABLE `weather_archive_cache` ADD COLUMN `sunshine_duration` DECIMAL(12, 2) NULL COMMENT ''有效日照时长(秒)''',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
