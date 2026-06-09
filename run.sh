#!/usr/bin/env bash
# Единый запуск поиска. Сам встаёт в свою папку, грузит venv и ключ,
# выбирает свежий CSV и папку вывода. Просто запусти:  bash run.sh
# Можно сразу с режимом:  bash run.sh 1 | bash run.sh 2 | bash run.sh 3

# --- встаём в папку скрипта (где бы он ни лежал) ---
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

# --- venv ---
if [ -d venv ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

# --- ключ из secrets.sh (чинит проблему с 401) ---
if [ -f secrets.sh ]; then
  # shellcheck disable=SC1091
  source secrets.sh
fi
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "❌ Нет ANTHROPIC_API_KEY."
  echo "   Впиши ключ в secrets.sh:  export ANTHROPIC_API_KEY=sk-ant-..."
  echo "   (открыть:  open -e secrets.sh), потом запусти снова."
  exit 1
fi

# --- выбор режима ---
MODE="${1:-}"
if [ -z "$MODE" ]; then
  echo ""
  echo "Что запускаем?"
  echo "  1) Все вакансии        — за неделю, обрабатываем весь пул        -> ./review"
  echo "  2) За последние 24 часа — только свежий батч                     -> ./review_24h"
  echo "  3) Мало откликов        — самые свежие (последние 6 часов)        -> ./review_fresh"
  echo "  4) Неделя, мало откликов — за неделю, свежие первыми              -> ./review_week"
  echo ""
  read -rp "Выбор [1/2/3/4]: " MODE
fi

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
freshest_csv() { ls -t jobs_*.csv 2>/dev/null | head -1; }

case "$MODE" in
  1|all)
    echo "=== $(ts) РЕЖИМ 1: все вакансии (168ч), весь пул ===" | tee -a run.log
    python job_finder.py        2>&1 | tee -a run.log
    python pipeline.py          2>&1 | tee -a run.log
    OUT="review"
    ;;
  2|24h)
    echo "=== $(ts) РЕЖИМ 2: за 24 часа ===" | tee -a run.log
    python job_finder.py 24      2>&1 | tee -a run.log
    CSV="$(freshest_csv)"
    python pipeline.py "$CSV" review_24h  2>&1 | tee -a run.log
    OUT="review_24h"
    ;;
  3|fresh)
    echo "=== $(ts) РЕЖИМ 3: свежие / мало откликов (6ч) ===" | tee -a run.log
    python job_finder.py 6       2>&1 | tee -a run.log
    CSV="$(freshest_csv)"
    python pipeline.py "$CSV" review_fresh 2>&1 | tee -a run.log
    OUT="review_fresh"
    ;;
  4|week|week-fresh)
    echo "=== $(ts) РЕЖИМ 4: за неделю, свежие первыми (мало откликов) ===" | tee -a run.log
    python job_finder.py         2>&1 | tee -a run.log
    CSV="$(freshest_csv)"
    python pipeline.py "$CSV" review_week 2>&1 | tee -a run.log
    OUT="review_week"
    ;;
  *)
    echo "Неизвестный режим: '$MODE' (нужно 1, 2 или 3)"
    exit 1
    ;;
esac

echo "=== $(ts) Готово (режим $MODE). Комплекты: ./$OUT ===" | tee -a run.log
echo ""
echo "Открыть результат:  open $OUT"
echo "Трекер откликов:    open applications.csv"
