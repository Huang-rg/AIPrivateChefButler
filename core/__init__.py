"""核心业务逻辑包：AI私厨管家的"大脑"。

    - llm_models.py：★ 大模型初始化中心（换模型只改这一个文件）
    - agent.py     ：主 Agent，绑定工具（Tavily 搜索）与记忆
    - memory.py    ：PostgreSQL 持久化记忆，保证多轮对话连续
"""
