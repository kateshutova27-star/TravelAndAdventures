# Travel Adventure Backend — План разработки (v2, с учётом ревью)

## Команда экспертов

| Роль | Область ответственности |
|------|------------------------|
| Backend-архитектор | Структура проекта, API, БД, инфраструктура |
| GIS/POI специалист | Импорт данных, OSM, PostGIS, изображения |
| Специалист по маршрутизации | Алгоритм генерации, маршруты, трекинг |
| Security Engineer | Безопасность, валидация, rate limiting |
| Performance Engineer | Производительность, кеширование, масштабирование |
| API Contract Reviewer | Совместимость фронтенд-бэкенд |
| DevOps Engineer | Инфраструктура, CI/CD, мониторинг |

---

## 1. Технологический стек

- **Python 3.12+** + **FastAPI**
- **PostgreSQL 16** + **PostGIS 3.4** (пространственные запросы)
- **Redis 7** (очередь задач + кеш)
- **SQLAlchemy 2.0** (async, asyncpg)
- **Alembic** (миграции)
- **ARQ** (async фоновый воркер, `max_jobs=10`)
- **httpx** (HTTP-клиент для внешних API)
- **GeoAlchemy2** (интеграция с PostGIS)
- **slowapi** (rate limiting)
- **structlog** (структурированное логирование)
- **sentry-sdk** (error tracking)
- **Docker Compose** (PostgreSQL + Redis + API + Worker + Nginx)

## 2. Внешние API

| Сервис | Назначение |
|--------|-----------|
| Overpass API (OSM) | Импорт POI (бесплатно) |
| Geoapify Routing | Маршрут + polyline |
| Geoapify Route Matrix | Матрица расстояний |
| GraphHopper | Геокодинг + fallback routing |
| Pixabay | Фото мест |

Все ключи хранятся ТОЛЬКО в `.env` (в `.gitignore`). В коде и документации — плейсхолдеры.

**Лимиты Geoapify (бесплатный план):** 3000 req/day, 5 req/sec.
Реализуем семафор 4 req/sec в `routing_service.py` + кеш матриц в Redis.
Fallback на GraphHopper при исчерпании лимита.

## 3. Структура проекта

```
travel_backend/
├── pyproject.toml
├── Makefile
├── alembic.ini
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── nginx/
│   └── nginx.conf
├── tests/
│   ├── conftest.py
│   ├── test_places.py
│   ├── test_adventures.py
│   └── test_route_optimizer.py
├── alembic/
│   ├── env.py
│   └── versions/
├── scripts/
│   ├── import_pois.py
│   ├── osm_fetcher.py
│   ├── pixabay_fetcher.py
│   └── enrichment.py
└── app/
    ├── __init__.py
    ├── main.py
    ├── config.py
    ├── database.py
    ├── redis.py
    ├── dependencies.py
    ├── models/
    │   ├── __init__.py
    │   ├── place.py
    │   ├── adventure.py
    │   └── saved_item.py
    ├── schemas/
    │   ├── __init__.py
    │   ├── common.py           # CamelModel + enums (camelCase!)
    │   ├── place.py
    │   └── adventure.py
    ├── api/
    │   ├── __init__.py
    │   ├── router.py
    │   ├── places.py
    │   └── adventures.py
    ├── services/
    │   ├── __init__.py
    │   ├── place_service.py
    │   ├── adventure_service.py
    │   ├── routing_service.py
    │   ├── route_optimizer.py
    │   └── adventure_generator.py
    ├── workers/
    │   ├── __init__.py
    │   └── adventure_worker.py
    └── utils/
        ├── __init__.py
        ├── geo.py
        └── cache.py
```

## 4. КРИТИЧЕСКОЕ: JSON-контракт с фронтендом

