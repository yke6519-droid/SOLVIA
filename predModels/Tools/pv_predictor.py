"""
pv_predictor.py - 光伏发电预测模块 (LangChain Tool)
=====================================================
基于历史气象 + 未来气象数据，使用 Stacking 集成模型预测未来 24 小时光伏发电量。

架构分三层：
  Layer 1  @tool predict_power          — LLM 工具入口，Agent 唯一调用点
  Layer 2  predict_station_power        — 站点整合层（拉气象 → 转格式 → 预测）
           compare_with_actual          — 历史对比（预测 vs MySQL 实际发电量）
  Layer 3  predict_24h_auto             — 基础预测层（核心 24h 预测逻辑）
           convert_to_era5_format       — 数据适配器（open-meteo → ERA5）

模型管理：
  ModelManager                          — 单例缓存，避免重复加载 TF 模型

原脚本搬移（不改逻辑）：
  build_features / correct_pred / stacking_predict
  judge_weather_type / get_lcc_adjust_factor / apply_physical_monotonic_constraint
  radiation_aware_loss / radiation_stable_loss（TF 自定义损失函数注册）

TODO 留口：
  1. ✅ predict_power 的 target_date 参数 — 已支持任意日期(YYYY-MM-DD/M月D日/M月D号/M-D/今天/昨天)
     实现：parse_flexible_date 解析 + predict_station_power 改用底层 _fetch_* 函数
  2. ModelManager 多站点映射 — 当前硬编码英杰站点路径，后续从配置/DB 读
  3. 【架构优化】当前 pv_predictor 与 weather_fetcher 同放 Tools/（方案A）。
     后续引入第二个站点预测模型或 file_io 工具后，建议拆分为方案B：
       tools/pv_predictor.py     — 薄 @tool 入口（仅 predict_power）
       models/ensemble.py        — predict_24h_auto + 特征/纠偏/融合
       models/model_manager.py   — ModelManager
       models/losses.py          — radiation_aware_loss 等
       models/era5_adapter.py    — convert_to_era5_format
     拆分信号：Tools/ 下超过 3 个文件、或 pv_predictor 被其他模块复用 ML 逻辑时
"""
import os
import warnings
import numpy as np
import pandas as pd
import joblib
import tensorflow as tf
from datetime import datetime, timedelta
from typing import Annotated, Optional
from langchain_core.tools import tool, ToolException
from dotenv import load_dotenv
from predModels.Tools.date_parser_tool import parse_flexible_date

# ============================================================
# 环境配置（必须在 import tensorflow 之后、加载模型之前设置）
# ============================================================

# 【硬约束】TF 2.15 加载含自定义 loss 的 .keras 模型必须用 Keras 2 兼容模式
# 不设这个环境变量，load_model 时会报 "Unable to restore custom loss function" 错误
os.environ.setdefault('TF_USE_LEGACY_KERAS', '1')
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')  # 减少 TF 日志输出
warnings.filterwarnings('ignore')


# ============================================================
# 配置区
# ============================================================

# 模型根路径（按天气类型分目录）
# 晴天模型和多 云模型是两套独立的训练结果，预测时根据天气判断选其一
MODEL_SUNNY = os.getenv("MODEL_SUNNY")
MODEL_CLOUDY = os.getenv("MODEL_CLOUDY")

# LSTM / LSTNet 的时间步长（训练时用的 24，预测时必须一致）
TIME_STEP = 24

# MySQL 连接配置（用于 compare_with_actual 查询实际发电量）
MYSQL_URL = os.getenv("MYSQL_URL")


# ============================================================
# 【硬约束】TF 自定义损失函数注册
# ============================================================
# 这两个装饰器 @register_keras_serializable 必须在 load_model 之前执行，
# 否则 load_model 时找不到自定义 loss 函数会报错。
# 函数逻辑与原脚本完全一致，不做任何改动。

@tf.keras.saving.register_keras_serializable()
def radiation_aware_loss(y_true, y_pred):
    """辐射感知损失函数：高发电量(>100)样本权重×4，让模型更关注白天高峰时段"""
    weight = tf.where(y_true > 100, 4.0, 1.0)
    mse = tf.square(y_true - y_pred)
    return tf.reduce_mean(mse * weight)


@tf.keras.saving.register_keras_serializable()
def radiation_stable_loss(y_true, y_pred):
    """辐射稳定损失函数：高发电量样本权重×5 + 相邻预测波动惩罚，抑制跳变"""
    weight = tf.where(y_true > 100, 5.0, 1.0)
    mse = tf.square(y_true - y_pred)
    pred_diff = tf.abs(y_pred[1:] - y_pred[:-1])
    diff_penalty = 0.5 * tf.reduce_mean(pred_diff)
    return tf.reduce_mean(mse * weight) + diff_penalty


# ============================================================
# ModelManager — 模型缓存管理（新增）
# ============================================================
# 【为什么需要这个类】
# 原脚本每次调用 predict_24h_auto 都会 load_all_models，加载 TF 模型 + 5 个 pkl
# 文件需要好几秒。Agent 场景下可能多次调用预测，每次都重新加载太慢。
# ModelManager 把已加载的模型缓存在内存中，第二次调用直接返回缓存。
# todo 目前 ModelManager 中仅是英杰站点预测模型的硬加载。
#  如果后续有新站点模型加入，要把该方法写的更灵活一些。能够根据用户要求的站点进行动态加载。

