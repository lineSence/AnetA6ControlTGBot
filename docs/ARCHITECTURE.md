# Архитектура

## Общая схема

```
Telegram (aiogram 3)
        |
        v
  tgbot/handlers.py  <-- команды, кнопки, медиа
        |  \
        |   \--> tgbot/safety.py     разрешить / запретить действие
        |   \--> tgbot/ui.py         клавиатуры и тексты
        |   \--> tgbot/errorlog.py   журнал ошибок (SQLite)
        v
  tgbot/printer.py   --HTTP-->  Moonraker REST
  tgbot/ws.py        --WS---->  Moonraker WebSocket (события)
        |
        v
     Klipper  ->  Anet A6 + дисплей ST7920 128x64

  tgbot/anims.py  --subprocess-->  gif2klipper.py  -->  anims/*.cfg
```

## Модули

| Модуль | Строк | Задача |
| --- | --- | --- |
| `tgbot/main.py` | 44 | Сборка приложения: конфиг, логи, `Bot`/`Dispatcher`, фоновые задачи |
| `tgbot/logsetup.py` | 22 | Файловое логирование с ротацией |
| `tgbot/config.py` | 178 | Загрузка YAML, миграции `config_version`, валидация, типизация |
| `tgbot/printer.py` | 142 | Клиент Moonraker REST: статус, G-code, файлы, загрузка, камера |
| `tgbot/ws.py` | 239 | WebSocket-подписка на события, живое сообщение статуса, `health_loop` |
| `tgbot/handlers.py` | 677 | Все команды и callback-и, подтверждения, приём медиа |
| `tgbot/safety.py` | 72 | Шлюзы: `require_idle`, `require_motion`, `require_power_action` |
| `tgbot/ui.py` | 143 | Клавиатуры, циклические переключатели, форматирование |
| `tgbot/anims.py` | 278 | Сессии анимаций, вызов конвертера, бекап, откат, индекс |
| `tgbot/errorlog.py` | 88 | SQLite-журнал ошибок, разбор хвоста klippy.log |
| `gif2klipper.py` | 872 | Автономный конвертер GIF/видео -> Klipper display |

## Асинхронные задачи

`main.py` запускает три параллельные задачи:

1. `dp.start_polling(bot)` — обработка апдейтов Telegram.
2. `MoonrakerWS.run()` — WebSocket с автопереподключением (пауза 3 с).
3. `health_loop()` — периодическая проверка состояния и уведомления.

## Состояние в памяти

Состояние живёт в модульных словарях `handlers.py`:

| Словарь | Содержит |
| --- | --- |
| `PENDING` | Ожидание подтверждения опасного действия |
| `SESSIONS` | Активные сессии конвертации анимаций |
| `RENAMING` | Ожидание нового имени анимации |
| `MOVE_BY_CHAT` | Шаг перемещения по чатам |
| `LOCKS` | Блокировки на чат против параллельных операций |

Состояние не переживает рестарт сервиса и не имеет TTL (см. аудит, раздел P2).

## Конвейер анимаций

```
пользователь прислал GIF
  -> handlers.on_media -> anims.start_session (НЕ РЕАЛИЗОВАНА, аудит P0-1)
  -> мастер настроек: fit / mode / frames / invert / fill
  -> anims.convert_params -> subprocess: python gif2klipper.py ... --json-result
  -> anims.snapshot_anims (бекап каталога anims/)
  -> атомарная запись anim_NNN.cfg + обновление index.json
  -> restart_and_wait (FIRMWARE_RESTART и ожидание ready)
  -> успех: PREFIX_START;  ошибка: rollback_anims -> возврат бекапа
```

Генерация в `gif2klipper.py`:

1. Чтение кадров (Pillow; видео — через ffmpeg в GIF).
2. Лимиты: кадры, пиксели, длительность, таймаут ffmpeg.
3. Масштаб до 128x64 (`contain` / `cover` / `stretch`).
4. Бинаризация: Otsu, adaptive (4 радиуса), edges, dither + инверсии; выбор по `candidate_score` (заливка, рамка, детали).
5. Нарезка на 32 глифа 16x16 (8 колонок x 4 строки), дедупликация в `GlyphBank`.
6. Опциональные `--fps` (пересэмплирование) и склейка одинаковых кадров.
7. Сборка секций Klipper и проверка результата.

Генерируемые секции:

- `[display_glyph <prefix>_gNNN]` — глифы 16x16.
- `[display_data <prefix>_f<i> r<r>]` — строки кадра.
- `[gcode_macro <PREFIX>_START]`, `[gcode_macro <PREFIX>_STOP]`.
- `[delayed_gcode <prefix>_t<i>]` — цепочка переключения кадров.
- `[delayed_gcode <prefix>_boot]` — только с `--autostart`.

## Шлюзы безопасности

| Метод | Когда разрешает |
| --- | --- |
| `require_idle` | Klippy `ready` и печать не идёт |
| `require_motion` | Нет активной печати (иначе ответ со словом «печать») |
| `require_power_action` | Перезагрузка/выключение вне печати; `restart` разрешён после `shutdown` |

Сверху есть два флага конфига: `power_actions_require_confirmation` и `dangerous_macros_require_confirmation`.

## Данные на диске

| Путь | Содержимое |
| --- | --- |
| `/root/tgbot/tgbot/` | Код пакета |
| `/root/tgbot/config.yaml` | Конфиг (права 0600) |
| `/root/tgbot/errors.db` | Журнал ошибок SQLite |
| `/root/tgbot/backups/animations/` | Снимки каталога анимаций |
| `/root/printer_data/config/anims/` | Сгенерированные `anim_NNN.cfg` и `index.json` |
| `/root/gif2klipper.py` | Конвертер |
| `/opt/tgbot/` | Virtualenv с зависимостями |
| `/var/log/tg2printer.log` | Лог бота |
