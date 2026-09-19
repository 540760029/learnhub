"""
用「同源 iframe」在无头浏览器里验证提交前校验对话框。

为什么不直接导航：headless 下 location.replace 之后父页面的定时器不保证再执行，
--virtual-time-budget 也不太可控。改用 iframe 后父页面存活，可以稳定地
操作 iframe 内部 DOM、并把结果写回父页面截图。
"""
import json
import os
import subprocess
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
STATIC = os.path.join(HERE, "static")

PROBE_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{margin:0;background:#0b1120;color:#e6edf7;font:14px/1.6 system-ui,sans-serif}
#frame{width:100%%;height:900px;border:0}
#out{position:fixed;left:0;bottom:0;right:0;background:#000;color:#0f0;
padding:8px 12px;font:13px/1.5 monospace;white-space:pre-wrap;z-index:9}</style></head>
<body>
<iframe id="frame"></iframe>
<pre id="out">PENDING</pre>
<script>
var TOK = '%(tok)s';
var QID = %(qid)s;
var MODE = '%(mode)s';
var f = document.getElementById('frame');
var log = [];
function done(obj){
  document.getElementById('out').textContent = 'RESULT:' + JSON.stringify(obj);
}
function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }

(async function(){
  try {
    // 先在同源下写入 token（父页面与 iframe 同源 127.0.0.1:8000）
    localStorage.setItem('lh_token', TOK);
    f.src = '/#/quiz/' + QID;
    await sleep(3000);                       // 等前端登录并渲染
    var d = f.contentDocument;
    if (!d) { return done({error:'无法访问 iframe DOM'}); }
    var submit = d.getElementById('submitQuiz');
    if (!submit) { return done({error:'没有找到提交按钮', html: d.body ? d.body.innerHTML.length : -1}); }

    log.push('提交按钮文案=' + submit.textContent.trim());
    log.push('题号导航数量=' + d.querySelectorAll('#qnav .qchip').length);
    log.push('已作答高亮=' + d.querySelectorAll('#qnav .qchip.done').length);
    log.push('进度文本=' + (d.getElementById('progress') || {}).textContent.trim());

    if (MODE === 'complete') {
      // 每题都点第一个选项
      var cards = d.querySelectorAll('#qArea .q');
      cards.forEach(function(c){ var i = c.querySelector('input'); if (i) i.click(); });
      await sleep(600);
      log.push('作答后按钮=' + d.getElementById('submitQuiz').textContent.trim());
      log.push('作答后高亮=' + d.querySelectorAll('#qnav .qchip.done').length);
    }

    d.getElementById('submitQuiz').click();
    await sleep(900);

    var box = d.getElementById('modalBox');
    var modal = d.getElementById('modal');
    var modalShown = !!(modal && !modal.classList.contains('hidden') && box && box.innerHTML.length > 20);
    var title = '';
    var jump = 0, hasOk = false, hasGoFirst = false, panelText = '';
    if (modalShown) {
      var h2 = box.querySelector('h2');
      title = h2 ? h2.textContent.trim() : '';
      jump = box.querySelectorAll('[data-jump]').length;
      hasOk = !!box.querySelector('#preOk');
      hasGoFirst = !!box.querySelector('#goFirst');
      panelText = (box.querySelector('.summary') || {}).innerText || '';
      panelText = panelText.replace(/\\s+/g, ' ').slice(0, 200);
    }
    log.push('弹窗出现=' + modalShown);
    log.push('弹窗标题=' + title);
    log.push('弹窗内可点题号数=' + jump);
    log.push('有「确认提交」=' + hasOk);
    log.push('有「跳到第1道未作答」=' + hasGoFirst);

    done({
      mode: MODE,
      modalShown: modalShown,
      title: title,
      jumpButtons: jump,
      hasConfirm: hasOk,
      hasGoFirst: hasGoFirst,
      panel: panelText,
      log: log
    });
  } catch (e) {
    done({error: String(e), log: log});
  }
})();
</script></body></html>
"""


def req(path, data=None, token=None, method="GET"):
    r = urllib.request.Request(BASE + path,
                               data=json.dumps(data).encode() if data is not None else None,
                               method=method)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def login(email):
    return req("/api/auth/login", {"email": email, "password": "demo1234"}, method="POST")[1]["token"]


def run(tok, qid, mode, png, wait=15000):
    html = PROBE_HTML % {"tok": tok, "qid": qid, "mode": mode}
    bp = os.path.join(STATIC, "_probe.html")
    with open(bp, "w", encoding="utf-8") as f:
        f.write(html)
    if os.path.exists(png):
        os.remove(png)
    try:
        subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--force-device-scale-factor=1", "--window-size=1280,1080",
             "--screenshot=" + png, "--virtual-time-budget=%d" % wait,
             "http://127.0.0.1:8000/static/_probe.html"],
            capture_output=True, text=True, timeout=240)
    finally:
        if os.path.exists(bp):
            os.remove(bp)
    return os.path.exists(png)


def main():
    s = login("student@demo.edu")
    st, course = req("/api/courses/1", token=s)
    qid = course["quiz_sets"][-1]["id"]
    st, full = req(f"/api/quizzes/{qid}", token=s)
    n = len(full["questions"])
    print("试卷 #%d，共 %d 题" % (qid, n))
    before = len(req("/api/my/attempts", token=s)[1]["attempts"])

    print("\n[1] 未作答就点提交（期望：弹窗阻止、题号可点、不产生答题记录）")
    ok1 = run(s, qid, "incomplete", os.path.join(HERE, "_probe_incomplete.png"))
    after = len(req("/api/my/attempts", token=s)[1]["attempts"])
    print("    截图:", ok1, " 答题记录 %d → %d %s" % (
        before, after, "✅ 未提交" if after == before else "❌ 被提交了"))

    print("\n[2] 全部作答后点提交（期望：弹确认框）")
    ok2 = run(s, qid, "complete", os.path.join(HERE, "_probe_complete.png"))
    print("    截图:", ok2)

    return 0 if (ok1 and ok2 and after == before) else 1


if __name__ == "__main__":
    raise SystemExit(main())
