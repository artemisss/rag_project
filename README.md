# ReviewOps AI

Внутренний AI-сервис для брендов и customer care команд, который автоматически собирает новые отзывы с маркетплейсов, готовит качественные ответы в стиле бренда и помогает безопасно управлять публикацией через автоответ, ручную модерацию и эскалацию.

## Quick Start

Локальный запуск:

```bash
python3 -m pip install -r requirements.txt
python3 -m uvicorn run:app --reload
```

После запуска откройте:

```text
http://127.0.0.1:8000/welcome
```

Что уже реализовано в текущей версии репозитория:

- onboarding-gate и welcome flow первого запуска;
- хранение бренд-настроек и OpenAI-конфигурации;
- серверная админка без отдельной frontend-сборки;
- Brand Storage с policy/example/product fact/FAQ/forbidden phrase элементами;
- retrieval по SQLite FTS5;
- двухшаговый generation pipeline: classification + reply generation;
- история запусков, аудит и управление prompt version.

## Зачем нужен проект

Сервис решает три основные задачи:

- снижает ручную нагрузку на команду поддержки;
- ускоряет SLA ответа на отзывы;
- делает коммуникацию с клиентами единообразной, управляемой и проверяемой.

На вход система получает новые неотвеченные отзывы из Ozon, Wildberries и Яндекс.Маркета. На выходе команда получает:

- готовый черновик ответа;
- оценку риска и уверенности;
- решение policy engine: автоответ, ручная модерация или эскалация;
- аудит всех действий, версий промптов и публикаций.

## Продуктовое позиционирование

`ReviewOps AI` не является публичным клиентским продуктом. Это внутренняя операционная платформа для брендов, агентств и support-команд, работающих с отзывами на маркетплейсах.

Ключевая ценность:

- один контур работы с отзывами вместо трех разрозненных кабинетов;
- централизованные правила бренда и ограничения по коммуникации;
- объяснимый AI-пайплайн с retrieval, журналом решений и human-in-the-loop;
- возможность начать с SQLite и FastAPI, а затем без смены логики перейти на PostgreSQL, Redis и worker-процессы.

## Основные сценарии

### 1. Автоматическая обработка отзывов

1. Система синхронизирует новые отзывы с маркетплейсов.
2. Отзывы нормализуются в единую внутреннюю модель.
3. Retrieval достает релевантные policy-блоки и лучшие примеры ответов.
4. LLM классифицирует отзыв и генерирует структурированный черновик ответа.
5. Policy engine принимает решение:
   - опубликовать автоматически;
   - отправить на ручную модерацию;
   - эскалировать.
6. Результат публикуется обратно в marketplace API либо попадает в очередь модерации.

### 2. Ручная генерация ответа

Support-менеджер или контент-менеджер отправляет произвольный текст отзыва через админку или API `POST /api/v1/reviews/generate`, чтобы:

- быстро протестировать промпт;
- получить черновик без реального sync;
- использовать сервис как copilot для сложных кейсов.

### 3. Обучение системы на удачных ответах

После ручного approve качественные ответы можно повышать до статуса `golden example`, чтобы retrieval использовал их как ориентир для будущих генераций.

## Границы MVP

### В MVP входит

- FastAPI backend;
- SQLite как основная БД;
- SQLAlchemy ORM и миграции;
- APScheduler для фонового polling;
- OpenAI Responses API с моделью `gpt-5.4`;
- Structured Outputs для classification и generation;
- RAG на SQLite FTS5 + метафильтры;
- коннекторы для Wildberries и Яндекс.Маркета;
- Ozon через адаптерный слой без вымышленных endpoint-ов;
- админка с экранами очереди, знаний, правил, интеграций и аналитики.

### В MVP не входит

- полноценная multi-tenant billing-модель;
- внешняя очередь задач и распределенный worker-кластер;
- vector DB;
- автообучение или fine-tuning модели;
- сложная RBAC/SSO интеграция уровня enterprise.

## Как будет работать система

### Поток данных

```text
Marketplace APIs / Webhooks
        ->
Connectors
        ->
Normalization + Deduplication
        ->
Knowledge Retrieval
        ->
LLM Classification
        ->
LLM Reply Generation
        ->
Policy Engine
        ->
Auto publish / Moderation / Escalation
        ->
Audit + Metrics + Analytics
```

### Блок 1. Коннекторы маркетплейсов

Задачи:

- получать новые неотвеченные отзывы;
- приводить payload к унифицированной схеме;
- публиковать ответ обратно;
- обрабатывать лимиты, ошибки, retry и idempotency.

