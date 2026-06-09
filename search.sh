#!/usr/bin/env bash
# Гибкий поиск по ОДНОМУ ключевому слову + локации.
# Использование:
#   bash search.sh "<ключевое слово или позиция>" "<локация>" [часы]
# Примеры:
#   bash search.sh "slack support specialist" "United Arab Emirates"
#   bash search.sh "integration engineer" "Germany" 24
#   bash search.sh "sql support" "worldwide"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

if [ -d venv ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi
if [ -f secrets.sh ]; then
  # shellcheck disable=SC1091
  source secrets.sh
fi
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "❌ Нет ANTHROPIC_API_KEY. Впиши ключ в secrets.sh и запусти снова."
  exit 1
fi

TERM="$1"
LOC="$2"
HRS="${3:-168}"

if [ -z "$TERM" ] || [ -z "$LOC" ]; then
  echo "Использование: bash search.sh \"<ключевое слово>\" \"<локация>\" [часы]"
  echo "Пример:        bash search.sh \"slack support specialist\" \"United Arab Emirates\""
  exit 1
fi

# имя папки из ключевого слова: нижний регистр, пробелы -> _, только буквы/цифры/_
SLUG=$(printf '%s' "$TERM" | tr '[:upper:] ' '[:lower:]_' | tr -cd 'a-z0-9_' | cut -c1-40)
OUT="review_${SLUG}"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') Поиск: '$TERM' в '$LOC' за ${HRS}ч -> ./$OUT ===" | tee -a run.log

# Целевой сбор: только JobSpy по term×location, свободный фильтр (доверяем ключевому слову)
python job_finder.py "$HRS" --term "$TERM" --location "$LOC" --loose 2>&1 | tee -a run.log

CSV="$(ls -t jobs_*.csv 2>/dev/null | head -1)"
if [ -z "$CSV" ]; then
  echo "Не найден свежий jobs_*.csv — поиск ничего не собрал."
  exit 0
fi

# Обработка в свободном режиме (не режем фильтром ролей), вывод в свою папку
LOOSE_FILTER=1 python pipeline.py "$CSV" "$OUT" 2>&1 | tee -a run.log

echo "=== $(date '+%Y-%m-%d %H:%M:%S') Готово. Комплекты: ./$OUT ===" | tee -a run.log
echo ""
echo "Открыть результат:  open $OUT"
