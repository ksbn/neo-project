# 🌠 NASA NeoWs Data Pipeline

A robust, containerized, and idempotent data pipeline that fetches Near Earth Object (NeoWs) data from NASA's public API, validates it via Pydantic models, and safely ingests/upserts records into a PostgreSQL database.

---

## 🛠️ Tech Stack
* **Language:** Python 3.14 (`requests`, `pydantic`, `psycopg`, `python-dotenv`)
* **Database:** PostgreSQL 15 (Dockerized)
* **Testing:** `pytest`
* **Infrastructure:** Docker Compose

---

## 🚀 Quick Start Guide

### 1. Clone & Configure Environment
Clone the repository and copy the example environment file:
```bash
cp .env.example .env

Fill out your .env with your PostgreSQL credentials and your free NASA API Key.

2. Start PostgreSQL via Docker
Ensure Docker Desktop is running, then spin up the database container:

Bash
docker-compose up -d
(The schema script in schema.sql will automatically initialize your asteroids table on container startup).

3. Set Up Virtual Environment & Dependencies
Bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
4. Run the Pipeline
Fetch and upsert asteroid data for today:

Bash
python pipeline.py
Or specify a custom date range:

Bash
python pipeline.py --start 2026-11-10 --end 2026-11-15
🧪 Running Tests
Execute the test suite to verify data validation logic:

Bash
pytest

