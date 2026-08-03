from backend.tools.weather_fetcher_tool import get_current_datetime, get_weather_by_range

from backend.tools.pv_predictor import (
    build_prediction_confirmation_request,
    predict_power,
)

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
from backend.tools.ask_user_tool import ask_user, request_user_input
from backend.tools.chart_plan_tool import get_power_dataset, create_chart_plan, get_chart_capabilities
from backend.tools.import_tool import import_power_data
from backend.app.runtime import (
    RuntimeEngine,
    RuntimeInteractionService,
    UserIntentInterpreter,
    build_tool_spec,
    wrap_tool,
)
from backend.app.runtime.tool_input import wrap_tool_input


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


# R3 将预测工具纳入 Runtime 管理，使 ConfirmationHook 能在真正执行前介入。
RUNTIME_MANAGED_TOOL_NAMES = {"get_station_location", "predict_power"}
# 保留旧常量名，避免已有测试或外部注册代码导入时产生不必要的兼容问题。
R2_MANAGED_TOOL_NAMES = RUNTIME_MANAGED_TOOL_NAMES


def get_all_tools(
    *,
    intent_interpreter: UserIntentInterpreter | None = None,
):
    """返回 Agent 工具列表，保持原工具名和参数协议不变。"""

    interaction_service = RuntimeInteractionService(
        request_user_input,
        intent_interpreter=intent_interpreter,
    )
    runtime_engine = RuntimeEngine(
        interaction_service=interaction_service,
        confirmation_request_builders={
            "predict_power": build_prediction_confirmation_request,
        },
    )

    prepared_tools = []
    for tool in ALL_TOOLS:
        if tool.name in RUNTIME_MANAGED_TOOL_NAMES:
            tool = wrap_tool(
                tool,
                runtime_engine=runtime_engine,
                runtime_spec=build_tool_spec(
                    tool,
                    requires_confirmation=tool.name == "predict_power",
                ),
            )
        else:
            tool = wrap_tool_input(tool)
        prepared_tools.append(tool)
    return prepared_tools
