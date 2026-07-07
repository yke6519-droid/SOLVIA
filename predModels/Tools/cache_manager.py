"""
cache_manager.py - 缓存管理模块
=================================
统一管理三类数据的缓存读写:
  1. prediction_cache       预测发电量缓存   (当天结束后逻辑删除)
  2. weather_forecast_cache 未来气象缓存     (过期物理删除)
  3. weather_archive_cache  历史气象缓存     (永久保存)

设计原则:
  - 每个缓存表对应一组 read / write / clean 方法
  - read 方法返回 DataFrame(命中) 或 None(未命中),由调用方决定下一步
  - write 方法用 INSERT IGNORE 幂等写入,重复执行不会报错
  - clean 方法在 read 时被动触发(惰性清理),不依赖定时任务

被以下模块调用:
  - pv_predictor.predict_station_power        (预测结果缓存)
  - weather_fetcher_tool._fetch_from_archive  (历史气象缓存)
  - weather_fetcher_tool._fetch_from_forecast (未来气象缓存)

todo 后续缓存表中数据量变大后，可以改为用 MySQL事件来进行定时清理。
    不依赖于 python进程，MySQL内部自己调度
    ------ 示例 -------
    -- 每 3 小时清理一次过期预测缓存
    CREATE EVENT IF NOT EXISTS clean_prediction_event
    ON SCHEDULE EVERY 3 HOUR
    DO UPDATE prediction_cache SET status = 0
        WHERE status = 1 AND
              predicted_at < DATE_SUB(NOW(), INTERVAL 3 HOUR);
    ------------------
"""
import pandas as pd
from datetime import datetime, date
from typing import Optional
from sqlalchemy import create_engine, text

# 复用 weather_fetcher_tool 里的 MySQL 连接配置
# (两个文件都在 predModels/Tools/ 下,配置保持一致)
MYSQL_URL = "mysql+pymysql://root:755028@localhost:3306/solar_agent?charset=utf8mb4"

# 气象字段列表(open-meteo 拉取后的字段,风分量已转换)
WEATHER_FIELDS = [
    "temperature_2m", "dew_point_2m", "cloud_cover_low",
    "shortwave_radiation", "direct_radiation",
    "10m_u_component_of_wind", "10m_v_component_of_wind",
]
# 数据库列名(去掉了前缀,更简洁)
DB_WEATHER_FIELDS = [
    "temperature_2m", "dew_point_2m", "cloud_cover_low",
    "shortwave_radiation", "direct_radiation",
    "wind_u_component", "wind_v_component",
]


# ============================================================
# 预测发电量缓存
# ============================================================

