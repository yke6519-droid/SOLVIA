-- 阶段二前置迁移：区分历史实况回测与未来预报预测缓存。
-- 执行前请确认 prediction_cache 中旧缓存已经清理；本迁移不删除业务数据。

ALTER TABLE prediction_cache
    ADD COLUMN weather_data_mode VARCHAR(32) NOT NULL DEFAULT 'forecast'
        COMMENT '气象数据模式: historical_actual/forecast';

ALTER TABLE prediction_cache
    DROP INDEX uk_station_time,
    ADD UNIQUE KEY uk_station_time_mode (station_id, record_time, weather_data_mode);

CREATE INDEX idx_prediction_mode_date
    ON prediction_cache (station_id, predict_date, weather_data_mode, status);
