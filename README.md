# FitShed
 
 FitShed — розклад і онлайн-запис для фітнес-центру (вимоги й C4, DDD з портами й адаптерами, Saga, оцінювання архітектури, REST API з контрактними тестами, подійна взаємодія через RabbitMQ, API Gateway зі стійкістю до відмов, контроль доступу JWT і підсумкова оцінка рішень).
 
## Інструменти та версії
 
| Інструмент | Версія | Для чого |
|---|---|---|
| Python | 3.11+ (перевірено на 3.14) | увесь код і тести |
| FastAPI | 0.115+ | Booking API (ЛР 5–8) |
| uvicorn | 0.30+ | локальний запуск API, шлюзу й заглушки |
| Starlette | разом з FastAPI | API Gateway (ЛР 7) |
| requests | 2.31+ | HTTP-клієнт API, виклики шлюзу до залежностей |
| PyJWT + cryptography | 2.8+ | access token JWT, підпис RS256 (ЛР 8) |
| pika | 1.3+ | клієнт RabbitMQ (ЛР 6) |
| pytest | 8+ | модульні, контрактні, інтеграційні тести, тести стійкості й доступу |
| PyYAML, jsonschema | 6+, 4.18+ | перевірка відповідей за `openapi.yaml` (ЛР 5) |
| matplotlib | будь-яка актуальна | діаграма результатів оцінювання (ЛР 4, необов'язково) |
| Docker / Docker Compose | актуальні | брокер RabbitMQ 4 (`rabbitmq:4-management`, ЛР 6) і Structurizr (ЛР 1) |
| Structurizr DSL | `structurizr/structurizr local` | діаграми C4 як код (ЛР 1) |
| Mermaid | — | діаграми послідовності, станів і меж довіри (`*.mmd` у `docs/`) |
 
## Налаштування середовища (один раз)
 
```
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows; на Linux/macOS: source .venv/bin/activate
pip install fastapi uvicorn requests "pyjwt[crypto]" pika pytest pyyaml jsonschema
```
 
Якщо Windows блокує активацію: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`.
 
**Секрети.** Реальних секретів у репозиторії немає. Пароль RabbitMQ синтетичний і лежить у `.env` (шаблон — `.env.example`). Ключі JWT генеруються локально командою `python -m fitshed.auth.keys` у `backend/var/keys/`, а тести створюють власні ключі в пам'яті. Файли `.env` і `backend/var/` не потрапляють у Git.
 
## Запуск перевірок за лабораторними
 
Усі команди виконуються з активованим `.venv`: тести — з папки `backend`, скрипти `python -m fitshed...` — з `backend/src`.
 
**Усі тести одразу** (150 тестів, RabbitMQ для них не потрібен):
```
python -m pytest
```
 
**ЛР 1. Вимоги, стилі, C4.** Діаграми (з кореня репозиторію), далі відкрити http://localhost:8080:
```
docker run -it --rm -p 8080:8080 -v ${PWD}/docs/architecture:/usr/local/structurizr structurizr/structurizr local
```
 
**ЛР 2. Доменне ядро, DDD, порти й адаптери:**
```
python -m pytest tests/test_group_class.py tests/test_value_objects.py tests/test_repository.py
```
 
**ЛР 3. Оркестрована Saga і компенсації:**
```
python -m pytest tests/test_saga.py
python -m fitshed.saga.demo
```
 
**ЛР 4. Багатометрикове оцінювання архітектури** (з кореня репозиторію):
```
python evaluation/mcda.py --save docs/lab4/results.md
python -m pytest evaluation
```
 
**ЛР 5. REST API і контрактні тести клієнта й провайдера:**
```
python -m pytest tests/test_api.py tests/test_contract_consumer.py tests/test_contract_provider.py
 
python -m fitshed.auth.keys         # один раз 
python -m fitshed.api.server        # термінал 1: http://127.0.0.1:8000, документація /docs
python -m fitshed.api.scenario      # термінал 2: сценарій від імені різних користувачів
```
 
**ЛР 6. Подійна взаємодія через RabbitMQ та ідемпотентний споживач:**
```
python -m pytest tests/test_events.py
docker compose up -d                # з кореня: брокер RabbitMQ
python -m fitshed.messaging.rabbit_checks --save ../../docs/lab6/rabbitmq-checks.txt
```
 
**ЛР 7. API Gateway: таймаути, повтори, deadline, circuit breaker, rate limiting:**
```
python -m pytest tests/test_resilience.py tests/test_gateway.py tests/test_gateway_booking.py
python -m fitshed.gateway.checks --save ../../docs/lab7/checks.txt
python -m fitshed.gateway.compare --save ../../docs/lab7/comparison.md
```
 
**ЛР 8. Контроль доступу JWT, роль і володіння, точка чутливості:**
```
python -m pytest tests/test_tokens.py tests/test_access.py
python -m fitshed.auth.issue --sub anna --role client      # токен для ручних запитів
python -m fitshed.gateway.compare --vary max_attempts=2 --save ../../docs/lab8/param-change.md
```
 
## Звіти

| Робота | Тема | Звіт | Код |
|---|---|---|---|
| №1 | Вимоги, архітектурні стилі та C4 | [`Lab1-report.md`](docs/lab1/Lab1-report.md) | `docs/architecture/` |
| №2 | DDD і доменне ядро з портами й адаптерами | [`Lab2-report.md`](docs/lab2/Lab2-report.md) | `backend/src/fitshed/booking/`, `backend/src/fitshed/memberships/` |
| №3 | Взаємодія компонентів і узгодженість даних. Патерн Saga | [`Lab3-report.md`](docs/lab3/Lab3-report.md) | `backend/src/fitshed/saga/` |
| №4 | Оцінювання архітектури та багатометриковий вибір конфігурації | [`Lab4-report.md`](docs/lab4/Lab4-report.md) | `evaluation/` |
| №5 | Проєктування API та перевірка контрактів | [`Lab5-report.md`](docs/lab5/Lab5-report.md) | `backend/openapi.yaml`, `backend/src/fitshed/api/` |
| №6 | Подійна взаємодія та ідемпотентна обробка повідомлень | [`Lab6-report.md`](docs/lab6/Lab6-report.md) | `backend/src/fitshed/messaging/`, `docker-compose.yml` |
| №7 | API Gateway та захист від каскадних відмов | [`Lab7-report.md`](docs/lab7/Lab7-report.md) | `backend/src/fitshed/gateway/`, `backend/config/gateway.json` |
| №8 | Контроль доступу та підсумкова оцінка архітектурних рішень | [`Lab8-report.md`](docs/lab8/Lab8-report.md) | `backend/src/fitshed/auth/`, `backend/src/fitshed/api/authz.py` |
 
## Специфікації
 
- [OpenAPI 3.1.1 — Booking API](backend/openapi.yaml)
- [Файл очікувань клієнта для контрактних тестів](backend/contracts/booking-client.expectations.json)
- [Модель C4 у Structurizr DSL](docs/architecture/workspace.dsl)
- [Конфігурація шлюзу](backend/config/gateway.json)

## Архітектурні рішення (ADR)
 
| ADR | Рішення | Робота | Статус |
|---|---|---|---|
| [ADR-001](docs/adr/ADR-001-modular-monolith.md) | Модульний моноліт з окремим фоновим воркером | ЛР 1, уточнено в ЛР 2 | частково замінено ADR-005 |
| [ADR-002](docs/adr/ADR-002-booking-saga.md) | Оркестрована Saga для запису з абонементом | ЛР 3 | прийнято |
| [ADR-003](docs/adr/ADR-003-deployment.md) | Розміщення системи: одна VM з Docker Compose | ЛР 4 | прийнято |
| [ADR-004](docs/adr/ADR-004-rest-api.md) | REST серед REST, gRPC і GraphQL | ЛР 5 | прийнято |
| [ADR-005](docs/adr/ADR-005-event-messaging.md) | Черга RabbitMQ проти журналу Kafka, ризик втрати події, outbox | ЛР 6 | прийнято |
| [ADR-006](docs/adr/ADR-006-gateway-resilience.md) | Шлюз, повтори, circuit breaker, rate limiting | ЛР 7 | замінено ADR-008 |
| [ADR-007](docs/adr/ADR-007-access-policy.md) | Політика доступу: JWT RS256, роль і володіння, перевірка в Booking API | ЛР 8 | прийнято |
| [ADR-008](docs/adr/ADR-008-resilience-after-evaluation.md) | Параметри стійкості, уточнені дослідом зі зміною `max_attempts` | ЛР 8 | прийнято |
 
## Структура репозиторію
 
| Шлях | Що там |
|---|---|
| `docs/lab1/` … `docs/lab8/` | Звіти лабораторних, діаграми, результати перевірок і вимірювань |
| `docs/architecture/` | Модель C4 у Structurizr DSL і експорт діаграм у PNG |
| `docs/adr/` | Архітектурні рішення ADR-001 … ADR-008 |
| `backend/src/fitshed/booking/` | Контекст «Розклад і запис»: доменне ядро, порти й адаптери (ЛР 2) |
| `backend/src/fitshed/memberships/` | Контекст «Абонементи»: агрегат абонемента (ЛР 2–3) |
| `backend/src/fitshed/saga/` | Оркестратор Saga і демонстрація (ЛР 3) |
| `backend/src/fitshed/api/` | HTTP API на FastAPI, клієнт, сценарій, правила доступу `authz.py` (ЛР 5, 8) |
| `backend/src/fitshed/messaging/` | Публікатор, ідемпотентний споживач, перевірки з RabbitMQ (ЛР 6) |
| `backend/src/fitshed/gateway/` | API Gateway, політики стійкості, заглушка сервісу абонементів, вимірювання (ЛР 7) |
| `backend/src/fitshed/auth/` | Перевірка JWT, навчальний емітент, генерація ключів (ЛР 8) |
| `backend/tests/` | Тести ЛР 2–8 |
| `backend/openapi.yaml`, `backend/contracts/` | Контракт API та очікування клієнта (ЛР 5) |
| `backend/config/gateway.json` | Таймаути, повтори, breaker і ліміти шлюзу (ЛР 7) |
| `backend/var/` | Локальні ключі JWT і база SQLite споживача; у Git не потрапляє |
| `evaluation/` | Дані альтернатив, ваги, скрипт розрахунку та його тести (ЛР 4) |
| `docker-compose.yml`, `.env.example` | Локальний стенд RabbitMQ і приклад змінних оточення (ЛР 6) |
 