"""
run.py - Agent CLI 测试入口
"""
import sys
import os

# 把 solar_agent 根目录加入 sys.path,保证 predModels / Agent 可导入
# __file__ 是 Agent/run.py,往上两级就是 solar_agent/
_SOLAR_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SOLAR_AGENT_ROOT not in sys.path:
    sys.path.insert(0, _SOLAR_AGENT_ROOT)

from Agent.agent import build_agent

def main():
    print("=" * 60)
    print("光伏发电分析助手 (输入 quit 退出)")
    print("=" * 60)

    # 构建 Agent(首次会加载 TF 模型,稍慢)
    print("正在初始化 Agent...")
    agent_executor = build_agent()
    print("初始化完成!\n")

    # 多轮对话循环
    while True:
        user_input = input("你: ").strip()
        if user_input.lower() in ("quit", "exit", "q"):
            print("再见!")
            break
        if not user_input:
            continue

        try:
            # invoke 是同步调用,返回 {"input":..., "output":..., "intermediate_steps":...}
            result = agent_executor.invoke({"input": user_input})
            print(f"\n助手: {result['output']}\n")
        except Exception as e:
            print(f"\n[错误] {e}\n")

if __name__ == "__main__":
    main()