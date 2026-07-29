from backend.tools.weather_fetcher_tool import get_current_datetime, get_weather_by_range

from backend.tools.pv_predictor import predict_power

from backend.tools.power_query_tool import (
    get_actual_power,
    get_actual_power_by_range,
    get_predicted_power,
    get_weather_records,
    get_power_comparison,
)
from backend.tools.station_query_tool import (
    get_station_info,
    get_station_location,
    get_stations_by_region,
    list_all_stations,
)

from backend.tools.file_io_tool import write_file, read_file, verify_file
from backend.tools.table_io_tool import export_table, read_table
from backend.tools.knowledge_base_tool import search_knowledge_base
from backend.tools.date_parser_tool import parse_date
from backend.tools.ask_user_tool import ask_user
from backend.tools.chart_plan_tool import get_power_dataset, create_chart_plan, get_chart_capabilities
from backend.tools.import_tool import import_power_data
from backend.app.runtime import RuntimeEngine, wrap_tool


ALL_TOOLS = [
    # 站点查询类
    get_station_location,
    get_station_info,
    list_all_stations,
    get_stations_by_region,
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
    # 可视化数据类
    get_chart_capabilities,
    get_power_dataset,
    create_chart_plan,
    # 数据导入类
    import_power_data,
]


# R2 先选择低风险只读工具做最小纵切，其他工具继续沿用 R1 的旁路观察。
R2_MANAGED_TOOL_NAMES = {"get_station_location"}


def get_all_tools():
    """返回 Agent 工具列表，保持原工具名和参数协议不变。"""

    runtime_engine = RuntimeEngine()
    return [
        wrap_tool(tool, runtime_engine=runtime_engine)
        if tool.name in R2_MANAGED_TOOL_NAMES
        else tool
        for tool in ALL_TOOLS
    ]
