#!/usr/bin/env python3
"""Partner Platform Resource Tracker — API server (port 4323)

Jira 연동 2모드:
  - direct : config.json 의 email + api_token 으로 Jira REST 직접 호출 (빠름)
  - bridge : 토큰이 없으면 claude 헤드리스(MCP)로 대행 (느리지만 무설정 동작)
"""
import http.server, socketserver, json, os, re, base64, glob, subprocess
import urllib.request, urllib.parse, urllib.error

DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)
PORT = 4323

CFG = json.load(open("config.json"))
SITE, PROJ, CLOUD = CFG["jira_site"], CFG["project_key"], CFG["cloud_id"]
DIRECT = bool(CFG.get("api_token"))

ORDER_FILE = os.path.join(DIR, "order.json")
def load_order():
    try: return json.load(open(ORDER_FILE))
    except Exception: return {}
def save_order(o):
    json.dump(o, open(ORDER_FILE, "w"), ensure_ascii=False, indent=1)

KTLO_ROOT, OPS_ROOT = "CBPPSP-663", "CBPPSP-697"
SETTLE_PROJ = "CBPSPP"  # 정산플랫폼기획 프로젝트 (팀원 담당 건만 병합)
TEAM_IDS = ",".join('"%s"' % a["id"] for a in CFG["assignees"])
F_START, F_ESTMD = "customfield_10015", "customfield_12766"
FIELDS = f"summary,issuetype,parent,status,assignee,duedate,{F_START},{F_ESTMD}"
JQLS = {
    "ktlo": f'issue in portfolioChildIssuesOf("{KTLO_ROOT}") AND status not in (Done, HOLD)',
    "ops":  f'(issue in portfolioChildIssuesOf("{OPS_ROOT}") OR issue = {OPS_ROOT}) AND status not in (Done, HOLD)',
    "rest": (f'project = {PROJ} AND statusCategory != Done AND status != HOLD '
             f'AND issuetype in (Initiative, Epic, Product, Task) '
             f'AND issue not in portfolioChildIssuesOf("{KTLO_ROOT}") '
             f'AND issue not in portfolioChildIssuesOf("{OPS_ROOT}") '
             f'AND issue != {OPS_ROOT} AND issue != {KTLO_ROOT}'),
    "settle": (f'project = {SETTLE_PROJ} AND statusCategory != Done AND status != HOLD '
               f'AND issuetype in (Initiative, Epic, Product, Task) '
               f'AND assignee in ({TEAM_IDS})'),
}

# ── direct(REST) helpers ─────────────────────────────────────────────
def _auth():
    tok = base64.b64encode(f'{CFG["email"]}:{CFG["api_token"]}'.encode()).decode()
    return {"Authorization": f"Basic {tok}", "Content-Type": "application/json", "Accept": "application/json"}

def rest(method, path, body=None, params=None):
    url = SITE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, method=method, headers=_auth(),
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}

def rest_search(jql):
    for ep in ("/rest/api/3/search/jql", "/rest/api/3/search"):
        code, res = rest("GET", ep, params={"jql": jql, "maxResults": 100, "fields": FIELDS})
        if code == 200:
            return res.get("issues", [])
    raise RuntimeError(f"search failed: {res}")

# ── bridge(claude headless) helpers ──────────────────────────────────
def claude_bin():
    c = sorted(glob.glob(os.path.expanduser(
        "~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude")), reverse=True)
    return c[0] if c else None

def bridge(prompt, tools):
    out = subprocess.run(
        [claude_bin(), "-p", prompt, "--setting-sources", "user",
         "--permission-mode", "bypassPermissions", "--allowedTools", tools],
        capture_output=True, text=True, timeout=240).stdout
    m = re.search(r"```json\s*(.*?)```", out, re.S) or re.search(r"(\[.*\]|\{.*\})", out, re.S)
    if not m:
        raise RuntimeError("bridge: JSON 응답 파싱 실패: " + out[-300:])
    return json.loads(m.group(1))

MCP_SEARCH = "mcp__atlassian__searchJiraIssuesUsingJql"
MCP_TRANS  = "mcp__atlassian__getTransitionsForJiraIssue,mcp__atlassian__transitionJiraIssue"
MCP_CREATE = "mcp__atlassian__createJiraIssue"