def read_prediction_cache(station_id: str, predict_date: str) -> Optional[pd.DataFrame]:
    """
    查询预测缓存。

    参数:
        station_id: 站点ID
        predict_date: 预测日期 "YYYY-MM-DD"

    返回:
        命中: DataFrame(time, power_kwh, weather_type) 24行
        未命中: None
    """
    engine = create_engine(MYSQL_URL)
    query = text("""
        SELECT record_time, power_kwh, weather_type
        FROM prediction_cache
        WHERE station_id = :sid
          AND predict_date = :dt
          AND status = 1
        ORDER BY record_time
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"sid": station_id, "dt": predict_date})
    if len(df) == 0:
        return None
    df.rename(columns={"record_time": "time", "power_kwh": "fusion"}, inplace=True)
    print(f"♻️ 预测缓存命中: {station_id} / {predict_date} ({len(df)} 条)")
    return df


def write_prediction_cache(station_id: str, predict_date: str,
                           pred_df: pd.DataFrame, weather_type: str) -> None:
    """
    写入预测缓存。幂等(INSERT IGNORE)。

    参数:
        station_id: 站点ID
        predict_date: 预测日期 "YYYY-MM-DD"
        pred_df: 预测结果 DataFrame(需含 time 和 fusion 列)
        weather_type: 天气类型
    """
    engine = create_engine(MYSQL_URL)
    rows = []
    for _, row in pred_df.iterrows():
        rows.append({
            "station_id": int(station_id),
            "record_time": pd.to_datetime(row["time"]).strftime("%Y-%m-%d %H:%M:%S"),
            "power_kwh": float(row["fusion"]),
            "weather_type": weather_type,
            "predict_date": predict_date,
        })
    with engine.begin() as conn:
        for r in rows:
            conn.execute(text("""
                INSERT IGNORE INTO prediction_cache
                    (station_id, record_time, power_kwh, weather_type, predict_date, status)
                VALUES
                    (:station_id, :record_time, :power_kwh, :weather_type, :predict_date, 1)
            """), r)
    print(f"💾 预测结果已缓存: {station_id} / {predict_date} ({len(rows)} 条)")


def clean_prediction_cache() -> int:
    """
    清理过期的预测缓存(3小时前的预测逻辑删除)。
    在 read_prediction_cache 时被动触发。

    策略: 天气预报每隔几小时会更新,3小时前的预测基于旧预报数据,
    应该失效重算。保留最近3小时的预测缓存即可。

    返回: 清理的记录数
    """
    engine = create_engine(MYSQL_URL)
    # 把"3小时前写入"的预测标记为失效
    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE prediction_cache
            SET status = 0
            WHERE status = 1
              AND predicted_at < DATE_SUB(NOW(), INTERVAL 3 HOUR)
        """))
    return result.rowcount


# ============================================================
# 未来气象缓存 (forecast)
# ============================================================

