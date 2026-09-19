"""准备一个"部分作答"的学生答题记录，用于截图验证前端展示。"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def req(path, data=None, token=None, method="GET", ctype="application/json"):
    r = urllib.request.Request(BASE + path,
                              data=json.dumps(data).encode() if data is not None else None,
                              method=method)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    if data is not None:
        r.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def login(email):
    return req("/api/auth/login", {"email": email, "password": "demo1234"}, method="POST")[1]["token"]


def main():
    t = login("teacher@demo.edu")
    s = login("student@demo.edu")

    long_analysis = ("本题考查卡尔曼增益的物理意义。正确选项 B 正确，因为增益 "
                     "K = P⁻Hᵀ(HP⁻Hᵀ+R)⁻¹ 在预测与观测之间做加权平衡：预测不确定就多信观测，"
                     "观测噪声大就多信预测。选项 A 把增益误当成观测噪声的方差；"
                     "选项 C 混淆了状态转移矩阵的作用；选项 D 则把过程噪声谱密度与增益混为一谈。")
    short_analysis = ("该说法错误。Q 增大意味着更不信任预测模型，预测协方差 P⁻ 变大，"
                      "卡尔曼增益随之增大（而非减小），从而更多采纳观测值。"
                      "判断这类题要抓住「谁变大→谁被更信任→增益往哪边移」这条因果链。")

    st, res = req("/api/courses/1/quizzes", {
        "title": "第 2 章 · UKF 随堂自测（演示：含未作答）",
        "questions": [
            {"qtype": "single", "stem": "卡尔曼增益 K 的物理含义是：",
             "options": ["A. 观测噪声的方差", "B. 在预测与观测之间做加权平衡的系数",
                         "C. 状态转移矩阵的逆", "D. 过程噪声的功率谱密度"],
             "answer": "B", "analysis": long_analysis, "difficulty": 2},
            {"qtype": "judge", "stem": "当过程噪声方差 Q 增大时，卡尔曼增益会相应减小。",
             "options": ["对", "错"], "answer": "错",
             "analysis": short_analysis, "difficulty": "简单"},
            {"qtype": "single", "stem": "UKF 相比 EKF 的主要优势是：",
             "options": ["A. 计算量更小", "B. 无需计算雅可比矩阵且可达二阶以上精度",
                         "C. 只适用于线性系统", "D. 不需要观测数据"],
             "answer": "B",
             "analysis": ("UKF 使用无迹变换，用 2n+1 个 Sigma 点直接传播非线性函数，"
                          "不需要求导得到雅可比矩阵，且均值与协方差的近似精度可达二阶以上，"
                          "因此在强非线性场景下优于只做一阶线性化的 EKF。选项 A 不对，"
                          "UKF 计算量与 EKF 同阶；选项 C、D 与事实相反。"),
             "difficulty": "中等"},
            {"qtype": "multi", "stem": "多传感器加权融合中，权重 Wᵢ 的性质包括：",
             "options": ["A. 方差越小的传感器权重越大", "B. 所有权重之和为 1",
                         "C. 融合后方差恒小于任一单传感器方差", "D. 权重与噪声大小无关"],
             "answer": "ABC",
             "analysis": ("按最小均方误差准则，Wᵢ = Pᵢ⁻¹ / ΣPⱼ⁻¹，因此信息量（方差倒数）越大权重越大，"
                          "A 正确；权重是归一化的，和为 1，B 正确；融合后方差 P = (ΣPᵢ⁻¹)⁻¹ "
                          "必然小于任何一个单独的 Pᵢ，这正是融合提升精度的理论依据，C 正确。"
                          "D 错误：权重完全由各传感器噪声方差决定。"),
             "difficulty": 4},
        ]}, token=t, method="POST")
    print("建卷:", st, res)
    qid = res["id"]

    st, qs = req(f"/api/quizzes/{qid}", token=s)
    questions = qs["questions"]
    print("题目数:", len(questions))

    # 故意只作答 2 题（第 1 题答对、第 3 题答错），第 2、4 题留空
    answers = {str(questions[0]["id"]): "B", str(questions[2]["id"]): "A"}
    st, out = req(f"/api/quizzes/{qid}/submit", {"answers": answers}, token=s, method="POST")
    print("交卷:", st)
    print("小结:", json.dumps(out["summary"], ensure_ascii=False))
    print("得分:", out["score"], "/", out["total"])
    return qid


if __name__ == "__main__":
    print("quiz_id =", main())