Фронтенд (Flutter/Freezed) ожидает **camelCase** во всех JSON-ответах:
- `thumbnailUrl`, `photoUrls`, `reviewCount`, `bestTimeToVisit`, `workingHours`
- `driveTimeMinutes`, `timeToSpendMinutes`, `totalTimeMinutes`, `encodedPolyline`

Все Pydantic-схемы наследуются от `CamelModel`:
```python
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
```

Enum-значения точно как во фронте:
- `PlaceCategory`: `nature`, `city`, `coffee`, `restaurant`, `photo`
- `AdventureDuration`: `oneHour`, `threeHours`, `halfDay`, `fullDay`
- `GenerationStatus`: `pending`, `findingPlaces`, `buildingRoute`, `completed`, `failed`

Ответы списков — чистые JSON-массивы `[{...}, {...}]`, без обёрток с пагинацией.

`AdventureStop` содержит полный вложенный объект `place`, не просто `placeId`.

## 5. Безопасность

### Идентификация (MVP)
- Заголовок `X-Device-Id` в каждом запросе
- Формат: UUID v4, валидация regex
- 400 Bad Request при отсутствии или невалидном
- `created_by_device_id` в таблице `adventures` — проверка владельца при PATCH
- GPS-данные трекинга автоудаляются через 24ч после завершения сессии

### Rate Limiting (slowapi)
- `POST /adventures/generate`: 5 req/min на device_id, макс 2 pending задачи
- `GET /places`: 30 req/min на IP
- `POST /tracking/.../update`: 2 req/min на session
- Глобальный: 100 req/min на IP
- Ответ: 429 + `Retry-After`

### Валидация входных данных (Pydantic)
- `lat: float = Field(ge=-90, le=90)`
- `lng: float = Field(ge=-180, le=180)`
- `radius: float = Field(gt=0, le=50000)` (макс 50 км)
- `category: Optional[PlaceCategoryEnum]`
- `q: Optional[str] = Field(min_length=2, max_length=200)`

### Санитизация OSM-данных
- Удаление HTML-тегов при импорте
- Обрезка строк (name≤255, description≤2000)
- Удаление управляющих Unicode-символов

### HTTPS
- Nginx reverse proxy с TLS (Caddy для авто-сертификатов в production)
- `Strict-Transport-Security` заголовок

## 6. База данных

Расширения: `postgis`, `pg_trgm`, `uuid-ossp`
Создаются в init-скрипте `/docker-entrypoint-initdb.d/init.sql`.

### Таблица `places`
(без изменений от v1, + partial GIST index)

Индексы (обновлённые):
- `GIST` на `geog` WHERE `is_active = true` (partial — быстрее)
- `GIN trigram` на `name` (для `%` оператора, без ILIKE)
- `BTREE` на `category`
- `BTREE` на `rating DESC`
- `UNIQUE(osm_id, osm_type)`

### Таблица `adventures`
(+ столбец `created_by_device_id VARCHAR(255)`)

### Таблицы `saved_places`, `saved_adventures`
(+ `BTREE(device_id)` индекс на обе)

### Connection Pool
- API: `pool_size=10, max_overflow=20, pool_timeout=30, pool_recycle=1800`
- Worker: `pool_size=5, max_overflow=10`
- PostgreSQL: `max_connections=200`

## 7. Кеширование (Redis, обновлённое)

- Координаты в ключах кеша округляются до 3 знаков (~111м точность)
- Cache stampede protection: SETNX lock
- Статус генерации хранится в Redis (не в БД) для быстрого polling

| Данные | TTL |
|--------|-----|
| Место (детали) | 5 мин |
| Поиск мест | 5 мин (было 2) |
| Похожие места | 5 мин |
| Приключение | 10 мин |
| Route Matrix | 30 мин |

## 8. ARQ Worker (обновлённое)

- `max_jobs=10` (concurrency) — большая часть работы I/O-bound
- `job_timeout=60` секунд
- `max_tries=2` с exponential backoff для Geoapify
- 2 воркера в docker-compose
- Мониторинг глубины очереди: `LLEN arq:queue:default`
- Зависшие задачи (>5 мин в pending/building) → автоматически `failed`

