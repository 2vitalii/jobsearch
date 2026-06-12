#!/bin/bash
# Ежедневный автозапуск конвейера поиска работы.
# Активирует venv, подхватывает ключ из secrets.sh, гоняет поиск + тюнинг, пишет лог.

DIR="/Users/vitaliivlasov/Desktop/jobsearch"
cd "$DIR" || exit 1

source venv/bin/activate
source "$DIR/secrets.sh"          # отсюда берётся ANTHROPIC_API_KEY

{
  echo "=== Запуск $(date '+%Y-%m-%d %H:%M:%S') ==="
  python -m jobsearch.finder
  python -m jobsearch.pipeline
  echo "=== Готово $(date '+%Y-%m-%d %H:%M:%S') ==="
  echo ""
} >> "$DIR/run.log" 2>&1
