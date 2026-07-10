#!/bin/zsh
# 원클릭 갱신: Jira 최신 조회 → data.json 갱신 → GitHub(공유 링크) 반영
# 사용법:  ./publish.sh
set -e
cd "$(dirname "$0")"

echo "① Jira에서 최신 데이터 가져오는 중..."
python3 build_data.py

if git diff --quiet data.json order.json 2>/dev/null; then
  echo "변경 사항이 없습니다. (이미 최신)"
  exit 0
fi

echo "② GitHub에 올리는 중..."
git add data.json order.json
git commit -q -m "update: $(date '+%Y-%m-%d %H:%M') 데이터 갱신"
git push -q

echo "✅ 완료! 1~2분 뒤 공유 링크 새로고침하면 최신 상태가 보입니다."
echo "   https://kim-darae.github.io/team-tracker/"
