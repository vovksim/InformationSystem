# Microservices System – Auth, CRM, DB Stresser, Monitoring Stack

This repository contains a small microservices ecosystem built with **Flask**, **Redis**, **SQLite**, **Prometheus**, and **Grafana**.  
It includes:

- **Auth Service** – registration, login, session tokens in Redis, metrics  
- **CRM Service** – validates sessions via Auth, shows dashboard  
- **DB Stresser** – load-generator for testing the system  
- **Monitoring Stack** – Prometheus + Grafana  
- **Docker Compose** for orchestration  

---

## Project Structure

## 1. Auth Service
Handles:
- User registration & login  
- Issues UUID session tokens  
- Stores sessions in Redis with TTL  
- Validates tokens via `/api/validate`  
- Exposes Prometheus metrics  

Session Flow:
1. Login  
2. Auth stores `session:<token>` in Redis  
3. Sets cookie `auth_token`  
4. CRM validates via `/api/validate?token=...`

Metrics include: request counts, login success/fail, Redis latency, session gauges.

---

## 2. CRM Service
- Reads `auth_token` cookie  
- Validates it with Auth service  
- Shows a simple dashboard  

---

## 3. DB Stresser
- Generates configurable load  
- Default: **10 ops/sec**  
- Useful for stressing Auth/CRM/Redis  
- Exposes Prometheus metrics  

---

## 4. Redis
Stores sessions created by Auth service.

---

## 5. Prometheus
Scrapes all microservices’ `/metrics` endpoints.

---

## 6. Grafana
Dashboards for:
- Auth performance  
- Request rate  
- Token validation latency  
- Redis errors  
- Stresser throughput  

---

# Running the System

From project root:

```bash
./scripts/start.sh


