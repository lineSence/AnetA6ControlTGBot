# Аудит кода tgbot 2.5.7

Дата: 9 сентября 2026.
Объект: `install_tgbot_v2.5.7.sh` — самораспаковывающийся установщик (131 960 байт, 3437 строк), внутри 21 встроенный файл.
Объём кода: 2968 строк Python в 11 модулях + конвертер 872 строки + 212 строк тестов.

## 1. Резюме

Архитектура проекта зрелая: есть разделение на слои, валидация конфига, миграции, бекапы, откат, тесты, CI и усиленный systemd-юнит.

Но версию 2.5.7 ставить нельзя: три блокера ломают главную функцию (анимации) и текут ресурсы.

| Класс | Сколько | Суть |
| --- | --- | --- |
| P0 — блокеры | 3 | Падение на любом медиа, нерабочий конвертер, утечка дескрипторов |
| P1 — важные | 8 | Падение сервиса, неполный откат, таймаут загрузки, потеря аварийных уведомлений |
| P2 — мелкие | 25+ | Рассинхрон версий, рост памяти, слабые валидации, UX-ошибки |
| T — тесты/CI | 3 | Тесты не ловят блокеры, жёсткие пути, CI без PYTHONPATH |

## 2. Метод проверки

1. Распаковка всех 21 встроенных файлов из heredoc-блоков установщика.
2. Ручное чтение всего кода (bash + Python).
3. AST-анализ: проверка синтаксиса и сверка кросс-ссылок `модуль.функция` с реальными определениями.
4. Запуск модулей с заглушками `aiogram` и `aiohttp` — проверка блокеров на практике.

Вывод AST-анализа:

```
SYNTAX OK
PROBLEM: handlers.py:180: anims.start_session -> NOT DEFINED in anims.py
```

Вывод рунтайм-проверки:

```
anims has start_session: False
handlers.on_media calls anims.start_session -> AttributeError at runtime: True
open fds before: 4 after 40 register() calls: 83 delta: 79
db file mode: 0o600
```

## 3. Блокеры (P0)

### P0-1. Вызов несуществующей функции — падение на любом медиа

Файл: `tgbot/handlers.py`, строка 180 (в установщике — строка 2505).

```python
await anims.start_session(bot, cfg, msg, msg.chat.id, SESSIONS)
```

Функции `start_session` в `tgbot/anims.py` нет. Модуль анимаций экспортирует `apply_session`, `convert_params`, `save_index`, `num_to_prefix` и др., но не точку входа сессии.

Последствие: `AttributeError` на любом GIF, фото или документе. Главная функция бота не работает вообще.

Почему не поймали раньше: `scripts/check.sh` делает только `import`, а тесты не вызывают `on_media`.

Фикс: реализовать `anims.start_session(bot, cfg, msg, chat_id, sessions)` — скачать файл, создать запись в `SESSIONS`, показать мастер настроек. Или вернуть вызов на существующий обработчик.
Плюс тест: `assert hasattr(anims, "start_session")` и вызов `on_media` с фейковым `Message`.

### P0-2. Конвертер запускается чужим интерпретатором

Файл: `config.yaml.template`.

```yaml
python: "python3"
converter: "/root/gif2klipper.py"
```

Pillow ставится только в venv `/opt/tgbot`. Системный `python3` его не видит, а `gif2klipper.py` на старте делает:

```python
raise RuntimeError("Нужен Pillow. Установите зависимости бота из requirements.lock")
```

Последствие: конвертация падает даже после фикса P0-1.

Фикс: писать `python: "/opt/tgbot/bin/python"` и добавить миграцию `config_version: 6`, которая заменяет `python3` на путь venv. В `config.example.yaml` это уже сделано.

### P0-3. Утечка соединений SQLite

Файл: `tgbot/errorlog.py`, строки 41, 60, 68.

```python
with _connect(cfg.error_db) as db:
    ...
```

`sqlite3.Connection` как контекстный менеджер управляет только транзакцией. Соединение не закрывается.

Измерено: 40 вызовов `register()` дают +79 открытых дескрипторов. Сервис живёт месяцами — дойдёт до `Too many open files`.

Фикс:

```python
from contextlib import closing

with closing(_connect(cfg.error_db)) as db, db:
    ...
```

## 4. Важные замечания (P1)

