-- ============================================================
-- 光伏发电分析 Agent - 缓存表建表脚本
-- 数据库: MySQL 8.0+
-- ============================================================
-- 三张缓存表，对应三种数据生命周期:
--   1. prediction_cache       预测发电量缓存   (当天结束后逻辑删除)
--   2. weather_forecast_cache 未来气象缓存     (到期直接删除)
--   3. weather_archive_cache  历史气象缓存     (永久保存)
-- ============================================================

USE solar_agent;

-- ------------------------------------------------------------
-- 3. 预测发电量缓存表
-- ------------------------------------------------------------
DROP TABLE IF EXISTS `prediction_cache`;
CREATE TABLE `prediction_cache`
(
    `id`            BIGINT         NOT NULL AUTO_INCREMENT COMMENT '主键',
    `station_id`    BIGINT         NOT NULL COMMENT '站点ID',
    `record_time`   DATETIME       NOT NULL COMMENT '预测的整点时段',
    `power_kwh`     DECIMAL(10, 2) NOT NULL COMMENT '预测发电量(kWh)',
    `weather_type`  VARCHAR(16)    DEFAULT NULL COMMENT '天气类型(晴天/非晴天)',
    `predict_date`  DATE           NOT NULL COMMENT '预测的目标日期(冗余，方便按日查询)',
    `predicted_at`  DATETIME       DEFAULT CURRENT_TIMESTAMP COMMENT '预测执行时间',
    `status`        TINYINT        DEFAULT 1 COMMENT '状态: 1有效 0已失效(逻辑删除)',
    `created_at`    DATETIME       DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_station_time` (`station_id`, `record_time`),
    KEY `idx_station_date` (`station_id`, `predict_date`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='预测发电量缓存表';

-- ------------------------------------------------------------
-- 4. 未来气象数据缓存表 (forecast API)
-- ------------------------------------------------------------
DROP TABLE IF EXISTS `weather_forecast_cache`;
CREATE TABLE `weather_forecast_cache`
(
    `id`                        BIGINT          NOT NULL AUTO_INCREMENT COMMENT '主键',
    `station_id`                BIGINT          NOT NULL COMMENT '站点ID',
    `record_time`               DATETIME        NOT NULL COMMENT '气象整点时段',
    `temperature_2m`            DECIMAL(10, 2)  DEFAULT NULL COMMENT '2m温度(°C)',
    `dew_point_2m`              DECIMAL(10, 2)  DEFAULT NULL COMMENT '2m露点(°C)',
    `cloud_cover_low`           DECIMAL(10, 2)  DEFAULT NULL COMMENT '低云量(%)',
    `shortwave_radiation`       DECIMAL(10, 2)  DEFAULT NULL COMMENT '短波辐射(W/m²)',
    `direct_radiation`          DECIMAL(10, 2)  DEFAULT NULL COMMENT '直接辐射(W/m²)',
    `wind_u_component`          DECIMAL(10, 2)  DEFAULT NULL COMMENT '10m风U分量',
    `wind_v_component`          DECIMAL(10, 2)  DEFAULT NULL COMMENT '10m风V分量',
    `fetched_at`                DATETIME        DEFAULT CURRENT_TIMESTAMP COMMENT '拉取时间',
    `created_at`                DATETIME        DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_station_time` (`station_id`, `record_time`),
    KEY `idx_station_date` (`station_id`, `record_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='未来气象数据缓存表';

-- ------------------------------------------------------------
-- 5. 历史气象数据缓存表 (archive API，永久保存)
-- ------------------------------------------------------------
DROP TABLE IF EXISTS `weather_archive_cache`;
CREATE TABLE `weather_archive_cache`
(
    `id`                        BIGINT          NOT NULL AUTO_INCREMENT COMMENT '主键',
    `station_id`                BIGINT          NOT NULL COMMENT '站点ID',
    `record_time`               DATETIME        NOT NULL COMMENT '气象整点时段',
    `temperature_2m`            DECIMAL(10, 2)  DEFAULT NULL COMMENT '2m温度(°C)',
    `dew_point_2m`              DECIMAL(10, 2)  DEFAULT NULL COMMENT '2m露点(°C)',
    `cloud_cover_low`           DECIMAL(10, 2)  DEFAULT NULL COMMENT '低云量(%)',
    `shortwave_radiation`       DECIMAL(10, 2)  DEFAULT NULL COMMENT '短波辐射(W/m²)',
    `direct_radiation`          DECIMAL(10, 2)  DEFAULT NULL COMMENT '直接辐射(W/m²)',
    `wind_u_component`          DECIMAL(10, 2)  DEFAULT NULL COMMENT '10m风V分量',
    `wind_v_component`          DECIMAL(10, 2)  DEFAULT NULL COMMENT '10m风V分量',
    `fetched_at`                DATETIME        DEFAULT CURRENT_TIMESTAMP COMMENT '拉取时间',
    `created_at`                DATETIME        DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_station_time` (`station_id`, `record_time`),
    KEY `idx_station_date` (`station_id`, `record_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='历史气象数据缓存表(永久保存)';
