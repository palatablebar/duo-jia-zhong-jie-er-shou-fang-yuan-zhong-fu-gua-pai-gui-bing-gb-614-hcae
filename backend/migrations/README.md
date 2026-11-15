# 数据库迁移说明

本项目用 SQLAlchemy `Base.metadata.create_all()` 在 backend 启动时建表（见 `src/config/app.py`），
因此首次 `docker compose up` 会自动建好表，不需要额外执行迁移。

- `001_core_tables.sql`：核心表的人类可读 DDL 参考（MySQL 语法），用于 reviewer 对照字段；
  运行时不执行它。其中 `listings` 的来源链路字段、`source_records` 来源台账表与 `properties` 的报价/转载计数
  是本轮「转载对齐原发布」改造新增的。
- 后续如果要做字段变更，建议引入 Alembic，并在本目录按 `00x_<描述>.py` 递增。
- seed 数据只走 `backend/scripts/seed_demo.py`，不要写进 SQL。
