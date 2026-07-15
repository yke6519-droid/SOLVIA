"""
run.py - Agent CLI 测试入口
"""
import sys
import os
from dotenv import load_dotenv

# 把 solar_agent 根目录加入 sys.path,保证 backend/tools / backend.Agent 可导入
# __file__ 是 agent/run.py,往上两级就是 solar_agent/
_SOLAR_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SOLAR_AGENT_ROOT not in sys.path:
    sys.path.insert(0, _SOLAR_AGENT_ROOT)

from backend.Agent.agent import build_agent


def main():
    print("=" * 60)
    print("光伏发电分析助手 (输入 quit 退出)")
    print("=" * 60)

    # 构建 Agent (首次会加载 TF 模型,稍慢)
    # use_db=True: 启用 MySQL 持久化记忆
    # use_db=False: 纯内存记忆 (测试用)
    print("正在初始化 Agent...")
    agent_executor = build_agent(session_id="user_02", use_db=True)
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
            result = agent_executor.invoke({"input": user_input})
            print(f"\n助手: {result['output']}\n")

            # 对话结束后触发摘要 (失败不影响对话)
            try:
                agent_executor.memory.maybe_summarize()
            except Exception as e:
                print(f"[摘要] 生成失败(不影响对话): {e}")

        except Exception as e:
            print(f"\n[错误] {e}\n")


if __name__ == "__main__":
    main()