Поддерживаемые интеграции:

- `Wildberries`: polling каждые 2-5 минут, быстрая проверка количества неотвеченных, затем догрузка списка и публикация ответа;
- `Yandex Market`: основной режим webhook + fallback polling по `reactionStatus=NEED_REACTION`;
- `Ozon`: отдельный adapter layer, в котором реальные endpoint-ы и payload-ы закрываются после подтверждения актуального seller-доступа.

Принцип:

- никакие Ozon URL и поля не придумываются до верификации реальной документации кабинета.

### Блок 2. Ядро обработки

Внутри backend есть единый processing pipeline:

- ingest новых отзывов;
- `upsert` в БД;
- дедупликация по уникальному ключу;
- определение состояния отзыва;
- запуск retrieval и LLM;
- запись событий и результатов.

### Блок 3. LLM-слой

LLM работает не как свободный чат, а как строго управляемый сервис принятия решения.

Используется двухшаговая схема:

1. `classification`
   - intent;
   - topics;
   - sentiment;
   - risk_level;
   - needs_human;
   - response_strategy.
2. `generation`
   - reply_text;
   - tone;
   - confidence_score;
   - needs_human;
   - reason_codes;
   - used_knowledge_ids.

Обязательные принципы:

- structured outputs вместо парсинга свободного текста;
- `store: false`;
- минимизация PII перед отправкой в модель;
- versioning промптов и логирование каждого LLM run.

### Блок 4. База знаний

Хранилище знаний делится на два типа сущностей:

- `Policies`: обязательные правила, ограничения, запреты, инструкции;
- `Golden examples`: сильные живые ответы, одобренные командой.

Retrieval в MVP строится на:

- `FTS5` по тексту;
- фильтрах по `marketplace`, `brand_id`, `issue_type`, `rating_bucket`, `language`;
- приоритизации по `priority`, `is_active`, `outcome_quality`.

### Блок 5. Админка

Админка строится как операционная панель, а не как CRM.

Ключевые экраны:

- `Dashboard`;
- `Reviews`;
- `Moderation Queue`;
- `Knowledge Base`;
- `Auto-Reply Rules`;
- `Integrations`;
- `Prompts & Model`;
- `Analytics`;
- `Audit Log`;
- `Users & Roles`.

## Внутренняя модель данных

### UnifiedReview

```text
UnifiedReview
- id
- marketplace
- marketplace_account_id
- external_review_id
- external_parent_id
- shop_id / business_id
- product_sku
- product_name
- rating
- review_text
- pros
- cons
- author_name
- published_at
- raw_payload_json
- reaction_required
- reply_exists
- review_status
- risk_level
- confidence_score
- last_model_version
- last_prompt_version
- last_error
- updated_at
- created_at
```

Ключевой инвариант:

```text
UNIQUE(marketplace, marketplace_account_id, external_review_id)
```

Это защищает систему от дублей при повторном sync, повторных webhook-событиях и retry.

## Статусы отзыва

- `new` — отзыв только получен;
- `drafted` — черновик ответа создан;
- `approved` — ответ одобрен человеком или policy engine;
- `posted` — ответ успешно опубликован в marketplace;
- `skipped` — отзыв осознанно пропущен;
- `escalated` — кейс передан на ручной разбор;
- `error` — в обработке или публикации произошла ошибка.

## Правила принятия решения

### Автоответ допустим

- рейтинг `4-5`;
- нет тем `refund`, `defect`, `legal`, `safety`, `health`, `fraud`, `authenticity`;
- confidence выше порога;
- нет конфликта с policy;
- шаблон ответа безопасен для автоматической публикации.

### Только ручная модерация

- рейтинг `1-3`;
- есть брак, некомплект, гарантия, возврат или спор;
- отзыв токсичный или неоднозначный;
- нет текста или мало контекста;
- модель не уверена.

### Эскалация

- юридические риски;
- вред здоровью или безопасность;
- обвинение в мошенничестве, подделке, опасном товаре;
- приоритетный магазин или VIP-клиент;
- повторный негатив по тому же SKU;
- системный сбой, который не позволяет безопасно ответить.

## Архитектура MVP

### Технологический стек

- `FastAPI` — HTTP API и backend для админки;
- `SQLAlchemy` — ORM и работа с БД;
- `SQLite` — хранилище MVP;
- `Alembic` — миграции;
- `APScheduler` — планировщик sync jobs;
- `httpx` — HTTP-клиент для marketplace API;
- `Pydantic` / `pydantic-settings` — схемы и конфиг;
- `OpenAI Responses API` — классификация и генерация;
- `FTS5` — текстовый retrieval;
- `pytest` — тесты.