| № | Место | Проблема | Фикс |
| --- | --- | --- | --- |
| P1-1 | systemd-юнит | `ReadWritePaths=... /var/log/tg2printer.log` — файла лога нет при первой установке, сервис не стартует | Префикс `-` или создавать файл до старта (сделано в `systemd/tg2printer.service`) |
| P1-2 | установщик, стр. 38 и 112 | `fail()` вызывает `exit 1` и минует `trap on_err ERR` — отката нет | Вызывать `on_err` явно или добавить `trap ... EXIT` |
| P1-3 | установщик, стр. 26 | `set -euo pipefail` без `set -E` — ERR не наследуется в функциях | Добавить `set -E` |
| P1-4 | установщик, `rollback` | Откат восстанавливает код и конфиг, но не venv | Бекапить `requirements.lock` и пересобирать venv при откате |
| P1-5 | `printer.py:59`, `printer.py:119` | Единый `ClientTimeout(total=15)` применяется и к `upload_gcode` — большой G-code не успевает | Отдельный таймаут для загрузки (`sock_read`, 300 с) |
| P1-6 | `handlers.py`, `/error <id>` | Отправляет `details[-6000:]` при лимите Telegram 4096 | Резать до 3500 или шлать файлом |
| P1-7 | `handlers.py`, `is_quiet` | Тихие часы глушат аварийные уведомления (shutdown, error) | Ввести уровни: critical игнорирует тишину |
| P1-8 | systemd-юнит | `Restart=always` + `RestartSec=5` без `StartLimitIntervalSec`/`StartLimitBurst` — бесконечный цикл при битом токене | Добавить лимит стартов |

## 5. Мелкие замечания (P2)

### Версии и установка

- Три версии в одном артефакте: имя файла 2.5.7, маркер `# tgbot animation include (v2.5.5)`, `__init__.py` с `__version__ = "2.5.0"`.
- `cp -f "$0"` ломается при запуске через `curl | bash` (`$0` = `bash`).
- Каталоги `backups/upgrade_*` никогда не чистятся.
- Парсинг токена через `grep -m1 '^bot_token:' | sed` — ломается на YAML-комментариях и отступах.
- `[[ "$CHAT" =~ ^[0-9-]+$ ]]` пропускает мусор вида `1-2-3`.
- `printf ... >> "$PRINTER_CFG"` (стр. 3376) пишет в `printer.cfg` без проверки синтаксиса Klipper.

### Состояние и память

- Словари `PENDING`, `SESSIONS`, `RENAMING`, `MOVE_BY_CHAT`, `LOCKS` без TTL и лимита — растут бесконечно.
- `read_klippy_tail` читает лог через `readlines()` — весь файл в памяти.
- `errors.db` без ретенции; каждая запись до 20 000 символов (`details[-20000:]`).
- WS-реконнект с фиксированной паузой `await asyncio.sleep(3)` — нет backoff.

### Логика и безопасность

- `p:do:<action>` выполняется без записи в `PENDING` — подтверждение можно обойти старой кнопкой.
- `mc:run:` не сверяет `pending["index"]` и `pending["digest"]` — риск запуска другого макроса.
- `DANGEROUS_MACRO_WORDS` ищет подстроку: `RESTART` ловит `MY_RESTART_HELPER`, а `G28X` может пройти.
- Нет проверки `msg.from_user is None` — возможен `AttributeError` в каналах.
- Нет поддержки API-ключа Moonraker и HTTPS — только `http://127.0.0.1:7125`.
- `is_quiet` считает по локальному времени; при `start == end` тишина полностью выключена.
- `restart_and_wait` вызывается после `require_idle` — между проверкой и действием возможна гонка.

### Анимации и UI

- `rollback_anims` чистит каталог целиком — удалит файлы, добавленные вручную.
- `snapshot_anims` использует `mkdir(exist_ok=False)` — падает при двух операциях в одну секунду.
- Видео принимается только как документ `.gif`, хотя конвертер умеет `mp4/webm/mov` через ffmpeg.
- `preview_base` и `clean_preview` объявлены, но не используются — превью остаются в `/tmp`.
- `convert_params` ждёт 120 с, а `animation_ffmpeg_timeout` = 45 с — таймауты не согласованы.
- `ui.py:cycle` вызывает `lst.index(cur)` без защиты — `ValueError`, если текущего значения нет в списке.
- `tune_kb` шлёт `u:noop`, а обработчик ждёт `m:noop` — кнопка-заглушка висит с часами ожидания.

