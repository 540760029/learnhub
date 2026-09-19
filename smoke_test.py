"""
端到端冒烟测试：不依赖网络，覆盖权限隔离、答题评分、AI 出题额度三条主线。

运行：set PYTHONPATH=..\\_libs 然后 python smoke_test.py
"""
from __future__ import annotations

import os
import sys

# 用独立的测试库，避免污染演示数据
os.environ["LEARNHUB_DB_URL"] = "sqlite:///" + os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "smoke_test.db")
if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "smoke_test.db")):
    os.remove(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "smoke_test.db"))
os.environ["LEARNHUB_SEED"] = "1"
os.environ["LEARNHUB_SECRET"] = "test-secret"
os.environ["LEARNHUB_DAILY_AI_LIMIT"] = "3"

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
import llm  # noqa: E402

PASS, FAIL = 0, 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \u2713 {label}")
    else:
        FAIL += 1
        print(f"  \u2717 {label}  {extra}")


def hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def main() -> int:
    client = TestClient(app_module.app)

    print("\n[1] 认证与角色")
    r = client.post("/api/auth/login", json={"email": "teacher@demo.edu", "password": "demo1234"})
    check("教师登录成功", r.status_code == 200, r.text[:200])
    t_tok = r.json()["token"]

    r = client.post("/api/auth/login", json={"email": "student@demo.edu", "password": "demo1234"})
    check("学生登录成功", r.status_code == 200, r.text[:200])
    s_tok = r.json()["token"]

    r = client.post("/api/auth/login", json={"email": "teacher@demo.edu", "password": "wrong"})
    check("错误密码被拒绝", r.status_code == 400)

    r = client.get("/api/me")
    check("无 token 访问被拒", r.status_code == 401)

    print("\n[2] 多教师隔离")
    r = client.post("/api/auth/register", json={
        "email": "teacher2@demo.edu", "name": "王老师", "password": "demo1234",
        "role": "teacher", "school": "山西农业大学"})
    check("注册第二位教师", r.status_code == 200, r.text[:200])
    t2_tok = r.json()["token"]
    t2_id = r.json()["user"]["id"]

    r = client.post("/api/courses", json={"title": "王老师的课", "description": "测试隔离"},
                    headers=hdr(t2_tok))
    check("教师2 建课成功", r.status_code == 200, r.text[:200])
    c2_id = r.json()["id"]

    r = client.get("/api/courses", headers=hdr(t_tok))
    ids = [c["id"] for c in r.json()["teaching"]]
    check("教师1 看不到教师2 的课", c2_id not in ids, str(ids))

    r = client.post(f"/api/courses/{c2_id}/knowledge", json={"title": "偷偷发知识点"},
                    headers=hdr(t_tok))
    check("教师1 无法修改教师2 的课程", r.status_code == 403, r.text[:200])

    print("\n[3] 教师建课 / 知识点 / 作业")
    r = client.get("/api/courses", headers=hdr(t_tok))
    teaching = r.json()["teaching"]
    check("教师有演示课程", len(teaching) >= 1)
    cid = teaching[0]["id"]
    join_code = teaching[0]["join_code"]
    check("课程带邀请码", bool(join_code), str(teaching[0]))

    r = client.post(f"/api/courses/{cid}/knowledge",
                    json={"title": "测试知识点", "content": "这是内容"}, headers=hdr(t_tok))
    check("发布知识点", r.status_code == 200)
    kp_id = r.json()["id"]

    r = client.post(f"/api/courses/{cid}/knowledge", json={"title": "x"}, headers=hdr(s_tok))
    check("学生发布知识点被强制为私有", r.status_code == 200 and r.json()["scope"] == "private",
          r.text[:160])
    client.delete(f"/api/knowledge/{r.json()['id']}", headers=hdr(s_tok))

    r = client.post(f"/api/courses/{cid}/assignments",
                    json={"title": "测试作业", "content": "写 500 字", "full_score": 100},
                    headers=hdr(t_tok))
    check("发布作业", r.status_code == 200)
    aid = r.json()["id"]

    print("\n[4] 学生加入课程 / 交作业")
    r = client.post("/api/auth/register", json={
        "email": "stu2@demo.edu", "name": "李四", "password": "demo1234",
        "role": "student", "student_no": "2026S002"})
    s2_tok = r.json()["token"]
    s2_id = r.json()["user"]["id"]

    r = client.get(f"/api/courses/{cid}", headers=hdr(s2_tok))
    check("未选课学生访问课程被拒", r.status_code == 403)

    r = client.post("/api/courses/join", json={"join_code": "BADCODE"}, headers=hdr(s2_tok))
    check("错误邀请码被拒", r.status_code == 404)

    r = client.post("/api/courses/join", json={"join_code": join_code}, headers=hdr(s2_tok))
    check("凭邀请码加入课程", r.status_code == 200, r.text[:200])

    r = client.post("/api/courses/join", json={"join_code": join_code}, headers=hdr(s2_tok))
    check("重复加入不报错", r.status_code == 200)

    r = client.post(f"/api/assignments/{aid}/submit", json={"content": "我的答案"}, headers=hdr(s2_tok))
    check("学生提交作业", r.status_code == 200)
    sub_id = r.json()["id"]

    r = client.post(f"/api/submissions/{sub_id}/grade", json={"score": 88, "feedback": "不错"},
                    headers=hdr(t_tok))
    check("教师评分", r.status_code == 200)

    r = client.post(f"/api/submissions/{sub_id}/grade", json={"score": 999}, headers=hdr(t_tok))
    check("超满分评分被拒", r.status_code == 400)

    r = client.get(f"/api/assignments/{aid}", headers=hdr(s_tok))
    check("学生能看到自己的作业页", r.status_code == 200)

    print("\n[5] 答题与评分（答案不应提前下发）")
    r = client.get(f"/api/courses/{cid}", headers=hdr(s_tok))
    quizzes = r.json()["quiz_sets"]
    check("课程里有演示试题", len(quizzes) >= 1)
    qid = quizzes[0]["id"]

    r = client.get(f"/api/quizzes/{qid}", headers=hdr(s_tok))
    qs = r.json()
    check("学生取题成功", r.status_code == 200)
    check("学生取题时不下发答案", all("answer" not in q for q in qs["questions"]))
    check("学生取题时不下发解析", all("analysis" not in q for q in qs["questions"]))

    r = client.get(f"/api/quizzes/{qid}", headers=hdr(t_tok))
    check("教师取题带答案解析", all("answer" in q for q in r.json()["questions"]))

    answers = {str(q["id"]): ("B" if q["qtype"] == "single" else
                             ("对" if q["qtype"] == "judge" else
                              ("ABC" if q["qtype"] == "multi" else "x"))) for q in qs["questions"]}
    r = client.post(f"/api/quizzes/{qid}/submit", json={"answers": answers}, headers=hdr(s_tok))
    check("学生交卷成功", r.status_code == 200, r.text[:200])
    res = r.json()
    check("返回分数与解析", "score" in res and all("analysis" in x for x in res["review"]))
    print(f"      得分 {res['score']}/{res['total']}，正确率 {res['accuracy']}%")

    r = client.post(f"/api/quizzes/{qid}/submit", json={"answers": answers}, headers=hdr(t_tok))
    check("教师不能以学生身份交卷", r.status_code == 403)

    r = client.get("/api/my/attempts", headers=hdr(s_tok))
    check("学生能查到练习记录", r.json()["attempts"][0]["quiz_id"] == qid)

    aid2 = r.json()["attempts"][0]["id"]
    r = client.get(f"/api/attempts/{aid2}", headers=hdr(s2_tok))
    check("学生不能看别人的答题记录", r.status_code == 403)

    print("\n[6] 薄弱知识点统计")
    r = client.get(f"/api/courses/{cid}/weak-points", headers=hdr(s_tok))
    weak = r.json()["weak_points"]
    check("薄弱点有统计结果", len(weak) >= 1, str(weak[:2]))
    if weak:
        print(f"      首位薄弱点：{weak[0]['title']} 错误率 {weak[0]['wrong_rate']}%")

    print("\n[7] AI 出题：额度与 Key 优先级")
    calls = {"n": 0}

    def fake_generate(**kw):
        calls["n"] += 1
        return ([{"qtype": "single", "stem": f"AI 题 {calls['n']}",
                  "options": ["A. 对", "B. 错"], "answer": "A",
                  "analysis": "因为如此", "difficulty": 3}], kw.get("provider", "deepseek"))

    app_module.llm.generate_questions = fake_generate
    llm.generate_questions = fake_generate

    r = client.get(f"/api/courses/{cid}/ai-quota", headers=hdr(s_tok))
    q = r.json()
    check("初始额度为 3", q["limit"] == 3 and q["used"] == 0, str(q))
    check("未配置 Key 时走离线模式", q["will_fallback_mock"] is True, str(q))

    for i in range(3):
        r = client.post("/api/ai/generate", json={"course_id": cid, "count": 2}, headers=hdr(s_tok))
        check(f"第 {i + 1} 次 AI 出题成功", r.status_code == 200, r.text[:200])

    r = client.post("/api/ai/generate", json={"course_id": cid, "count": 2}, headers=hdr(s_tok))
    check("第 4 次被额度限制拦截", r.status_code == 429, r.text[:200])

    # 「失败不扣额度」用另一个还有额度的学生来验证
    app_module.llm.generate_questions = lambda **kw: (_ for _ in ()).throw(llm.LlmError("模拟服务故障"))
    r = client.post("/api/ai/generate", json={"course_id": cid, "count": 2}, headers=hdr(s2_tok))
    check("AI 失败时返回 400", r.status_code == 400, r.text[:200])
    app_module.llm.generate_questions = fake_generate

    r = client.get(f"/api/courses/{cid}/ai-quota", headers=hdr(s_tok))
    check("失败的出题不消耗额度（原学生仍为 3）", r.json()["used"] == 3, str(r.json()))
    r = client.get(f"/api/courses/{cid}/ai-quota", headers=hdr(s2_tok))
    check("失败的出题不消耗额度（另一学生为 0）", r.json()["used"] == 0, str(r.json()))

    print("\n[8] 学生配置自己的 Key → 解除限制")
    r = client.post("/api/me/ai-key", json={"provider": "deepseek", "api_key": "sk-test-1234567890"},
                    headers=hdr(s_tok))
    check("保存自己的 Key", r.status_code == 200, r.text[:200])
    check("额度变为无限", r.json()["unlimited"] is True, str(r.json()))

    r = client.get("/api/me", headers=hdr(s_tok))
    check("Key 只回显后 4 位", r.json()["ai"]["key_masked"] == "••••••••7890",
          r.json()["ai"]["key_masked"])
    check("响应中不含明文 Key", "sk-test-1234567890" not in r.text)

    for i in range(4):
        r = client.post("/api/ai/generate", json={"course_id": cid, "count": 1}, headers=hdr(s_tok))
    check("有自己 Key 时可超额出题", r.status_code == 200, r.text[:200])

    r = client.get(f"/api/courses/{cid}/ai-quota", headers=hdr(s_tok))
    check("自带 Key 不计入每日额度", r.json()["used"] == 3, str(r.json()))

    r = client.post("/api/ai/generate", json={"course_id": cid, "count": 1}, headers=hdr(s2_tok))
    check("另一个学生仍受每日限制", r.status_code == 200)

    print("\n[9] 教师看板")
    r = client.get(f"/api/courses/{cid}/analytics", headers=hdr(t_tok))
    check("教师可看学情分析", r.status_code == 200, r.text[:200])
    an = r.json()
    check("含学生明细", len(an["students"]) >= 2, str(len(an["students"])))
    check("含薄弱知识点排行", len(an["weak_knowledge_points"]) >= 1)
    check("含答题趋势", len(an["trend"]) >= 1)

    r = client.get(f"/api/courses/{cid}/analytics", headers=hdr(s_tok))
    check("学生无权看学情分析", r.status_code == 403)

    print("\n[10] 静态页面")
    r = client.get("/")
    check("首页可访问", r.status_code == 200 and "LearnHub" in r.text)
    r = client.get("/static/app.js")
    check("前端脚本可访问", r.status_code == 200)

    # ================================================= 需求 1~7 回归
    print("\n[11] 需求1：管理员用平台 Key 无限次，教师/学生每日 3 次")
    r = client.post("/api/auth/login", json={"email": "admin@demo.edu", "password": "demo1234"})
    check("管理员登录成功", r.status_code == 200, r.text[:200])
    a_tok = r.json()["token"]

    r = client.get("/api/me", headers=hdr(a_tok))
    check("管理员标记正确", r.json()["is_admin"] is True, str(r.json().get("is_admin")))
    check("管理员额度无限", r.json()["quota"]["unlimited"] is True, str(r.json()["quota"]))

    for _ in range(5):     # 管理员连出 5 套（远超 3 次）不应被拦
        r = client.post("/api/ai/generate", json={"course_id": cid, "count": 1}, headers=hdr(a_tok))
    check("管理员连续出题 5 次不被限制", r.status_code == 200, r.text[:200])
    r = client.get(f"/api/courses/{cid}/ai-quota", headers=hdr(a_tok))
    check("管理员额度来源标记为 admin", r.json()["reason"] == "admin", str(r.json()))

    print("\n[12] 需求2：教师卷全班可见，学生生成的题仅本人可见")
    r = client.post(f"/api/courses/{cid}/quizzes", headers=hdr(t_tok), json={
        "title": "教师发布卷", "questions": [
            {"qtype": "single", "stem": "1+1=?", "options": ["A. 1", "B. 2"],
             "answer": "B", "analysis": "基础算术", "difficulty": 1}]})
    check("教师建卷成功", r.status_code == 200)
    teacher_qid = r.json()["id"]

    for _ in range(2):     # 学生 AI 出两套
        client.post("/api/ai/generate", json={"course_id": cid, "count": 1}, headers=hdr(s2_tok))
    r = client.get(f"/api/courses/{cid}", headers=hdr(s2_tok))
    s2_ai = [q for q in r.json()["quiz_sets"] if q["source"] == "ai"]
    stu_private_qid = s2_ai[0]["id"] if s2_ai else None
    check("学生能生成自己的题", stu_private_qid is not None)
    check("学生生成的题 scope=private", bool(s2_ai) and s2_ai[0]["scope"] == "private",
          str(s2_ai[0] if s2_ai else None))

    r = client.get(f"/api/courses/{cid}", headers=hdr(s_tok))
    others = [q["id"] for q in r.json()["quiz_sets"]]
    check("另一学生看不到别人生成的题", stu_private_qid not in others)
    check("另一学生能看到教师发布的卷", teacher_qid in others)

    r = client.get(f"/api/quizzes/{stu_private_qid}", headers=hdr(s_tok))
    check("另一学生直接访问别人的私有卷被拒", r.status_code == 403, r.text[:200])
    r = client.get(f"/api/quizzes/{stu_private_qid}", headers=hdr(s2_tok))
    check("本人可以访问自己的私有卷", r.status_code == 200)
    r = client.get(f"/api/quizzes/{stu_private_qid}", headers=hdr(t_tok))
    check("教师可以看到学生生成的题（便于答疑）", r.status_code == 200)

    r = client.patch(f"/api/quizzes/{teacher_qid}/scope", headers=hdr(t_tok),
                     json={"scope": "teacher"})
    check("教师可把卷设为仅教师可见", r.status_code == 200 and r.json()["scope"] == "teacher")
    r = client.get(f"/api/courses/{cid}", headers=hdr(s_tok))
    check("仅教师可见的卷学生看不到", teacher_qid not in [q["id"] for q in r.json()["quiz_sets"]])
    r = client.patch(f"/api/quizzes/{teacher_qid}/scope", headers=hdr(t_tok),
                     json={"scope": "course"})
    check("改回全班可见", r.status_code == 200)

    print("\n[13] 需求3：教师试题详情 —— 答案解析 + 全班作答情况")
    q0 = client.get(f"/api/quizzes/{teacher_qid}", headers=hdr(t_tok)).json()["questions"][0]
    client.post(f"/api/quizzes/{teacher_qid}/submit",
                json={"answers": {str(q0["id"]): "B"}}, headers=hdr(s2_tok))
    client.post(f"/api/quizzes/{teacher_qid}/submit",
                json={"answers": {str(q0["id"]): "A"}}, headers=hdr(s_tok))

    r = client.get(f"/api/quizzes/{teacher_qid}/overview", headers=hdr(t_tok))
    check("教师可打开试题详情页", r.status_code == 200, r.text[:200])
    ov = r.json()
    check("详情含答案解析", all("analysis" in q for q in ov["quiz"]["questions"]))
    check("详情含作答统计", ov["stats"]["attempt_count"] == 2, str(ov["stats"]))
    check("详情含每题正确率", ov["questions_stat"][0]["accuracy"] is not None,
          str(ov["questions_stat"][0]))
    check("详情含学生作答明细", len(ov["attempts"]) == 2)
    r = client.get(f"/api/quizzes/{teacher_qid}/overview", headers=hdr(s_tok))
    check("学生无权看教师视角的试题详情", r.status_code == 403)

    print("\n[14] 需求4：学生可重复作答")
    before = len(client.get("/api/my/attempts", headers=hdr(s_tok)).json()["attempts"])
    client.post(f"/api/quizzes/{teacher_qid}/submit",
                json={"answers": {str(q0["id"]): "B"}}, headers=hdr(s_tok))
    after = len(client.get("/api/my/attempts", headers=hdr(s_tok)).json()["attempts"])
    check("同一套题可以重复作答", after == before + 1, f"{before} -> {after}")

    print("\n[15] 需求5：资料上传 → AI 整理知识点 → 选择是否公开")
    doc = ("第一章 卡尔曼滤波\n卡尔曼滤波是最小二乘递推估计方法。\n\n"
           "第二章 扩展卡尔曼滤波\nEKF 用雅可比矩阵做一阶线性化。\n\n"
           "第三章 无迹卡尔曼滤波\nUKF 用 Sigma 点做无迹变换，精度可达二阶以上。\n")
    r = client.post(f"/api/courses/{cid}/knowledge/extract", headers=hdr(t_tok),
                    files={"file": ("ch1.md", doc.encode("utf-8"), "text/markdown")})
    check("上传资料解析成功", r.status_code == 200, r.text[:200])
    ex = r.json()
    check("AI 整理出多个知识点", len(ex["points"]) >= 3, str(len(ex["points"])))

    r = client.post(f"/api/courses/{cid}/knowledge/commit", headers=hdr(t_tok), json={
        "source_file": "ch1.md", "scope": "teacher", "points": ex["points"]})
    check("可选择「仅教师可见」入库", r.status_code == 200, r.text[:200])
    check("入库数量正确", r.json()["created"] == len(ex["points"]))

    stu_titles = [k["title"] for k in
                  client.get(f"/api/courses/{cid}", headers=hdr(s_tok)).json()["knowledge_points"]]
    check("学生看不到「仅教师可见」的知识点",
          not any(p["title"] in stu_titles for p in ex["points"]), str(stu_titles[:4]))
    tea_titles = [k["title"] for k in
                  client.get(f"/api/courses/{cid}", headers=hdr(t_tok)).json()["knowledge_points"]]
    check("教师能看到自己入库的知识点", all(p["title"] in tea_titles for p in ex["points"]))

    r = client.post(f"/api/courses/{cid}/knowledge/commit", headers=hdr(t_tok), json={
        "source_file": "ch1.md", "scope": "course", "points": ex["points"][:1]})
    check("可选择「全班可见」入库", r.status_code == 200)
    stu_titles = [k["title"] for k in
                  client.get(f"/api/courses/{cid}", headers=hdr(s_tok)).json()["knowledge_points"]]
    check("全班可见的知识点学生能看到", ex["points"][0]["title"] in stu_titles)

    r = client.post(f"/api/courses/{cid}/knowledge/extract", headers=hdr(s_tok),
                    files={"file": ("x.md", b"hello world hello world", "text/markdown")})
    check("学生不能上传资料", r.status_code == 403, r.text[:200])
    r = client.post(f"/api/courses/{cid}/knowledge/extract", headers=hdr(t_tok),
                    files={"file": ("bad.exe", b"MZ\x90\x00binary", "application/octet-stream")})
    check("不支持的格式被拒", r.status_code == 400, r.text[:200])

    print("\n[16] 需求6：学生添加自己的知识点（仅本人可见）")
    r = client.post(f"/api/courses/{cid}/knowledge", headers=hdr(s_tok),
                    json={"title": "我的笔记：UKF 参数理解", "content": "λ = α²(n+κ) − n"})
    check("学生可添加自己的知识点", r.status_code == 200, r.text[:200])
    my_kp_id = r.json()["id"]
    check("学生知识点被强制设为 private", r.json()["scope"] == "private", str(r.json()))

    r = client.post(f"/api/courses/{cid}/knowledge", headers=hdr(s_tok),
                    json={"title": "我想公开发布", "content": "x", "scope": "course"})
    check("学生无法把知识点设为公开", r.json()["scope"] == "private")

    r = client.get(f"/api/courses/{cid}", headers=hdr(s2_tok))
    check("其他学生看不到我的笔记", my_kp_id not in [k["id"] for k in r.json()["knowledge_points"]])
    r = client.get(f"/api/courses/{cid}", headers=hdr(t_tok))
    check("教师能看到学生的笔记", my_kp_id in [k["id"] for k in r.json()["knowledge_points"]])

    r = client.delete(f"/api/knowledge/{my_kp_id}", headers=hdr(s2_tok))
    check("别人不能删我的笔记", r.status_code == 403)
    r = client.patch(f"/api/knowledge/{my_kp_id}", headers=hdr(s2_tok),
                     json={"title": "改一下", "content": "y"})
    check("别人不能改我的笔记", r.status_code == 403)
    r = client.delete(f"/api/knowledge/{my_kp_id}", headers=hdr(s_tok))
    check("本人可以删自己的笔记", r.status_code == 200)

    print("\n[17] 需求7：管理员管理全局数据")
    r = client.get("/api/admin/stats", headers=hdr(a_tok))
    check("管理员可看全局统计", r.status_code == 200, r.text[:200])
    st = r.json()
    check("统计含用户/课程/题目", st["users"] >= 4 and st["courses"] >= 1, str(st)[:170])
    check("统计含管理员数量", st["admins"] >= 1)
    r = client.get("/api/admin/stats", headers=hdr(t_tok))
    check("普通教师不能看全局统计", r.status_code == 403)

    r = client.get("/api/admin/users", headers=hdr(a_tok))
    check("管理员可列出全部用户", r.status_code == 200 and len(r.json()["users"]) >= 4)
    r = client.get("/api/admin/users?q=李小凡", headers=hdr(a_tok))
    check("管理员可按关键字搜索用户", len(r.json()["users"]) == 1, r.text[:160])
    r = client.get("/api/admin/courses", headers=hdr(a_tok))
    check("管理员可列出全部课程", r.status_code == 200 and len(r.json()["courses"]) >= 2)

    r = client.patch(f"/api/admin/users/{t2_id}", json={"is_admin": True}, headers=hdr(a_tok))
    check("管理员可提升他人为管理员", r.status_code == 200, r.text[:160])
    client.patch(f"/api/admin/users/{t2_id}", json={"is_admin": False}, headers=hdr(a_tok))

    my_admin_id = client.get("/api/me", headers=hdr(a_tok)).json()["id"]
    r = client.patch(f"/api/admin/users/{my_admin_id}", json={"is_admin": False}, headers=hdr(a_tok))
    check("管理员不能取消自己的管理员权限", r.status_code == 400, r.text[:160])
    r = client.delete(f"/api/admin/users/{my_admin_id}", headers=hdr(a_tok))
    check("管理员不能删除自己", r.status_code == 400)

    r = client.patch(f"/api/admin/users/{s2_id}", json={"is_active": False}, headers=hdr(a_tok))
    check("管理员可停用账号", r.status_code == 200, r.text[:160])
    r = client.post("/api/auth/login", json={"email": "stu2@demo.edu", "password": "demo1234"})
    check("被停用的账号无法登录", r.status_code == 403, r.text[:160])
    client.patch(f"/api/admin/users/{s2_id}", json={"is_active": True}, headers=hdr(a_tok))

    r = client.patch(f"/api/admin/users/{s2_id}", json={"role": "teacher"}, headers=hdr(a_tok))
    check("管理员可修改用户角色", r.status_code == 200, r.text[:160])

    r = client.delete(f"/api/admin/courses/{c2_id}", headers=hdr(a_tok))
    check("管理员可删除任意课程", r.status_code == 200, r.text[:160])
    r = client.get(f"/api/courses/{c2_id}", headers=hdr(t2_tok))
    check("课程确实被删除", r.status_code == 404)

    print("\n[18] 需求8：管理员上传平台默认 API Key（对所有用户生效）")
    # 用一个没有配置自己 Key 的学生来验证「平台默认 Key」链路
    r = client.post("/api/auth/register", json={
        "email": "stu3@demo.edu", "name": "王五", "password": "demo1234",
        "role": "student", "student_no": "2026S003"})
    check("注册一个没有自己 Key 的学生", r.status_code == 200, r.text[:160])
    s3_tok = r.json()["token"]
    client.post("/api/courses/join", json={"join_code": join_code}, headers=hdr(s3_tok))

    r = client.get("/api/admin/platform-ai", headers=hdr(a_tok))
    check("管理员可读取平台 AI 配置", r.status_code == 200, r.text[:200])
    check("初始未配置 Key", r.json()["configured"] is False, str(r.json()))
    check("默认每日额度为 3", r.json()["daily_limit"] == 3)

    r = client.get("/api/admin/platform-ai", headers=hdr(t_tok))
    check("普通教师不能读取平台 AI 配置", r.status_code == 403)

    r = client.post("/api/admin/platform-ai", headers=hdr(a_tok), json={
        "provider": "deepseek", "api_key": "sk-platform-secret-ABCD",
        "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
        "enabled": True, "daily_limit": 5})
    check("管理员上传平台 Key 成功", r.status_code == 200, r.text[:200])
    check("响应显示已配置", r.json()["configured"] is True)
    check("Key 只回显后 4 位", r.json()["key_masked"] == "••••••••ABCD", str(r.json()))
    check("响应不含明文 Key", "sk-platform-secret-ABCD" not in r.text)
    check("每日额度已改为 5", r.json()["daily_limit"] == 5)

    r = client.get("/api/ai/providers")
    check("所有用户能看到平台 Key 已配置", r.json()["platform_key_configured"] is True)
    check("公开接口不含明文 Key", "sk-platform-secret" not in r.text)
    check("公开接口额度反映管理员设置", r.json()["daily_limit"] == 5, str(r.json()))

    r = client.get(f"/api/courses/{cid}/ai-quota", headers=hdr(s3_tok))
    q = r.json()
    check("学生额度跟随管理员设置（5）", q["limit"] == 5, str(q))
    check("学生 Key 来源为 platform", q["key_source"] == "platform", str(q))
    check("学生不再走离线模拟", q["will_fallback_mock"] is False, str(q))

    r = client.get(f"/api/courses/{cid}/ai-quota", headers=hdr(a_tok))
    check("管理员用平台 Key 无限次",
          r.json()["unlimited"] is True and r.json()["reason"] == "admin", str(r.json()))
    r = client.get(f"/api/courses/{cid}/ai-quota", headers=hdr(t_tok))
    check("教师用平台 Key 受限（5 套）", r.json()["limit"] == 5, str(r.json()))

    # 出题时是否真的带上了管理员上传的 Key
    seen = {}

    def spy_generate(**kw):
        seen.update(kw)
        return ([{"qtype": "single", "stem": "平台题", "options": ["A. 1", "B. 2"],
                  "answer": "A", "analysis": "解析", "difficulty": 2}], kw.get("provider"))

    app_module.llm.generate_questions = spy_generate
    r = client.post("/api/ai/generate", json={"course_id": cid, "count": 1}, headers=hdr(s3_tok))
    check("学生能成功出题", r.status_code == 200, r.text[:200])
    check("出题确实使用了管理员上传的平台 Key",
          seen.get("api_key") == "sk-platform-secret-ABCD", str(seen.get("api_key")))
    check("出题使用管理员配置的模型", seen.get("model") == "deepseek-chat", str(seen.get("model")))
    check("出题使用管理员配置的 Base URL",
          seen.get("base_url") == "https://api.deepseek.com/v1", str(seen.get("base_url")))
    app_module.llm.generate_questions = fake_generate

    # 额度按管理员设置的 5 套生效：上面已用 1 套，再出 4 套刚好用完
    for _ in range(4):
        r = client.post("/api/ai/generate", json={"course_id": cid, "count": 1}, headers=hdr(s3_tok))
    q = client.get(f"/api/courses/{cid}/ai-quota", headers=hdr(s3_tok)).json()
    check("学生用满 5 套（额度跟随管理员设置）",
          r.status_code == 200 and q["used"] == 5 and q["remaining"] == 0, str(q))
    r = client.post("/api/ai/generate", json={"course_id": cid, "count": 1}, headers=hdr(s3_tok))
    check("学生超过 5 套后被拦", r.status_code == 429, r.text[:200])

    # 管理员不受该限制
    for _ in range(3):
        r = client.post("/api/ai/generate", json={"course_id": cid, "count": 1}, headers=hdr(a_tok))
    check("管理员同一天继续出题不被拦", r.status_code == 200, r.text[:200])

    r = client.post("/api/admin/platform-ai", headers=hdr(a_tok), json={"enabled": False})
    check("管理员可停用平台 AI", r.status_code == 200 and r.json()["enabled"] is False)
    r = client.get("/api/ai/providers")
    check("停用后对外显示未提供", r.json()["platform_key_configured"] is False, str(r.json()))
    r = client.get(f"/api/courses/{cid}/ai-quota", headers=hdr(t_tok))
    check("停用后回到离线模拟", r.json()["will_fallback_mock"] is True, str(r.json()))
    client.post("/api/admin/platform-ai", headers=hdr(a_tok), json={"enabled": True})

    r = client.post("/api/admin/platform-ai", headers=hdr(a_tok), json={"api_key": ""})
    check("留空 api_key 不会清掉已配置的 Key", r.json()["configured"] is True, str(r.json()))

    r = client.post("/api/admin/platform-ai", headers=hdr(a_tok),
                    json={"api_key": "", "clear_key": True})
    check("管理员可清除平台 Key",
          r.status_code == 200 and r.json()["configured"] is False, str(r.json()))
    r = client.get(f"/api/courses/{cid}/ai-quota", headers=hdr(t_tok))
    check("清除后回到离线模拟", r.json()["will_fallback_mock"] is True, str(r.json()))

    r = client.post("/api/admin/platform-ai", headers=hdr(a_tok), json={"provider": "notexist"})
    check("非法服务商被拒", r.status_code == 422, r.text[:160])

    print(f"\n{'=' * 52}\n通过 {PASS} 项，失败 {FAIL} 项\n{'=' * 52}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    with TestClient(app_module.app):
        pass  # 触发 startup（建表 + 种子）
    sys.exit(main())
