# Partner Platform — Resource Tracker

파트너 플랫폼 팀(파트너 + 정산)의 OKR · KTLO · 운영효율화 리소스를 한 화면에서 보고,
Jira에 바로 티켓 생성 · 상태 변경 · 일정(시작/종료일) 입력 · 담당자별 타임라인까지 관리하는 로컬 웹앱.

## 무엇을 하나
- **리스트**: OKR / KTLO / 운영효율화 / 기타 트랙별 Epic·Task 트리, 상태 드롭다운(→Jira 즉시 반영), 시작/종료일 입력, 드래그로 우선순위 조정
- **타임라인**: 담당자별 Epic 일정 바(오늘 기준 중앙)
- **사람 필터**: 팀원별로 걸러보기
- 데이터 소스: Jira `CBPPSP`(파트너) + `CBPSPP`(정산, 팀원 담당 건)

## 설치 (팀원용)
사전 준비: **Python 3** (macOS 기본 내장), 개인 **Jira API 토큰**.

1. 저장소 클론
   ```bash
   git clone <REPO_URL> partner-platform-tracker
   cd partner-platform-tracker
   ```
2. 설정 파일 만들기 (본인 토큰)
   ```bash
   cp config.example.json config.json
   ```
   - https://id.atlassian.com/manage-profile/security/api-tokens 에서 **API 토큰 발급** → `config.json` 의 `api_token` 에 붙여넣기
   - `email` 을 본인 무신사 이메일로 수정
   - ⚠️ `config.json` 은 `.gitignore` 로 커밋되지 않음(개인 토큰 보호)
3. 실행
   ```bash
   python3 server.py
   ```
   → 브라우저에서 http://localhost:4323 접속

## 참고
- 토큰이 없으면(비워두면) `claude` CLI 헤드리스 브리지 모드로도 동작하지만 느립니다. 토큰 방식(직접연동) 권장.
- 상태/일정/생성은 **본인 Jira 권한으로 실제 티켓을 바꿉니다**. 신중히 사용하세요.
- 우선순위 정렬은 앱 로컬(`order.json`)에 저장됩니다(Jira Rank 미변경).

## 파일
| 파일 | 설명 |
|---|---|
| `index.html` | 프런트엔드(SPA) |
| `server.py` | 로컬 API 서버(포트 4323) + Jira REST 연동 |
| `config.example.json` | 설정 템플릿 (복사해서 `config.json` 으로) |
| `data.json` | Jira 스냅샷(자동 갱신) |
| `refresh.sh` / `refresh-prompt.md` | 브리지 모드 데이터 갱신 스크립트 |
