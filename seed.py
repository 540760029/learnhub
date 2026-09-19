"""
种子数据：首次启动自动写入演示账号与课程。

演示账号（密码均为 demo1234）：
    管理员  admin@demo.edu          平台管理员（平台默认 API Key 无限次）
    教师    teacher@demo.edu        张明
    学生    student@demo.edu        李小凡
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from db import (Announcement, Assignment, Attempt, Course, KnowledgePoint, Question,
                QuizSet, SessionLocal, User, WeakStat, now)
from security import hash_password

DEMO_PASSWORD = "demo1234"

KP_SEED = [
    ("卡尔曼滤波的基本思想",
     "卡尔曼滤波是一种递推的最小方差估计方法。它把系统建模为状态方程与观测方程，"
     "在每个时刻交替执行「预测」与「更新」两步：\n\n"
     "- 预测：用状态转移矩阵 F 外推状态与协方差\n"
     "- 更新：用卡尔曼增益 K 融合观测，修正估计\n\n"
     "核心公式：K = P⁻Hᵀ(HP⁻Hᵀ+R)⁻¹。卡尔曼增益本质上是在「相信预测」和"
     "「相信观测」之间做加权平衡：预测协方差大就多信观测，观测噪声大就多信预测。\n\n"
     "适用前提：系统线性、噪声为互不相关的高斯白噪声。"),
    ("扩展卡尔曼滤波（EKF）",
     "当状态方程或观测方程非线性时，EKF 通过一阶泰勒展开在工作点附近做线性化：\n\n"
     "- 用雅可比矩阵 F_k = ∂f/∂x 代替线性系统的状态转移矩阵\n"
     "- 用雅可比矩阵 H_k = ∂h/∂x 代替观测矩阵\n\n"
     "EKF 的优点是可以处理非线性系统且计算量小；缺点是：\n"
     "1. 一阶线性化在强非线性下误差大，甚至发散；\n"
     "2. 雅可比矩阵需要解析求导，模型复杂时推导困难；\n"
     "3. 线性化点偏离真值较远时估计精度明显下降。"),
    ("无迹卡尔曼滤波（UKF）与无迹变换",
     "UKF 不使用线性化，而是用「无迹变换（UT）」处理非线性：\n\n"
     "1. 按确定性规则在均值周围选取 2n+1 个 Sigma 点；\n"
     "2. 将 Sigma 点直接代入非线性函数传播；\n"
     "3. 用传播后点集的加权均值与协方差近似后验分布。\n\n"
     "关键参数：\n"
     "- λ = α²(n+κ) − n，α 决定 Sigma 点的散布范围（常取 1e-3 ~ 1）\n"
     "- 权系数 W₀ᵐ、W₀ᶜ 分别用于均值与协方差的加权\n\n"
     "UKF 能达到二阶以上精度，且无需计算雅可比矩阵，在强非线性场景下优于 EKF。"),
    ("多传感器加权融合准则",
     "对 L 个传感器的局部估计 x̂ᵢ（方差 Pᵢ），在最小均方误差准则下，"
     "线性加权融合估计为：\n\n"
     "x̂ = Σ Wᵢ x̂ᵢ， 其中权重 Wᵢ = Pᵢ⁻¹ / Σ Pⱼ⁻¹\n\n"
     "结论：\n"
     "1. 方差越小的传感器权重越大，符合直觉；\n"
     "2. 融合后方差 P = (Σ Pᵢ⁻¹)⁻¹，恒小于任一局部方差——"
     "这正是「融合能提高精度」的数学依据；\n"
     "3. 若各传感器噪声相关，最优权重需用互协方差矩阵修正，"
     "经典的标量权重公式不再最优。"),
]


def seed_if_empty() -> None:
    db = SessionLocal()
    try:
        if db.scalar(select(User).limit(1)):
            return

        teacher = User(email="teacher@demo.edu", name="张明", role="teacher",
                       school="山西农业大学软件学院", password_hash=hash_password(DEMO_PASSWORD))
        student = User(email="student@demo.edu", name="李小凡", role="student",
                       school="山西农业大学软件学院", student_no="2026S001",
                       password_hash=hash_password(DEMO_PASSWORD))
        admin = User(email="admin@demo.edu", name="平台管理员", role="teacher", is_admin=True,
                     school="山西农业大学软件学院", password_hash=hash_password(DEMO_PASSWORD))
        db.add_all([teacher, student, admin])
        db.flush()

        course = Course(title="多传感器信息融合滤波技术", cover_emoji="🛰️",
                        description="卡尔曼滤波 / EKF / UKF 与多传感器加权融合，"
                                    "含状态估计方差对比实验。",
                        join_code="DEMO01", teacher_id=teacher.id)
        db.add(course)
        db.flush()
        course.students.append(student)

        kps = []
        for i, (t, c) in enumerate(KP_SEED):
            k = KnowledgePoint(course_id=course.id, title=t, content=c, order_no=i,
                               scope="course", created_by=teacher.id)
            db.add(k)
            kps.append(k)
        db.flush()

        db.add(Announcement(course_id=course.id,
                            content="本周重点：UKF 的无迹变换与 Sigma 点选取，"
                                    "请完成课后作业并做一遍知识点自测。"))

        a = Assignment(course_id=course.id, title="实验一：卡尔曼滤波状态估计方差对比",
                       content="用 MATLAB 或 Python 复现课件中的仿真：\n"
                               "1. 建立线性系统模型，生成三路传感器观测；\n"
                               "2. 分别给出三路传感器的状态估计方差曲线；\n"
                               "3. 与融合后的方差曲线对比，说明融合带来的精度提升。\n\n"
                               "提交内容：代码 + 方差对比图 + 不超过 500 字的结论分析。",
                       due_at=now() + timedelta(days=7), full_score=100)
        db.add(a)

        # 一套教师手工题
        qs = QuizSet(course_id=course.id, title="第 1 章 · 卡尔曼滤波基础自测",
                     source="manual", scope="course", created_by=teacher.id,
                     kp_ids=[kps[0].id])
        db.add(qs)
        db.flush()
        manual = [
            dict(qtype="single", stem="卡尔曼滤波的卡尔曼增益 K 的物理含义是：",
                 options=["A. 观测噪声的方差", "B. 在预测与观测之间做加权平衡的系数",
                          "C. 状态转移矩阵的逆", "D. 系统过程噪声的功率谱密度"],
                 answer="B", analysis="卡尔曼增益 K = P⁻Hᵀ(HP⁻Hᵀ+R)⁻¹，"
                                      "它根据预测协方差与观测噪声的相对大小决定信任谁更多："
                                      "预测不确定就多信观测，观测噪声大就多信预测。故选 B。",
                 difficulty=2, kp_id=kps[0].id),
            dict(qtype="judge", stem="当过程噪声方差 Q 增大时，卡尔曼增益会相应减小。",
                 options=["对", "错"], answer="错",
                 analysis="Q 增大表示对预测模型更不信任，预测协方差 P⁻ 变大，"
                          "卡尔曼增益 K 随之增大，从而更多采纳观测值。故该说法错误。",
                 difficulty=2, kp_id=kps[0].id),
            dict(qtype="multi", stem="卡尔曼滤波的适用前提包括：",
                 options=["A. 系统为线性系统", "B. 过程噪声与观测噪声为高斯白噪声",
                          "C. 噪声之间互不相关", "D. 系统必须是时不变的"],
                 answer="ABC",
                 analysis="经典卡尔曼滤波要求线性系统、高斯白噪声且互不相关，"
                          "但并不要求时不变——时变系统同样可以逐步递推。故 D 错误，选 ABC。",
                 difficulty=3, kp_id=kps[0].id),
            dict(qtype="single", stem="融合前后状态估计方差的关系是：",
                 options=["A. 融合后方差等于各传感器方差的算术平均",
                          "B. 融合后方差大于最小单传感器方差",
                          "C. 融合后方差小于任一单传感器方差",
                          "D. 二者没有确定关系"],
                 answer="C",
                 analysis="按最小均方误差准则，P = (Σ Pᵢ⁻¹)⁻¹，即各传感器信息量（方差倒数）之和的倒数，"
                          "必然小于任何一个单独的 Pᵢ。这正是多传感器融合提升精度的理论依据。故选 C。",
                 difficulty=3, kp_id=kps[0].id),
        ]
        for i, q in enumerate(manual):
            db.add(Question(quiz_set_id=qs.id, order_no=i, **q))

        db.commit()
        print("[seed] 演示数据已创建：admin@demo.edu / teacher@demo.edu / student@demo.edu"
              "（密码 demo1234）")
    finally:
        db.close()
