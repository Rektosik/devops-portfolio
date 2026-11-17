#!/usr/bin/env bash

set -euo pipefail
set -E   


readonly EX_OK=0
readonly EX_USAGE=2      
readonly EX_SOURCE=3     
readonly EX_DEST=4      
readonly EX_ARCHIVE=5    
readonly EX_VERIFY=6     

SCRIPT_NAME="$(basename "$0")"
SOURCE=""
DEST=""
KEEP=5
DRY_RUN=0
LOG_OPT=""         
LOG_FILE=""         
TMP_PATH=""
EXCLUDES=()

usage() {
  cat <<EOF
Використання:
  $SCRIPT_NAME -s ТЕКА -d КУДИ [параметри]

Створює архів backup_<імʼя>_<РРРРММДД>_<ГГХХСС>.tar.gz у теці призначення,
перевіряє його, пише контрольну суму (.sha256) і видаляє найстаріші копії,
залишаючи лише N найновіших.

Обовʼязкові параметри:
  -s, --source ТЕКА     що копіювати (тека має існувати)
  -d, --dest ТЕКА       куди класти архіви (буде створена, якщо немає)

Необовʼязкові параметри:
  -k, --keep N          скільки копій зберігати (ціле число >= 1, типово 5)
  -e, --exclude ШАБЛОН  не включати файли за шаблоном; можна повторювати
  -l, --log ФАЙЛ        файл журналу (типово <КУДИ>/backup.log)
  -n, --dry-run         лише показати, що було б зроблено, нічого не змінюючи
  -h, --help            показати цю довідку

Коди виходу:
  0  успіх
  2  неправильні параметри
  3  джерело не існує або не читається
  4  не вдалося створити/записати теку призначення чи журнал
  5  не вдалося створити архів
  6  перевірка архіву не пройшла

Приклад:
  $SCRIPT_NAME -s ~/projects/app -d ~/backups -k 5 -e node_modules -e '*.log'
EOF
}

log() {
  local level="$1"; shift
  local line
  line="$(date '+%Y-%m-%d %H:%M:%S') [$level] $*"
  if [[ "$level" == "ERROR" ]]; then
    echo "$line" >&2
  else
    echo "$line"
  fi
  if [[ -n "$LOG_FILE" ]]; then
    echo "$line" >> "$LOG_FILE"
  fi
}

die() {
  local code="$1"; shift
  log ERROR "$*"
  exit "$code"
}

cleanup() {
  if [[ -n "$TMP_PATH" && -e "$TMP_PATH" ]]; then
    rm -f -- "$TMP_PATH"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'log ERROR "Несподіваний збій у рядку $LINENO (код $?)"' ERR

need_value() {  
  if (( $2 < 2 )); then
    echo "Помилка: параметр $1 потребує значення. Див. $SCRIPT_NAME --help" >&2
    exit "$EX_USAGE"
  fi
}

while (( $# > 0 )); do
  case "$1" in
    -h|--help)    usage; exit "$EX_OK" ;;
    -s|--source)  need_value "$1" "$#"; SOURCE="$2"; shift 2 ;;
    -d|--dest)    need_value "$1" "$#"; DEST="$2"; shift 2 ;;
    -k|--keep)    need_value "$1" "$#"; KEEP="$2"; shift 2 ;;
    -e|--exclude) need_value "$1" "$#"; EXCLUDES+=("$2"); shift 2 ;;
    -l|--log)     need_value "$1" "$#"; LOG_OPT="$2"; shift 2 ;;
    -n|--dry-run) DRY_RUN=1; shift ;;
    *)
      echo "Помилка: невідомий параметр '$1'. Див. $SCRIPT_NAME --help" >&2
      exit "$EX_USAGE" ;;
  esac
done

if [[ -z "$SOURCE" ]]; then
  echo "Помилка: не вказано джерело (-s). Див. $SCRIPT_NAME --help" >&2
  exit "$EX_USAGE"
fi
if [[ -z "$DEST" ]]; then
  echo "Помилка: не вказано теку призначення (-d). Див. $SCRIPT_NAME --help" >&2
  exit "$EX_USAGE"
fi
if [[ ! "$KEEP" =~ ^[1-9][0-9]*$ ]]; then
  echo "Помилка: --keep має бути цілим числом >= 1, отримано '$KEEP'" >&2
  exit "$EX_USAGE"
fi
if [[ ! -d "$SOURCE" ]]; then
  die "$EX_SOURCE" "Джерело не існує або це не тека: $SOURCE"
fi
if [[ ! -r "$SOURCE" ]]; then
  die "$EX_SOURCE" "Немає прав на читання джерела: $SOURCE"
fi

