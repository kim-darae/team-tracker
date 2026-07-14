#!/usr/bin/env python3
"""data.json 을 Jira에서 새로 생성 (GitHub Actions 자동 갱신용).

- 인증: 환경변수 JIRA_EMAIL / JIRA_TOKEN (없으면 config.json 사용 — 로컬 테스트)
- 구조값(사이트·프로젝트·담당자): config.example.json (비밀 아님, repo에 있음)
"""
import os, json, base64, urllib.request, urllib.parse, urllib.error

DIR = os.path.dirname(os.path.abspath(__file__))

def load_cfg():
    # 비밀 아닌 구조값은 config.example.json, 있으면 config.json 로 덮음(로컬)
    cfg = json.load(open(os.path.join(DIR, "config.example.json")))
    p = os.path.join(DIR, "config.json")
    if os.path.exists(p):
        cfg.update({k: v for k, v in json.load(open(p)).items() if v})
    return cfg

CFG   = load_cfg()
SITE  = CFG["jira_site"]
PROJ  = CFG["project_key"]
EMAIL = os.environ.get("JIRA_EMAIL") or CFG.get("email")
TOKEN = os.environ.get("JIRA_TOKEN") or CFG.get("api_token")
ASSIGNEES = CFG["assignees"]
if not TOKEN:
    raise SystemExit("JIRA_TOKEN 이 없습니다 (환경변수 또는 config.json)")

KTLO_ROOT, OPS_ROOT, SETTLE_PROJ = "CBPPSP-663", "CBPPSP-697", "CBPSPP"
F_START, F_ESTMD = "customfield_10015", "customfield_12766"
F_HEALTH, F_KR = "customfield_10071", "customfield_10712"  # TM 전용: Health Check / 분기 KR 달성
FIELDS = f"summary,issuetype,parent,status,assignee,duedate,{F_START},{F_ESTMD},{F_HEALTH},{F_KR}"
TEAM_IDS = ",".join('"%s"' % a["id"] for a in ASSIGNEES)
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

def _auth():
    t = base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()
    return {"Authorization": f"Basic {t}", "Accept": "application/json"}

def search(jql):
    issues, token = [], None
    while True:
        params = {"jql": jql, "maxResults": 100, "fields": FIELDS}
        if token:
            params["nextPageToken"] = token
        url = SITE + "/rest/api/3/search/jql?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=_auth())
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read().decode())
        issues += res.get("issues", [])
        token = res.get("nextPageToken")
        if not token:
            break
    return issues

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
    for i in search(JQLS["ktlo"]): items.append(norm(i, "ktlo"))
    for i in search(JQLS["ops"]):  items.append(norm(i, "ops"))
    classifiable = [norm(i, "?") for i in search(JQLS["rest"])]
    try:
        classifiable += [norm(i, "?") for i in search(JQLS["settle"])]
    except Exception as e:
        print("settle 조회 실패(무시):", e)
    okr = {x["key"] for x in classifiable if x["tm"] or x["type"] == "Initiative"}
    for _ in range(4):
        okr |= {x["key"] for x in classifiable if x["parent"] in okr}
    for x in classifiable:
        x["track"] = "okr" if x["key"] in okr else "etc"
    items += classifiable
    attach_tm(items)
    return items

def _has_val(v):
    """커스텀필드 값 입력 여부 (None/빈 리스트/빈 문자열 = 미입력)."""
    if v is None:
        return False
    if isinstance(v, (list, dict, str)):
        return bool(v)
    return True


def attach_tm(items):
    keys = sorted({i["tm"] for i in items if i.get("tm")})
    if not keys:
        return
    m = {}
    try:
        for c in [keys[x:x+90] for x in range(0, len(keys), 90)]:
            for raw in search("key in (%s)" % ",".join(c)):
                s = raw["fields"]["status"]
                m[raw["key"]] = (s["name"], s["statusCategory"]["name"], raw["fields"].get("duedate"),
                                 _has_val(raw["fields"].get(F_HEALTH)), _has_val(raw["fields"].get(F_KR)))
    except Exception as e:
        print("TM 상태 조회 실패(무시):", e)
    for i in items:
        t = i.get("tm")
        if t and t in m:
            i["tmStatus"], i["tmStatusCat"], i["tmDue"], i["tmHealth"], i["tmKr"] = m[t]

def main():
    p = os.path.join(DIR, "data.json")
    data = json.load(open(p)) if os.path.exists(p) else {
        "jiraBase": "https://musinsa-oneteam.atlassian.net/browse/",
        "boards": {
            "okr": "https://jira.team.musinsa.com/jira/software/c/projects/CBPPSP/boards/10156",
            "ktlo": "https://jira.team.musinsa.com/jira/software/c/projects/CBPPSP/boards/6061",
            "plan": "https://jira.team.musinsa.com/jira/plans/9817/scenarios/9817/timeline",
        },
    }
    data["items"] = build_items()
    # UTC → KST 표기
    import datetime
    kst = datetime.timezone(datetime.timedelta(hours=9))
    data["updatedAt"] = datetime.datetime.now(kst).strftime("%Y-%m-%d %H:%M")
    json.dump(data, open(p, "w"), ensure_ascii=False, indent=1)
    print(f"data.json 갱신 완료 — {len(data['items'])}건 @ {data['updatedAt']}")

if __name__ == "__main__":
    main()
