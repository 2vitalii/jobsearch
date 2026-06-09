#!/usr/bin/env bash
# Быстрый коммит+пуш. Использование:  bash push.sh ["сообщение"]
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1
git add -A
if git diff --cached --quiet; then echo "Нечего коммитить."; exit 0; fi
git commit -m "${1:-update $(date '+%Y-%m-%d %H:%M')}"
git push && echo "✅ Запушено. Теперь в проекте нажми 'Sync now'."