## 6. Тесты и CI

| № | Проблема | Статус в репозитории |
| --- | --- | --- |
| T-1 | Тесты не вызывают обработчики, поэтому P0-1 остался незамеченным | Нужен новый тест (в плане) |
| T-2 | `python -m pytest tests -q` без `PYTHONPATH` не видит пакет `tgbot` | Исправлено в `ci/ci.yml` (`PYTHONPATH: .`) |
| T-3 | `tests/test_converter.py` грузит конвертер строго из `/root/gif2klipper.py` | Обход в CI; в плане — перевести на переменную окружения |

Покрытие сейчас: миграции конфига, форматтеры, парсеры Moonraker, класс `Safety`, откат анимации, конвертер. Не покрыты: обработчики Telegram, WebSocket, журнал ошибок.

## 7. Безопасность

Сделано хорошо:

- Белый список `allowed_user_ids` проверяется в одной точке.
- `config.yaml` и `errors.db` создаются с правами `0600` (проверено).
- Опасные макросы и питание требуют подтверждения.
- systemd-юнит закрывает систему (`ProtectSystem=strict`, `RestrictAddressFamilies`, `NoNewPrivileges`).
- Конвертер ограничивает вход по кадрам, пикселям, длительности и времени ffmpeg.
- Префиксы анимаций валидируются, есть список зарезервированных имён.

Требует внимания:

- Бот работает под `root`. Нужен отдельный пользователь.
- Токен бота передаётся аргументом/переменной и может попасть в `~/.bash_history`.
- Нет аутентификации к Moonraker.
- Запись в `printer.cfg` без проверки синтаксиса.

## 8. Сильные стороны

- Чёткое разделение: транспорт, безопасность, UI, анимации, журнал ошибок.
- Версионированный конфиг с миграциями и валидацией.
- Атомарная замена файлов (`.tmp` + `replace`) и откат анимаций с бекапом.
- Конвертер автоматически подбирает бинаризацию (Otsu, adaptive, edges + инверсии) и оценивает результат.
- Генерируемый Klipper-конфиг проверяется до записи (`validate_config_text`).
- Библиотечный код никогда не зовёт `sys.exit`, все ошибки — свои исключения.

## 9. План устранения

Шаг 1 (обязательно до установки):

1. Реализовать `anims.start_session` (P0-1).
2. Поставить `python: /opt/tgbot/bin/python` и миграцию до `config_version: 6` (P0-2).
3. Закрыть соединения SQLite через `closing()` (P0-3).
4. Добавить тест на `on_media` и проверку числа дескрипторов.

Шаг 2 (стабильность): P1-1 … P1-8.

Шаг 3 (гигиена): единая версия, TTL для словарей, ретенция `errors.db`, backoff для WS, API-ключ Moonraker, точная проверка опасных макросов.

## 10. Команды проверки

```bash
# синтаксис и импорты
PYTHONPATH=. python -m compileall -q tgbot

# тесты
PYTHONPATH=. python -m pytest tests -q

# проверка P0-1 без установки aiogram
python - <<'PY'
import ast, pathlib
tree = ast.parse(pathlib.Path("tgbot/anims.py").read_text())
names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
print("start_session defined:", "start_session" in names)
PY

# утечка дескрипторов
ls -1 /proc/$(pgrep -f 'tgbot.main' | head -1)/fd | wc -l
```

## 11. Приложение: карта артефактов в установщике

| Файл | Строки в `install_tgbot_v2.5.7.sh` |
| --- | --- |
| `gif2klipper.py` | 259–1130 |
| `tgbot/config.py` | 1140–1317 |
| `tgbot/printer.py` | 1346–1487 |
| `tgbot/errorlog.py` | 1491–1578 |
| `tgbot/safety.py` | 1582–1653 |
| `tgbot/ui.py` | 1657–1799 |
| `tgbot/anims.py` | 1803–2080 |
| `tgbot/ws.py` | 2084–2322 |
| `tgbot/handlers.py` | 2326–3002 |
| `tgbot/main.py`, `logsetup.py`, `__init__.py` | 3006–3049 |
| `tests/*` | 3061–3278 |
| `check.sh`, CI, `pyproject.toml` | 3283–3417 |
