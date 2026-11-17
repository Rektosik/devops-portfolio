import argparse
import http.client
import json
import os
import socket
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlparse

EXIT_OK = 0             
EXIT_FAILED = 1         
EXIT_CONFIG_MISSING = 2  
EXIT_CONFIG_INVALID = 3  
EXIT_USAGE = 4           
EXIT_INTERNAL = 5       

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_monitor.log")


class ConfigError(Exception):
    """Помилка структури конфігурації."""


class Parser(argparse.ArgumentParser):
    """argparse за замовчуванням виходить із кодом 2, а він у нас означає «немає конфігу»."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: помилка: {message}\n")


def positive_number(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' не є числом")
    if value <= 0:
        raise argparse.ArgumentTypeError("значення має бути більше нуля")
    return value


def log_event(level, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] [{level}] {message}\n")
    except OSError as exc:
        print(f"Попередження: не вдалося записати журнал {LOG_FILE}: {exc.strerror}", file=sys.stderr)


def load_endpoints(config_path):
    """Читає й перевіряє конфігурацію. Повертає список ендпоінтів."""
    if not os.path.isfile(config_path):
        print(f"Помилка: файл конфігурації '{config_path}' не існує.", file=sys.stderr)
        log_event("ERROR", f"Файл конфігурації не знайдено: {config_path}")
        sys.exit(EXIT_CONFIG_MISSING)

    try:
        with open(config_path, "r", encoding="utf-8") as cfg_file:
            data = json.load(cfg_file)
    except json.JSONDecodeError as exc:
        print(f"Помилка: некоректний JSON у файлі '{config_path}': {exc}", file=sys.stderr)
        log_event("ERROR", f"Помилка парсингу JSON: {exc}")
        sys.exit(EXIT_CONFIG_INVALID)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Помилка: не вдалося прочитати '{config_path}': {exc}", file=sys.stderr)
        log_event("ERROR", f"Не вдалося прочитати конфіг: {exc}")
        sys.exit(EXIT_CONFIG_MISSING)

    try:
        if not isinstance(data, dict):
            raise ConfigError("корінь конфігурації має бути JSON-обʼєктом")
        endpoints = data.get("endpoints")
        if not isinstance(endpoints, list) or not endpoints:
            raise ConfigError("поле 'endpoints' має бути непорожнім списком")
        for number, item in enumerate(endpoints, start=1):
            if not isinstance(item, dict):
                raise ConfigError(f"endpoints[{number}] має бути обʼєктом")
            url = item.get("url")
            parsed = urlparse(url) if isinstance(url, str) else None
            if parsed is None or parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ConfigError(f"endpoints[{number}]: 'url' має починатися з http:// або https://")
            expected = item.get("expected_status", 200)
            if isinstance(expected, bool) or not isinstance(expected, int) or not 100 <= expected <= 599:
                raise ConfigError(f"endpoints[{number}]: 'expected_status' має бути числом 100-599")
    except ConfigError as exc:
        print(f"Помилка конфігурації '{config_path}': {exc}", file=sys.stderr)
        log_event("ERROR", f"Некоректна конфігурація: {exc}")
        sys.exit(EXIT_CONFIG_INVALID)
    return endpoints


def check_endpoints(config_path, timeout):
    endpoints = load_endpoints(config_path)
    log_event("INFO", f"Старт перевірки: конфіг={config_path}, ендпоінтів={len(endpoints)}, таймаут={timeout:g} с")

    any_failed = False

    for item in endpoints:
        name = item.get("name", "Unnamed")
        url = item["url"]
        expected = item.get("expected_status", 200)

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
        except (TimeoutError, socket.timeout):
            print(f"[ERROR] {name} ({url}) — таймаут {timeout:g} с", file=sys.stderr)
            log_event("ERROR", f"{name} ({url}) -> таймаут {timeout:g} с")
            any_failed = True
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                reason = f"таймаут {timeout:g} с"
            else:
                reason = f"помилка мережі: {exc.reason}"
            print(f"[ERROR] {name} ({url}) — {reason}", file=sys.stderr)
            log_event("ERROR", f"{name} ({url}) -> {reason}")
            any_failed = True
        except (http.client.HTTPException, OSError) as exc:
            print(f"[ERROR] {name} ({url}) — помилка мережі: {exc}", file=sys.stderr)
            log_event("ERROR", f"{name} ({url}) -> помилка мережі: {exc}")
            any_failed = True

    log_event("INFO", "Перевірку завершено: " + ("є помилки" if any_failed else "усе гаразд"))
    return EXIT_FAILED if any_failed else EXIT_OK


def main():
    parser = Parser(
        description="Монітор доступності HTTP/API ендпоінтів.",
        epilog=(
            "Коди виходу:\n"
            "  0  усі ендпоінти відповіли як очікувалось\n"
            "  1  є недоступні ендпоінти або неочікуваний статус\n"
            "  2  файл конфігурації не існує або не читається\n"
            "  3  некоректний JSON або структура конфігурації\n"
            "  4  неправильні параметри командного рядка\n"
            "  5  внутрішня помилка\n\n"
            "Приклад: python3 api_monitor.py --config config/monitor.json --timeout 5"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", required=True, help="Шлях до JSON-файлу конфігурації")
    parser.add_argument("--timeout", type=positive_number, default=5,
                        help="Таймаут запиту в секундах (за замовчуванням: 5)")

    args = parser.parse_args()
    try:
        exit_code = check_endpoints(args.config, args.timeout)
    except KeyboardInterrupt:
        log_event("WARNING", "Перервано користувачем")
        sys.exit(130)
    except Exception as exc:  # остання лінія захисту: без трасування стека для користувача
        print(f"Внутрішня помилка: {type(exc).__name__}: {exc}", file=sys.stderr)
        log_event("ERROR", f"Внутрішня помилка: {type(exc).__name__}: {exc}")
        if os.environ.get("DEBUG"):
            traceback.print_exc()
        exit_code = EXIT_INTERNAL
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