SOURCE="$(cd "$SOURCE" && pwd)"
if [[ "$SOURCE" == "/" ]]; then
  die "$EX_USAGE" "Копіювати кореневу теку / не підтримується"
fi
NAME="$(basename "$SOURCE")"

if (( DRY_RUN )); then
  if [[ -d "$DEST" ]]; then DEST="$(cd "$DEST" && pwd)"; fi
else
  dest_existed=0
  if [[ -d "$DEST" ]]; then dest_existed=1; fi
  mkdir -p "$DEST" 2>/dev/null || die "$EX_DEST" "Не вдалося створити теку призначення: $DEST"
  [[ -w "$DEST" ]] || die "$EX_DEST" "Немає прав на запис у теку призначення: $DEST"
  DEST="$(cd "$DEST" && pwd)"
  
  case "$DEST/" in
    "$SOURCE"/*)
      if (( ! dest_existed )); then rmdir "$DEST" 2>/dev/null || true; fi
      die "$EX_DEST" "Тека призначення знаходиться всередині джерела: $DEST" ;;
  esac
  log_target="${LOG_OPT:-$DEST/backup.log}"
  touch "$log_target" 2>/dev/null || die "$EX_DEST" "Не вдалося відкрити журнал для запису: $log_target"
  LOG_FILE="$log_target"
fi

if (( DRY_RUN )); then
  case "$DEST/" in
    "$SOURCE"/*) die "$EX_DEST" "Тека призначення знаходиться всередині джерела: $DEST" ;;
  esac
fi

TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
ARCHIVE_NAME="backup_${NAME}_${TIMESTAMP}.tar.gz"
ARCHIVE_PATH="$DEST/$ARCHIVE_NAME"
TMP_PATH="$ARCHIVE_PATH.partial"

rotate() {
  local ts='[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]'
  local files=()
  if [[ -d "$DEST" ]]; then
    shopt -s nullglob
    files=("$DEST"/"backup_${NAME}_"$ts.tar.gz)  
    shopt -u nullglob
  fi
  
  local total=$(( ${#files[@]} + DRY_RUN ))
  local excess=$(( total - KEEP ))
  local i
  if (( excess > 0 )); then
    for (( i = 0; i < excess; i++ )); do
      if (( DRY_RUN )); then
        log INFO "[dry-run] було б видалено старий архів: ${files[i]}"
      else
        rm -f -- "${files[i]}" "${files[i]}.sha256"
        log INFO "Видалено старий архів: ${files[i]}"
      fi
    done
  else
    log INFO "Ротація не потрібна: архівів $total із дозволених $KEEP"
  fi
}

log INFO "Старт: джерело=$SOURCE призначення=$DEST зберігати=$KEEP dry-run=$DRY_RUN"

if (( DRY_RUN )); then
  log INFO "[dry-run] було б створено архів: $ARCHIVE_PATH"
  for pattern in ${EXCLUDES[@]+"${EXCLUDES[@]}"}; do
    log INFO "[dry-run] було б виключено за шаблоном: $pattern"
  done
  rotate
  log INFO "Завершено (dry-run), нічого не змінено"
  exit "$EX_OK"
fi

if [[ -e "$ARCHIVE_PATH" ]]; then
  die "$EX_ARCHIVE" "Архів уже існує: $ARCHIVE_PATH (зачекайте секунду й повторіть)"
fi

tar_args=(-czf "$TMP_PATH")
for pattern in ${EXCLUDES[@]+"${EXCLUDES[@]}"}; do
  tar_args+=(--exclude="$pattern")
done
tar_args+=(-C "$(dirname "$SOURCE")" "$NAME")

if ! tar_err="$(tar "${tar_args[@]}" 2>&1)"; then
  die "$EX_ARCHIVE" "tar не зміг створити архів: $tar_err"
fi

if ! tar -tzf "$TMP_PATH" > /dev/null 2>&1; then
  die "$EX_VERIFY" "Створений архів пошкоджений (tar -t завершився помилкою)"
fi

mv -- "$TMP_PATH" "$ARCHIVE_PATH"
if ! ( cd "$DEST" && sha256sum "$ARCHIVE_NAME" > "$ARCHIVE_NAME.sha256" \
       && sha256sum -c "$ARCHIVE_NAME.sha256" > /dev/null 2>&1 ); then
  die "$EX_VERIFY" "Не вдалося записати або підтвердити контрольну суму для $ARCHIVE_NAME"
fi

SIZE="$(du -h "$ARCHIVE_PATH" | cut -f1)"
log INFO "Архів створено й перевірено: $ARCHIVE_PATH ($SIZE)"

rotate
log INFO "Завершено успішно"
exit "$EX_OK"