# ── issue normalize / track assignment ───────────────────────────────
def norm(raw, track):
    f = raw["fields"]
    par = (f.get("parent") or {}).get("key")
    tm = par if (par or "").startswith("TM-") else None
    a = f.get("assignee")
    return {
        "key": raw["key"], "track": track, "summary": f.get("summary") or "(제목 없음)",
        "type": f["issuetype"]["name"], "status": f["status"]["name"],
        "statusCat": f["status"]["statusCategory"]["name"],
        "assignee": (a["displayName"].split("/")[0].strip() if a else None),
        "parent": (None if tm else par), "tm": tm,
        "start": f.get(F_START), "due": f.get("duedate"), "estMd": f.get(F_ESTMD),
    }

def build_items():
    items = []
    for i in rest_search(JQLS["ktlo"]): items.append(norm(i, "ktlo"))
    for i in rest_search(JQLS["ops"]):  items.append(norm(i, "ops"))
    # 파트너(CBPPSP) 미분류 + 정산(CBPSPP) 팀원 담당 건을 함께 okr/etc로 분류
    rest_items = [norm(i, "?") for i in rest_search(JQLS["rest"])]
    try:
        rest_items += [norm(i, "?") for i in rest_search(JQLS["settle"])]
    except Exception as e:
        print("settle 조회 실패(무시):", e)
    okr = {x["key"] for x in rest_items if x["tm"] or x["type"] == "Initiative"}
    for _ in range(4):  # descend children
        okr |= {x["key"] for x in rest_items if x["parent"] in okr}
    for x in rest_items:
        x["track"] = "okr" if x["key"] in okr else "etc"
    items += rest_items
    return items

