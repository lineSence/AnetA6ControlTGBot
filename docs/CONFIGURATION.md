# Конфигурация

Файл: `/root/tgbot/config.yaml`. Права: `0600`. Образец: `config.example.yaml`.
Сервис читает конфиг при старте. После правки: `systemctl restart tg2printer`.

Текущая схема — `config_version: 6`. Конфиг старой версии обновляется сам при старте.

## Обязательные поля

| Поле | Тип | Смысл |
| --- | --- | --- |
| `bot_token` | строка | Токен от @BotFather. Можно передать через переменную `TG_BOT_TOKEN` |
| `allowed_user_ids` | список чисел | Кто может управлять принтером. Пустой список = доступа нет ни у кого |
| `notify_chat` | число | Куда слать автоматические уведомления. Если не задано, берётся наименьший id из `allowed_user_ids` |

## Подключения и пути

| Поле | По умолчанию | Смысл |
| --- | --- | --- |
| `moonraker` | `http://127.0.0.1:7125` | Адрес Moonraker (REST и WebSocket) |
| `moonraker_api_key` | `""` | API-ключ Moonraker. Уходит в заголовке `X-Api-Key`. Можно задать через `MOONRAKER_API_KEY` |
| `http_timeout` | `15` | Таймаут обычных запросов к Moonraker в секундах. Минимум 5 |
| `upload_timeout` | `300` | Таймаут загрузки G-code в секундах. Минимум 30 |
| `camera_url` | `http://127.0.0.1:8080/?action=snapshot` | Кадр с камеры для `/photo` |
| `config_dir` | `/root/printer_data/config` | Каталог конфигов Klipper |
| `converter` | `/root/gif2klipper.py` | Путь к конвертеру |
| `python` | `/opt/tgbot/bin/python` | Интерпретатор для конвертера. Только venv, там есть Pillow (аудит P0-2) |
| `backup_dir` | `/root/tgbot/backups/animations` | Снимки каталога анимаций |
| `error_db` | `/root/tgbot/errors.db` | Журнал ошибок |
| `preview_base` | `/tmp/tg_preview` | Префикс файлов превью. Предпросмотр пишет их в свой временный каталог и удаляет после отправки |
| `log_file` | `/var/log/tg2printer.log` | Файл лога |
| `log_level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Путь к самому конфигу можно задать переменной `TG_CONFIG`.

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
| `animation_ffmpeg_timeout` | `45` | Таймаут ffmpeg в секундах. Общий лимит конвертации считается как `timeout * 2 + 30` |

Входной файл принимается как анимация, видео, фото или документ. Лимит размера — 20 МБ, список расширений задан в `anims.SUPPORTED_MEDIA_EXT`.

## Безопасность и уведомления

| Поле | По умолчанию | Смысл |
| --- | --- | --- |
| `power_actions_require_confirmation` | `true` | Перезагрузка и выключение требуют второго нажатия |
| `dangerous_macros_require_confirmation` | `true` | Опасные макросы требуют подтверждения |
| `quiet_hours` | `{start: 23, end: 7}` | Тихие часы по локальному времени. При `start == end` выключены |
| `critical_alerts_ignore_quiet_hours` | `true` | Аварийные сообщения (остановка Klipper, обрыв связи) проходят и в тихие часы |
| `pending_ttl_seconds` | `900` | Срок жизни подтверждения кнопки. Минимум 60 |
| `session_ttl_seconds` | `3600` | Срок жизни сессии анимации и блокировок. Минимум 300 |
| `error_log_keep` | `500` | Сколько последних ошибок хранить в `errors.db`. Минимум 50 |
| `max_error_log_lines` | `180` | Сколько строк `klippy.log` разбирать |

Опасные макросы проверяются по границам слова: токены `G28`, `SAVE_CONFIG`, `FIRMWARE_RESTART`, `RESTART`, `M112`, `SHUTDOWN` и префиксы `PROBE_`, `BED_MESH`, `PID_CALIBRATE`, `TESTZ`.
Поэтому макрос вида `PRESTART_CHECK` больше не считается опасным (аудит, раздел P2).

Подтверждения кнопок живут в памяти с TTL, а фоновая задача чистит просроченные записи каждые 300 секунд.

## Версии и миграции

`config_version` сейчас `6`. При старте `_migrate()` добавляет новые поля со значениями по умолчанию, подменяет старые пути и переводит `python: python3` на `/opt/tgbot/bin/python`.
Свой путь к интерпретатору, если он отличается от `python3`, сохраняется без изменений.

Переменные окружения установщика: `TG_BOT_TOKEN`, `TG_CHAT_ID`, `SKIP_TESTS`, `NO_SERVICE`, `UPGRADE_KEEP`, `BASE`, `VENV`, `CONFIG_DIR`, `CONVERTER`, `SERVICE`.
Переменные окружения бота: `TG_CONFIG`, `TG_BOT_TOKEN`, `MOONRAKER_API_KEY`.
