#!/bin/zsh
# Partner Platform Tracker — data.json 갱신 (claude 헤드리스)
set -u
DIR="$HOME/Documents/partner-platform-tracker"
CLAUDE_BIN=$(ls -dt "$HOME"/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | head -1)

if [ -z "$CLAUDE_BIN" ] || [ ! -x "$CLAUDE_BIN" ]; then
  echo "ERROR: claude 바이너리를 찾지 못했습니다."; exit 1
fi

echo "데이터 갱신 중... (Jira 조회)"
"$CLAUDE_BIN" -p "$(cat "$DIR/refresh-prompt.md")" \
  --setting-sources user \
  --permission-mode bypassPermissions \
  --allowedTools "Bash,Read,Write,Edit,mcp__atlassian__searchJiraIssuesUsingJql"
echo "완료. 브라우저를 새로고침하세요."