## 9. Eager Loading (N+1 fix)

```python
select(Adventure).options(
    selectinload(Adventure.stops).selectinload(AdventureStop.place)
).where(Adventure.id == id)
```

## 10. Инфраструктура

### Docker Compose
- `db`: postgis/postgis:16-3.4, volume, health check (`pg_isready`), restart: unless-stopped
- `redis`: redis:7-alpine, volume (RDB persistence), health check (`redis-cli ping`)
- `api`: uvicorn, depends_on db+redis (service_healthy), health check (`/health`)
- `worker`: ARQ ×2 реплики, depends_on db+redis
- `nginx`: reverse proxy, TLS, rate limiting
- `stop_grace_period: 30s` для api и worker

### Инициализация БД
PostgreSQL init script → Alembic migrations → Uvicorn start (в entrypoint)

### Логирование
- `structlog` с JSON-форматом
- `request_id`, `device_id` в каждой записи
- Уровни: INFO (запросы), WARNING (таймауты API), ERROR (исключения)

### Error Tracking
- Sentry SDK с `traces_sample_rate`
- Привязка `device_id`, `adventure_id` к событиям

### CI/CD (GitHub Actions)
- PR: lint (ruff) → type check (mypy) → tests (pytest + testcontainers)
- Merge в main: build Docker → push → deploy staging
- Tag/release: deploy production (manual approval)

### Тестирование
- `pytest` + `pytest-asyncio` + `httpx`
- `testcontainers` для PostGIS + Redis
- `respx` для мока Geoapify/Overpass
- Фикстуры с известными координатами для пространственных запросов

### Бэкапы
- Dev: `pg_dump` ежедневно
- Prod: управляемая БД (RDS) с point-in-time recovery

### Makefile
- `make setup` — .env + docker-compose up
- `make migrate` — alembic upgrade head
- `make import` — импорт тестового региона
- `make test` — pytest
- `make dev` — docker-compose up с hot-reload

## 11. План реализации (обновлённый)

### Фаза 1: Фундамент
- pyproject.toml, docker-compose.yml, .env.example, .gitignore, Dockerfile, Makefile
- app/config.py, app/database.py, app/redis.py
- app/models/ (все модели с PostGIS)
- alembic init + первая миграция
- docker-entrypoint-initdb.d/init.sql

### Фаза 2: API-оболочка + безопасность
- app/schemas/ (CamelModel + все схемы с camelCase + точные enum values)
- app/dependencies.py (get_db, get_redis, get_device_id с UUID валидацией)
- app/main.py (lifespan, CORS, Sentry, structlog, rate limiting, health check)
- app/api/ (router + заглушки всех эндпоинтов)

### Фаза 3: Сервис мест
- app/services/place_service.py (PostGIS, trigram поиск, similar)
- app/api/places.py (все 5 эндпоинтов)
- app/utils/cache.py (Redis + stampede protection)

### Фаза 4: Импорт данных
- scripts/ (OSM + Pixabay + enrichment + санитизация)
- Первый импорт Calgary

### Фаза 5: Генерация приключений
- routing_service.py (Geoapify + семафор + fallback)
- route_optimizer.py (NN + 2-opt)
- adventure_generator.py (pipeline)
- adventure_worker.py (ARQ, max_jobs=10)

### Фаза 6: CRUD + редактирование
- adventures.py (полная реализация с проверкой владельца)
- adventure_service.py (reorder/remove/replace/add + rebuild)

### Фаза 7: Трекинг
- ActiveRouteSession + эндпоинты
- Auto-cleanup GPS данных (24ч)

### Фаза 8: Полировка
- Nginx конфигурация
- Dockerfile multi-stage
- CI/CD pipeline
- Тесты
