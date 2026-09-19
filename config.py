"""
环境变量加载：把同目录下的 .env 读进 os.environ。

必须在读取任何 LEARNHUB_* 配置之前 import 本模块，所以 app.py / db.py 都会先
`import config`。已存在的系统环境变量优先，.env 只作为补充（不会覆盖）。

这样部署时只要：
    1) python setup_env.py      # 生成 .env（含随机主密钥）
    2) uvicorn app:app
就够了，不用手工 export 一堆变量。
"""
from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
except ImportError:                       # 没装 python-dotenv 也不影响运行
    load_dotenv = None  # type: ignore

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

if load_dotenv is not None:
    # override=False：系统环境变量优先，.env 只补缺
    load_dotenv(ENV_PATH, override=False)
