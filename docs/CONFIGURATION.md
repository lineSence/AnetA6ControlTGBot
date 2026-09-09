# Конфигурация

Файл: `/root/tgbot/config.yaml`. Права: 0600. Образец: `config.example.yaml`.
Сервис читает конфиг при старте. После правки: `systemctl restart tg2printer`.

## Обязательные поля

| Поле | Тип | Смысл |
| --- | --- | --- |
| `bot_token` | строка | Токен от @BotFather |
| `allowed_user_ids` | список чисел | Кто может управлять принтером. Пустой список = доступа нет ни у кого |
| `notify_chat` | число | Куда слать автоматические уведомления |

## Подключения и пути

| Поле | По умолчанию | Смысл |
| --- | --- | --- |
| `moonraker` | `http://127.0.0.1:7125` | Адрес Moonraker (REST и WebSocket) |
| `camera_url` | `http://127.0.0.1:8080/?action=snapshot` | Кадр с камеры для `/photo` |
| `config_dir` | `/root/printer_data/config` | Каталог конфигов Klipper |
| `converter` | `/root/gif2klipper.py` | Путь к конвертеру |
| `python` | `/opt/tgbot/bin/python` | Интерпретатор для конвертера. Важно: только venv, там есть Pillow (аудит P0-2) |
| `backup_dir` | `/root/tgbot/backups/animations` | Снимки каталога анимаций |
| `error_db` | `/root/tgbot/errors.db` | Журнал ошибок |
| `preview_base` | `/tmp/tg_preview` | Префикс файлов превью (в 2.5.7 не используется) |
| `log_file` | `/var/log/tg2printer.log` | Файл лога |
| `log_level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Управление движением

| Поле | По умолчанию | Смысл |
| --- | --- | --- |
| `xy_steps` | `[1, 5, 10, 50]` | Шаги XY в мм для кнопок |
| `z_steps` | `[0.1, 0.5, 1, 5]` | Шаги Z в мм |
| `move_default` | `{xy: 10, z: 1}` | Шаг при старте сессии |

## Анимации

| Поле | По умолчанию | Смысл |
| --- | --- | --- |
| `max_glyphs` | `250` | Лимит уникальных глифов на анимацию |
| `fits` | `[contain, cover, stretch]` | Доступные режимы масштаба |
| `frames` | `[5, 10, 15, 20]` | Варианты числа кадров в мастере |
| `animation_backup_keep` | `5` | Сколько снимков хранить |
| `animation_max_input_frames` | `500` | Лимит кадров во входном файле |
| `animation_max_input_pixels` | `12000000` | Лимит пикселей на кадр |
| `animation_max_duration_ms` | `120000` | Лимит длительности анимации |
| `animation_ffmpeg_timeout` | `45` | Таймаут ffmpeg в секундах |

## Безопасность и уведомления

| Поле | По умолчанию | Смысл |
| --- | --- | --- |
| `power_actions_require_confirmation` | `true` | Перезагрузка/выключение требуют второго нажатия |
| `dangerous_macros_require_confirmation` | `true` | Опасные макросы требуют подтверждения |
| `quiet_hours` | `{start: 23, end: 7}` | Тихие часы по локальному времени. При `start == end` выключены |
| `max_error_log_lines` | `180` | Сколько строк klippy.log разбирать |

Список опасных слов в коде (`DANGEROUS_MACRO_WORDS`): `G28`, `SAVE_CONFIG`, `FIRMWARE_RESTART`, `RESTART`, `M112`, `BED_MESH`, `PID_CALIBRATE`, `PROBE_`, `TESTZ`, `SHUTDOWN`.
Проверка идёт по подстроке, поэтому возможны ложные срабатывания. См. аудит, раздел P2.

## Версии и миграции

`config_version` сейчас `5`. При старте `_migrate()` добавляет новые поля со значениями по умолчанию и подменяет старые пути (например, `backup_dir` из `/tmp`).
В плане — версия `6`: автоматическая замена `python: python3` на `/opt/tgbot/bin/python`.

Переменные окружения для установщика: `TG_BOT_TOKEN`, `TG_CHAT_ID`, `SKIP_TESTS`, `NO_SERVICE`, `BASE`, `VENV`, `CONFIG_DIR`, `LOG_FILE`.
