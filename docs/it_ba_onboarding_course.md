# IT Business Analyst: onboarding и рабочая библиотека

## Цель курса

Курс предназначен для нового IT бизнес-аналитика и одновременно работает как библиотека для повторения. Он формирует связный рабочий маршрут: бизнес-потребность → сбор требований → формализация постановки → процессы и данные → интеграционные контракты → SQL-проверки → итоговый кейс.

В качестве единого контекста используется банковский кейс подбора карточного продукта и тарифного предложения с ограничениями по клиенту. Это позволяет повторять одинаковые сущности и правила в requirements, API и SQL, а не изучать темы изолированно.

## Принципы дизайна

1. **От смысла к реализации.** Каждая техническая тема начинается с бизнес-вопроса, который она помогает решить.
2. **Повторяемость.** Learning activities можно проходить многократно без расходования попыток assessment.
3. **Практика в текущем функционале проекта.** Используются `html_content`, `drag_mapping`, `drag_order`, `practice_quiz` и `sql_practice`; в assessment — `quiz`, `sql_task` и `open_answer`.
4. **SQL только для чтения.** Обучающий scope включает `SELECT`, `WHERE`, сортировку, агрегаты, `GROUP BY`, `JOIN`, `IN`, `EXISTS`, `NOT EXISTS`; создание, изменение и удаление объектов БД не является целью обучения.
5. **Один доменный dataset.** Таблицы `clients`, `products`, `price_levels`, `tariffs`, `client_cards`, `card_applications`, `restrictions` используются во всех SQL-упражнениях.

## Структура learning modules

| № | Модуль | Основные результаты | Activities |
|---|---|---|---|
| 1 | Роль IT BA и системное мышление | Need, stakeholder, value, scope, evidence; набор артефактов | reading, mapping, quiz |
| 2 | Сбор требований и discovery | Подготовка elicitation, вопросы, решения, assumptions, open questions | reading, ordering, quiz |
| 3 | Формализация и постановка требований | Levels of requirements, user story, rules, NFR, AC, traceability | reading, mapping, quiz |
| 4 | Процессы, данные и постановка для разработки | Process/rule/data/integration/test, data dictionary и mapping | reading, ordering, quiz |
| 5 | Микросервисы, REST и SOAP | Service responsibility, contract, HTTP errors, SOAP Fault, OpenAPI | reading, mapping, quiz |
| 6 | SQL SELECT: выборка, фильтры и агрегаты | SELECT/WHERE/ORDER BY/COUNT/GROUP BY | reading, 3 SQL practices, quiz |
| 7 | SQL JOIN: связи между данными | INNER/LEFT JOIN, ключи, кардинальность и дубли | reading, mapping, 3 SQL practices |
| 8 | SQL подзапросы и аналитические проверки | IN, EXISTS, NOT EXISTS, контроль исключений | reading, 3 SQL practices, quiz |
| 9 | Сквозной кейс: выдача карты и тарифное предложение | Rules, API, mapping, AC и UAT SQL checks в одном scenario | reading, ordering, SQL practice, quiz |

Итого learning-секция содержит **34 activities**, включая **10 SQL practical exercises**.

## Учебная SQL-модель

Модель повторяет вопросы, с которыми работает IT BA при проектировании карточных предложений:

| Таблица | Смысл |
|---|---|
| `clients` | Клиент, сегмент и страна для бизнес-правил |
| `products` | Доступные комбинации карточного продукта |
| `price_levels` | Standard/special предложения и eligible segment |
| `tariffs` | Стоимость продукта в price level |
| `client_cards` | Уже имеющиеся карты клиента |
| `card_applications` | Заявки на выпуск карт |
| `restrictions` | Ограничение доступного продукта по стране |

Все задания выполняются встроенным SQL runtime приложения по `SELECT`-запросам; schema initialization используется приложением только для подготовки учебного набора данных в браузере.

## Итоговый assessment: 180 минут

Assessment реализован одним timed-модулем с одной попыткой, проходным порогом 70% и таймером **180 минут**.

| Часть | Проверяемый навык | Тип activity | Время | Баллы |
|---|---|---|---:|---:|
| 1 | Concepts: BA, requirements, API, SQL | `quiz` | 20 мин | 20 |
| 2 | SELECT и агрегирование | `sql_task` | 25 мин | 15 |
| 3 | JOIN и выбор тарифа | `sql_task` | 30 мин | 15 |
| 4 | NOT EXISTS и поиск исключений | `sql_task` | 30 мин | 15 |
| 5 | Постановка требований и AC по кейсу | `open_answer` | 40 мин | 20 |
| 6 | REST contract, error handling и mapping | `open_answer` | 35 мин | 15 |
|  | **Всего** |  | **180 мин** | **100** |

### Рубрика ручной оценки открытых ответов

**Часть 5 — Requirements, 20 баллов**

| Критерий | Баллы |
|---|---:|
| Business goal и корректная user story | 3 |
| Scope и business rules, включая приоритет restriction | 4 |
| Минимум четыре проверяемых AC: normal, restricted, service failure, no offer | 8 |
| Источники данных / поля | 3 |
| Open questions и assumptions | 2 |

**Часть 6 — API contract и mapping, 15 баллов**

| Критерий | Баллы |
|---|---:|
| Корректные method/endpoint/parameters | 3 |
| Success JSON model и пример | 3 |
| Validation/service failure errors и correlation handling | 3 |
| Mapping response fields к источникам | 4 |
| Пояснение применения restriction | 2 |

## Текущее техническое ограничение assessment

Текущая версия приложения сохраняет `open_answer` как `pending_review`, однако ещё не содержит UI/API для выставления проверяющим баллов и пересчёта итогового результата попытки после ручной проверки. Поэтому:

- quiz и SQL tasks проверяются автоматически;
- развернутые BA/API ответы сохраняются для проверки;
- для полноценного финального score необходимо добавить функцию manual grading отдельной доработкой.

Это ограничение сознательно не обходится автоматическими «ключевыми словами», поскольку качество постановки требований и API-контракта должно оцениваться содержательно.

## Установка в текущий проект

В ветке курса добавлены:

- `seed_it_ba_course.py` — идемпотентное наполнение существующей `training.db`;
- `run_it_ba_course.py` — запуск приложения с предварительной инициализацией и наполнением базы.

Для запуска из корня проекта:

```bash
python run_it_ba_course.py
```

Скрипт запуска сначала создаёт необходимые системные таблицы через существующую `init_db()`, затем добавляет/обновляет только записи с идентификаторами нового курса и запускает сервер. Пользовательские learning events и assessment attempts не удаляются.

## Использованные reference points

- IIBA, BABOK Guide overview — areas and vocabulary for business analysis and requirements work.
- OpenAPI Specification — formal description of HTTP API contracts.
- W3C SOAP Version 1.2 Part 1 — SOAP envelope/body/fault structure.
- SQLite documentation — `SELECT` and `WITH`/subquery material used for SQL practice scope.
