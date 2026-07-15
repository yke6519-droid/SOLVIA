# =============================================================================
# 文件名: autoPred_英杰_hourly_post_schedule.py
# 说明: 英杰光伏预测 - 每小时58分触发，预测并上传下一小时数据
# 功能:
#   1. 每小时58分自动触发
#   2. 拉取昨天24h历史气象 + 今天24h未来气象
#   3. 预测24小时，提取下一小时结果
#   4. POST到API（带重试机制）
#   5. 保存气象数据（文件名标记执行时间）
#   6. 保存预测结果
# =============================================================================

import numpy as np
import pandas as pd
import joblib
import tensorflow as tf
import warnings
import os
import requests
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import schedule
import time
import json


# ===================== 【必须加：注册自定义损失函数】=====================
@tf.keras.saving.register_keras_serializable()
def radiation_aware_loss(y_true, y_pred):
    weight = tf.where(y_true > 100, 4.0, 1.0)
    mse = tf.square(y_true - y_pred)
    return tf.reduce_mean(mse * weight)


@tf.keras.saving.register_keras_serializable()
def radiation_stable_loss(y_true, y_pred):
    weight = tf.where(y_true > 100, 5.0, 1.0)
    mse = tf.square(y_true - y_pred)
    pred_diff = tf.abs(y_pred[1:] - y_pred[:-1])
    diff_penalty = 0.5 * tf.reduce_mean(pred_diff)
    return tf.reduce_mean(mse * weight) + diff_penalty


