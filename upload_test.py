"""独立测试：文件上传 → 解析 → AI 整理 → 入库（含真实 .docx 构造）"""
import io
import json
import urllib.error
import urllib.request
import zipfile

BASE = "http://127.0.0.1:8000"
BOUNDARY = "----learnhubboundary"


def req(path, data=None, token=None, method="GET", ctype=None):
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    if ctype:
        r.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def multipart(fname: str, content: bytes) -> bytes:
    b = BOUNDARY.encode()
    head = (b"--" + b + b"\r\n"
            + b'Content-Disposition: form-data; name="file"; filename="'
            + fname.encode("utf-8") + b'"\r\n'
            + b"Content-Type: application/octet-stream\r\n\r\n")
    tail = b"\r\n--" + b + b"--\r\n"
    return head + content + tail


DOC_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    "<w:p><w:r><w:t>第一章 卡尔曼滤波基本原理</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>卡尔曼滤波是递推最小方差估计，包含预测与更新两步。</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>第二章 扩展卡尔曼滤波 EKF</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>EKF 通过雅可比矩阵做一阶泰勒线性化，强非线性下精度下降。</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>第三章 无迹卡尔曼滤波 UKF</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>UKF 使用 Sigma 点进行无迹变换，无需计算雅可比矩阵。</w:t></w:r></w:p>"
    "</w:body></w:document>")


def make_docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
                   '/package/2006/content-types"/>')
        z.writestr("word/document.xml", DOC_XML)
    return buf.getvalue()


MD = ("第一章 概述\n这是概述内容，介绍多传感器融合的意义。\n\n"
      "第二章 方法\n这是方法内容，给出加权融合的最小均方误差准则。\n\n"
      "第三章 实验\n这是实验内容，对比 EKF 与 UKF 的方差。\n").encode("utf-8")


def main():
    st, d = req("/api/auth/login",
                json.dumps({"email": "teacher@demo.edu", "password": "demo1234"}).encode(),
                method="POST", ctype="application/json")
    print("教师登录:", st)
    tok = d["token"]

    # 学生 token 用于越权测试
    st, ds = req("/api/auth/login",
                 json.dumps({"email": "student@demo.edu", "password": "demo1234"}).encode(),
                 method="POST", ctype="application/json")
    stu_tok = ds["token"]

    docx_bytes = make_docx()
    print("构造 .docx:", len(docx_bytes), "bytes")

    for fname, content in (("第1章卡尔曼滤波.docx", docx_bytes), ("课程要点.md", MD)):
        st, d = req("/api/courses/1/knowledge/extract", data=multipart(fname, content),
                    token=tok, method="POST",
                    ctype="multipart/form-data; boundary=" + BOUNDARY)
        print("\n=== 上传 %s -> HTTP %s ===" % (fname, st))
        if st != 200:
            print("  失败:", d)
            continue
        print("  解析 %d 字 | 整理方式 %s | 识别 %d 个知识点"
              % (d["chars"], d["provider"], len(d["points"])))
        for p in d["points"]:
            print("    ·", p["title"][:44], f"(正文 {len(p['content'])} 字)")

        st2, d2 = req("/api/courses/1/knowledge/commit", token=tok, method="POST",
                      data=json.dumps({"source_file": fname, "scope": "teacher",
                                       "points": d["points"]}).encode(),
                      ctype="application/json")
        print("  → 入库 HTTP %s，写入 %s 条，scope=%s" % (st2, d2.get("created"), d2.get("scope")))

    # 学生视角：不应看到「仅教师可见」的知识点
    st, d = req("/api/courses/1", token=stu_tok)
    titles = [k["title"] for k in d["knowledge_points"]]
    print("\n学生可见知识点 %d 条:" % len(titles))
    for t in titles:
        print("    ·", t[:50], "| scope=",
              next(k["scope"] for k in d["knowledge_points"] if k["title"] == t))
    leaked = [t for t in titles if "章" in t or "要点" in t]
    print("  泄露检查（不应出现上传资料生成的『第X章/要点』）:", "有泄露!" if leaked else "OK，无泄露")

    # 教师视角应全部可见
    st, d = req("/api/courses/1", token=tok)
    print("教师可见知识点 %d 条（含仅教师可见）" % len(d["knowledge_points"]))

    # 越权：学生上传
    st, d = req("/api/courses/1/knowledge/extract", data=multipart("x.md", MD),
                token=stu_tok, method="POST",
                ctype="multipart/form-data; boundary=" + BOUNDARY)
    print("\n学生上传资料 -> HTTP", st, "(应为 403)")


if __name__ == "__main__":
    main()
