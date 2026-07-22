-- 移除天气查询侧的 WMO weather_code 字段。
-- 预测缓存中的 weather_type 与此字段无关，继续保留。
-- 迁移可重复执行：字段不存在时不执行删除。
USE solar_agent;

SET @has_forecast_weather_code = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'weather_forecast_cache'
      AND column_name = 'weather_code'
);
SET @sql = IF(
    @has_forecast_weather_code = 1,
    'ALTER TABLE `weather_forecast_cache` DROP COLUMN `weather_code`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_archive_weather_code = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'weather_archive_cache'
      AND column_name = 'weather_code'
);
SET @sql = IF(
    @has_archive_weather_code = 1,
    'ALTER TABLE `weather_archive_cache` DROP COLUMN `weather_code`',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
