# SolarAgent 光伏智能调度预测智能体系统
## 项目简介
`SolarAgent` 是一套面向工厂分布式光伏场景的**智能体光伏预测与数据调度系统**，基于大语言模型构建自主决策 Agent，实现光伏发电量预测、气象数据查询、电力负荷查询、SQL 数据缓存管理、持久化多轮对话记忆一体化自动化流程。

项目核心解决传统光伏预测固定时段、数据零散、查询繁琐、长对话上下文易丢失的痛点，支持用户自定义任意日期区间做光伏出力预测，自动持久化气象、发电预测结果与对话记忆至数据库，提供可复用工具插件化架构，便于扩展更多能源业务工具。

## 技术栈
### 后端核心
- Python 3.11
- LLM 大模型：通义千问 / Qwen 系列
- Agent 框架：自研模块化 Agent（提示词管理、记忆存储、工具调用、执行入口解耦）
- 预测模型：XGBoost / LightGBM / LSTM 多模型融合光伏预测 `predModels`
- 数据存储：SQLite / MySQL（缓存数据表、对话记忆表预定义 SQL）
### 工程配套
- Git 版本管理，Windows 开发环境
- 虚拟环境 `.venv` 隔离依赖
- 工具插件化设计：气象查询、发电量查询、缓存管理独立工具类

## 项目目录结构
```
solar_agent/
├── Agent/                      # 智能Agent核心框架（原agent文件夹重命名）
│   ├── __init__.py
│   ├── agent.py                # Agent主调度逻辑，工具调用循环
│   ├── llm.py                 # 大模型对话封装
│   ├── prompt.py              # 系统提示词、角色定义
│   ├── memory.py              # 新增：滑动窗口摘要持久化对话记忆
│   ├── tools.py               # 工具统一注册与分发
│   └── run.py                 # 程序启动入口
├── predModels/                 # 光伏预测模型模块
│   ├── pv_predictor.py        # 光伏发电预测核心
│   └── Tools/                 # 业务工具集
│       ├── weather_fetcher_tool.py   # 气象数据获取工具
│       ├── power_query_tool.py      # 电力/发电量查询工具
│       └── cache_manager.py         # SQL缓存读写管理工具
├── sql/                        # 数据库初始化脚本
│   ├── init_schema.sql         # 基础数据表建表语句
│   ├── cache_schema.sql        # 预测、气象数据缓存表
│   └── memory_schema.sql       # 新增：对话记忆、摘要存储数据表
├── temp/                       # 临时测试脚本
│   ├── LLM.py
│   ├── conversationSummaryBufferMemory.py
│   └── helloQwen.py
├── .venv/                      # Python虚拟环境
├── .gitignore                  # 新增：屏蔽pycache编译缓存文件
└── README.md                   # 项目说明文档
```

## 核心功能更新
### 原有基础功能
1. **完整 Agent 框架搭建**
    分层解耦大模型交互、记忆、提示词、工具调度，形成可复用智能体骨架。
2. **插件式业务工具调用**
    封装气象查询、光伏出力查询、数据缓存工具，Agent 自动识别并调用对应工具完成用户指令。
3. **预测数据持久化优化**
    完成光伏发电预测、气象数据查询后，自动将结果存入数据库缓存，避免重复请求与重复计算。
4. **灵活时间区间预测**
    破除固定当日预测限制，支持用户自定义任意起止日期进行光伏出力预测与历史数据查询。

### 本次迭代新增记忆相关能力
1. **多轮连贯对话交互**
    单进程内完整保存人机对话上下文，支持连续多轮问答交互。
2. **MySQL 双层对话记忆持久化**
    新增专用建表脚本，分别存储完整原始对话记录与压缩摘要，进程重启后历史记忆不丢失。
3. **SessionID 会话隔离**
    通过会话标识区分不同用户对话，各会话记忆数据独立互不干扰。
4. **滑动窗口自动摘要机制**
    保留窗口内最新完整对话，超出阈值自动调用大模型生成历史摘要，降低上下文 Token 消耗，预留记忆分层扩展接口。
5. **工程规范优化**
    新增 gitignore 过滤编译缓存文件，统一代码换行适配 Windows 开发环境。

## 快速启动
### 1. 环境准备
```powershell
# 进入项目目录
cd D:\AAA_myProjects\howso\myAgent\solar_agent
# 激活虚拟环境
.venv\Scripts\Activate.ps1
# 安装依赖（自行补充requirements.txt）
pip install -r requirements.txt
```

### 2. 数据库初始化
依次执行 `sql/init_schema.sql`、`sql/cache_schema.sql`、`sql/memory_schema.sql` 完成全部数据表创建，支持 MySQL / SQLite。

### 3. 运行智能Agent
```powershell
python Agent/run.py
```

## 工具模块说明
| 工具 | 功能 |
|------|------|
| weather_fetcher_tool | 获取指定时间段气象数据（辐照度、温度、湿度等光伏输入特征） |
| power_query_tool | 查询历史光伏发电量、厂区负荷数据 |
| cache_manager | 将预测结果、气象原始数据写入数据库缓存，提供查询接口 |

## Git 开发记录说明
1. 目录重构：将小写 `agent/` 重命名为 `Agent/` 规范模块命名
2. 新增对话记忆全套代码、数据库脚本与测试文件，完善仓库忽略规则
3. 远程仓库存在历史提交，推送前需执行 pull 合并代码避免冲突；GitHub 443端口连接超时可使用 ghproxy 镜像加速推送

## 后续规划
1. 优化对话记忆逻辑，增加多轮对话上下文纠错能力，优化 Agent 工具调用判断逻辑
2. 新增可视化模块，导出光伏预测曲线图表
3. 接入定时任务，实现自动日度光伏预测入库
4. 封装 Docker 部署，支持 Linux 服务器一键运行

## 仓库地址
GitHub：https://github.com/yke6519-droid/SolarAgent