def do_publish():
    """build_data.py 로 최신 생성 → 변경 있으면 git commit+push (공유 링크 반영)."""
    r = subprocess.run(["python3", os.path.join(DIR, "build_data.py")],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError("데이터 생성 실패: " + (r.stderr or r.stdout)[-300:])
    g = lambda *a: subprocess.run(["git", "-C", DIR, *a], capture_output=True, text=True)
    g("add", "data.json", "order.json")
    if g("diff", "--staged", "--quiet").returncode == 0:
        return {"ok": True, "changed": False, "message": "변경 없음 — 이미 최신입니다"}
    g("-c", "user.name=김다래", "-c", "user.email=darae.kim@musinsa.com",
      "commit", "-q", "-m", "update: 데이터 갱신")
    # github_token 이 있으면 토큰 URL로 바로 push (완전 원클릭), 없으면 안내
    tok = CFG.get("github_token")
    if tok:
        u = subprocess.run(["git", "-C", DIR, "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
        push_url = u.replace("https://", f"https://{tok}@") if u.startswith("https://") else u
        p = subprocess.run(["git", "-C", DIR, "push", "-q", push_url, "HEAD:main"], capture_output=True, text=True, timeout=90)
    else:
        p = subprocess.run(["git", "-C", DIR, "push", "-q"], capture_output=True, text=True, timeout=90)
    if p.returncode != 0:
        hint = "config.json 의 github_token 을 확인하세요." if tok else "GitHub Desktop에서 Push 를 눌러주세요(또는 config.json 에 github_token 추가하면 자동)."
        return {"ok": False, "changed": True, "message": "커밋 완료. push 실패 → " + hint}
    return {"ok": True, "changed": True, "message": "공유 링크 반영 완료 (1~2분 뒤 새로고침)"}

def do_sync():
    if DIRECT:
        data = json.load(open("data.json"))
        data["items"] = build_items()
        data["updatedAt"] = subprocess.run(["date", "+%Y-%m-%d %H:%M"], capture_output=True, text=True).stdout.strip()
        json.dump(data, open("data.json", "w"), ensure_ascii=False, indent=1)
        return data
    subprocess.run(["/bin/zsh", os.path.join(DIR, "refresh.sh")], capture_output=True, text=True, timeout=600)
    return json.load(open("data.json"))

def do_transitions(key):
    if DIRECT:
        code, res = rest("GET", f"/rest/api/3/issue/{key}/transitions")
        return [{"id": t["id"], "name": t["to"]["name"], "cat": t["to"]["statusCategory"]["name"]}
                for t in res.get("transitions", [])]
    return bridge(
        f'cloudId {CLOUD} 에서 mcp__atlassian__getTransitionsForJiraIssue 를 issueIdOrKey="{key}" 로 호출하고, '
        f'가능한 전이 목록을 ```json [{{"id":"...","name":"<to 상태명>","cat":"<to statusCategory name>"}}] ``` 형식으로만 출력해.',
        "mcp__atlassian__getTransitionsForJiraIssue")

def do_status(key, tid):
    if DIRECT:
        code, res = rest("POST", f"/rest/api/3/issue/{key}/transitions", body={"transition": {"id": tid}})
        if code not in (200, 204): raise RuntimeError(str(res))
        return {"ok": True}
    return bridge(
        f'cloudId {CLOUD} 에서 mcp__atlassian__transitionJiraIssue 로 issueIdOrKey="{key}" 를 transition id "{tid}" 로 전이시켜. '
        f'성공하면 ```json {{"ok": true}} ``` 만 출력해.', MCP_TRANS)

def do_create(parent, summary, itype, assignee_id):
    if DIRECT:
        fields = {"project": {"key": PROJ}, "summary": summary,
                  "issuetype": {"name": itype}, "parent": {"key": parent}}
        if assignee_id: fields["assignee"] = {"id": assignee_id}
        code, res = rest("POST", "/rest/api/3/issue", body={"fields": fields})
        if code not in (200, 201): raise RuntimeError(str(res))
        return {"key": res["key"]}
    extra = f' assignee_account_id="{assignee_id}"' if assignee_id else ""
    return bridge(
        f'cloudId {CLOUD} 에서 mcp__atlassian__createJiraIssue 로 projectKey="{PROJ}" issueTypeName="{itype}" '
        f'parent="{parent}" summary="{summary}"{extra} 티켓을 생성해. '
        f'생성된 키를 ```json {{"key":"CBPPSP-000"}} ``` 형식으로만 출력해.', MCP_CREATE)

def do_dates(key, start, due):
    """start/due: 'YYYY-MM-DD' 또는 None(값 지우기). 둘 중 준 것만 반영."""
    fields = {}
    if start is not None: fields[F_START] = start or None
    if due is not None:   fields["duedate"] = due or None
    if not fields: return {"ok": True}
    if DIRECT:
        code, res = rest("PUT", f"/rest/api/3/issue/{key}", body={"fields": fields})
        if code not in (200, 204): raise RuntimeError(str(res))
        return {"ok": True}
    parts = []
    if F_START in fields: parts.append(f'Start date(customfield_10015)={fields[F_START] or "null(제거)"}')
    if "duedate" in fields: parts.append(f'Due date(duedate)={fields["duedate"] or "null(제거)"}')
    return bridge(
        f'cloudId {CLOUD} 에서 mcp__atlassian__editJiraIssue 로 issueIdOrKey="{key}" 의 '
        f'{", ".join(parts)} 로 수정해. 성공하면 ```json {{"ok":true}} ``` 만 출력.',
        "mcp__atlassian__editJiraIssue")

# ── HTTP handler ─────────────────────────────────────────────────────
class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/config":
            return self._json({"mode": "direct" if DIRECT else "bridge", "assignees": CFG["assignees"]})
        if u.path == "/api/order":
            return self._json(load_order())
        if u.path == "/api/transitions":
            key = urllib.parse.parse_qs(u.query).get("key", [""])[0]
            try: return self._json(do_transitions(key))
            except Exception as e: return self._json({"error": str(e)}, 500)
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            if self.path == "/api/sync":
                return self._json(do_sync())
            if self.path == "/api/status":
                return self._json(do_status(body["key"], body["transitionId"]))
            if self.path == "/api/create":
                return self._json(do_create(body["parent"], body["summary"], body["type"], body.get("assigneeId")))
            if self.path == "/api/dates":
                return self._json(do_dates(body["key"], body.get("start"), body.get("due")))
            if self.path == "/api/order":
                save_order(body.get("order", {}))
                return self._json({"ok": True})
            if self.path == "/api/publish":
                return self._json(do_publish())
            return self._json({"error": "unknown endpoint"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

socketserver.TCPServer.allow_reuse_address = True
with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), H) as httpd:
    mode = "direct(REST)" if DIRECT else "bridge(claude headless)"
    print(f"Partner Platform Tracker → http://localhost:{PORT}  [Jira 모드: {mode}]")
    httpd.serve_forever()