class ModelManager:
    """
    模型管理单例，按 (站点, 天气类型) 缓存已加载的模型对象。

    使用方式：
        manager = ModelManager()
        models = manager.get_station_models("英杰", "晴天")
        # models 是一个字典，包含 xgb/lgb/lstm/lstnet/scaler/corr/meta_model 等
    """

    _instance = None

    def __new__(cls):
        """单例模式：全局只有一个 ModelManager 实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._cache = {}  # 缓存字典: key=(station, weather_type) → models dict
        return cls._instance

    def get_station_models(self, station: str, weather_type: str) -> dict:
        """
        获取指定站点的模型（带缓存）。

        参数:
            station: 站点名（当前仅支持 "英杰"）
            weather_type: "晴天" 或 "非晴天"

        返回:
            dict: {
                "feat_cols": 特征列名列表,
                "scaler": StandardScaler,
                "xgb": XGBoost模型,
                "lgb": LightGBM模型,
                "lstm": Keras LSTM模型,
                "lstnet": Keras LSTNet模型,
                "corr": {"xgb":..., "lgb":..., "lstm":..., "lstnet":...},  # Ridge纠偏模型
                "meta_model": Stacking元模型
            }
        """
        cache_key = (station, weather_type)

        # 命中缓存直接返回，不重新加载
        if cache_key in self._cache:
            print(f"♻️ 模型缓存命中: {station}/{weather_type}")
            return self._cache[cache_key]

        # 未命中缓存 → 加载并缓存
        print(f"📥 首次加载模型: {station}/{weather_type}")
        models = self._load_models(weather_type)
        self._cache[cache_key] = models
        return models

    def _load_models(self, weather_type: str) -> dict:
        """
        实际加载模型的内部方法（从原脚本 load_all_models 搬移，逻辑不变）。

        参数:
            weather_type: "晴天" → 用晴天模型目录; 其他 → 用多云模型目录

        返回:
            dict: 包含所有模型对象的字典
        """
        # 【步骤1】根据天气类型选模型目录
        if weather_type == "晴天":
            model_dir = MODEL_SUNNY
            print("✅ 使用【晴天模型】")
        else:
            model_dir = MODEL_CLOUDY
            print("✅ 使用【多云/非晴天模型】")

        # 【步骤2】拼出所有模型文件路径
        # 每个模型文件的作用：
        #   xgb_model.model.pkl  — XGBoost 预测模型
        #   lgb_model.pkl        — LightGBM 预测模型
        #   lstm_model.keras     — LSTM 深度学习模型
        #   lstnet_model.keras   — LSTNet 深度学习模型
        #   scaler.pkl           — 特征标准化器
        #   feature_cols.txt     — 特征列名（模型训练时的特征顺序）
        #   ridge_*_correction.pkl — 4个Ridge回归纠偏模型
        #   meta_stacking_model.pkl — Stacking 元融合模型
        model_paths = {
            "xgb": os.path.join(model_dir, "xgb_model.model.pkl"),
            "lgb": os.path.join(model_dir, "lgb_model.pkl"),
            "lstm": os.path.join(model_dir, "lstm_model.keras"),
            "lstnet": os.path.join(model_dir, "lstnet_model.keras"),
            "scaler": os.path.join(model_dir, "scaler.pkl"),
            "feature_cols": os.path.join(model_dir, "feature_cols.txt"),
            "ridge_xgb": os.path.join(model_dir, "ridge_xgb_correction.pkl"),
            "ridge_lgb": os.path.join(model_dir, "ridge_lgb_correction.pkl"),
            "ridge_lstm": os.path.join(model_dir, "ridge_lstm_correction.pkl"),
            "ridge_lstnet": os.path.join(model_dir, "ridge_lstnet_correction.pkl"),
            "meta_model": os.path.join(model_dir, "meta_stacking_model.pkl"),
        }

        # 【步骤3】读取特征列名（模型训练时的特征顺序，预测时必须一致）
        with open(model_paths["feature_cols"], "r", encoding="utf-8") as f:
            feat_cols = [line.strip() for line in f.readlines()]

        # 【步骤4】加载所有模型
        # joblib.load 用于 pkl 文件（sklearn/xgboost/lightgbm 模型）
        # tf.keras.models.load_model 用于 .keras 文件（需要自定义 loss 已注册）
        scaler = joblib.load(model_paths["scaler"])
        xgb_model = joblib.load(model_paths["xgb"])
        lgb_model = joblib.load(model_paths["lgb"])
        lstm = tf.keras.models.load_model(model_paths["lstm"])
        lstnet = tf.keras.models.load_model(model_paths["lstnet"])

        # 4个 Ridge 纠偏模型，分别纠正 4 个子模型的预测偏差
        corr = {
            "xgb": joblib.load(model_paths["ridge_xgb"]),
            "lgb": joblib.load(model_paths["ridge_lgb"]),
            "lstm": joblib.load(model_paths["ridge_lstm"]),
            "lstnet": joblib.load(model_paths["ridge_lstnet"]),
        }

        # Stacking 元模型：把 4 个纠偏后的子模型预测融合成最终结果
        meta_model = joblib.load(model_paths["meta_model"])

        return {
            "feat_cols": feat_cols,
            "scaler": scaler,
            "xgb": xgb_model,
            "lgb": lgb_model,
            "lstm": lstm,
            "lstnet": lstnet,
            "corr": corr,
            "meta_model": meta_model,
        }

    def clear_cache(self):
        """清空模型缓存（切换站点或调试时用）"""
        self._cache.clear()
        print("🗑️ 模型缓存已清空")

    def warmup(self, station: str = "英杰"):
        """
        预热：提前加载晴天和多云两套模型。
        Agent 启动时调用一次，首次预测就不用等加载了。
        """
        print(f"🔥 预热 {station} 站点模型...")
        self.get_station_models(station, "晴天")
        self.get_station_models(station, "非晴天")
        print(f"✅ 预热完成")


# ============================================================
# Layer 3: 基础预测层 — 原脚本搬移（逻辑不变）
# ============================================================
# 以下函数全部从原脚本原样搬移，仅添加注释，不改任何逻辑。
# 分两类：
#   1. 数据预处理：build_features（特征工程）
#   2. 模型预测：correct_pred（Ridge纠偏）、stacking_predict（Stacking融合）
#   3. 物理约束：judge_weather_type、get_lcc_adjust_factor、apply_physical_monotonic_constraint

def build_features(df):
    """
    特征工程：在原始 ERA5 气象数据上构建模型需要的特征列。

    【搬移自原脚本，逻辑不变】

    新增特征包括：
      - 时间特征: hour, month, hour_sin/cos, month_sin/cos（周期性编码）
      - 辐射特征: ssrd_diff, fdir_diff, ssrd_abs_change, ssrd_fdir_ratio, ssrd_power3 等
      - 复合特征: rad_total, rad_fluctuation, rad_rate, ssrd_lcc_corrected

    参数:
        df: ERA5 格式的 DataFrame（必须含 t2m, d2m, lcc, ssrd, fdir, wind_speed 等列）

    返回:
        DataFrame: 原始列 + 新增特征列
    """
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    # 气象值不能为负，clip 到 0
    for col in ["t2m", "d2m", "lcc", "ssrd", "fdir", "wind_speed", "wind_direction"]:
        if col in df.columns:
            df[col] = np.clip(df[col], 0, None)

    # 时间周期性编码（用 sin/cos 让模型理解 23点和0点是相邻的）
    t = df["time"].dt
    df["hour"] = t.hour
    df["month"] = t.month
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["month_sin"] = np.sin(2 * np.pi * (df["month"] - 1) / 12)
    df["month_cos"] = np.cos(2 * np.pi * (df["month"] - 1) / 12)

    # 辐射相关特征
    df["ssrd"] = np.clip(df["ssrd"], 0, None)
    df["fdir"] = np.clip(df["fdir"], 0, None)

    # 辐射变化率（相邻小时的差值），反映云层变化速度
    df["ssrd_diff"] = df["ssrd"].diff().fillna(0)
    df["fdir_diff"] = df["fdir"].diff().fillna(0)
    df["ssrd_abs_change"] = np.abs(df["ssrd_diff"])
    df["fdir_abs_change"] = np.abs(df["fdir_diff"])

    # 辐射比值与高次项，增强模型对非线性关系的拟合
    df["ssrd_fdir_ratio"] = df["ssrd"] / (df["fdir"] + 1e-6)
    df["ssrd_power3"] = df["ssrd"] ** 3
    df["fdir_power3"] = df["fdir"] ** 3

    # 复合辐射特征
    df["rad_total"] = df["ssrd"] + df["fdir"]
    df["rad_fluctuation"] = (df["ssrd_abs_change"] + df["fdir_abs_change"]) / 2
    df["rad_rate"] = (df["ssrd"] + df["fdir"]) / (df["t2m"] + 1e-6)
    df["ssrd_t2m_ratio"] = df["ssrd"] / (df["t2m"] + 1e-6)
    df["ssrd_lcc_corrected"] = df["ssrd"] * (1 - df["lcc"] / 100)

    return df


def correct_pred(raw_pred, hour, month, ssrd, fdir, lcc, ssrd_diff, fdir_diff, model, pred_history):
    """
    Ridge 回归纠偏：对单个子模型的原始预测值进行偏差校正。

    【搬移自原脚本，逻辑不变】

    原理：训练时发现各子模型在不同时段、不同辐射条件下偏差规律不同，
    用 Ridge 回归学习"原始预测 → 真实值"的映射，预测时做校正。

    纠偏特征包括：
      - 原始预测值及其平方
      - 时间特征（hour, month 及周期编码）
      - 气象特征（ssrd, fdir, lcc, 辐射比）
      - 历史预测值（1h/3h/6h/12h 前）

    参数:
        raw_pred: 子模型的原始预测值
        hour, month: 当前时间
        ssrd, fdir, lcc: 当前辐射和云量
        ssrd_diff, fdir_diff: 辐射变化率
        model: 对应子模型的 Ridge 纠偏器
        pred_history: 该子模型的历史预测值列表

    返回:
        float: 纠偏后的预测值（≥0）
    """
    hour_sin = np.sin(2 * np.pi * hour / 24)
    hour_cos = np.cos(2 * np.pi * hour / 24)
    month_sin = np.sin(2 * np.pi * (month - 1) / 12)
    month_cos = np.cos(2 * np.pi * (month - 1) / 12)
    ssrd_pred_ratio = ssrd / (raw_pred + 1e-6)

    # 从历史预测中取不同时间窗口的值作为特征
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
    """
    Stacking 融合：把 4 个纠偏后的子模型预测值融合成最终预测。

    【搬移自原脚本，逻辑不变】

    元特征包括：
      - 4个子模型的纠偏预测值
      - 子模型间差值的绝对值（反映模型分歧）
      - 子模型间的平均值
      - 放大系数（×1.5）

    参数:
        c_xgb, c_lgb, c_lstm, c_lstnet: 4个子模型纠偏后的预测值
        meta_model: Stacking 元模型

    返回:
        float: 融合后的最终预测值（≥0）
    """
    X_meta = [
        c_xgb, c_lgb, c_lstm, c_lstnet,
        abs(c_xgb - c_lstm), abs(c_lstm - c_lstnet),
        (c_xgb + c_lgb) / 2, (c_lstm + c_lstnet) / 2,
        c_xgb * 1.5, c_lgb * 1.5
    ]
    X_meta = np.array(X_meta).reshape(1, -1)
    fusion_pred = meta_model.predict(X_meta)[0]
    return max(fusion_pred, 0)


def judge_weather_type(future_df):
    """
    天气类型判断：根据未来气象的辐射数据判断晴天 or 非晴天。

    【搬移自原脚本，逻辑不变】
    【新增】此结果会一路返回到 LLM，LLM 可告知用户"今天判断为晴天"。

    判断逻辑：
      - 取白天时段（6:00-19:00）的短波辐射
      - 平均辐射 ≥ 350 且 最大辐射 ≥ 700 → 晴天
      - 否则 → 非晴天
      - 全是夜间 → "夜间"

    参数:
        future_df: ERA5 格式的未来气象 DataFrame

    返回:
        str: "晴天"、"非晴天" 或 "夜间"
    """
    df = future_df.copy()
    df["hour"] = pd.to_datetime(df["time"]).dt.hour
    day_hour = df[(df.hour >= 6) & (df.hour <= 19)].copy()

    if len(day_hour) == 0:
        return "夜间"

    avg_ssrd = day_hour["ssrd"].mean()
    max_ssrd = day_hour["ssrd"].max()
    is_sunny_ssrd = (avg_ssrd >= 350) and (max_ssrd >= 700)

    return "晴天" if is_sunny_ssrd else "非晴天"


def get_lcc_adjust_factor(pred_df, future_df):
    """
    LCC 云层约束因子：根据低云量变化率计算预测值的调整系数。

    【搬移自原脚本，逻辑不变】

    原理：低云量上升→发电量下降；低云量下降→发电量上升。
    根据 lcc_diff 的大小给出不同级别的调整因子（0.92~1.08）。

    参数:
        pred_df: 预测结果 DataFrame
        future_df: 未来气象 DataFrame（含 lcc 列）

    返回:
        np.array: 每小时的调整因子（shape = [24,]）
    """
    df = future_df.copy()
    df = df.sort_values("time").reset_index(drop=True)
    df["lcc_diff"] = df["lcc"].diff().fillna(0)
    lcc_diff_series = df["lcc_diff"].values

    factor_lcc_list = np.ones(len(pred_df))
    for i in range(len(pred_df)):
        diff = lcc_diff_series[i]
        abs_diff = abs(diff)

        if diff > 0:  # 云量增加 → 降低预测
            if abs_diff >= 0.15:
                factor = 1.0 - 0.08
            elif abs_diff >= 0.10:
                factor = 1.0 - 0.06
            elif abs_diff >= 0.05:
                factor = 1.0 - 0.04
            else:
                factor = 1.0
        elif diff < 0:  # 云量减少 → 提高预测
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
    """
    物理硬约束：根据辐射变化趋势约束预测值，防止预测曲线违背物理规律。

    【搬移自原脚本，逻辑不变】

    原理：
      - 辐射上升时，发电量不应大幅下降
      - 辐射下降时，发电量不应大幅上升
      - 约束幅度与辐射变化幅度成正比，上限 8%~15%

    参数:
        prediction: 预测值数组
        ssrd_series: 短波辐射数组

    返回:
        tuple: (约束后的预测数组, 约束因子数组)
    """
    pred = prediction.copy()
    ssrd = ssrd_series.copy()
    factor_ssrd = np.ones_like(pred)

    for i in range(1, len(pred)):
        # 夜间不约束
        if ssrd[i] < 5 and ssrd[i-1] < 5:
            continue

        ssrd_diff = ssrd[i] - ssrd[i-1]
        abs_diff = abs(ssrd_diff)

        if ssrd_diff > 0:  # 辐射上升
            allow_drop = min(0.08, abs_diff / 1500)
            min_allowed = 1.0 - allow_drop
            boost_ratio = min(0.15, abs_diff / 1500)
            adjust_factor = 1.0 + boost_ratio

            if pred[i] < pred[i-1] * min_allowed:
                pred[i] = pred[i-1] * adjust_factor
                factor_ssrd[i] = adjust_factor

        elif ssrd_diff < 0:  # 辐射下降
            allow_rise = min(0.08, abs_diff / 1500)
            max_allowed = 1.0 + allow_rise
            drop_ratio = min(0.15, abs_diff / 1500)
            adjust_factor = 1.0 - drop_ratio

            if pred[i] > pred[i-1] * max_allowed:
                pred[i] = pred[i-1] * adjust_factor
                factor_ssrd[i] = adjust_factor

    pred = np.clip(pred, 0, None)
    return pred, factor_ssrd


# ============================================================
# Layer 3: 基础预测层 — convert_to_era5_format（新增，数据适配器）
# ============================================================

def convert_to_era5_format(df_openmeteo: pd.DataFrame) -> pd.DataFrame:
    """
    数据适配器：将 weather_fetcher 返回的 open-meteo 格式转为预测模型需要的 ERA5 格式。

    【新增方法】这是连接 weather_fetcher 和 pv_predictor 的关键桥梁。
    weather_fetcher 拉取的是 open-meteo 原始格式（列名如 temperature_2m），
    但预测模型的 build_features 和 predict_24h_auto 需要的是 ERA5 格式
    （列名如 t2m, ssrd, fdir）。少了这一步转换，模型拿到的列名对不上直接报错。

    转换对照表：
        open-meteo 列名          → ERA5 列名     说明
        ─────────────────────────────────────────────────
        temperature_2m           → t2m           温度（℃）
        dew_point_2m             → d2m           露点（℃）
        cloud_cover_low / 100    → lcc           低云量（0~1 比例）
        shortwave_radiation      → ssrd          短波辐射（W/m²）
        direct_radiation         → fdir          直接辐射（W/m²）
        10m_u/v_component_of_wind → wind_speed   风速（U/V合成）
                                   wind_direction 风向（U/V反算）

    参数:
        df_openmeteo: weather_fetcher 返回的 DataFrame，包含以下列：
            time, temperature_2m, dew_point_2m, cloud_cover_low,
            shortwave_radiation, direct_radiation,
            10m_u_component_of_wind, 10m_v_component_of_wind

    返回:
        DataFrame: ERA5 格式，列 = [time, t2m, d2m, lcc, ssrd, fdir, wind_speed, wind_direction]
    """
    df = df_openmeteo.copy()

    # 【步骤1】列名转换（直接重命名）
    df["lcc"] = df["cloud_cover_low"] / 100.0  # 云量从百分比转为 0~1 比例
    df["ssrd"] = df["shortwave_radiation"]
    df["fdir"] = df["direct_radiation"]
    df["t2m"] = df["temperature_2m"]
    df["d2m"] = df["dew_point_2m"]

    # 【步骤2】风速/风向：从 U/V 分量反算
    # weather_fetcher 的 _parse_wind_components 把风速+风向转成了 U/V 分量，
    # 这里需要反过来，从 U/V 分量合成风速和风向。
    #   风速 = sqrt(U² + V²)
    #   风向 = (270 - atan2(V, U) * 180/π) % 360
    u = df["10m_u_component_of_wind"]
    v = df["10m_v_component_of_wind"]
    df["wind_speed"] = np.sqrt(u ** 2 + v ** 2)
    df["wind_direction"] = (270 - np.arctan2(v, u) * 180 / np.pi) % 360

    # 【步骤3】选取 ERA5 格式需要的列
    target_cols = ["time", "t2m", "d2m", "lcc", "ssrd", "fdir", "wind_speed", "wind_direction"]
    df_result = df[target_cols].copy()

    # 【步骤4】辐射和风速不能为负，clip 到 0
    for col in ["ssrd", "fdir", "wind_speed"]:
        df_result[col] = df_result[col].clip(lower=0)

    return df_result


# ============================================================
# Layer 3: 基础预测层 — predict_24h_auto（改造自原脚本）
# ============================================================

def predict_24h_auto(history_era5: pd.DataFrame, future_era5: pd.DataFrame) -> tuple:
    """
    核心 24 小时预测：基于历史气象 + 未来气象，预测未来 24 小时发电量。

    【改造自原脚本 predict_24h_auto，核心逻辑不变，两处调整：】
      1. 模型加载改为通过 ModelManager 缓存加载（原脚本每次重新加载）
      2. 返回值新增 weather_type（暴露给上层 LLM）

    完整流程：
      1. judge_weather_type 判断晴天/多云 → 选模型
      2. ModelManager 加载对应模型（首次加载后缓存）
      3. 逐小时循环预测（24次）：
         a. build_features 构建特征
         b. 4个子模型分别预测：xgb → lgb → lstm → lstnet
         c. correct_pred 对每个子模型纠偏
         d. stacking_predict 融合4个子模型 → 最终预测
      4. 物理约束后处理：apply_physical_monotonic_constraint + get_lcc_adjust_factor
      5. 夜间置零（ssrd < 10 的时段发电量强制为0）

    参数:
        history_era5: 历史气象（ERA5格式），通常为昨天24h，作为预测的基准序列
        future_era5:  未来气象（ERA5格式），通常为今天24h，每行预测一个小时的发电量

    返回:
        tuple: (pred_df_before, pred_df, weather_type)
            pred_df_before: 物理约束前的预测结果（调试用）
            pred_df: 物理约束后的最终预测结果，列 = [time, hour, xgb, lgb, lstm, lstnet, fusion]
            weather_type: 天气类型字符串（"晴天"/"非晴天"/"夜间"）
    """
    # 【步骤1】判断天气类型 → 决定用晴天模型还是多云模型
    weather = judge_weather_type(future_era5)
    print(f"\n🌤️ 天气类型：{weather}")

    # 【步骤2】通过 ModelManager 加载模型（带缓存，第二次调用不重新加载）
    manager = ModelManager()
    models = manager.get_station_models("英杰", weather)
    feat_cols = models["feat_cols"]
    scaler = models["scaler"]
    xgb = models["xgb"]
    lgb = models["lgb"]
    lstm = models["lstm"]
    lstnet = models["lstnet"]
    corr = models["corr"]
    meta_model = models["meta_model"]

    # 【步骤3】逐小时预测
    history = history_era5.copy()
    results = []

    # 每个子模型维护自己的历史预测序列（用于 correct_pred 的历史特征）
    pred_history_xgb = []
    pred_history_lgb = []
    pred_history_lstm = []
    pred_history_lstnet = []

    for i in range(len(future_era5)):
        curr = future_era5.iloc[[i]].copy()

        # 3a. 拼接历史+当前，构建特征
        df_total = build_features(pd.concat([history, curr], ignore_index=True))

        # 3b. 特征矩阵准备
        X = df_total[feat_cols].values
        X_scaled = scaler.transform(X)          # 标准化
        X_seq = X_scaled[-TIME_STEP:].reshape(1, TIME_STEP, -1)  # LSTM/LSTNet 需要序列输入
        X_flat = X_scaled[-1:]                    # XGBoost/LightGBM 用最后一行

        # 3c. 4个子模型分别预测
        # 注意 lgb 的输入要拼上 xgb 的预测值（级联特征）
        p_xgb = xgb.predict(X_flat)[0]
        p_lgb = lgb.predict(np.hstack([X_flat, [[p_xgb]]]), verbose=-1)[0]
        p_lstm = lstm.predict(X_seq, verbose=0)[0][0]
        p_lstnet = lstnet.predict(X_seq, verbose=0)[0][0]

        # 3d. 提取当前小时的气象参数（用于纠偏）
        t = pd.to_datetime(curr["time"].iloc[0])
        h = t.hour
        mo = t.month
        ssrd = curr["ssrd"].values[0]
        fdir = curr["fdir"].values[0]
        lcc = curr["lcc"].values[0]
        ssrd_diff = df_total["ssrd_diff"].iloc[-1]
        fdir_diff = df_total["fdir_diff"].iloc[-1]

        # 3e. 4个子模型分别纠偏
        c_xgb = correct_pred(p_xgb, h, mo, ssrd, fdir, lcc, ssrd_diff, fdir_diff, corr["xgb"], pred_history_xgb)
        c_lgb = correct_pred(p_lgb, h, mo, ssrd, fdir, lcc, ssrd_diff, fdir_diff, corr["lgb"], pred_history_lgb)
        c_lstm = correct_pred(p_lstm, h, mo, ssrd, fdir, lcc, ssrd_diff, fdir_diff, corr["lstm"], pred_history_lstm)
        c_lstnet = correct_pred(p_lstnet, h, mo, ssrd, fdir, lcc, ssrd_diff, fdir_diff, corr["lstnet"], pred_history_lstnet)

        # 3f. 记录历史预测（供下一个小时的纠偏使用）
        pred_history_xgb.append(c_xgb)
        pred_history_lgb.append(c_lgb)
        pred_history_lstm.append(c_lstm)
        pred_history_lstnet.append(c_lstnet)

        # 3g. Stacking 融合
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

        # 3h. 滑动窗口：把当前小时加入历史，移除最早的一小时
        history = pd.concat([history.iloc[1:], curr], ignore_index=True)

    pred_df = pd.DataFrame(results)
    pred_df_before_physical = pred_df.copy()  # 保存物理约束前的结果（调试对比用）

    # 【步骤4】物理约束后处理
    # 4a. 辐射单调性约束
    ssrd_corrected, factor_ssrd = apply_physical_monotonic_constraint(
        pred_df["fusion"].values, future_era5["ssrd"].values
    )
    # 4b. LCC 云层约束
    factor_lcc = get_lcc_adjust_factor(pred_df, future_era5)

    # 4c. 两个约束因子加权融合（辐射权重0.7，云层权重0.3）
    weight_ssrd = 0.7
    weight_lcc = 0.3
    total_factor = factor_ssrd * weight_ssrd + factor_lcc * weight_lcc
    total_factor = np.clip(total_factor, 0.75, 1.2)  # 约束因子限制在 0.75~1.2

    # 4d. 对所有子模型和融合结果都施加约束
    for col in ["xgb", "lgb", "lstm", "lstnet", "fusion"]:
        pred_df[col] = pred_df[col] * total_factor

    # 【步骤5】夜间置零（ssrd < 10 的时段没有发电量）
    night_mask = future_era5["ssrd"].values < 10
    for c in ["xgb", "lgb", "lstm", "lstnet", "fusion"]:
        pred_df[c] = np.where(night_mask, 0, pred_df[c])
        pred_df[c] = pred_df[c].clip(lower=0)

    # 【改动点】返回值新增 weather_type，供上层 LLM 告知用户
    return pred_df_before_physical, pred_df, weather


# ============================================================
# Layer 2: 站点整合层 — compare_with_actual（新增，历史对比）
# ============================================================

def compare_with_actual(pred_df: pd.DataFrame, station_id: str, predict_date: str,
                        db_url: str = MYSQL_URL) -> str:
    """
    历史对比：查询 MySQL 中的实际发电量，与预测结果对比，计算误差指标。

    【新增方法 — 简历亮点】
    这是异常归因 Agent 的核心能力：不仅预测发电量，还能事后对比实际值，
    算出预测误差，让 LLM 分析"为什么预测偏高/偏低"。

    误差指标：
      - MAE（平均绝对误差）：预测值与实际值的平均偏差
      - RMSE（均方根误差）：对大偏差更敏感
      - MAPE（平均绝对百分比误差）：相对误差，方便跨天比较

    注意：实际发电量需要等数据入库后才有，所以这个方法通常用于：
      - 对"昨天"的预测做事后对比（昨天预测了今天，今天有实际值了）
      - 或对历史日期的预测做回溯验证

    参数:
        pred_df: 预测结果 DataFrame（含 time, fusion 列）
        station_id: 站点ID
        predict_date: 预测日期 "YYYY-MM-DD"
        db_url: MySQL 连接字符串

    返回:
        str: 对比结果文字摘要（供 LLM 阅读）
    """
    # 【已迁移】实际发电量查询已迁移到 power_query_tool._query_actual_power
    from predModels.Tools.power_query_tool import _query_actual_power

    try:
        actual_df = _query_actual_power(station_id, predict_date)

        # 如果没有实际数据，返回提示（数据可能还没入库）
        if len(actual_df) == 0:
            return f"⏳ MySQL 中暂无 {predict_date} 的实际发电量数据（可能尚未入库），无法对比。"

        # 对齐预测和实际数据的时间（按小时匹配）
        pred_df = pred_df.copy()
        pred_df["hour"] = pd.to_datetime(pred_df["time"]).dt.hour
        actual_df["hour"] = pd.to_datetime(actual_df["record_time"]).dt.hour

        # 合并预测和实际
        merged = pd.merge(
            pred_df[["hour", "fusion"]],
            actual_df[["hour", "power_kwh"]],
            on="hour",
            how="inner"
        )

        if len(merged) == 0:
            return "⚠️ 预测与实际数据时间无法对齐，跳过对比。"

        # 计算误差指标
        pred = merged["fusion"].values
        actual = merged["power_kwh"].values

        mae = np.mean(np.abs(pred - actual))  # 平均绝对误差
        rmse = np.sqrt(np.mean((pred - actual) ** 2))  # 均方根误差
        # MAPE 避免除零：只在实际值 > 0.1 的时段计算
        mask = actual > 0.1
        mape = np.mean(np.abs((pred[mask] - actual[mask]) / actual[mask])) * 100 if mask.any() else float('nan')

        total_pred = pred.sum()
        total_actual = actual.sum()

        return (
            f"📊 预测 vs 实际对比（{predict_date}，{len(merged)} 小时）：\n"
            f"  预测总发电量: {total_pred:.1f} kWh\n"
            f"  实际总发电量: {total_actual:.1f} kWh\n"
            f"  偏差: {total_pred - total_actual:+.1f} kWh "
            f"({(total_pred - total_actual) / max(total_actual, 0.1) * 100:+.1f}%)\n"
            f"  MAE（平均绝对误差）: {mae:.2f} kWh\n"
            f"  RMSE（均方根误差）: {rmse:.2f} kWh\n"
            f"  MAPE（平均百分比误差）: {mape:.1f}%" if not np.isnan(mape) else ""
        )

    except Exception as e:
        return f"⚠️ 历史对比查询失败: {e}（MySQL 可能未启动或表未建）"


# ============================================================
# Layer 2: 站点整合层 — format_prediction_summary（LLM摘要）
# ============================================================

def format_prediction_summary(pred_df: pd.DataFrame, station_name: str,
                              weather_type: str, comparison: str = "",
                              weather_data_mode: str = "forecast") -> str:

    # todo 后期可以把各模型的预测结果去除，目前来看有些冗余。只展示最终模型的结果即可

    """
    将预测结果 DataFrame 转为 LLM 可读的文字摘要。

    【新增方法】
    预测结果 DataFrame 有 24 行 × 6 列（time, hour, xgb, lgb, lstm, lstnet, fusion），
    直接给 LLM 会浪费大量 token。此方法提取关键信息组成摘要，LLM 读摘要做分析。

    摘要内容：
      - 站点名、天气类型
      - 总发电量、峰值时段、峰值发电量
      - 发电时段（白天有效发电的小时数）
      - 各子模型对比（可选，用于分析模型分歧）
      - 历史对比结果（如有）

    参数:
        pred_df: 预测结果 DataFrame
        station_name: 站点名称
        weather_type: 天气类型
        comparison: compare_with_actual 返回的对比摘要（可选）

    返回:
        str: LLM 可读的文字摘要
    """
    fusion = pred_df["fusion"].values
    hours = pred_df["hour"].values

    total_power = fusion.sum()
    peak_idx = np.argmax(fusion)
    peak_hour = hours[peak_idx]
    peak_power = fusion[peak_idx]

    # 有效发电时段（fusion > 0.1 的小时）
    generating_hours = hours[fusion > 0.1]
    gen_start = int(generating_hours.min()) if len(generating_hours) > 0 else 0
    gen_end = int(generating_hours.max()) if len(generating_hours) > 0 else 0

    mode_label = "历史实况回测" if weather_data_mode == "historical_actual" else "未来预报"
    lines = [
        f"✅ {station_name} 预测完成（{weather_type}）",
        f"数据模式: {mode_label}",
        f"总发电量: {total_power:.1f} kWh",
        f"峰值时段: {peak_hour}:00，峰值: {peak_power:.1f} kWh",
        f"发电时段: {gen_start}:00~{gen_end}:00（{len(generating_hours)}小时）",
    ]

    if comparison:
        lines.append("")
        lines.append(comparison)

    return "\n".join(lines)


# ============================================================
# Layer 2: 站点整合层 — predict_station_power（内部整合方法）
# ============================================================

def predict_station_power(station_name: str, lat: float, lon: float,
                          station_id: str,
                          predict_date: Optional[str] = None,
                          history_date: Optional[str] = None) -> tuple:
    """
    站点整合：拉取气象 → ERA5转换 → 预测 → 历史对比 → 返回完整结果。

    【内部方法】被 @tool predict_power 调用，不建议直接给 LLM 用。
    整合了 weather_fetcher（拉气象）和 predict_24h_auto（预测）两个模块。

    完整流程：
      1. 调 weather_fetcher.get_yesterday_weather 拉历史气象
      2. 调 weather_fetcher.get_today_weather 拉未来气象
      3. convert_to_era5_format 把两份数据都转成 ERA5 格式
      4. predict_24h_auto 执行预测
      5. compare_with_actual 查 MySQL 实际值做对比
      6. format_prediction_summary 生成摘要

    参数:
        station_name: 站点名称（如 "英杰"）
        lat, lon: 经纬度
        station_id: 站点ID
        predict_date: 待预测日期 "YYYY-MM-DD"，None=今天
        history_date: 历史日期 "YYYY-MM-DD"，None=昨天

    返回:
        tuple: (summary, pred_df, weather_type)
            summary: LLM 可读的文字摘要
            pred_df: 预测结果 DataFrame（供下游 file_io 工具存 Excel）
            weather_type: 天气类型
    """
    # 延迟导入 weather_fetcher 底层函数，避免循环依赖
    from predModels.Tools.weather_fetcher_tool import _fetch_from_archive, _fetch_from_forecast

    # predict_date / history_date 由上层 predict_power 解析后传入，不再在这里默认今天
    from predModels.Tools.cache_manager import (
        read_prediction_cache, write_prediction_cache,
        read_archive_cache, write_archive_cache,
        read_forecast_cache, write_forecast_cache,
        clean_prediction_cache, get_prediction_data_mode,
        PREDICTION_MODE_HISTORICAL,
    )

    # 【缓存查询】先查预测缓存，命中则跳过整个预测流程
    # 惰性清理过期缓存(当天已结束的预测标记为失效)
    prediction_mode = get_prediction_data_mode(predict_date)
    clean_prediction_cache()
    cached_pred = read_prediction_cache(
        station_id, predict_date, weather_data_mode=prediction_mode
    )
    if cached_pred is not None:
        print(f"\n⚡ 预测缓存命中，跳过气象拉取和模型预测")
        # 缓存里只有 fusion 列，补齐其他模型列(用 fusion 填充)保持 DataFrame 结构一致
        cached_pred["xgb"] = cached_pred["fusion"]
        cached_pred["lgb"] = cached_pred["fusion"]
        cached_pred["lstm"] = cached_pred["fusion"]
        cached_pred["lstnet"] = cached_pred["fusion"]
        cached_pred["hour"] = cached_pred["time"].dt.hour

        # 仍然查一下实际值做对比(对比不入缓存，每次实时查)
        comparison = compare_with_actual(cached_pred, station_id, predict_date)
        weather_type = "缓存(未知)"
        summary = format_prediction_summary(cached_pred, station_name, weather_type, comparison, prediction_mode)
        return summary, cached_pred, weather_type

    print(f"\n🚀 开始预测 {station_name} 站点 {predict_date} 发电量")
    print(f"   历史基准日: {history_date}")
    print(f"   经纬度: lat={lat}, lon={lon}")

    # 【步骤1】拉取历史基准气象（history_date 24h）
    # 过去日期使用 archive；今天及未来日期使用 forecast。
    today = datetime.now().date()
    history_is_historical = datetime.strptime(history_date, "%Y-%m-%d").date() < today
    history_label = "历史气象" if history_is_historical else "预报气象"
    print(f"\n📥 步骤1: 拉取{history_label} ({history_date})...")
    if history_is_historical:
        history_openmeteo = read_archive_cache(station_id, history_date)
        if history_openmeteo is not None:
            print("   历史气象缓存命中，跳过 API 调用")
        else:
            history_openmeteo = _fetch_from_archive(lat, lon, history_date, history_date).head(24)
            write_archive_cache(station_id, history_openmeteo)
    else:
        history_openmeteo = read_forecast_cache(station_id, history_date)
        if history_openmeteo is not None:
            print("   预报气象缓存命中，跳过 API 调用")
        else:
            history_openmeteo = _fetch_from_forecast(lat, lon, history_date, history_date).head(24)
            write_forecast_cache(station_id, history_openmeteo)

    if history_openmeteo is None or len(history_openmeteo) == 0:
        raise ToolException(f"历史气象拉取失败: {history_date}")

    # 【步骤2】拉取目标日气象（predict_date 24h）
    # 历史目标日使用历史实况回测，今天及未来目标日使用预报。
    target_label = "历史实况" if prediction_mode == PREDICTION_MODE_HISTORICAL else "未来预报"
    print(f"\n📥 步骤2: 拉取{target_label}气象 ({predict_date})...")
    if prediction_mode == PREDICTION_MODE_HISTORICAL:
        future_openmeteo = read_archive_cache(station_id, predict_date)
        if future_openmeteo is not None:
            print("   目标日历史气象缓存命中，跳过 API 调用")
        else:
            future_openmeteo = _fetch_from_archive(lat, lon, predict_date, predict_date).head(24)
            write_archive_cache(station_id, future_openmeteo)
    else:
        future_openmeteo = read_forecast_cache(station_id, predict_date)
        if future_openmeteo is not None:
            print("   目标日预报气象缓存命中，跳过 API 调用")
        else:
            future_openmeteo = _fetch_from_forecast(lat, lon, predict_date, predict_date).head(24)
            write_forecast_cache(station_id, future_openmeteo)

    if future_openmeteo is None or len(future_openmeteo) == 0:
        raise ToolException(f"未来气象拉取失败: {predict_date}")

    # 【步骤3】转换为 ERA5 格式
    print(f"\n🔄 步骤3: 转换 ERA5 格式...")
    history_era5 = convert_to_era5_format(history_openmeteo)
    future_era5 = convert_to_era5_format(future_openmeteo)
    print(f"   历史 ERA5: {history_era5.shape}, 未来 ERA5: {future_era5.shape}")

    # 【步骤4】执行 24h 预测
    print(f"\n🚀 步骤4: 执行 24h 预测...")
    _, pred_df, weather_type = predict_24h_auto(history_era5, future_era5)
    print(f"   预测完成: {len(pred_df)} 小时, 天气类型: {weather_type}")

    # 【步骤5】历史对比（查 MySQL 实际发电量）
    # 注意：如果是预测今天，实际值可能还没入库，compare_with_actual 会返回提示
    print(f"\n📊 步骤5: 历史对比...")
    comparison = compare_with_actual(pred_df, station_id, predict_date)

    # 【步骤6】生成摘要
    print(f"\n📝 步骤6: 生成摘要...")
    summary = format_prediction_summary(pred_df, station_name, weather_type, comparison, prediction_mode)

    # 【步骤7】预测结果写入缓存(当天有效，当天结束后逻辑删除)
    print(f"\n💾 步骤7: 写入预测缓存...")
    try:
        write_prediction_cache(
            station_id, predict_date, pred_df, weather_type,
            weather_data_mode=prediction_mode,
        )
    except Exception as e:
        print(f"   ⚠️ 预测缓存写入失败(不影响预测结果): {e}")

    return summary, pred_df, weather_type


# ============================================================
# Layer 1: LLM 工具入口 — @tool predict_power
# ============================================================

@tool(response_format="content_and_artifact")
def predict_power(
    station_name: Annotated[str, "站点名称，例如 '英杰'"],
    target_date: Annotated[str, "待预测日期，支持 'YYYY-MM-DD'、'M月D日'、'M月D号'、'M-D' 等格式。留空或传 '今天' 则预测今天"] = "",
) -> tuple[str, pd.DataFrame]:
    """预测指定光伏站点在指定日期的 24 小时发电量。

    传入站点名称和目标日期，自动完成完整流程：
    1. 查询站点经纬度
    2. 解析 target_date，历史基准日自动取前一天（如 target_date=7月3号，则历史日=7月2号）
    3. 拉取历史基准日气象（archive API）和目标日气象（forecast API）
    4. 将气象数据转换为模型需要的 ERA5 格式
    5. 使用 Stacking 集成模型预测 24 小时发电量
    6. 与 MySQL 中的实际发电量对比（如已有数据）
    7. 返回预测摘要和完整预测数据

    日期格式说明：
    - target_date 支持多种自然写法：'2026-07-03'、'7月3日'、'7月3号'、'7-3'、'今天'、'昨天'
    - 留空字符串 "" 或传 "今天" 表示预测今天
    - 历史基准日自动取 target_date 的前一天，无需单独传入

    预测结果包含 5 个模型的预测值（XGBoost、LightGBM、LSTM、LSTNet、融合模型），
    以及天气类型判断（晴天/多云）。

    返回:
        content: 预测结果文字摘要（总发电量、峰值时段、天气类型、历史对比）
        artifact: 预测结果 DataFrame（24行，列: time, hour, xgb, lgb, lstm, lstnet, fusion）
                  可供文件读写工具保存为 Excel。
    """
    from predModels.Tools.weather_fetcher_tool import (
        get_station_location, _load_stations_from_db, _match_station
    )

    # 【日期解析】支持 YYYY-MM-DD / M月D日 / M月D号 / M-D / 今天 / 昨天 等格式
    predict_date = parse_flexible_date(target_date)
    history_date = (datetime.strptime(predict_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


    print(f"\n📅 日期解析: target_date='{target_date}' → predict_date={predict_date}, history_date={history_date}")

    # 【步骤1】查站点经纬度
    print(f"\n🔍 查询站点信息: {station_name}")
    location_str = get_station_location.invoke({"station_name": station_name})

    # 从数据库查站点信息（get_station_location 返回的是文字摘要，这里需要结构化数据）
    stations = _load_stations_from_db()
    station_info = _match_station(station_name, stations)

    if station_info is None:
        raise ToolException(f"未找到站点: '{station_name}'")

    lat = station_info["lat"]
    lon = station_info["lon"]
    station_id = station_info["station_id"]
    full_name = station_info["name"]

    # 【步骤2】调用站点整合方法完成完整预测流程
    summary, pred_df, weather_type = predict_station_power(
        station_name=full_name,
        lat=lat,
        lon=lon,
        station_id=station_id,
        predict_date=predict_date,
        history_date=history_date,
    )

    return summary, pred_df


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    # 自测时把 solar_agent 根目录加入 path，保证 predModels 包能被导入
    # 当前文件路径: solar_agent/predModels/Tools/pv_predictor.py
    # 需要往上跳 3 级到 solar_agent/
    import sys
    _solar_agent_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, _solar_agent_root)

    print("=" * 60)
    print("pv_predictor.py 模块自测")
    print("=" * 60)

    # 测试 1: ModelManager 模型加载
    print("\n--- 测试 1: ModelManager 模型加载 ---")
    manager = ModelManager()
    models = manager.get_station_models("英杰", "晴天")
    print(f"  feat_cols 数量: {len(models['feat_cols'])}")
    print(f"  scaler: {type(models['scaler']).__name__}")
    print(f"  xgb: {type(models['xgb']).__name__}")
    print(f"  lgb: {type(models['lgb']).__name__}")
    print(f"  lstm: {type(models['lstm']).__name__}")
    print(f"  lstnet: {type(models['lstnet']).__name__}")
    print(f"  meta_model: {type(models['meta_model']).__name__}")

    # 测试 2: 缓存命中
    print("\n--- 测试 2: 缓存命中 ---")
    models2 = manager.get_station_models("英杰", "晴天")  # 应该命中缓存
    print(f"  同一对象: {models is models2}")

    # 测试 3: 完整预测流程
    print("\n--- 测试 3: predict_power 完整流程 ---")
    result = predict_power.invoke({"station_name": "英杰"})
    print(f"\n  返回类型: {type(result)}")

    if hasattr(result, "content") and hasattr(result, "artifact"):
        print(f"\n  === content (LLM 摘要) ===")
        print(result.content)
        print(f"\n  === artifact (DataFrame) ===")
        df = result.artifact
        print(f"  shape: {df.shape}")
        print(f"  列: {list(df.columns)}")
        print(f"  前5行:")
        print(df.head().to_string(index=False))
    else:
        print(f"  结果: {result}")

    print("\n" + "=" * 60)
    print("自测完成")
    print("=" * 60)
