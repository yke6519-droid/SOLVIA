from predModels.Tools.weather_fetcher_tool import (
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

from predModels.Tools.file_io_tool import write_file, read_file, verify_file
from predModels.Tools.table_io_tool import export_table, read_table
from predModels.Tools.knowledge_base_tool import search_knowledge_base
from predModels.Tools.date_parser_tool import parse_date
from predModels.Tools.ask_user_tool import ask_user

ALL_TOOLS = [
    # 站点查询类
    get_station_location,
    get_station_info,
    # 时间查询类
    get_current_datetime,
    parse_date,
    # 气象数据类
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
    verify_file,
    # 表格导入导出类
    export_table,
    read_table,
    # 知识库检索类
    search_knowledge_base,
    # 用户交互类
    ask_user,
]

def get_all_tools():
    return ALL_TOOLS
