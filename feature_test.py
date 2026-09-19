"""验证需求 1~4：答题小结、正确选项标记、解析必填、难度不为 undefined"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["LEARNHUB_DB_URL"] = "sqlite:///" + os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "feat_test.db")
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "feat_test.db")
if os.path.exists(DB):
    os.remove(DB)
os.environ["LEARNHUB_SEED"] = "1"
os.environ["LEARNHUB_SECRET"] = "feat-secret"

from fastapi.testclient import TestClient  # noqa: E402

import app as A  # noqa: E402
import llm  # noqa: E402

PASS = FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \u2713 {label}")
    else:
        FAIL += 1
        print(f"  \u2717 {label}   {extra}")


def hdr(t):
    return {"Authorization": "Bearer " + t}


def main():
    c = TestClient(A.app)

    def login(e):
        return c.post("/api/auth/login", json={"email": e, "password": "demo1234"}).json()["token"]

    t_tok, s_tok = login("teacher@demo.edu"), login("student@demo.edu")

    print("\n[A] 答案规范化（保证正确选项一定标绿）")
    check("单选 'B. 观测噪声' → B", llm.canonical_answer("B. 观测噪声", None, "single") == "B")
    check("单选小写 'b' → B", llm.canonical_answer("b", None, "single") == "B")
    check("多选乱序 'c,a' → AC", llm.canonical_answer("c,a", None, "multi") == "AC")
    check("多选 'A、B、D' → ABD", llm.canonical_answer("A、B、D", None, "multi") == "ABD")
    check("判断 '正确' → 对", llm.canonical_answer("正确", ["对", "错"], "judge") == "对")
    check("判断 'false' → 错", llm.canonical_answer("false", ["对", "错"], "judge") == "错")
    check("判断 'T' → 对", llm.canonical_answer("T", ["对", "错"], "judge") == "对")

    print("\n[B] 难度规范化（前端不再出现 undefined）")
    for raw, want in [(3, 3), ("3", 3), ("中等", 3), ("简单", 1), ("较难", 4),
                      ("困难", 4), ("easy", 2), ("hard", 4), (None, 3),
                      ("难度：4", 4), (9, 5), (0, 1), ("", 3), ("abc", 3)]:
        got = llm.normalize_difficulty(raw)
        check(f"normalize_difficulty({raw!r}) == {want}", got == want, f"得到 {got}")
    check("不会返回 None", llm.normalize_difficulty(None) is not None)

    print("\n[C] 解析合格性判定")
    check("太短的解析不合格", llm.analysis_ok("略") is False)
    check("套话不合格", llm.analysis_ok("见教材") is False)
    check("空解析不合格", llm.analysis_ok("") is False)
    long_ok = ("本题考查卡尔曼增益的物理意义。正确选项 B 正确，因为增益 K = P⁻Hᵀ(HP⁻Hᵀ+R)⁻¹ "
               "在预测与观测之间做加权平衡；选项 A 把增益误解为观测噪声的方差，"
               "选项 C 混淆了状态转移矩阵，选项 D 则把过程噪声谱密度与增益混为一谈。")
    check("详细解析合格", llm.analysis_ok(long_ok) is True)

    print("\n[D] AI 出题：解析缺失时会补写/剔除")
    calls = []

    def fake_llm_ok(**kw):
        calls.append(kw)
        return ([{"qtype": "single", "stem": "题A", "options": ["A. 甲", "B. 乙"],
                  "answer": "B", "analysis": long_ok, "difficulty": "中等"},
                 {"qtype": "judge", "stem": "题B", "options": ["对", "错"],
                  "answer": "正确", "analysis": long_ok, "difficulty": "3"}], "deepseek")

    # 直接测 _normalize + analysis 判定链路
    norm = llm._normalize([
        {"qtype": "single", "stem": "s1", "options": ["A. x", "B. y"], "answer": "b.",
         "analysis": long_ok, "difficulty": "中等"},
        {"qtype": "judge", "stem": "s2", "options": ["对", "错"], "answer": "正确",
         "analysis": "略", "difficulty": None},
    ])
    check("答案被规范化（小写/带点）", norm[0]["answer"] == "B", str(norm[0]["answer"]))
    check("难度被规范化成整数 3", norm[0]["difficulty"] == 3, str(norm[0]["difficulty"]))
    check("判断题答案规范为 对", norm[1]["answer"] == "对", str(norm[1]["answer"]))
    check("缺失难度回落为 3", norm[1]["difficulty"] == 3, str(norm[1]["difficulty"]))
    check("短解析被识别为不合格", llm.analysis_ok(norm[1]["analysis"]) is False)

    print("\n[E] 手工组卷：缺解析必须被拒")
    r = c.post("/api/courses/1/quizzes", headers=hdr(t_tok), json={
        "title": "缺解析的卷", "questions": [
            {"qtype": "single", "stem": "1+1=?", "options": ["A. 1", "B. 2"],
             "answer": "B", "analysis": "", "difficulty": 2}]})
    check("缺解析的题被拒（400）", r.status_code == 400, r.text[:160])

    r = c.post("/api/courses/1/quizzes", headers=hdr(t_tok), json={
        "title": "合格卷", "questions": [
            {"qtype": "single", "stem": "卡尔曼增益的作用是？",
             "options": ["A. 观测噪声方差", "B. 平衡预测与观测的权重",
                         "C. 状态转移矩阵", "D. 过程噪声谱密度"],
             "answer": "b.", "analysis": long_ok, "difficulty": "中等"},
            {"qtype": "judge", "stem": "Q 增大会使卡尔曼增益减小。",
             "options": ["对", "错"], "answer": "错误",
             "analysis": ("该说法错误。Q 增大意味着更不信任预测模型，预测协方差 P⁻ 变大，"
                          "卡尔曼增益 K 随之增大，从而更多采纳观测值，因此增益是增大而非减小。"),
             "difficulty": "简单"}]})
    check("合格卷创建成功", r.status_code == 200, r.text[:200])
    qid = r.json()["id"]

    r = c.get(f"/api/quizzes/{qid}", headers=hdr(t_tok))
    qs = r.json()["questions"]
    check("答案已规范化为 B", qs[0]["answer"] == "B", str(qs[0]["answer"]))
    check("判断题答案规范化为 错", qs[1]["answer"] == "错", str(qs[1]["answer"]))
    check("难度是整数 3 / 1",
          qs[0]["difficulty"] == 3 and qs[1]["difficulty"] == 1,
          f"{qs[0]['difficulty']} / {qs[1]['difficulty']}")

    print("\n[F] 学生部分作答后交卷 → 小结能指出未作答的题")
    q1, q2 = qs[0]["id"], qs[1]["id"]
    r = c.post(f"/api/quizzes/{qid}/submit", headers=hdr(s_tok),
               json={"answers": {str(q1): "B"}})     # 只答第 1 题（且答对）
    check("交卷成功", r.status_code == 200, r.text[:200])
    res = r.json()
    sm = res["summary"]
    check("小结：共 2 题", sm["total"] == 2, str(sm))
    check("小结：已作答 1 题", sm["answered_count"] == 1, str(sm))
    check("小结：未作答 1 题", sm["unanswered_count"] == 1, str(sm))
    check("小结：未作答列表给出题号", sm["unanswered"] == [{"id": q2, "no": 2}], str(sm["unanswered"]))
    check("小结：答错列表为空（未作答不算答错）", sm["wrong"] == [], str(sm["wrong"]))
    check("小结：答对 1 道", sm.get("correct_count") == 1, str(sm))
    check("小结：答错 0 道", sm.get("wrong_count") == 0, str(sm))
    check("小结：作答列表含第 1 题", sm["answered_ids"] == [q1], str(sm["answered_ids"]))

    print("\n[G] 逐题 review：正确选项可被前端标绿")
    rev = {x["id"]: x for x in res["review"]}
    check("第 1 题标记为正确", rev[q1]["correct"] is True)
    check("第 1 题 answered=True", rev[q1]["answered"] is True)
    check("第 1 题正确答案为 B", rev[q1]["answer"] == "B", str(rev[q1]["answer"]))
    check("第 2 题 answered=False（未作答）", rev[q2]["answered"] is False)
    check("第 2 题标记为错误", rev[q2]["correct"] is False)
    check("判断题正确答案为 错（前端据此标绿）", rev[q2]["answer"] == "错", str(rev[q2]["answer"]))
    check("每题都带解析", all(x["analysis"].strip() for x in res["review"]))
    check("解析不是占位文案",
          all("暂无解析" not in x["analysis"] for x in res["review"]))
    check("每题难度都是整数",
          all(isinstance(x["difficulty"], int) for x in res["review"]),
          str([x["difficulty"] for x in res["review"]]))

    print("\n[H] 历史答题记录页同样有小结")
    r = c.get("/api/my/attempts", headers=hdr(s_tok))
    aid = r.json()["attempts"][0]["id"]
    r = c.get(f"/api/attempts/{aid}", headers=hdr(s_tok))
    a = r.json()
    check("记录页含 summary", "summary" in a and a["summary"]["unanswered_count"] == 1, str(a.get("summary")))
    check("记录页含 quiz_id / course_id（用于“再练一遍”）",
          a.get("quiz_id") == qid and a.get("course_id") == 1, str({k: a.get(k) for k in ("quiz_id", "course_id")}))
    check("记录页 review 带 answered 字段", all("answered" in x for x in a["review"]))

    print("\n[I] 全部作答时小结正确")
    r = c.post(f"/api/quizzes/{qid}/submit", headers=hdr(s_tok),
               json={"answers": {str(q1): "B", str(q2): "错"}})
    sm = r.json()["summary"]
    check("全部作答：未作答 0", sm["unanswered_count"] == 0, str(sm))
    check("全部作答：答错 0", sm["wrong"] == [], str(sm["wrong"]))
    check("全对得满分", r.json()["score"] == 2, str(r.json()["score"]))

    print("\n[J] 判断题答错时，正确项仍能被标绿")
    r = c.post(f"/api/quizzes/{qid}/submit", headers=hdr(s_tok),
               json={"answers": {str(q1): "A", str(q2): "对"}})
    rev = {x["id"]: x for x in r.json()["review"]}
    check("第 2 题答错", rev[q2]["correct"] is False)
    check("但正确答案仍下发为 错（前端标绿）", rev[q2]["answer"] == "错", str(rev[q2]["answer"]))
    check("小结把第 2 题列为答错", {"id": q2, "no": 2} in r.json()["summary"]["wrong"],
          str(r.json()["summary"]["wrong"]))
    check("此时未作答列表为空", r.json()["summary"]["unanswered"] == [],
          str(r.json()["summary"]["unanswered"]))
    check("答错 2 道", r.json()["summary"]["wrong_count"] == 2, str(r.json()["summary"]))

    print("\n[K] 离线 Mock 题的解析也足够详细")
    mock = llm.MockProvider().generate(["卡尔曼滤波"], ["卡尔曼滤波是一种递推最小方差估计方法。"],
                                       3, "mixed", "medium")
    check("离线题数量正确", len(mock) == 3)
    check("离线题解析全部合格", all(llm.analysis_ok(q["analysis"]) for q in mock),
          str([len(q["analysis"]) for q in mock]))
    check("离线题难度为整数", all(isinstance(q["difficulty"], int) for q in mock))
    check("离线题答案可被规范化",
          all(llm.canonical_answer(q["answer"], q["options"], q["qtype"]) for q in mock))

    print(f"\n{'=' * 52}\n通过 {PASS} 项，失败 {FAIL} 项\n{'=' * 52}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    with TestClient(A.app):
        pass
    code = main()
    try:
        import db as _db
        _db.engine.dispose()
        if os.path.exists(DB):
            os.remove(DB)
    except OSError:
        pass
    sys.exit(code)
