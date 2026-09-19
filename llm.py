"""
AI 出题层：统一走 OpenAI 兼容的 /chat/completions 协议

支持：DeepSeek（默认）、OpenAI、通义千问、智谱、Kimi、自定义 OpenAI 兼容端点。
没有配置任何 key 时自动降级为 MockProvider（离线模板出题），
保证本地 Demo 不联网也能完整跑通链路。
"""
from __future__ import annotations

import json
import os
import random
import re
from typing import Any

import httpx

import config  # noqa: F401  —— 必须先加载 .env，再读下面的环境变量

# ------------------------------------------------------------------ 提供商注册表
PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "dashscope": {"label": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "model": "qwen-plus"},
    "zhipu": {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "moonshot": {"label": "Kimi", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "custom": {"label": "自定义 OpenAI 兼容接口", "base_url": "", "model": ""},
}

DEFAULT_PROVIDER = os.environ.get("LEARNHUB_DEFAULT_PROVIDER", "deepseek")
# 注意：平台默认 API Key 不再从环境变量读取，而是由管理员在后台配置并存入
# settings 表（加密存储），对全体用户生效。见 platform_config() / resolve_ai_config()。
ENV_FALLBACK_KEY = os.environ.get("LEARNHUB_PLATFORM_API_KEY", "").strip()  # 可选：容器初始化用


class LlmError(RuntimeError):
    """AI 调用失败（额度、网络、格式等），由路由层转成 HTTP 错误。"""


# ------------------------------------------------------------------ Prompt
ANALYSIS_MIN_LEN = 30          # 解析低于这个长度视为"不合格"

SYSTEM_PROMPT = """你是一位严谨的高校课程命题老师。请根据给定的课程知识点命制试题。
要求：
1. 只考查给定知识点范围内的内容，不超纲；
2. 题干表述清晰、无歧义，选择题的干扰项要合理；
3. 严格输出 JSON，不要输出任何解释性文字或 Markdown 代码块标记；
4. difficulty 必须是 1~5 的整数（1 最容易，5 最难），不要写"中等""简单"等文字。

【解析（analysis）是硬性要求，必须详细，不达标视为无效题目】
- 必须说明正确选项为什么正确：给出依据的定义、公式或推理过程；
- 必须指出错误选项分别错在哪里（至少覆盖主要干扰项）；
- 建议不少于 60 字，绝对不能只写"略""同上""见教材""因为 A 对"这类空话；
- 判断题也要写清判断依据（哪个条件不满足、哪个概念被偷换）。

JSON 结构：
{"questions":[{"qtype":"single|multi|judge","stem":"题干","options":["A. ...","B. ...","C. ...","D. ..."],"answer":"A","analysis":"详细解析","difficulty":1-5}]}
说明：判断题 options 固定为 ["对","错"]，answer 填 "对" 或 "错"；多选题 answer 形如 "AB"。"""


def build_user_prompt(kp_titles: list[str], kp_contents: list[str], count: int,
                      qtype: str, difficulty: str, extra: str = "",
                      retry_hint: str = "") -> str:
    type_cn = {"single": "单项选择题", "multi": "多项选择题", "judge": "判断题", "mixed": "混合题型"}[qtype]
    diff_cn = {"easy": "偏基础", "medium": "中等", "hard": "偏难", "mixed": "难度适中、有梯度"}[difficulty]
    kp_block = "\n\n".join(
        f"【知识点 {i + 1}】{t}\n{c[:1200]}" for i, (t, c) in enumerate(zip(kp_titles, kp_contents)))
    return (f"请命制 {count} 道{type_cn}，难度{diff_cn}。\n"
            f"{'补充要求：' + extra if extra else ''}\n"
            f"{retry_hint}\n\n"
            f"可考查的知识点如下：\n{kp_block}")


# ------------------------------------------------------------------ 解析
def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise LlmError(f"AI 返回内容不是合法 JSON：{e}") from e
    raise LlmError("AI 返回内容里找不到 JSON")


def _normalize(questions: list[dict]) -> list[dict]:
    out: list[dict] = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        stem = str(q.get("stem") or q.get("question") or "").strip()
        if not stem:
            continue
        qtype = str(q.get("qtype") or q.get("type") or "single").lower()
        if qtype not in ("single", "multi", "judge", "short"):
            qtype = "single"
        options = q.get("options")
        if qtype == "judge" and not options:
            options = ["对", "错"]
        if isinstance(options, str):
            options = [x.strip() for x in options.split("|") if x.strip()]
        if isinstance(options, list):
            options = [str(o).strip() for o in options if str(o).strip()]
            if qtype == "judge" and len(options) != 2:
                options = ["对", "错"]
        else:
            options = None

        answer = canonical_answer(q.get("answer"), options, qtype)
        analysis = str(q.get("analysis") or q.get("explanation") or "").strip()
        out.append({
            "qtype": qtype,
            "stem": stem,
            "options": options,
            "answer": answer,
            "analysis": analysis,
            "difficulty": normalize_difficulty(q.get("difficulty")),
        })
    return out


# ------------------------------------------------------------------ 规范化工具
DIFFICULTY_WORDS = {
    "很容易": 1, "极简单": 1, "简单": 1, "容易": 2, "基础": 2, "较易": 2, "easy": 2,
    "中等": 3, "一般": 3, "适中": 3, "medium": 3, "normal": 3,
    "较难": 4, "偏难": 4, "困难": 4, "hard": 4, "很难": 5, "极难": 5,
}


def normalize_difficulty(value, default: int = 3) -> int:
    """把 AI 可能返回的 '中等' / '3' / 3 / None 统一成 1~5 的整数。

    这样前端永远不会出现「难度 undefined」。
    """
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        try:
            return max(1, min(5, int(round(float(value)))))
        except (TypeError, ValueError):
            return default
    text = str(value).strip().lower()
    for word in sorted(DIFFICULTY_WORDS, key=len, reverse=True):   # 长词优先，避免"很简单"命中"简单"
        if word in text:
            return DIFFICULTY_WORDS[word]
    m = re.search(r"[1-5]", text)
    if m:
        return int(m.group(0))
    return default


def canonical_answer(value, options: list[str] | None, qtype: str) -> str:
    """把答案统一成"规范形式"，避免 AI 返回的答案和选项对不上。

    规范形式：
      * single / multi : 选项字母，如 "A"、"ABD"（升序去重）
      * judge          : "对" 或 "错"
      * short          : 原文

    兼容处理：AI 常返回 "B. 观测噪声"、"B、观测噪声"、小写 "b"、
    或（判断题）返回 "正确"/"错误"/"T"/"F"/"true"/"false"，
    这里统一归一化，保证前端"正确选项标绿"一定能匹配上。
    """
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""

    if qtype == "judge":
        t = raw.lower()
        if raw in ("对", "√", "正确", "是") or t in ("true", "t", "yes", "y", "right", "correct", "1"):
            return "对"
        if raw in ("错", "×", "错误", "否") or t in ("false", "f", "no", "n", "wrong", "incorrect", "0"):
            return "错"
        if "对" in raw or "正确" in raw:
            return "对"
        if "错" in raw or "错误" in raw:
            return "错"
        return raw[:1]

    if qtype == "short":
        return raw

    # single / multi：抽取所有字母
    letters = re.findall(r"[A-Za-z]", raw)
    if letters:
        joined = "".join(sorted({c.upper() for c in letters}))
        if qtype == "single" and len(joined) > 1:
            # 单选题答案里出现多个字母，通常是 "B. xxx" 这种带了解释，取第一个
            joined = joined[0]
        return joined
    return raw


def analysis_ok(analysis: str) -> bool:
    """解析是否够详细。太短或明显是套话则判为不合格。"""
    t = (analysis or "").strip()
    if len(t) < ANALYSIS_MIN_LEN:
        return False
    if t in ("略", "同上", "见教材", "无", "略述", "解析", "暂无"):
        return False
    return True


# ------------------------------------------------------------------ 真实调用
def _call_openai_compatible(base_url: str, api_key: str, model: str,
                            system: str, user: str, timeout: float = 90.0) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.7,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise LlmError(f"无法连接 AI 服务（{base_url}）：{e}") from e

    if resp.status_code == 401:
        raise LlmError("API Key 无效或已过期（HTTP 401）")
    if resp.status_code == 402:
        raise LlmError("API 账户余额不足（HTTP 402）")
    if resp.status_code == 429:
        raise LlmError("AI 服务限流，请稍后重试（HTTP 429）")
    if resp.status_code >= 400:
        raise LlmError(f"AI 服务返回错误 HTTP {resp.status_code}：{resp.text[:300]}")

    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        raise LlmError(f"无法解析 AI 响应：{e}") from e


# ------------------------------------------------------------------ Mock（离线）
class MockProvider:
    """没有 API Key 时的降级方案：基于知识点文本生成模板题，保证流程可演示。"""

    _TPL_SINGLE = [
        ("关于「{kp}」，下列说法正确的是：", ["符合教材描述的表述", "与定义相反的表述",
                                              "把概念张冠李戴的表述", "过度绝对化的表述"], "A"),
        ("下列关于「{kp}」的叙述中，错误的是：", ["与基本定义一致的说法", "混淆了相近概念的说法",
                                                  "遗漏关键前提的说法", "扩大了适用范围的说法"], "B"),
    ]

    def generate(self, kp_titles, kp_contents, count, qtype, difficulty, extra="") -> list[dict]:
        rnd = random.Random(hash(tuple(kp_titles)) & 0xFFFF)
        items: list[dict] = []
        for i in range(count):
            kp = kp_titles[i % len(kp_titles)] if kp_titles else "本课程知识点"
            snippet = (kp_contents[i % len(kp_contents)][:120].replace("\n", " ")
                       if kp_contents else "")
            if qtype == "judge" or (qtype == "mixed" and i % 3 == 2):
                ans = rnd.choice(["对", "错"])
                items.append({
                    "qtype": "judge", "options": ["对", "错"], "answer": ans,
                    "stem": f"判断：{kp} 的核心结论在任意条件下都严格成立。",
                    "analysis": (
                        f"【模拟题·离线模式】本题考查「{kp}」的适用前提。"
                        f"结论本身是正确的，但它成立需要满足特定条件（例如模型线性、"
                        f"噪声为互不相关的高斯白噪声等），一旦前提被破坏，结论就不再保证成立。"
                        f"题干用了「在任意条件下」「严格成立」这类绝对化表述，因此判断为「错」。"
                        f"做这类判断题的关键是：先找出结论的前提条件，再看题目有没有把它去掉或扩大。"
                        f"参考知识点原文：{snippet}"),
                    "difficulty": 2,
                })
            else:
                stem_tpl, opts, ans = rnd.choice(self._TPL_SINGLE)
                correct_txt = opts["ABCD".index(ans)]
                items.append({
                    "qtype": "single", "options": [f"{c}. {o}" for c, o in zip("ABCD", opts)],
                    "answer": ans,
                    "stem": stem_tpl.format(kp=kp),
                    "analysis": (
                        f"【模拟题·离线模式】本题考查「{kp}」。"
                        f"正确选项 {ans}（{correct_txt}）与知识点的定义/结论完全一致，"
                        f"是在给定前提下的标准表述。"
                        f"其余选项之所以错误：有的把定义反向表述（把「增大」说成「减小」之类），"
                        f"有的偷换概念（用相近但不同的名词替代关键词），"
                        f"还有的以偏概全或过度绝对化（把「在满足某条件时成立」说成「总是成立」）。"
                        f"判断这类题的方法：逐项回到知识点原文核对关键词、条件与取值范围。"
                        f"参考知识点原文：{snippet}"),
                    "difficulty": 3,
                })
        return items


# ------------------------------------------------------------------ 对外入口
def generate_questions(*, provider: str, api_key: str, base_url: str, model: str,
                       kp_titles: list[str], kp_contents: list[str], count: int,
                       qtype: str, difficulty: str, extra: str = "") -> tuple[list[dict], str]:
    """返回 (题目列表, 实际使用的 provider 名)。api_key 为空时走 Mock。"""
    count = max(1, min(int(count), 20))
    if not api_key:
        return MockProvider().generate(kp_titles, kp_contents, count, qtype, difficulty, extra), "mock"

    cfg = PROVIDERS.get(provider, PROVIDERS["deepseek"])
    url = (base_url or cfg["base_url"]).strip()
    mdl = (model or cfg["model"]).strip()
    if not url or not mdl:
        raise LlmError("自定义服务商需要同时填写 Base URL 和模型名")

    raw = _call_openai_compatible(
        url, api_key, mdl, SYSTEM_PROMPT,
        build_user_prompt(kp_titles, kp_contents, count, qtype, difficulty, extra))
    data = _extract_json(raw)
    questions = _normalize(data.get("questions") or [])
    if not questions:
        raise LlmError("AI 没有生成有效题目，请重试或调整知识点内容")

    # 解析不合格的题目：先尝试让 AI 补齐解析，仍不合格就丢弃该题
    bad = [q for q in questions if not analysis_ok(q["analysis"])]
    if bad:
        good = _fill_missing_analysis(url, api_key, mdl, bad)
        kept = [q for q in questions if analysis_ok(q["analysis"])] + good
        if not kept:
            raise LlmError("AI 生成的题目缺少解析，请重试（可在补充要求里强调「必须给出详细解析」）")
        questions = kept

    return questions, provider


def _fill_missing_analysis(url: str, api_key: str, mdl: str, items: list[dict]) -> list[dict]:
    """对解析过短/缺失的题目再请求一次，只补解析。补不出来就丢弃。"""
    payload = [{"stem": q["stem"], "options": q.get("options"),
                "answer": q["answer"], "qtype": q["qtype"]} for q in items]
    prompt = ("下面这些题目缺少合格解析。请为每一题补写**详细解析**（≥60 字，"
              "说明正确项为什么对、错误项错在哪），保持题目与答案不变。\n"
              "严格输出 JSON：{\"questions\":[{\"analysis\":\"...\"}]}，顺序与输入一致。\n\n"
              + json.dumps(payload, ensure_ascii=False))
    try:
        raw = _call_openai_compatible(url, api_key, mdl, SYSTEM_PROMPT, prompt, timeout=90.0)
        data = _extract_json(raw)
        fixes = [str(x.get("analysis") or "").strip()
                 for x in (data.get("questions") or []) if isinstance(x, dict)]
    except (LlmError, Exception):
        fixes = []

    out = []
    for i, q in enumerate(items):
        if i < len(fixes) and analysis_ok(fixes[i]):
            q = dict(q, analysis=fixes[i])
            out.append(q)
    return out


def platform_config(db) -> dict[str, Any]:
    """
    读取管理员配置的平台默认 AI 配置（存在 settings 表里，Key 加密）。

    返回 {"configured": bool, "provider", "api_key", "base_url", "model", "enabled"}
    兼容处理：若数据库里没有配置，但环境变量里有 LEARNHUB_PLATFORM_API_KEY，
    则以环境变量作为兜底（方便容器化部署时初始化）。
    """
    from db import (SK_PLATFORM_BASE_URL, SK_PLATFORM_ENABLED, SK_PLATFORM_KEY_ENC,
                    SK_PLATFORM_MODEL, SK_PLATFORM_PROVIDER, get_setting)
    from security import decrypt_secret

    enc = get_setting(db, SK_PLATFORM_KEY_ENC)
    api_key = ""
    if enc:
        try:
            api_key = decrypt_secret(enc)
        except Exception:
            api_key = ""
    if not api_key and ENV_FALLBACK_KEY:
        api_key = ENV_FALLBACK_KEY

    enabled = (get_setting(db, SK_PLATFORM_ENABLED, "1") or "1") == "1"
    return {
        "configured": bool(api_key),
        "enabled": enabled,
        "provider": get_setting(db, SK_PLATFORM_PROVIDER, DEFAULT_PROVIDER) or DEFAULT_PROVIDER,
        "api_key": api_key if enabled else "",
        "base_url": get_setting(db, SK_PLATFORM_BASE_URL, "") or "",
        "model": get_setting(db, SK_PLATFORM_MODEL, "") or "",
    }


def resolve_ai_config(db, user, course, platform_default: bool = True) -> dict[str, Any]:
    """
    决定这次 AI 调用用谁的 key。优先级：
      1) 自己的 key                       → 不限次数（source='own'）
      2) 管理员 + 平台默认 key（管理员配置）→ 不限次数（source='admin'）
      3) 课程负责教师的 key                → 走每日限制（source='teacher'）
      4) 平台默认 key（管理员配置）         → 走每日限制（source='platform'）；没配则离线 Mock
    """
    from security import decrypt_secret

    if user.ai_api_key_enc:
        return {"provider": user.ai_provider or DEFAULT_PROVIDER,
                "api_key": decrypt_secret(user.ai_api_key_enc),
                "base_url": user.ai_base_url or "", "model": user.ai_model or "",
                "source": "own"}

    plat = platform_config(db)
    if getattr(user, "is_admin", False):
        return {"provider": plat["provider"], "api_key": plat["api_key"],
                "base_url": plat["base_url"], "model": plat["model"], "source": "admin"}

    owner = course.owner if course is not None else None
    if owner and owner.ai_api_key_enc:
        return {"provider": owner.ai_provider or DEFAULT_PROVIDER,
                "api_key": decrypt_secret(owner.ai_api_key_enc),
                "base_url": owner.ai_base_url or "", "model": owner.ai_model or "",
                "source": "teacher"}

    return {"provider": plat["provider"], "api_key": plat["api_key"],
            "base_url": plat["base_url"], "model": plat["model"], "source": "platform"}


# ------------------------------------------------------------------ 资料 → 知识点
EXTRACT_SYSTEM = """你是一位课程助教。请把老师上传的课程资料整理成结构化的知识点列表。
要求：
1. 按内容逻辑切分成若干个独立知识点，每个知识点自成一个完整主题；
2. title 简洁（不超过 25 字），content 保留关键定义、公式、结论与要点，可适当润色但不得编造；
3. 覆盖资料中的全部主要内容，不要遗漏，也不要重复；
4. 严格输出 JSON，不要输出任何解释文字或 Markdown 代码块标记。

JSON 结构：
{"points":[{"title":"知识点标题","content":"该知识点的完整内容，可含换行"}]}"""


def extract_knowledge_points(*, provider: str, api_key: str, base_url: str, model: str,
                             text: str, course_title: str = "",
                             max_points: int = 20) -> tuple[list[dict], str]:
    """把上传资料的正文整理成知识点列表，返回 (points, provider)。未配 key 时走本地启发式切分。"""
    text = (text or "").strip()
    if not text:
        raise LlmError("文件内容为空，或该格式无法解析出文字")

    if not api_key:
        return _heuristic_split(text, max_points), "mock"

    cfg = PROVIDERS.get(provider, PROVIDERS["deepseek"])
    url = (base_url or cfg["base_url"]).strip()
    mdl = (model or cfg["model"]).strip()
    if not url or not mdl:
        raise LlmError("自定义服务商需要同时填写 Base URL 和模型名")

    user_prompt = (f"课程名称：{course_title or '（未提供）'}\n"
                   f"请把下面的资料整理成不超过 {max_points} 个知识点：\n\n"
                   f"---- 资料开始 ----\n{text[:20000]}\n---- 资料结束 ----")
    raw = _call_openai_compatible(url, api_key, mdl, EXTRACT_SYSTEM, user_prompt, timeout=150.0)
    data = _extract_json(raw)
    points = []
    for p in (data.get("points") or []):
        if not isinstance(p, dict):
            continue
        title = str(p.get("title") or "").strip()
        content = str(p.get("content") or p.get("detail") or "").strip()
        if title:
            points.append({"title": title[:200], "content": content})
    if not points:
        raise LlmError("AI 没有从资料中识别出知识点，请换一份内容更完整的文件")
    return points[:max_points], provider


def _heuristic_split(text: str, max_points: int) -> list[dict]:
    """离线降级：按 Markdown 标题 / 编号标题切分，保证不配 Key 也能演示「上传→整理」流程。"""
    import re as _re
    blocks: list[tuple[str, list[str]]] = []
    cur_title, cur_body = None, []
    head_re = _re.compile(r"^\s*(#{1,4}\s+.+|第[一二三四五六七八九十百\d]+[章节讲部分].*|"
                          r"[一二三四五六七八九十]+[、.．].*|\d+[、.．)]\s*.+)\s*$")
    for ln in text.splitlines():
        if head_re.match(ln) and len(ln.strip()) <= 60:
            if cur_title:
                blocks.append((cur_title, cur_body))
            cur_title = _re.sub(r"^\s*#{1,4}\s*", "", ln).strip(" #　")
            cur_body = []
        else:
            cur_body.append(ln)
    if cur_title:
        blocks.append((cur_title, cur_body))

    if not blocks:                                   # 没有任何标题 → 按段落均分
        paras = [p.strip() for p in _re.split(r"\n\s*\n", text) if p.strip()]
        if not paras:
            return [{"title": "上传资料要点", "content": text[:4000]}]
        per = max(1, len(paras) // min(max_points, 8) + 1)
        for i in range(0, min(len(paras), max_points * per), per):
            chunk = paras[i:i + per]
            blocks.append((chunk[0][:24] or f"要点 {i // per + 1}", chunk))

    points = [{"title": t[:200], "content": "\n".join(b).strip() or "（原文无正文，请手动补充）"}
              for t, b in blocks[:max_points]]
    return points or [{"title": "上传资料要点", "content": text[:4000]}]
