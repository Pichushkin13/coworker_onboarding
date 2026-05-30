# Training Library (Apps Script -> API + SQLite)

Локальный перенос Google Apps Script проекта в обычное веб-приложение:
- База: SQLite (`training.db`)
- API: Python HTTP server (`/api/*`)
- Frontend: статический SPA (`/`)

## Запуск

### macOS

```bash
cd /path/to/coworker_onboarding
python3 app.py
```

### Windows

В PowerShell или Command Prompt:

```powershell
cd C:\path\to\coworker_onboarding
py app.py
```

Если команда `py` не найдена, попробуйте:

```powershell
python app.py
```

Открыть: `http://127.0.0.1:8000`

## Примечания

- Пользователь определяется по заголовку `X-User-Email` (если не задан, используется `demo.user@example.com`).
- Админ по умолчанию: `admin / admin`.
- Логика попыток, таймера, soft-delete и сортировки сохранена по аналогии с исходным Google Script.
