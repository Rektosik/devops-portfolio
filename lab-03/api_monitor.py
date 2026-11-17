import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

LOG_FILE = "api_monitor.log"


def log_event(level, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] [{level}] {message}\n")


def check_endpoints(config_path, timeout):
    if not os.path.isfile(config_path):
        print(f"Помилка: файл конфігурації '{config_path}' не існує.", file=sys.stderr)
        log_event("ERROR", f"Файл конфігурації не знайдено: {config_path}")
        return 2

    try:
        with open(config_path, "r", encoding="utf-8") as cfg_file:
            data = json.load(cfg_file)
    except json.JSONDecodeError as exc:
        print(f"Помилка: некоректний JSON у файлі '{config_path}': {exc}", file=sys.stderr)
        log_event("ERROR", f"Помилка парсингу JSON: {exc}")
        return 3

    endpoints = data.get("endpoints", [])
    if not endpoints:
        print("Попередження: у конфігурації не вказано жодного ендпоінта.", file=sys.stderr)
        log_event("WARNING", "Порожній список ендпоінтів.")
        return 0

    any_failed = False

    for item in endpoints:
        name = item.get("name", "Unnamed")
        url = item.get("url")
        expected = item.get("expected_status", 200)

        if not url:
            continue

        start_time = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "DevOps-APIMonitor/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                status = response.getcode()
                if status == expected:
                    print(f"[OK] {name} ({url}) — {status} ({elapsed_ms} ms)")
                    log_event("SUCCESS", f"{name} ({url}) -> {status} ({elapsed_ms} ms)")
                else:
                    print(f"[FAIL] {name} ({url}) — очікувався {expected}, отримано {status}", file=sys.stderr)
                    log_event("FAILURE", f"{name} ({url}) -> статус {status} замість {expected}")
                    any_failed = True
        except urllib.error.HTTPError as exc:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            if exc.code == expected:
                print(f"[OK] {name} ({url}) — {exc.code} ({elapsed_ms} ms)")
                log_event("SUCCESS", f"{name} ({url}) -> {exc.code} ({elapsed_ms} ms)")
            else:
                print(f"[FAIL] {name} ({url}) — HTTP {exc.code}", file=sys.stderr)
                log_event("FAILURE", f"{name} ({url}) -> HTTP {exc.code}")
                any_failed = True
        except urllib.error.URLError as exc:
            print(f"[ERROR] {name} ({url}) — помилка мережі: {exc.reason}", file=sys.stderr)
            log_event("ERROR", f"{name} ({url}) -> помилка мережі: {exc.reason}")
            any_failed = True

    return 1 if any_failed else 0


def main():
    parser = argparse.ArgumentParser(
        description="Монітор доступності HTTP/API ендпоінтів.",
        epilog="Приклад: python3 api_monitor.py --config config/monitor.json --timeout 5"
    )
    parser.add_argument("--config", required=True, help="Шлях до JSON-файлу конфігурації")
    parser.add_argument("--timeout", type=int, default=5, help="Таймаут запиту в секундах (за замовчуванням: 5)")

    args = parser.parse_args()
    exit_code = check_endpoints(args.config, args.timeout)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
