# Queueless Bharat

**Know before you go.** Queueless Bharat is a civic-tech queue intelligence and management platform for discovering public service facilities, exploring services and counter capacities, and tracking wait-time projections across Indian administrative centers.

---

## Table of Contents

- [Overview & Key Features](#overview--key-features)
- [Technology Stack](#technology-stack)
- [Architecture & Request Flow](#architecture--request-flow)
- [Local Development Setup](#local-development-setup)
- [Configuration & Environment Variables](#configuration--environment-variables)
- [Validation, Testing & Linting](#validation-testing--linting)
- [CI/CD & Security Scanning](#cicd--security-scanning)
- [Production Deployment Strategy](#production-deployment-strategy)
- [Health Check Endpoints](#health-check-endpoints)
- [Repository Structure & Documentation](#repository-structure--documentation)
- [Contributing & License](#contributing--license)

---

## Overview & Key Features

Queueless Bharat empowers citizens with visibility into service queues and helps public administrators manage counter loads with transparent, deterministic metrics:

- **Facility & Service Directory:** Discover municipal corporations, regional transport offices, and civil hospitals with structured service categories, active counters, and operating hours.
- **Queue Lifecycle & Token Tracking:** Manage token states (`WAITING`, `CALLED`, `SERVING`, `COMPLETED`, `CANCELLED`) with role-based checks, idempotency, and Broken Object Level Authorization (BOLA) safeguards.
- **Deterministic Wait Projections:** Projections combine baseline service duration, active counter count, and queue length into bounded wait estimates with explicit confidence indicators and fallback states for sparse data.
- **Smart Visit Planner:** Recommend arrival windows and provide prerequisites and document checklists for municipal services to minimize facility dwell time.
- **Bounded Citizen Observations:** Crowdsourced queue updates (`POST /api/v1/services/{id}/observations`) allow visitors to contribute live telemetry, with strict payload bounds (`people_ahead` 0–500, `active_counters` 0–50, `reported_wait_minutes` 0–480).
- **Abuse Deterrence & Sliding-Window Rate Limiting:** In-memory, concurrency-safe sliding-window rate limiters guard observation submissions (30 req/min), polling, check-ins, registration, and logins, coupled with automatic stale client-key cleanup.
- **Role-Based Access Control:** Separate workflows for anonymous citizens (passwordless ticket lookup and Google Sign-In), operators (counter assignment and token calling), and administrators.
- **Multilingual & Accessible Interface:** High-contrast UI localized in English, Hindi (हिन्दी), and Marathi (मराठी) with dark and light theme support.

---

## Technology Stack

| Layer | Technologies |
| :--- | :--- |
| **Frontend** | React 18, TypeScript, Vite, Tailwind CSS, Lucide Icons, OpenStreetMap |
| **Backend** | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.0 (asyncpg / psycopg) |
| **Database & Migrations** | PostgreSQL, Alembic |
| **Quality & Tooling** | Pytest, Pytest-Asyncio, Playwright, Ruff, Trivy, Docker |
| **Deployment Platform** | AWS Elastic Beanstalk (Docker on Amazon Linux 2023), Amazon RDS PostgreSQL |
| **Container Registry** | Docker Hub (`heyyprakhar1/queuelessbharat-backend`, `heyyprakhar1/queuelessbharat-frontend`) |

---

## Architecture & Request Flow

```text
Citizens & Staff (Browser)
       │
       │ HTTP / HTTPS (Port 80 / 443)
       ▼
┌─────────────────────────────────────────────────────────────┐
│ Frontend Container (Nginx on Port 8080)                     │
│ ├── Serves Vite Single Page Application                     │
│ ├── Terminates static assets                                │
│ └── Proxies /api/* ───────────────┐                         │
└───────────────────────────────────┼─────────────────────────┘
                                    │ Reverse Proxy (Internal)
                                    ▼
┌─────────────────────────────────────────────────────────────┐
│ Backend Container (FastAPI on Port 8000)                    │
│ ├── API Router (/api/v1/...)                                │
│ ├── Sliding-Window Rate Limiter & Security Headers          │
│ ├── Estimation & Visit Planning Engines                     │
│ └── SQLAlchemy Async Engine                                 │
└───────────────────────────────────┬─────────────────────────┘
                                    │ TCP 5432 (Asyncpg / Psycopg)
                                    ▼
┌─────────────────────────────────────────────────────────────┐
│ PostgreSQL Database                                         │
│ ├── Local: Docker PostgreSQL Container (queless-postgres)   │
│ └── Prod:  Amazon RDS PostgreSQL Instance (queless_db)      │
└─────────────────────────────────────────────────────────────┘
```

---

## Local Development Setup

### Prerequisites

- [Docker Engine](https://docs.docker.com/engine/) (version 24+) & [Docker Compose v2](https://docs.docker.com/compose/)
- [Python](https://www.python.org/) 3.12+ (or [`uv`](https://github.com/astral-sh/uv) package manager)
- [Node.js](https://nodejs.org/) 20+ & `npm`

### Quickstart with Docker Compose

1. Clone the repository:
   ```bash
   git clone https://github.com/Heyyprakhar1/QueuelessBharat.git
   cd QueuelessBharat
   ```

2. Create a local environment configuration from the template:
   ```bash
   cp .env.example .env
   ```

3. Build and launch all services (frontend, backend, PostgreSQL):
   ```bash
   docker compose up -d --build
   ```

4. Verify service health:
   ```bash
   docker compose ps
   ```

5. Access local endpoints:
   - **Web Application:** [http://localhost:80](http://localhost:80)
   - **Direct Backend API Docs (Swagger):** [http://localhost:8001/docs](http://localhost:8001/docs)
   - **Proxied API Health Check:** [http://localhost:80/api/v1/health](http://localhost:80/api/v1/health)

6. Stop local containers:
   ```bash
   docker compose down
   ```

---

## Configuration & Environment Variables

The application relies on environment variables for runtime configuration. Use safe placeholders and never commit production credentials or `.env` files to source control.

| Variable Name | Default / Example | Purpose |
| :--- | :--- | :--- |
| `ENVIRONMENT` | `development` | Runtime mode: `development`, `staging`, or `production`. |
| `POSTGRES_SERVER` | `localhost` / `postgres` | Hostname of the PostgreSQL server or RDS endpoint. |
| `POSTGRES_PORT` | `5432` | PostgreSQL database port. |
| `POSTGRES_DB` | `queless_db` | Target PostgreSQL database name. |
| `POSTGRES_USER` | `queless_user` | Database user account. |
| `POSTGRES_PASSWORD` | `<secure-password>` | Database password. |
| `AUTH_SECRET_KEY` | `<min-32-char-secret-key>` | JWT secret key (must be >= 32 characters in production). |
| `ALLOWED_ORIGINS` | `["http://localhost:5173"]` | Allowed CORS origins (JSON array or comma-separated URLs). |
| `GOOGLE_AUTH_ENABLED` | `false` | Enable/disable Google Identity OAuth authentication. |
| `GOOGLE_CLIENT_ID` | `<client-id>.apps.googleusercontent.com` | Google OAuth Client ID when `GOOGLE_AUTH_ENABLED=true`. |
| `RATE_LIMIT_ENABLED` | `true` | Toggle sliding-window rate limiters. |
| `RATE_LIMIT_OBSERVATION_MAX` | `30` | Maximum observation submissions allowed per client per window. |
| `RATE_LIMIT_OBSERVATION_WINDOW` | `60` | Observation rate limit sliding window size in seconds. |
| `RUN_MIGRATIONS` | `true` | When true, runs Alembic migrations on container startup. |
| `SEED_DATA` | `false` | When true, populates demo Indian municipal services on startup. |

---

## Validation, Testing & Linting

### Backend Tests & Verification

The backend test suite includes unit, API, concurrency, security, and database-backed integration tests. Database tests run against an isolated test database (e.g. `queless_test`) with fail-closed safety guards:

```bash
cd backend

# Execute all pytest suites
TESTING=true \
POSTGRES_PORT=5432 \
POSTGRES_DB=queless_test \
POSTGRES_USER=queless_user \
POSTGRES_PASSWORD=queless_password \
AUTH_SECRET_KEY=test-secret-key-at-least-32-characters-for-ci-tests! \
ENVIRONMENT=development \
pytest

# Or run with coverage via uv:
uv run pytest --cov=app --cov-report=term-missing
```

### Static Analysis & Formatting

```bash
cd backend
# Ruff lint check
ruff check .

# Ruff format validation
ruff format --check .
```

### Frontend Build & Linting

```bash
cd frontend
npm install
npm run lint
npm run build
```

---

## CI/CD & Security Scanning

The GitHub Actions workflow ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) automatically validates pushes and pull requests to `main` and `dev`:

1. **Least-Privilege Security:** Root permissions are locked down to `contents: read`.
2. **Static Code Analysis:** Python checks (Ruff lint, format, and static validation) and repository vulnerability scan.
3. **Database Integration Tests:** Runs all 186 test cases against an automated PostgreSQL service container with coverage reporting.
4. **Browser E2E Testing:** Playwright runs multi-user queue lifecycles, booking, and cancellation flows against live preview containers.
5. **Container Security Gate:** Builds backend and frontend Docker images and runs [Aquasec Trivy](https://github.com/aquasecurity/trivy) container vulnerability scans. Builds strictly fail (`--exit-code 1`) if any `CRITICAL` unfixed vulnerabilities are detected.

---

## Production Deployment Strategy

Queueless Bharat supports containerized deployment on **AWS Elastic Beanstalk** (Docker on Amazon Linux 2023) paired with **Amazon RDS PostgreSQL**:

### Deployment Package Architecture

- **Bundle Artifact:** Elastic Beanstalk source bundles contain `docker-compose.yml` (sourced from `deploy/elasticbeanstalk/docker-compose.yml`) and `.ebextensions/` (proxy and health-check configurations).
- **Prebuilt Container Images:** Elastic Beanstalk does not build code from source on EC2 instances; it pulls prebuilt, scanned container images from Docker Hub:
  - Backend: `heyyprakhar1/queuelessbharat-backend:eb`
  - Frontend: `heyyprakhar1/queuelessbharat-frontend:eb`
- **Packaging Script:** Build release archives using:
  ```bash
  bash deploy/package_eb_bundle.sh v1
  ```
- **External Database Requirement:** Production containers connect to an external PostgreSQL instance in the VPC (Amazon RDS instance `queuelessbharatdb`, database `queless_db`).

> [!NOTE]
> **Environment Lifecycle Note:**
> In order to eliminate ongoing cloud infrastructure costs between active releases, the Elastic Beanstalk environment (`queueless-prod`) was intentionally terminated, and the Amazon RDS PostgreSQL instance (`queuelessbharatdb`) was cleanly stopped (`stopped` status). Redeployment requires starting the RDS instance (`aws rds start-db-instance`) prior to initializing the Elastic Beanstalk environment.

For detailed Elastic Beanstalk deployment steps, security group references, and configuration instructions, see [**deploy/README-EB.md**](deploy/README-EB.md).

---

## Health Check Endpoints

| Endpoint | Target Service | Expected Response | Purpose |
| :--- | :--- | :--- | :--- |
| `GET /healthz` | Frontend (Nginx) | `200 OK` (`healthy`) | Platform & load balancer health probe. |
| `GET /api/v1/health` | Backend (FastAPI) | `200 OK` (`{"status":"ok","database":"connected","environment":"..."}`) | End-to-end backend and database verification. |

---

## Repository Structure & Documentation

```text
├── .github/workflows/ci.yml       # CI pipeline, Pytest, Playwright & Trivy scans
├── backend/                       # FastAPI application, schemas, models & tests
│   ├── app/
│   │   ├── api/v1/endpoints/      # REST API route handlers
│   │   ├── core/                  # Rate limiting, configuration & security
│   │   ├── db/                    # Session management & Alembic migrations
│   │   ├── domain/                # Domain models, enums & state logic
│   │   └── services/              # Queue estimators & visit planner
│   ├── tests/
│   │   ├── api/                   # Endpoint and integration tests
│   │   ├── integration/           # DB transaction & precedence tests
│   │   └── unit/                  # Estimator matrix, config & limiter tests
├── frontend/                      # React / TypeScript / Vite source code
├── deploy/                        # Production deployment manifests & scripts
│   ├── README-EB.md               # Elastic Beanstalk architecture guide
│   ├── package_eb_bundle.sh       # Release bundle packager
│   ├── .ebextensions/             # EB Nginx proxy and health-check configs
│   └── elasticbeanstalk/          # Canonical EB docker-compose.yml template
└── docker-compose.yml             # Local multi-container development stack
```

---

## Contributing & License

Contributions are welcome. Before submitting pull requests, ensure that:
1. All Pytest test suites pass (`pytest`).
2. Ruff formatting and linting pass (`ruff check . && ruff format --check .`).
3. Frontend TypeScript compiles cleanly (`npm run build`).
4. Sensitive credentials, real passwords, and local `.env` files are never committed.

All rights reserved by the Queueless Bharat contributors.
