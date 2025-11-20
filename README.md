# Backend Code Samples: Engineering Portal

This repository contains selected backend modules extracted from a production **Mystru.com** application. 

**Note:** This is not a standalone library or a runnable application. These files are provided for demonstration purposes to showcase architectural patterns, code style, and problem-solving approaches in a modern Python web environment.

## 🛠 Technology Stack

*   **Framework:** FastAPI
*   **Database:** PostgreSQL + SQLAlchemy v2 (Async)
*   **Validation:** Pydantic v2
*   **Infrastructure:** Redis, Nginx
*   **Architecture:** Modular Monolith, Repository Pattern, Unit of Work

## 📂 Modules Overview

### 1. Activity Feed (`src/activity_feed`)
A sophisticated activity timeline system that tracks user actions (creating elements, uploading files, commenting).

**Key Features:**
*   **Event-Driven Architecture:** Uses listeners to decouple business logic from activity tracking.
*   **Smart Aggregation:** Instead of spamming the feed with individual events (e.g., "User uploaded image 1", "User uploaded image 2"), the system buffers raw events in Redis/DB and aggregates them into single logical activities (e.g., "User uploaded 5 images").
*   **Permission-Aware:** Feed generation respects complex access control lists (ACLs).

### 2. Monitoring System (`src/monitoring`)
A custom observability module designed for proactive error tracking and system health checks without relying on heavy external APM tools for basic needs.

**Key Features:**
*   **Real-time Alerts:** Integrates with **Telegram** to send critical alerts and daily reports.
*   **Error Deduplication:** Uses Redis to prevent alert fatigue by grouping similar errors occurring within a short time window.
*   **Middleware:** Automatically catches unhandled exceptions and tracks slow requests.
*   **Background Task Monitoring:** Wraps ARQ tasks to track execution time and failures.

### 3. Unit of Work (`src/core/unit_of_work.py`)
An implementation of the **Unit of Work (UoW)** pattern combined with the Repository pattern.

**Why this implementation?**
*   **Atomic Transactions:** Ensures data consistency by committing multiple changes in a single transaction block.
*   **Repository Management:** Provides a clean interface to access data repositories (`uow.elements`, `uow.folders`) while sharing the same async database session.
*   **Context Manager:** Uses Python's `async with` context manager for automatic resource cleanup and rollback on error.

## ⚠️ Context & Limitations

These modules were extracted from a larger monolithic application. Imports pointing to `app.core`, `app.users`, or `app.filemanager` refer to other parts of the system not included in this repository.