# ===================== 【配置区】 =====================
warnings.filterwarnings('ignore')
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
plt.rcParams["font.family"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# 模型路径
MODEL_SUNNY = r"D:\AAA光伏项目\TRAIN\Jiupian\Results\英杰\晴天_5-8"
MODEL_CLOUDY = r"D:\AAA光伏项目\TRAIN\Jiupian\Results\英杰\新融合_多云_ssrd增强版"

TIME_STEP = 24

# 数据保存根路径
BASE_SAVE_DIR = r"D:\AAA光伏项目\dataCatch\data\英杰_hourly_post_predictions"

# 气象API配置
LAT = 29.78
LON = 121.36
VARIABLES = [
    "temperature_2m", "dew_point_2m", "cloud_cover_low",
    "wind_speed_10m", "wind_direction_10m",
    "shortwave_radiation", "direct_radiation",
]

# API配置
API_URL = "http://120.26.86.38:8000/api/pvEnergyStoreChargeConsumer/pvShortForecast/addPrediction"
STATION_ID = "2003021982752313403" #英杰站点
MAX_RETRY = 3
RETRY_DELAY = 5  # 秒


# ===================== 工具函数 =====================
def get_timestamp():
    """获取当前时间戳"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def get_formatted_time():
    """获取格式化时间字符串（用于文件名标记）"""
    return datetime.now().strftime("%Y%m%d_%H%M")


def get_target_dates():
    """计算基准日期和预测日期"""
    today = datetime.now().date()
    base_date = today - timedelta(days=1)
    predict_date = today
    
    return {
        "base_date_str": base_date.strftime("%Y-%m-%d"),
        "predict_date_str": predict_date.strftime("%Y-%m-%d"),
        "base_date_md": base_date.strftime("%m%d"),
        "predict_date_md": predict_date.strftime("%m%d"),
    }


def get_next_hour_info():
    """获取下一个小时的信息"""
    now = datetime.now()
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return {
        "next_hour_dt": next_hour,
        "next_hour_str": next_hour.strftime("%Y-%m-%d %H:%M:%S"),
        "next_hour_idx": next_hour.hour,
    }


# ===================== 气象数据拉取 =====================
def get_base_day_weather(target_date):
    """拉取基准日期24小时历史气象"""
    print(f"📥 拉取历史气象: {target_date}")
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "start_date": target_date,
        "end_date": target_date,
        "hourly": ",".join(VARIABLES),
        "timezone": "Asia/Shanghai",
    }

    try:
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()
        df = pd.DataFrame(data["hourly"])
        df['time'] = pd.to_datetime(df['time'])
        df = df.sort_values("time").reset_index(drop=True)
        df = df.head(24).reset_index(drop=True)

        wind_dir_rad = np.radians(df['wind_direction_10m'])
        df['10m_u_component_of_wind'] = df['wind_speed_10m'] * np.sin(wind_dir_rad)
        df['10m_v_component_of_wind'] = df['wind_speed_10m'] * np.cos(wind_dir_rad)
        df.drop(columns=['wind_speed_10m', 'wind_direction_10m'], inplace=True)
        
        print(f"✅ 历史气象拉取完成：{len(df)} 条")
        return df
    except Exception as e:
        print(f"❌ 历史气象拉取失败：{e}")
        return None


def get_predict_day_weather(target_date):
    """拉取预测日期24小时未来气象"""
    print(f"📥 拉取未来24小时气象: {target_date}")
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": ",".join(VARIABLES),
        "timezone": "Asia/Shanghai",
        "start_date": target_date,
        "end_date": target_date,
    }

    try:
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()
        df = pd.DataFrame(data["hourly"])
        df['time'] = pd.to_datetime(df['time'])
        df = df.sort_values("time").reset_index(drop=True)
        df = df.head(24).reset_index(drop=True)

        wind_dir_rad = np.radians(df['wind_direction_10m'])
        df['10m_u_component_of_wind'] = df['wind_speed_10m'] * np.sin(wind_dir_rad)
        df['10m_v_component_of_wind'] = df['wind_speed_10m'] * np.cos(wind_dir_rad)
        df.drop(columns=['wind_speed_10m', 'wind_direction_10m'], inplace=True)
        
        print(f"✅ 未来气象拉取完成：{len(df)} 条")
        return df
    except Exception as e:
        print(f"❌ 未来气象拉取失败：{e}")
        return None


# ===================== 【核心：保存气象数据】=====================
def save_weather_data(history_raw, future_raw, save_dir, exec_time_label):
    """
    保存原始气象数据
    文件名格式: history_0601_20260602_1058.xlsx (标记执行时间)
    """
    os.makedirs(save_dir, exist_ok=True)
    
    dates = get_target_dates()
    
    # 保存历史气象（原始数据）
    history_path = os.path.join(save_dir, f"history_{dates['base_date_md']}_{exec_time_label}.xlsx")
    history_raw.to_excel(history_path, index=False)
    
    # 保存未来气象（原始数据）
    future_path = os.path.join(save_dir, f"future_{dates['predict_date_md']}_{exec_time_label}.xlsx")
    future_raw.to_excel(future_path, index=False)
    
    print(f"💾 气象数据已保存:")
    print(f"   历史: {history_path}")
    print(f"   未来: {future_path}")
    
    return history_path, future_path


# ===================== 数据格式转换 =====================
def convert_to_era5_format(df_meteo):
    """转换为ERA5格式"""
    df = df_meteo.copy()
    df["lcc"] = df["cloud_cover_low"] / 100.0
    df["ssrd"] = df["shortwave_radiation"]
    df["fdir"] = df["direct_radiation"]
    df["t2m"] = df["temperature_2m"]
    df["d2m"] = df["dew_point_2m"]

    u = df["10m_u_component_of_wind"]
    v = df["10m_v_component_of_wind"]
    df["wind_speed"] = np.sqrt(u ** 2 + v ** 2)
    df["wind_direction"] = (270 - np.arctan2(v, u) * 180 / np.pi) % 360

    target_cols = ["time", "t2m", "d2m", "lcc", "ssrd", "fdir", "wind_speed", "wind_direction"]
    df_result = df[target_cols].copy()
    for col in ["ssrd", "fdir", "wind_speed"]:
        df_result[col] = df_result[col].clip(lower=0)
    return df_result


# ===================== 特征工程 =====================
def build_features(df):
    """构建特征"""
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    for col in ["t2m", "d2m", "lcc", "ssrd", "fdir", "wind_speed", "wind_direction"]:
        if col in df.columns:
            df[col] = np.clip(df[col], 0, None)

    t = df["time"].dt
    df["hour"] = t.hour
    df["month"] = t.month
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["month_sin"] = np.sin(2 * np.pi * (df["month"] - 1) / 12)
    df["month_cos"] = np.cos(2 * np.pi * (df["month"] - 1) / 12)

    df["ssrd"] = np.clip(df["ssrd"], 0, None)
    df["fdir"] = np.clip(df["fdir"], 0, None)

    df["ssrd_diff"] = df["ssrd"].diff().fillna(0)
    df["fdir_diff"] = df["fdir"].diff().fillna(0)
    df["ssrd_abs_change"] = np.abs(df["ssrd_diff"])
    df["fdir_abs_change"] = np.abs(df["fdir_diff"])

    df["ssrd_fdir_ratio"] = df["ssrd"] / (df["fdir"] + 1e-6)
    df["ssrd_power3"] = df["ssrd"] ** 3
    df["fdir_power3"] = df["fdir"] ** 3

    df["rad_total"] = df["ssrd"] + df["fdir"]
    df["rad_fluctuation"] = (df["ssrd_abs_change"] + df["fdir_abs_change"]) / 2
    df["rad_rate"] = (df["ssrd"] + df["fdir"]) / (df["t2m"] + 1e-6)
    df["ssrd_t2m_ratio"] = df["ssrd"] / (df["t2m"] + 1e-6)
    df["ssrd_lcc_corrected"] = df["ssrd"] * (1 - df["lcc"] / 100)

    return df


# ===================== 模型加载 =====================
def load_all_models(weather_type):
    """加载模型"""
    if weather_type == "晴天":
        MODEL_SAVE_DIR = MODEL_SUNNY
        print("✅ 使用【晴天模型】")
    else:
        MODEL_SAVE_DIR = MODEL_CLOUDY
        print("✅ 使用【多云/非晴天模型】")

    MODEL_PATHS = {
        "xgb": os.path.join(MODEL_SAVE_DIR, "xgb_model.model.pkl"),
        "lgb": os.path.join(MODEL_SAVE_DIR, "lgb_model.pkl"),
        "lstm": os.path.join(MODEL_SAVE_DIR, "lstm_model.keras"),
        "lstnet": os.path.join(MODEL_SAVE_DIR, "lstnet_model.keras"),
        "scaler": os.path.join(MODEL_SAVE_DIR, "scaler.pkl"),
        "feature_cols": os.path.join(MODEL_SAVE_DIR, "feature_cols.txt"),
        "ridge_xgb": os.path.join(MODEL_SAVE_DIR, "ridge_xgb_correction.pkl"),
        "ridge_lgb": os.path.join(MODEL_SAVE_DIR, "ridge_lgb_correction.pkl"),
        "ridge_lstm": os.path.join(MODEL_SAVE_DIR, "ridge_lstm_correction.pkl"),
        "ridge_lstnet": os.path.join(MODEL_SAVE_DIR, "ridge_lstnet_correction.pkl"),
        "meta_model": os.path.join(MODEL_SAVE_DIR, "meta_stacking_model.pkl"),
    }

    with open(MODEL_PATHS["feature_cols"], "r", encoding="utf-8") as f:
        feat_cols = [line.strip() for line in f.readlines()]

    scaler = joblib.load(MODEL_PATHS["scaler"])
    xgb_model = joblib.load(MODEL_PATHS["xgb"])
    lgb_model = joblib.load(MODEL_PATHS["lgb"])
    lstm = tf.keras.models.load_model(MODEL_PATHS["lstm"])
    lstnet = tf.keras.models.load_model(MODEL_PATHS["lstnet"])

    corr = {
        "xgb": joblib.load(MODEL_PATHS["ridge_xgb"]),
        "lgb": joblib.load(MODEL_PATHS["ridge_lgb"]),
        "lstm": joblib.load(MODEL_PATHS["ridge_lstm"]),
        "lstnet": joblib.load(MODEL_PATHS["ridge_lstnet"]),
    }

    meta_model = joblib.load(MODEL_PATHS["meta_model"])
    return feat_cols, scaler, xgb_model, lgb_model, lstm, lstnet, corr, meta_model


# ===================== 纠偏和融合 =====================
def correct_pred(raw_pred, hour, month, ssrd, fdir, lcc, ssrd_diff, fdir_diff, model, pred_history):
    """纠偏"""
    hour_sin = np.sin(2 * np.pi * hour / 24)
    hour_cos = np.cos(2 * np.pi * hour / 24)
    month_sin = np.sin(2 * np.pi * (month - 1) / 12)
    month_cos = np.cos(2 * np.pi * (month - 1) / 12)
    ssrd_pred_ratio = ssrd / (raw_pred + 1e-6)

    pred_1h_ago = pred_history[-1] if len(pred_history) >= 1 else 0.0
    pred_3h_ago = pred_history[-3] if len(pred_history) >= 3 else 0.0
    pred_6h_ago = pred_history[-6] if len(pred_history) >= 6 else 0.0
    pred_12h_ago = pred_history[-12] if len(pred_history) >= 12 else 0.0

    true_1h_ago = pred_1h_ago
    true_3h_ago = pred_3h_ago
    err_1h_ago = np.abs(pred_1h_ago - true_1h_ago)
    err_3h_ago = np.abs(pred_3h_ago - true_3h_ago)

    X = [[
        raw_pred, raw_pred ** 2,
        hour, month, hour_sin, hour_cos, month_sin, month_cos,
        ssrd, fdir, lcc, ssrd_pred_ratio,
        ssrd_diff, fdir_diff,
        pred_1h_ago, pred_3h_ago, pred_6h_ago, pred_12h_ago,
        true_1h_ago, true_3h_ago,
        err_1h_ago, err_3h_ago
    ]]
    return max(model.predict(X, verbose=0)[0], 0)


def stacking_predict(c_xgb, c_lgb, c_lstm, c_lstnet, meta_model):
    """Stacking融合"""
    X_meta = [
        c_xgb, c_lgb, c_lstm, c_lstnet,
        abs(c_xgb - c_lstm), abs(c_lstm - c_lstnet),
        (c_xgb + c_lgb) / 2, (c_lstm + c_lstnet) / 2,
        c_xgb * 1.5, c_lgb * 1.5
    ]
    X_meta = np.array(X_meta).reshape(1, -1)
    fusion_pred = meta_model.predict(X_meta)[0]
    return max(fusion_pred, 0)


# ===================== 天气判断 =====================
def judge_weather_type(future_df):
    """判断天气类型"""
    df = future_df.copy()
    df["hour"] = pd.to_datetime(df["time"]).dt.hour
    day_hour = df[(df.hour >= 6) & (df.hour <= 19)].copy()

    if len(day_hour) == 0:
        return "夜间"

    avg_ssrd = day_hour["ssrd"].mean()
    max_ssrd = day_hour["ssrd"].max()
    is_sunny_ssrd = (avg_ssrd >= 350) and (max_ssrd >= 700)

    return "晴天" if is_sunny_ssrd else "非晴天"


# ===================== 物理约束 =====================
def get_lcc_adjust_factor(pred_df, future_df):
    """LCC云层约束"""
    df = future_df.copy()
    df = df.sort_values("time").reset_index(drop=True)
    df["lcc_diff"] = df["lcc"].diff().fillna(0)
    lcc_diff_series = df["lcc_diff"].values

    factor_lcc_list = np.ones(len(pred_df))
    for i in range(len(pred_df)):
        diff = lcc_diff_series[i]
        abs_diff = abs(diff)

        if diff > 0:
            if abs_diff >= 0.15:
                factor = 1.0 - 0.08
            elif abs_diff >= 0.10:
                factor = 1.0 - 0.06
            elif abs_diff >= 0.05:
                factor = 1.0 - 0.04
            else:
                factor = 1.0
        elif diff < 0:
            if abs_diff >= 0.15:
                factor = 1.0 + 0.08
            elif abs_diff >= 0.10:
                factor = 1.0 + 0.06
            elif abs_diff >= 0.05:
                factor = 1.0 + 0.04
            else:
                factor = 1.0
        else:
            factor = 1.0

        factor_lcc_list[i] = factor
    return factor_lcc_list


def apply_physical_monotonic_constraint(prediction, ssrd_series):
    """物理硬约束"""
    pred = prediction.copy()
    ssrd = ssrd_series.copy()
    factor_ssrd = np.ones_like(pred)

    for i in range(1, len(pred)):
        if ssrd[i] < 5 and ssrd[i-1] < 5:
            continue

        ssrd_diff = ssrd[i] - ssrd[i-1]
        abs_diff = abs(ssrd_diff)

        if ssrd_diff > 0:
            allow_drop = min(0.08, abs_diff / 1500)
            min_allowed = 1.0 - allow_drop
            boost_ratio = min(0.15, abs_diff / 1500)
            adjust_factor = 1.0 + boost_ratio

            if pred[i] < pred[i-1] * min_allowed:
                pred[i] = pred[i-1] * adjust_factor
                factor_ssrd[i] = adjust_factor

        elif ssrd_diff < 0:
            allow_rise = min(0.08, abs_diff / 1500)
            max_allowed = 1.0 + allow_rise
            drop_ratio = min(0.15, abs_diff / 1500)
            adjust_factor = 1.0 - drop_ratio

            if pred[i] > pred[i-1] * max_allowed:
                pred[i] = pred[i-1] * adjust_factor
                factor_ssrd[i] = adjust_factor

    pred = np.clip(pred, 0, None)
    return pred, factor_ssrd


# ===================== 24小时预测 =====================
def predict_24h_auto(history_df, future_df):
    """完整的24小时预测"""
    weather = judge_weather_type(future_df)
    print(f"\n🌤️ 天气类型：{weather}")

    feat_cols, scaler, xgb, lgb, lstm, lstnet, corr, meta_model = load_all_models(weather)

    history = history_df.copy()
    results = []

    pred_history_xgb = []
    pred_history_lgb = []
    pred_history_lstm = []
    pred_history_lstnet = []

    for i in range(len(future_df)):
        curr = future_df.iloc[[i]].copy()
        df_total = build_features(pd.concat([history, curr], ignore_index=True))

        X = df_total[feat_cols].values
        X_scaled = scaler.transform(X)
        X_seq = X_scaled[-TIME_STEP:].reshape(1, TIME_STEP, -1)
        X_flat = X_scaled[-1:]

        p_xgb = xgb.predict(X_flat)[0]
        p_lgb = lgb.predict(np.hstack([X_flat, [[p_xgb]]]), verbose=-1)[0]
        p_lstm = lstm.predict(X_seq, verbose=0)[0][0]
        p_lstnet = lstnet.predict(X_seq, verbose=0)[0][0]

        t = pd.to_datetime(curr["time"].iloc[0])
        h = t.hour
        mo = t.month
        ssrd = curr["ssrd"].values[0]
        fdir = curr["fdir"].values[0]
        lcc = curr["lcc"].values[0]
        ssrd_diff = df_total["ssrd_diff"].iloc[-1]
        fdir_diff = df_total["fdir_diff"].iloc[-1]

        c_xgb = correct_pred(p_xgb, h, mo, ssrd, fdir, lcc, ssrd_diff, fdir_diff, corr["xgb"], pred_history_xgb)
        c_lgb = correct_pred(p_lgb, h, mo, ssrd, fdir, lcc, ssrd_diff, fdir_diff, corr["lgb"], pred_history_lgb)
        c_lstm = correct_pred(p_lstm, h, mo, ssrd, fdir, lcc, ssrd_diff, fdir_diff, corr["lstm"], pred_history_lstm)
        c_lstnet = correct_pred(p_lstnet, h, mo, ssrd, fdir, lcc, ssrd_diff, fdir_diff, corr["lstnet"], pred_history_lstnet)

        pred_history_xgb.append(c_xgb)
        pred_history_lgb.append(c_lgb)
        pred_history_lstm.append(c_lstm)
        pred_history_lstnet.append(c_lstnet)

        fusion_pred = stacking_predict(c_xgb, c_lgb, c_lstm, c_lstnet, meta_model)

        results.append({
            "time": curr["time"].iloc[0],
            "hour": h,
            "xgb": c_xgb,
            "lgb": c_lgb,
            "lstm": c_lstm,
            "lstnet": c_lstnet,
            "fusion": fusion_pred
        })
        history = pd.concat([history.iloc[1:], curr], ignore_index=True)

    pred_df = pd.DataFrame(results)
    pred_df_before_physical = pred_df.copy()

    # 应用物理约束
    ssrd_corrected, factor_ssrd = apply_physical_monotonic_constraint(
        pred_df["fusion"].values, future_df["ssrd"].values
    )
    factor_lcc = get_lcc_adjust_factor(pred_df, future_df)

    weight_ssrd = 0.7
    weight_lcc = 0.3
    total_factor = factor_ssrd * weight_ssrd + factor_lcc * weight_lcc
    total_factor = np.clip(total_factor, 0.75, 1.2)

    for col in ["xgb", "lgb", "lstm", "lstnet", "fusion"]:
        pred_df[col] = pred_df[col] * total_factor

    # 夜间置零
    night_mask = future_df["ssrd"].values < 10
    for c in ["xgb", "lgb", "lstm", "lstnet", "fusion"]:
        pred_df[c] = np.where(night_mask, 0, pred_df[c])
        pred_df[c] = pred_df[c].clip(lower=0)

    return pred_df_before_physical, pred_df


# ===================== 提取下一小时结果 =====================
def extract_next_hour_prediction(pred_df, next_hour_idx):
    """从24小时预测中提取下一小时结果"""
    target_row = pred_df[pred_df['hour'] == next_hour_idx]
    
    if len(target_row) == 0:
        print(f"⚠️ 未找到 {next_hour_idx}:00 的预测结果，使用第一条")
        target_row = pred_df.iloc[[0]]
    
    row = target_row.iloc[0]
    return {
        "predict_time": str(row["time"]),
        "hour": int(row["hour"]),
        "xgb": float(row["xgb"]),
        "lgb": float(row["lgb"]),
        "lstm": float(row["lstm"]),
        "lstnet": float(row["lstnet"]),
        "fusion": float(row["fusion"]),
    }


# ===================== 【核心：API发送功能】=====================
def send_to_api(prediction_data, max_retry=MAX_RETRY):
    """
    发送预测结果到API，带重试机制
    注意：接口要求数组格式，单条数据也要包装成 [{}]
    """
    # 包装成数组格式（接口要求）
    payload = [{
        "stationId": STATION_ID,
        "predictionTime": prediction_data["predict_time"],
        "predictionPvPower": f"{prediction_data['fusion']:.2f}"
    }]
    
    print(f"\n📤 正在发送数据到API...")
    print(f"   URL: {API_URL}")
    print(f"   方法: POST")
    print(f"   数据: {json.dumps(payload, ensure_ascii=False)}")
    
    for attempt in range(max_retry):
        try:
            # 使用data参数而不是json参数，手动序列化
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            response = requests.post(
                API_URL,
                data=json.dumps(payload, ensure_ascii=False),
                headers=headers,
                timeout=10,
                allow_redirects=False  # 禁止重定向
            )
            
            print(f"   响应状态码: {response.status_code}")
            print(f"   响应内容: {response.text[:200]}")
            
            response.raise_for_status()
            print(f"✅ API发送成功 (尝试 {attempt+1}/{max_retry})")
            return True, payload, None
            
        except requests.exceptions.HTTPError as e:
            print(f"⚠️ HTTP错误 (尝试 {attempt+1}/{max_retry}): {e}")
            print(f"   响应内容: {response.text}")
            if attempt < max_retry - 1:
                print(f"   等待 {RETRY_DELAY} 秒后重试...")
                time.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"⚠️ API发送失败 (尝试 {attempt+1}/{max_retry}): {e}")
            if attempt < max_retry - 1:
                print(f"   等待 {RETRY_DELAY} 秒后重试...")
                time.sleep(RETRY_DELAY)
    
    # 全部重试失败
    print(f"❌ API发送最终失败，已重试 {max_retry} 次")
    return False, payload, str(e)


def save_to_pending_queue(payload, error_msg, queue_file):
    """保存到待发送队列"""
    queue = []
    if os.path.exists(queue_file):
        with open(queue_file, "r", encoding="utf-8") as f:
            queue = json.load(f)
    
    queue.append({
        "payload": payload,
        "error": error_msg,
        "failed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "retry_count": 0
    })
    
    with open(queue_file, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)
    
    print(f"💾 已保存到待发送队列: {queue_file}")


def process_pending_queue(queue_file):
    """处理待发送队列"""
    if not os.path.exists(queue_file):
        return 0
    
    with open(queue_file, "r", encoding="utf-8") as f:
        queue = json.load(f)
    
    if not queue:
        return 0
    
    print(f"\n🔄 发现 {len(queue)} 条待发送数据，尝试重新发送...")
    
    remaining = []
    success_count = 0
    
    for item in queue:
        # 构造符合send_to_api接口的数据格式
        # payload是数组格式，取第一条数据
        payload_item = item["payload"][0] if isinstance(item["payload"], list) else item["payload"]
        temp_data = {
            "predict_time": payload_item["predictionTime"],
            "fusion": float(payload_item["predictionPvPower"])
        }
        success, _, _ = send_to_api(temp_data, max_retry=1)
        
        if success:
            success_count += 1
        else:
            item["retry_count"] += 1
            if item["retry_count"] < 5:
                remaining.append(item)
            else:
                print(f"❌ 放弃发送: {payload_item['predictionTime']}")
    
    with open(queue_file, "w", encoding="utf-8") as f:
        json.dump(remaining, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 成功补发 {success_count} 条，剩余 {len(remaining)} 条")
    return success_count


# ===================== 保存预测结果 =====================
def save_prediction_result(next_hour_result, full_pred_df, timestamp, exec_time_label, save_dir, dates):
    """保存预测结果"""
    os.makedirs(save_dir, exist_ok=True)
    
    # 1. 保存下一小时的JSON（上传用）
    json_path = os.path.join(save_dir, f"prediction_next_hour_{exec_time_label}.json")
    
    json_output = {
        "stationId": STATION_ID,
        "predictionTime": next_hour_result["predict_time"],
        "predictionPvPower": f"{next_hour_result['fusion']:.2f}",
        "models": {
            "xgb": f"{next_hour_result['xgb']:.2f}",
            "lgb": f"{next_hour_result['lgb']:.2f}",
            "lstm": f"{next_hour_result['lstm']:.2f}",
            "lstnet": f"{next_hour_result['lstnet']:.2f}",
            "fusion": f"{next_hour_result['fusion']:.2f}",
        }
    }
    
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_output, f, ensure_ascii=False, indent=2)
    
    # 2. 保存完整的24小时预测
    full_csv_path = os.path.join(save_dir, f"prediction_24h_{exec_time_label}.csv")
    full_pred_df.to_csv(full_csv_path, index=False, encoding="utf-8-sig")
    
    # 3. 追加到每日汇总
    daily_csv_path = os.path.join(save_dir, f"daily_predictions_{dates['predict_date_md']}.csv")
    df_new = pd.DataFrame([{
        "timestamp": timestamp,
        "exec_time_label": exec_time_label,
        "execute_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "predict_hour": next_hour_result["hour"],
        "predict_time": next_hour_result["predict_time"],
        "xgb": next_hour_result["xgb"],
        "lgb": next_hour_result["lgb"],
        "lstm": next_hour_result["lstm"],
        "lstnet": next_hour_result["lstnet"],
        "fusion": next_hour_result["fusion"],
    }])
    
    if os.path.exists(daily_csv_path):
        df_existing = pd.read_csv(daily_csv_path)
        df_combined = pd.concat([df_existing, df_new], ignore_index=True)
        df_combined.to_csv(daily_csv_path, index=False, encoding="utf-8-sig")
    else:
        df_new.to_csv(daily_csv_path, index=False, encoding="utf-8-sig")
    
    return json_path, full_csv_path, daily_csv_path


# ===================== 日志记录 =====================
def log_send_result(timestamp, payload, success, error=None, log_file=None):
    """记录发送日志"""
    if log_file is None:
        log_file = os.path.join(BASE_SAVE_DIR, "api_send_log.json")
    
    log_entry = {
        "timestamp": timestamp,
        "send_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "payload": payload,
        "success": success,
        "error": error
    }
    
    logs = []
    if os.path.exists(log_file):
        with open(log_file, "r", encoding="utf-8") as f:
            try:
                logs = json.load(f)
            except:
                logs = []
    
    logs.append(log_entry)
    
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


# =========================================================================
# ===================== 【定时任务主函数】 =====================
# =========================================================================
def run_prediction_task():
    """
    每小时58分触发：
    1. 处理待发送队列
    2. 拉取气象数据并保存
    3. 预测24小时
    4. 提取下一小时结果
    5. POST到API
    6. 保存本地备份
    """
    now = datetime.now()
    timestamp = get_timestamp()
    exec_time_label = get_formatted_time()  # 例如: 20260602_1058
    next_hour_info = get_next_hour_info()
    dates = get_target_dates()
    
    print("\n" + "="*70)
    print(f"🤖 定时任务触发 | 当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📌 预测目标: {next_hour_info['next_hour_str']} 的发电量")
    print(f"📌 执行标记: {exec_time_label}")
    print(f"📌 数据来源: 基准日={dates['base_date_str']}, 预测日={dates['predict_date_str']}")
    print("="*70)
    
    # 初始化路径
    save_dir = os.path.join(BASE_SAVE_DIR, dates['predict_date_md'])
    queue_file = os.path.join(BASE_SAVE_DIR, "pending_queue.json")
    
    try:
        # 步骤0: 处理之前失败的数据
        print("\n📋 步骤0: 检查待发送队列...")
        resent_count = process_pending_queue(queue_file)
        if resent_count > 0:
            print(f"   成功补发 {resent_count} 条历史数据")
        
        # 步骤1: 拉取历史气象（昨天24h）
        print("\n📥 步骤1: 拉取历史气象数据...")
        history_raw = get_base_day_weather(dates['base_date_str'])
        if history_raw is None:
            print("❌ 历史气象获取失败，任务终止")
            return
        
        # 步骤2: 拉取未来气象（今天24h）
        print("\n📥 步骤2: 拉取未来24小时气象数据...")
        future_raw = get_predict_day_weather(dates['predict_date_str'])
        if future_raw is None:
            print("❌ 未来气象获取失败，任务终止")
            return
        
        # 步骤3: 保存原始气象数据（标记执行时间）
        print("\n💾 步骤3: 保存气象数据...")
        history_path, future_path = save_weather_data(
            history_raw, future_raw, save_dir, exec_time_label
        )
        
        # 转换为ERA5格式
        history_df = convert_to_era5_format(history_raw)
        future_df = convert_to_era5_format(future_raw)
        
        # 步骤4: 执行24小时预测
        print("\n🚀 步骤4: 执行24小时预测...")
        pred_before, pred_result = predict_24h_auto(history_df, future_df)
        
        # 步骤5: 提取下一小时的预测结果
        print(f"\n🎯 步骤5: 提取 {next_hour_info['next_hour_idx']}:00 的预测结果...")
        next_hour_result = extract_next_hour_prediction(
            pred_result, next_hour_info['next_hour_idx']
        )
        
        # 步骤6: 保存预测结果
        print("\n💾 步骤6: 保存预测结果...")
        json_path, full_csv_path, daily_csv_path = save_prediction_result(
            next_hour_result, pred_result, timestamp, exec_time_label, save_dir, dates
        )
        
        # 步骤7: 发送到API
        print("\n📤 步骤7: 发送数据到API...")
        success, payload, error = send_to_api(next_hour_result)
        
        # 记录发送日志
        log_send_result(timestamp, payload, success, error)
        
        if not success:
            # 保存到队列，下次再试
            save_to_pending_queue(payload, error, queue_file)
        
        # 打印结果
        print("\n" + "="*70)
        print("📊 预测结果汇总")
        print("="*70)
        print(f"预测时段: {next_hour_result['predict_time']}")
        print(f"融合模型预测: {next_hour_result['fusion']:.2f} kWh")
        print(f"  ├── XGBoost:  {next_hour_result['xgb']:.2f}")
        print(f"  ├── LightGBM: {next_hour_result['lgb']:.2f}")
        print(f"  ├── LSTM:      {next_hour_result['lstm']:.2f}")
        print(f"  └── LSTNet:    {next_hour_result['lstnet']:.2f}")
        print("-"*70)
        print(f"API发送状态: {'✅ 成功' if success else '❌ 失败（已入队）'}")
        print("="*70)
        print(f"📁 气象数据:")
        print(f"   历史: {history_path}")
        print(f"   未来: {future_path}")
        print(f"📁 预测结果:")
        print(f"   JSON: {json_path}")
        print(f"   24h:  {full_csv_path}")
        print(f"   汇总: {daily_csv_path}")
        print("🎉 任务执行完成！")
        
    except Exception as e:
        print(f"\n❌ 任务执行失败: {e}")
        import traceback
        traceback.print_exc()


# =========================================================================
# ===================== 【Schedule 定时配置】 =====================
# =========================================================================
if __name__ == "__main__":
    import sys
    
    print("="*70)
    print("🌞 英杰光伏预测 - 超短期定时任务（含API上传）")
    print("="*70)
    print(f"⏰ 触发时间: 每小时第58分")
    print(f"📌 策略: 预测24小时 → 提取下一小时 → POST到API")
    print(f"📋 保存目录: {BASE_SAVE_DIR}")
    print(f"🌐 API地址: {API_URL}")
    print(f"🏭 站点ID: {STATION_ID}")
    print("="*70)
    
    # 【关键配置：每小时58分触发】
    schedule.every().hour.at(":58").do(run_prediction_task)
    
    print(f"\n📝 已注册定时任务:")
    for job in schedule.jobs:
        print(f"   - {job}")
    
    # 立即执行一次（测试用）
    print("\n🚀 立即执行一次预测...")
    run_prediction_task()
    
    print(f"\n🔄 监听定时任务中... (按 Ctrl+C 退出)")
    print(f"   下次执行: {schedule.next_run()}")
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n\n👋 定时任务已终止")
        sys.exit(0)
