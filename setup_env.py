#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一键生成 .env 配置文件（生产部署前必做）

它会：
  1. 生成一个高强度的随机 LEARNHUB_SECRET（会话签名 + API Key 加密都用它）；
  2. 询问/沿用数据库地址、平台 AI Key、每日额度、是否写入演示数据；
  3. 写入 .env 并做一次自检（打印实际生效的配置，密钥只显示后 4 位）。

用法：
    python setup_env.py              # 交互式
    python setup_env.py --force      # 覆盖已存在的 .env
    python setup_env.py --no-seed    # 不写入演示数据（正式环境建议）

注意：.env 已在 .gitignore 里，绝不会被提交。
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")


def ask(prompt: str, default: str = "") -> str:
    tip = f"[{default}]" if default else ""
    try:
        v = input(f"{prompt}{tip}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return v or default


def mask(s: str) -> str:
    if not s:
        return "(未设置)"
    return "••••••••" + s[-4:] if len(s) > 4 else "••••"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 LearnHub 的 .env 配置")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 .env")
    ap.add_argument("--no-seed", action="store_true", help="不写入演示账号与课程")
    ap.add_argument("--db-url", default="", help="数据库地址，留空用本地 SQLite")
    ap.add_argument("--platform-key", default="", help="平台默认 AI Key（可留空，之后在后台配置）")
    ap.add_argument("--daily-limit", default="", help="教师/学生每日 AI 出题套数，默认 3")
    ap.add_argument("--yes", action="store_true", help="全部使用默认值，不交互")
    args = ap.parse_args()

    if os.path.exists(ENV_PATH) and not args.force:
        print(f"⚠️  {ENV_PATH} 已存在。")
        print("    如需重新生成请加 --force（会覆盖，原主密钥丢失后已加密的 API Key 将无法解密）")
        return 1

    print("=" * 60)
    print("  LearnHub 环境配置生成")
    print("=" * 60)

    if args.yes:
        db_url, pkey, limit = args.db_url, args.platform_key, args.daily_limit or "3"
    else:
        print("\n【1/4】数据库地址")
        print("      直接回车 = 本地 SQLite（data/learnhub.db）")
        print("      例如： postgresql+psycopg://user:pass@host:5432/learnhub")
        db_url = args.db_url or ask("  LEARNHUB_DB_URL", "")

        print("\n【2/4】平台默认 AI API Key（可留空）")
        print("      留空也可以，之后用管理员账号登录后台「🔑 平台 AI Key」上传。")
        pkey = args.platform_key or ask("  LEARNHUB_PLATFORM_API_KEY", "")

        print("\n【3/4】教师/学生每日 AI 出题套数")
        limit = args.daily_limit or ask("  LEARNHUB_DAILY_AI_LIMIT", "3")

    print("\n【4/4】是否写入演示数据？")
    if args.no_seed:
        seed = "0"
        print("      已通过 --no-seed 关闭")
    elif args.yes:
        seed = "1"
        print("      使用默认：写入（含 admin@demo.edu / teacher@demo.edu / student@demo.edu）")
    else:
        ans = ask("  写入演示数据？正式环境建议选 n (y/n)", "y").lower()
        seed = "0" if ans.startswith("n") else "1"

    secret = secrets.token_urlsafe(48)
    content = f"""# ============ LearnHub 配置（由 setup_env.py 生成，勿提交到 git） ============
# 会话签名 + API Key 加密主密钥。⚠️ 换掉这个值会导致已保存的 API Key 无法解密。
LEARNHUB_SECRET={secret}

# 数据库地址；留空 = 本地 SQLite（data/learnhub.db）
# 迁移到 Postgres 示例： postgresql+psycopg://user:pass@host:5432/learnhub
LEARNHUB_DB_URL={db_url}

# 平台默认 AI Key 的「容器初始化兜底」。
# 正常用法是在管理员后台「🔑 平台 AI Key」页面上传（加密存库、对所有用户生效）。
LEARNHUB_PLATFORM_API_KEY={pkey}
LEARNHUB_DEFAULT_PROVIDER=deepseek

# 教师/学生使用平台 Key 时的每日出题套数（管理员可随时在后台改）
LEARNHUB_DAILY_AI_LIMIT={limit}

# 首次启动是否写入演示账号与课程；正式环境建议 0
LEARNHUB_SEED={seed}

# 会话有效期（秒），默认 7 天
LEARNHUB_TOKEN_TTL=604800
"""
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        os.chmod(ENV_PATH, 0o600)          # 尽量收紧权限（Windows 上是 no-op 也不报错）
    except OSError:
        pass

    # ---------- 自检：真正加载一次并打印生效配置 ----------
    sys.path.insert(0, HERE)
    for m in ("config", "security", "db", "llm"):
        sys.modules.pop(m, None)
    import config  # noqa: F401
    import security
    import db

    print("\n" + "=" * 60)
    print("  ✅ 已写入", ENV_PATH)
    print("=" * 60)
    print(f"  LEARNHUB_SECRET         {mask(security.SECRET_KEY)}   (长度 {len(security.SECRET_KEY)})")
    print(f"  LEARNHUB_DB_URL         {db.DB_URL}")
    print(f"  LEARNHUB_DAILY_AI_LIMIT {os.environ.get('LEARNHUB_DAILY_AI_LIMIT', '3')}")
    print(f"  LEARNHUB_SEED           {os.environ.get('LEARNHUB_SEED', '1')}")
    print(f"  平台 AI Key             {mask(os.environ.get('LEARNHUB_PLATFORM_API_KEY', ''))}")
    print(f"  会话有效期              {security.TOKEN_TTL_SECONDS} 秒")

    if security.SECRET_KEY == "dev-only-change-me-in-production":
        print("\n  ⚠️  主密钥仍是默认值，请检查 .env 是否被正确加载！")
        return 2

    print("\n  ⚠️  备份 .env：丢失主密钥后，已保存的用户 API Key 将无法解密。")
    print("\n  下一步：")
    print("      uvicorn app:app --host 0.0.0.0 --port 8000")
    if seed == "1":
        print("\n  演示账号（密码均为 demo1234）：")
        print("      管理员 admin@demo.edu   教师 teacher@demo.edu   学生 student@demo.edu")
        print("      课程邀请码 DEMO01")
        print("      ⚠️  正式上线前请把这些演示账号改密码或直接删除！")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
