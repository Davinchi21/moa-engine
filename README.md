# MoA Engine — Mixture of Agents

Многослойный ансамбль LLM, который последовательно пропускает запрос через несколько ИИ-моделей для получения максимально качественного ответа. Бесплатные модели через OpenRouter.

```
Prompt → 3× Proposers → Aggregator → Final → Ответ
```

## Архитектура

**Layer 0 — Proposers** (3 модели NVIDIA Nemotron)
Каждая модель генерирует свой ответ на запрос. Запускаются последовательно с паузой 3с (защита от rate limit).

**Layer 1 — Aggregator** (Poolside Laguna M.1)
Получает 3 ответа от пропозеров и синтезирует один улучшенный.

**Layer 2 — Final** (Poolside Laguna XS.2)
Финальное улучшение — «мастер-синтезатор».

Опционально: Self-Critique (модель критикует и улучшает свой ответ).

```
                   ┌─────────────────────┐
                   │     Your Prompt      │
                   └──────────┬──────────┘
                              ▼
       ┌──────────────────────────────────────┐
       │        LAYER 0 — Proposers           │
       │                                      │
       │   ┌─────────┐  ┌─────────┐  ┌─────┐  │
       │   │Nemotron │  │Nemotron │  │Nemo │  │
       │   │Nano 30B │  │Nano 9B  │  │Super│  │
       │   └────┬────┘  └────┬────┘  └──┬──┘  │
       │        └────────────┼──────────┘      │
       └─────────────────────┼─────────────────┘
                              ▼
       ┌──────────────────────────────────────┐
       │      LAYER 1 — Aggregator            │
       │     Poolside Laguna M.1              │
       └──────────────────┬───────────────────┘
                              ▼
       ┌──────────────────────────────────────┐
       │      LAYER 2 — Final                 │
       │     Poolside Laguna XS.2             │
       └──────────────────┬───────────────────┘
                              ▼
                   ┌─────────────────────┐
                   │   Final Answer      │
                   └─────────────────────┘
```

## Быстрый старт

```bash
# 1. Клонировать
git clone https://github.com/Davinchi21/moa-engine.git
cd moa-engine

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Создать .env с API ключом OpenRouter
echo OPENROUTER_API_KEY=sk-or-v1-xxxx > .env

# 4. Запустить
python main.py "Напиши краткую историю Python"
```

## Режимы запуска

### CLI
```bash
python main.py "твой запрос"
python main.py --quick "быстрый режим (1 пропозер)"
python main.py --status  # проверить API ключи
```

### Web UI (Gradio)
```bash
python main.py --web
# Открыть http://127.0.0.1:7888
```

### MCP сервер (интеграция с OpenCode)
```bash
python main.py --mcp
```

В `opencode.json`:
```json
{
  "mcp": {
    "moa-engine": {
      "type": "local",
      "command": ["python", "путь/к/moa_mcp_server.py"],
      "enabled": true
    }
  }
}
```

### Управление скилами
```bash
python main.py --skills-list
python main.py --skills-install bregman-arie/devops-sre-skills
python main.py --skills-uninstall "Triage Pending Pods"
```

## Модели

Бесплатные модели OpenRouter (2026):

| Уровень | Модель | Вес |
|---------|--------|-----|
| Proposer 1 | `nvidia/nemotron-3-nano-30b-a3b:free` | 1.0 |
| Proposer 2 | `nvidia/nemotron-nano-9b-v2:free` | 0.9 |
| Proposer 3 | `nvidia/nemotron-3-super-120b-a12b:free` | 0.8 |
| Aggregator L1 | `poolside/laguna-m.1:free` | 1.0 |
| Final | `poolside/laguna-xs.2:free` | — |

Все модели в `moa_config.yaml` — легко заменить на любые другие.

## Защита от rate limit

- Exponential backoff: `(2^attempt) × 5 + random(1,3)` секунд
- 5 ретраев, пауза 3с между последовательными вызовами
- Кэш: SHA256 ключ, TTL 1 час (memory или sqlite)

## Установленные скилы

Проект включает 17 DevOps/SRE скилов для OpenCode:

**Kubernetes:** CrashLoopBackOff, ImagePullBackOff, EKS Node NotReady, Node Pressure, Service DNS, Pending Pods

**Cloud:** AWS AccessDenied, GCP Quota Exceeded, Cloud Cost Spike

**Terraform:** Drift, State Lock

**Incidents:** Sev1 First 15 Minutes, Error Budget Burn, Latency Regression, Secret Exposure

**Other:** Argo CD OutOfSync, Example

## Структура проекта

```
moa-engine/
├── main.py                # CLI / Web UI / MCP точка входа
├── moa_engine.py          # Ядро: 3-слойный MoA пайплайн
├── moa_mcp_server.py      # MCP сервер для OpenCode
├── moa_skills.py          # GitHub установщик скилов
├── moa_config.yaml        # Конфиг моделей
├── requirements.txt       # Зависимости
├── setup_moa.ps1          # Скрипт установки (Windows)
├── .env.example           # Шаблон .env
├── opencode.json          # Конфиг OpenCode
└── .opencode/skills/      # Установленные скилы
```

## Зависимости

- `httpx` — HTTP клиент
- `pyyaml` — парсинг конфига
- `mcp` — MCP протокол (для OpenCode)
- `python-dotenv` — загрузка .env
- `gradio` — Web UI (опционально)
