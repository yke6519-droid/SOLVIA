-- ============================================================
-- 光伏发电分析 Agent - 数据库建表脚本
-- 数据库: MySQL 8.0+
-- ============================================================

CREATE DATABASE IF NOT EXISTS solar_agent
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE solar_agent;

-- ------------------------------------------------------------
-- 1. 站点维度表
-- ------------------------------------------------------------
DROP TABLE IF EXISTS `power_generation`;
DROP TABLE IF EXISTS `solar_station`;

CREATE TABLE `solar_station` (
    `id`           BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
    `station_code` VARCHAR(64)  NOT NULL                COMMENT '站点编号（业务唯一，如 NBHS-YJ-250）',
    `name`         VARCHAR(128) NOT NULL                COMMENT '站点名称（如 宁波海曙英杰250KW光伏）',
    `capacity_kw`  DECIMAL(10,2) DEFAULT NULL           COMMENT '装机容量(kW)',
    `location`     VARCHAR(255)  DEFAULT NULL           COMMENT '地理位置描述',
    `province`     VARCHAR(64)   DEFAULT NULL           COMMENT '省份',
    `city`         VARCHAR(64)   DEFAULT NULL           COMMENT '城市',
    `longitude`    DECIMAL(10,6) DEFAULT NULL           COMMENT '经度',
    `latitude`     DECIMAL(10,6) DEFAULT NULL           COMMENT '纬度',
    `status`       TINYINT       DEFAULT 1              COMMENT '状态：1启用 0停用',
    `created_at`   DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`   DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_station_code` (`station_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='光伏站点信息表';


-- ------------------------------------------------------------
-- 2. 发电量事实表
-- ------------------------------------------------------------
CREATE TABLE `power_generation` (
    `id`          BIGINT        NOT NULL AUTO_INCREMENT COMMENT '主键',
    `station_id`  BIGINT        NOT NULL                COMMENT '站点ID，关联 solar_station.id',
    `record_time` DATETIME      NOT NULL                COMMENT '发电时段（整点时间）',
    `power_kwh`   DECIMAL(10,2) NOT NULL DEFAULT 0.00   COMMENT '发电量(kWh)',
    `created_at`  DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_station_time` (`station_id`, `record_time`),
    KEY `idx_record_time` (`record_time`),
    KEY `idx_station_time` (`station_id`, `record_time`),
    CONSTRAINT `fk_pg_station` FOREIGN KEY (`station_id`)
        REFERENCES `solar_station` (`id`)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='光伏发电量记录表';