### Логическая схема модулей

```text
app/
  api/
    v1/
      integrations.py
      reviews.py
      knowledge.py
      prompts.py
      webhooks.py
  core/
    config.py
    logging.py
    security.py
  db/
    base.py
    session.py
    models/
  schemas/
  services/
    connectors/
      wb.py
      yandex_market.py
      ozon.py
    llm/
      client.py
      classifier.py
      generator.py
    knowledge/
      retrieval.py
    policy/
      engine.py
    reviews/
      pipeline.py
      publisher.py
  jobs/
    sync_marketplaces.py
  admin/
    frontend_or_templates/
tests/
docs/
```

### Почему именно такая архитектура

- модули отделяют доменную логику от интеграций;
- LLM-код можно менять независимо от HTTP-слоя;
- retrieval и policy engine остаются прозрачными и тестируемыми;
- коннекторы изолированы и могут развиваться с учетом различий API;
- SQLite не мешает позже перенести ту же модель в PostgreSQL.

## База данных

Минимальный набор таблиц:

### `marketplace_accounts`

Хранит подключенные кабинеты и параметры sync.

Поля:

- `id`
- `marketplace`
- `account_name`
- `credentials_json`
- `shop_id`
- `business_id`
- `is_active`
- `sync_interval_seconds`
- `last_sync_at`

### `reviews`

Центральная таблица с унифицированными отзывами.

### `review_replies`

Все версии ответов, как сгенерированных, так и ручных.

Поля:

- `id`
- `review_id`
- `source_type`
- `reply_text`
- `status`
- `posted_external_id`
- `created_by`
- `created_at`

### `review_events`

Полная история изменений статуса и внутренних решений.

### `knowledge_items`

База правил и golden examples.

Поля:

- `id`
- `item_type`
- `title`
- `body`
- `brand_id`
- `marketplace`
- `category`
- `issue_type`
- `rating_bucket`
- `language`
- `priority`
- `is_active`
- `created_at`

### `prompt_versions`

Версии system/classifier/generator prompt.

### `llm_runs`

Журнал всех вызовов модели.

### `audit_log`

Фиксирует действие пользователя или автоматической системы.

## HTTP API

### Интеграции

- `GET /api/v1/integrations`
- `POST /api/v1/integrations`
- `PATCH /api/v1/integrations/{id}`
- `POST /api/v1/integrations/{id}/sync-now`

### Отзывы

- `GET /api/v1/reviews`
- `GET /api/v1/reviews/{id}`
- `POST /api/v1/reviews/generate`
- `POST /api/v1/reviews/{id}/draft`
- `POST /api/v1/reviews/{id}/approve`
- `POST /api/v1/reviews/{id}/publish`
- `POST /api/v1/reviews/{id}/skip`
- `POST /api/v1/reviews/{id}/escalate`

### База знаний

- `GET /api/v1/knowledge`
- `POST /api/v1/knowledge/examples`
- `POST /api/v1/knowledge/policies`
- `PATCH /api/v1/knowledge/{id}`
- `POST /api/v1/knowledge/{id}/archive`

### Промпты и модель

- `GET /api/v1/prompts`
- `POST /api/v1/prompts/test`
- `POST /api/v1/prompts/publish`
- `GET /api/v1/models`

### Вебхуки

- `POST /api/v1/webhooks/yandex-market`

## Как будет выглядеть админка

Интерфейс должен быть ориентирован на скорость оператора и объяснимость AI-решений.

### Dashboard

Показывает:

- новые отзывы за день;
- количество неотвеченных;
- долю автоответов;
- долю ручной модерации;
- среднее время ответа;
- процент успешной публикации;
- долю негатива;
- распределение по маркетплейсам.

Ниже:

- график по дням;
- проблемные SKU;
- частые причины негатива.

### Reviews

Главный операционный экран.

Содержит:

- фильтры слева;
- таблицу отзывов по центру;
- боковую панель деталей справа.

В боковой панели видны:

- исходный отзыв;
- метаданные и сырой payload;
- найденные policies;
- похожие examples;
- текущий драфт ответа;
- reason codes;
- история изменений.

### Moderation Queue

Отдельная очередь только для спорных кейсов:

- `High risk`;
- `Needs human`;
- `Publish failed`;
- `Reopened after customer edit`.

### Knowledge Base

Две основные вкладки:

- `Policies`;
- `Golden examples`.

Ключевые действия:

- добавить новый пример;
- добавить policy;
- проверить retrieval;
- архивировать;
- повысить quality-ответ в approved example.

### Auto-Reply Rules

Визуальный rule builder без кода.

Пример:

```text
IF marketplace = WB
AND rating >= 4
AND issue_type NOT IN [defect, refund, legal]
AND confidence >= 0.90
THEN auto publish
```

### Integrations

Показывает по каждому кабинету:

- статус подключения;
- время последнего sync;
- количество новых отзывов;
- ошибки API;
- состояние токена.

### Prompts & Model

Должна быть песочница:

- слева исходный отзыв;
- справа structured output;
- ниже итоговый reply;
- ниже retrieved knowledge;
- рядом сравнение prompt versions.

### Analytics

Ключевые метрики:

- SLA ответа;
- процент автоответов;
- процент ручных ответов;
- процент ошибок публикации;
- reopen rate;
- топ тем негатива;
- качество ответов по менеджерам;
- какие examples чаще всего используются retrieval-слоем.

## Роли и права доступа

### Admin

- полный доступ;
- управление интеграциями, промптами, ролями и аудитом.

### Support Manager

- работа с очередью;
- approve/publish/skip/escalate;
- ручная редактура ответов.

### Content Manager

- управление базой знаний и промптами;
- нет доступа к секретам интеграций.

### Analyst / Read-only

- только просмотр, отчеты и аналитика.

## Стратегия промптов

Система использует три слоя контекста:

### 1. System prompt

Постоянные правила:

- быть вежливым;
- не выдумывать факты;
- не обещать возврат, компенсацию или юридическое решение без основания;
- не обвинять клиента;
- соблюдать tone of voice;
- возвращать строгий JSON.

### 2. Dynamic business context

Подставляется на каждый запрос:

- бренд;
- marketplace;
- политика возврата;
- особенности категории;
- стиль общения;
- ограничения по формулировкам.

### 3. Retrieved context

- policy items;
- 3-5 golden examples.

Итог:

- система объяснима;
- правила обновляются без fine-tuning;
- команда видит, почему ответ был сгенерирован именно так.

## Безопасность и приватность

Обязательные правила:

- использовать `store: false` при вызове Responses API;
- по возможности маскировать PII до отправки в LLM;
- шифровать ключи маркетплейсов в БД;
- маскировать токены в UI;
- вести отдельный `audit_log`;
- отделять пользовательские права от системных интеграционных секретов;
- логировать только безопасные и необходимые данные.

## Нефункциональные требования

- API должно быть идемпотентным для sync и publish;
- все интеграции должны иметь retry и backoff;
- ошибки marketplace API должны быть диагностируемыми;
- все решения policy engine должны быть объяснимыми;
- каждая генерация должна ссылаться на prompt version и model version;
- backend должен запускаться локально в dev-режиме без внешних очередей;
- все критические переходы состояния должны писать `review_events`.

## Этапы реализации

### Этап 1. Backend foundation

- FastAPI app;
- конфиг;
- БД, модели, миграции;
- healthcheck;
- базовый CRUD для reviews и knowledge.

### Этап 2. Review pipeline

- ingest;
- normalizer;
- retrieval;
- policy engine;
- draft generation.

### Этап 3. Marketplace integrations

- WB polling и publish;
- Yandex webhook + polling;
- Ozon adapter contract.

### Этап 4. Admin panel

- Dashboard;
- Reviews;
- Moderation Queue;
- Knowledge Base;
- Prompts & Model.

### Этап 5. Analytics and hardening

- audit log;
- SLA metrics;
- retry strategy;
- role model;
- расширенные тесты.

## Критерии готовности MVP

MVP считается рабочим, если:

- система синхронизирует отзывы минимум из WB и Яндекс.Маркета;
- новый отзыв попадает в `reviews` без дублей;
- можно сгенерировать черновик через API и из UI;
- policy engine корректно разводит auto/manual/escalation;
- автоответ публикуется обратно в marketplace для безопасных кейсов;
- все сложные кейсы видны в moderation queue;
- знания и промпты управляются через админку;
- каждый шаг процесса попадает в аудит и аналитику.

## Источник правды

Для этого репозитория источником правды являются:

1. этот `README.md`;
2. `AGENT.md`;
3. подтвержденные контракты marketplace API;
4. реальные ограничения OpenAI и продавцов, подтвержденные на момент интеграции.

Если дизайн-файлы или старые заметки противоречат этим документам, приоритет у `README.md` и подтвержденной интеграционной спецификации.
