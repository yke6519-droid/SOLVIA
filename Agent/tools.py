from predModels.Tools.weather_fetcher_tool import (
    get_today_weather,
    get_yesterday_weather,
    get_current_datetime,
    get_station_location,
    get_weather_by_range
)

from predModels.Tools.pv_predictor import predict_power

from predModels.Tools.power_query_tool import (
    get_station_info,
    get_actual_power,
    get_actual_power_by_range,
    get_predicted_power,
    get_weather_records,
    get_power_comparison,
)

from predModels.Tools.file_io_tool import write_file, read_file

ALL_TOOLS = [
    # 站点查询类
    get_station_location,
    get_station_info,
    # 时间查询类
    get_current_datetime,
    # 气象数据类
    get_today_weather,
    get_yesterday_weather,
    get_weather_by_range,
    get_weather_records,
    # 发电预测类
    predict_power,
    # 发电量查询类
    get_actual_power,
    get_actual_power_by_range,
    get_predicted_power,
    get_power_comparison,
    # 文件 I/O 类
    write_file,
    read_file,
]

def get_all_tools():
    return ALL_TOOLS