def read_forecast_cache(station_id: str, target_date: str) -> Optional[pd.DataFrame]:
    """
    查询未来气象缓存。

    参数:
        station_id: 站点ID
        target_date: 目标日期 "YYYY-MM-DD"

    返回:
        命中: DataFrame(open-meteo 格式,24行)
        未命中: None
    """
    # 先惰性清理过期数据(record_time 已过去的)
    _clean_forecast_cache(station_id)

    engine = create_engine(MYSQL_URL)
    query = text("""
        SELECT record_time, temperature_2m, dew_point_2m, cloud_cover_low,
               shortwave_radiation, direct_radiation,
               wind_u_component, wind_v_component
        FROM weather_forecast_cache
        WHERE station_id = :sid
          AND DATE(record_time) = :dt
        ORDER BY record_time
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"sid": station_id, "dt": target_date})
    if len(df) == 0:
        return None
    # 转成 open-meteo 格式(字段名对齐 _fetch_from_forecast 的输出)
    df.rename(columns={
        "record_time": "time",
        "wind_u_component": "10m_u_component_of_wind",
        "wind_v_component": "10m_v_component_of_wind",
    }, inplace=True)
    df["time"] = pd.to_datetime(df["time"])
    print(f"♻️ 未来气象缓存命中: {station_id} / {target_date} ({len(df)} 条)")
    return df


def write_forecast_cache(station_id: str, df: pd.DataFrame) -> None:
    """
    写入未来气象缓存。幂等。
    参数:
        station_id: 站点ID
        df: open-meteo 格式 DataFrame(含 time + 7 个气象字段)
    """
    engine = create_engine(MYSQL_URL)
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "station_id": int(station_id),
            "record_time": pd.to_datetime(row["time"]).strftime("%Y-%m-%d %H:%M:%S"),
            "temperature_2m": float(row.get("temperature_2m", 0)),
            "dew_point_2m": float(row.get("dew_point_2m", 0)),
            "cloud_cover_low": float(row.get("cloud_cover_low", 0)),
            "shortwave_radiation": float(row.get("shortwave_radiation", 0)),
            "direct_radiation": float(row.get("direct_radiation", 0)),
            "wind_u_component": float(row.get("10m_u_component_of_wind", 0)),
            "wind_v_component": float(row.get("10m_v_component_of_wind", 0)),
        })
    with engine.begin() as conn:
        for r in rows:
            conn.execute(text("""
                INSERT IGNORE INTO weather_forecast_cache
                    (station_id, record_time, temperature_2m, dew_point_2m,
                     cloud_cover_low, shortwave_radiation, direct_radiation,
                     wind_u_component, wind_v_component)
                VALUES
                    (:station_id, :record_time, :temperature_2m, :dew_point_2m,
                     :cloud_cover_low, :shortwave_radiation, :direct_radiation,
                     :wind_u_component, :wind_v_component)
            """), r)
    print(f"💾 未来气象已缓存: {station_id} ({len(rows)} 条)")


def _clean_forecast_cache(station_id: str) -> int:
    """
    清理已过期的未来气象(record_time 已过去的小时)。
    过去的小时不再需要缓存,直接物理删除。
    """
    engine = create_engine(MYSQL_URL)
    with engine.begin() as conn:
        result = conn.execute(text("""
            DELETE FROM weather_forecast_cache
            WHERE station_id = :sid
              AND record_time < NOW()
        """), {"sid": station_id})
    return result.rowcount


# ============================================================
# 历史气象缓存 (archive,永久保存)
# ============================================================

def read_archive_cache(station_id: str, target_date: str) -> Optional[pd.DataFrame]:
    """
    查询历史气象缓存。历史数据永久保存,无需清理。

    参数:
        station_id: 站点ID
        target_date: 目标日期 "YYYY-MM-DD"

    返回:
        命中: DataFrame(open-meteo 格式,24行)
        未命中: None
    """
    engine = create_engine(MYSQL_URL)
    query = text("""
        SELECT record_time, temperature_2m, dew_point_2m, cloud_cover_low,
               shortwave_radiation, direct_radiation,
               wind_u_component, wind_v_component
        FROM weather_archive_cache
        WHERE station_id = :sid
          AND DATE(record_time) = :dt
        ORDER BY record_time
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"sid": station_id, "dt": target_date})
    if len(df) == 0:
        return None
    df.rename(columns={
        "record_time": "time",
        "wind_u_component": "10m_u_component_of_wind",
        "wind_v_component": "10m_v_component_of_wind",
    }, inplace=True)
    df["time"] = pd.to_datetime(df["time"])
    print(f"♻️ 历史气象缓存命中: {station_id} / {target_date} ({len(df)} 条)")
    return df


def write_archive_cache(station_id: str, df: pd.DataFrame) -> None:
    """
    写入历史气象缓存。幂等。永久保存,不清理。
    """
    engine = create_engine(MYSQL_URL)
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "station_id": int(station_id),
            "record_time": pd.to_datetime(row["time"]).strftime("%Y-%m-%d %H:%M:%S"),
            "temperature_2m": float(row.get("temperature_2m", 0)),
            "dew_point_2m": float(row.get("dew_point_2m", 0)),
            "cloud_cover_low": float(row.get("cloud_cover_low", 0)),
            "shortwave_radiation": float(row.get("shortwave_radiation", 0)),
            "direct_radiation": float(row.get("direct_radiation", 0)),
            "wind_u_component": float(row.get("10m_u_component_of_wind", 0)),
            "wind_v_component": float(row.get("10m_v_component_of_wind", 0)),
        })
    with engine.begin() as conn:
        for r in rows:
            conn.execute(text("""
                INSERT IGNORE INTO weather_archive_cache
                    (station_id, record_time, temperature_2m, dew_point_2m,
                     cloud_cover_low, shortwave_radiation, direct_radiation,
                     wind_u_component, wind_v_component)
                VALUES
                    (:station_id, :record_time, :temperature_2m, :dew_point_2m,
                     :cloud_cover_low, :shortwave_radiation, :direct_radiation,
                     :wind_u_component, :wind_v_component)
            """), r)
    print(f"💾 历史气象已缓存: {station_id} ({len(rows)} 条)")
