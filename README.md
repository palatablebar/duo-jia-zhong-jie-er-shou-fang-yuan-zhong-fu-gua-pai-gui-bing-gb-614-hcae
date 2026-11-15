# listingmerge · 多家中介二手房源重复挂牌归并

> 客户：安家数据 · 房源中台（7 人）。聚合 5 家中介门店的二手房挂牌 + 4 家平台/数据服务商的转载，供 3 家银行做抵押估价参考。
> 现状：爬虫把直达发布与渠道转载原样入库，同一套房出现 3-5 次、同一中介的挂牌还会被平台重复转载，
> 估价师手动挑一条；面积/楼层/朝向写法不一（`89.5㎡` / `89.5平米` / `建面89.5`），已成交未下架的僵尸挂牌还在拉低均价。

本系统把「采集 → 来源链路（转载对齐原发布）→ 字段解析归一 → blocking → 相似度 → 并查集 → golden record → 风险分诊 → 复核 → 导出」串成一条可追溯链路：
同一套房只留一份主档（价格中位 / 面积众数 / **中介去重计数 = 报价数**），
转载记录保留原发布中介与原挂牌编号，能对上直达记录的只作佐证、由直达记录定报价；
同一中介只算一份报价；对齐不上的转载留在来源台账、不进入银行估价清单，补齐后重算才参与。
价格离群（钓鱼价）与超期无带看（僵尸挂牌）单独进复核队列，导出清单带全部算法读数与来源链路。

## 1. 运行

```bash
cd batch-20261114/projects/gb-614
docker compose up -d --build          # 5 个服务全部 healthy（约 20-40s）
docker compose exec -T backend python -m scripts.seed_demo     # 幂等，可重复执行
docker compose exec -T backend python -m pytest tests -q
BASE_URL=http://localhost:18203 node ../../test-artifacts/gb-614/tests/smoke.mjs
BASE_URL=http://localhost:18203 node ../../test-artifacts/gb-614/tests/screenshots.cjs
docker compose down -v --remove-orphans
```

| 服务 | 端口 | 说明 |
| --- | --- | --- |
| frontend（Nginx 静态站） | 18203 | 原生 HTML/CSS/ES Modules，`/api` 反代到 backend 并剥掉前缀 |
| backend（FastAPI） | 19203 | 全部计算/校验/持久化 |
| db（MySQL 8.0） | 17703 | utf8mb4，启动时 `create_all` 建表 |
| redis | 内部 | Celery broker + worker 心跳 |
| worker（Celery） | 内部 | 10s 心跳 + 每日 07:40 全量重算（`service_healthy` 校验心跳） |

演示账号（密码 `Demo@2026`）：`admin@listingmerge.io` / `analyst@listingmerge.io` / `viewer@listingmerge.io`。

## 2. 六个领域实体

| 实体 | 表 | 作用 | 自然键 |
| --- | --- | --- | --- |
| Agency 中介渠道 | `agencies` | 5 家门店 + 4 家转载渠道（2 信息平台 + 2 数据服务商）、采集入口与口径 | `code` |
| Listing 采集记录 | `listings` | 采集原文 + 归一结果 + 来源性质（直达/转载）+ 原发布中介/编号 + 报价标记 + 所属簇 | `(agency_id, lid)` |
| SourceRecord 来源台账 | `source_records` | 每条转载的原发布中介、原挂牌编号、对齐状态与对齐到的直达记录 | `listing_id`（唯一） |
| Property 房源簇 | `properties` | golden record：报价数（去重中介）/价格中位/面积众数/风险分 + 记录构成 | `cluster_key`（直达成员哈希） |
| MatchRun 归并重算 | `match_runs` | 每次算法 A+B 的输入输出、来源链路口径与对账读数 | `run_no` / `seed_key` |
| Review 复核单 | `reviews` | 风险簇的人工结论（待复核/确认/驳回） | `seed_key` |

来源字段：`record_kind`（`direct` / `repost`）、`origin_agency_id` / `origin_agency_code` / `origin_agency_raw` / `origin_lid`、
`provenance_status`（`direct` / `linked` / `unresolved`）、`provenance_reason`、`source_listing_id`、`is_quote`。

每个实体都贯穿：表 → `models/` → `schemas/` → `services/` → `controllers/` → `routes/` → 前端 `api.js` + 页面。

## 3. 业务模块表（7 个）

| 模块 | 页面 | services + controller | 主要 API |
| --- | --- | --- | --- |
| ① 中介与采集 | `#/agencies` 中介与采集 | `agency_service` + `controllers/agency_controller.py` | `GET/POST /api/agencies`、`GET/PATCH /api/agencies/{id}` |
| ② 字段解析归一 | `#/listings` 挂牌明细（原文 vs 归一 / 来源链路） | `normalizer` + `listing_service` + `listing_controller.py` | `GET /api/listings`、`GET /api/listings/{id}` |
| ③ 来源链路 | `#/sources` 来源台账（转载 ↔ 原发布） | `provenance_service` + `source_service` + `source_controller.py` | `GET /api/sources`、`GET /api/sources/{id}` |
| ④ 分块与相似度 | `#/clusters` 归并簇（成员对照） | `entity_resolution`（blocking / similarity / UnionFind） | 由 `POST /api/runs` 驱动，读数见 `GET /api/runs/{id}` |
| ⑤ 聚类与 golden record | `#/clusters` 归并簇 | `entity_resolution.resolve` + `pipeline_service` + `property_service` + `property_controller.py` | `GET /api/properties`、`GET /api/properties/{id}`、`POST /api/runs` |
| ⑥ 风险与复核 | `#/reviews` 复核与导出 | `risk_rules` + `review_service` + `review_controller.py` | `GET /api/reviews`、`PATCH /api/reviews/{id}`、`GET /api/overview` |
| ⑦ 导出 | `#/reviews` 复核与导出 | `export_service` + `export_controller.py` | `GET /api/exports/{clusters,listings,reviews}.csv`、`GET /api/exports/reconcile` |

横切能力（不计入业务模块）：登录 JWT（`auth_service` / `routes/auth.py`）、审计日志（`middlewares/audit_logger.py` + `audit_service` + `routes/audit.py`）、限流（`middlewares/rate_limit.py`）。

## 4. 来源链路 + 两个算法（纯 Python，结果进页面与导出）

### 算法 A｜实体解析（`backend/src/services/entity_resolution.py`）

- **blocking**：同 `community_norm` + 同 `layout_norm` + 面积桶（5㎡ 一档，取同桶与相邻桶）且 `|Δ面积| ≤ 5㎡`；
- **相似度**：`0.35*area_close + 0.25*price_close + 0.20*floor_match + 0.10*orientation + 0.10*title_token`
  - `area_close = 1 − |Δa|/6`，`price_close = 1 − |Δp|/均值/0.30`（30% 价差处归零，跨中介报价天然差 1-3%），
    `floor_match` 同层 1.0 / 同区间 0.6，`orientation` 归一后相等 1.0，`title_token` = 标题字符二元组 Jaccard；
- **阈值 0.80 连边**，并查集 + 传递闭包；簇置信 = 簇内最大成对分（`max_pair_score`）；
- **只让直达记录入算法**：平台/数据服务商的转载不参与 blocking 与相似度，不自己成簇；
- **一中介一份报价**：同一中介在簇内有多条直达发布时，只保留一条报价（在售优先，其次入库更晚），
  其余直达记录与「已对齐」的转载只作佐证；
- **golden record**：价格取报价记录的中位、面积取众数（并列取小）、中介数/报价数去重计数、价差 = p90 − p10。

### 来源链路（`backend/src/services/provenance_service.py`）

- 转载记录按 `(原发布中介, 原挂牌编号)` 找回直达记录：`origin_agency_code` 或台账名匹配已登记渠道，
  `origin_lid` 在该中介下找到 `lid` 相同且 `record_kind=direct` 的记录 → `provenance_status=linked`；
- 对齐不上（渠道未登记 / 编号找不到 / 缺编号）→ `provenance_status=unresolved` 并给出 `provenance_reason`；
- `unresolved` 的转载只留在来源台账，`property_id` 为空、`is_quote=false`，不进入估价清单，补齐后重算自动参与；
- 台账 `source_records` 只 upsert 不删除，保证转载归并不丢记录。

### 算法 B｜僵尸与钓鱼挂牌识别（`backend/src/services/risk_rules.py`）

- 只在**报价记录**（每个中介一份直达发布）上识别，重复直达与转载不参与，避免转载次数放大离散度；
- 簇内价格离散度 `dispersion = (p90 − p10) / median`；`dispersion > 0.10` 打 `spread`；
- 挂牌价相对簇中位**低于 10% 以上** → `bait`（钓鱼价），同时落 `bait_lids`；
- 挂牌 **> 180 天且带看数为 0** → `stale`（僵尸挂牌），落 `stale_lids`；
- 风险分 `0-100 = 45*钓鱼 + 35*僵尸 + min(20, dispersion*200)`；`≥65` 高风险、`≥35` 中风险、`>0` 低风险、否则正常；
- 阈值常量集中在 `backend/src/constants/limits.py`，前端不复制任何算法结果。

## 5. 共享枚举出现位置

| 枚举组 | 后端 | 前端 |
| --- | --- | --- |
| 风险等级 `high/medium/low/none` | `constants/enums.py::RiskLevel` + `RISK_LEVEL_LABELS` | `js/config.js::ENUMS.riskLevel`、`riskTone()` |
| 风险标签 `bait/stale/spread` | `constants/enums.py::RiskTag` + `RISK_TAG_LABELS` | `js/config.js::ENUMS.riskTag`、`tagTone()` |
| 挂牌状态 `active/sold/off_market` | `constants/enums.py::ListingStatus` | `js/config.js::ENUMS.listingStatus`、`statusTone()` |
| 渠道类型 `broker/platform/feed` | `constants/enums.py::AgencyKind` | `js/config.js::ENUMS.agencyKind` |
| 来源性质 `direct/repost` | `constants/enums.py::RecordKind` + `RECORD_KIND_LABELS` | `js/config.js::ENUMS.recordKind`、`recordTone()` |
| 对齐状态 `direct/linked/unresolved` + 待补齐原因 | `enums.py::ProvenanceStatus` + `PROVENANCE_STATUS_LABELS` / `PROVENANCE_REASON_LABELS` | `js/config.js::ENUMS.provenanceStatus/provenanceReason`、`provenanceTone()` |
| 复核状态/原因、运行状态、触发方式 | `enums.py::ReviewStatus/ReviewReason/RunStatus/TriggerKind` | `js/config.js::ENUMS.reviewStatus/reviewReason/runStatus/trigger` |
| 角色与权限点 | `constants/roles.py::PERMISSIONS` | `js/config.js::PERMISSIONS` + `can()` |

## 6. 页面（7 个）

1. `#/login` 登录 —— 橱窗房源贴：4 张挂牌便签（3 家原文 + 1 张已归并）。
2. `#/overview` 总览 —— 采集记录（直达/转载）/ 归并簇 / 报价数 / 风险簇四张价签 + 渠道覆盖 + 风险台账 + 近 7 天重算。
3. `#/agencies` 中介与采集 —— 9 家渠道表 + 抽屉（采集口径、挂牌样例、暂停采集）+ 登记渠道。
4. `#/listings` 挂牌明细 —— 原文 → 归一 对照、来源链路（直达/转载、原发布、对齐状态）、所属簇；按中介/状态/风险/来源筛选。
5. `#/sources` 来源台账 —— 转载记录 × 原发布中介/编号 × 对齐状态，待补齐的单独筛出；抽屉给出待补齐原因与是否进清单。
6. `#/clusters` 归并簇 —— 簇列表（报价数/中介/价差/置信/风险）+ 抽屉（**每家中介采用的发布 + 相应转载**、成员对照表、golden record、钓鱼/僵尸命中）+ 全量重算 + 运行历史。
7. `#/reviews` 复核与导出 —— 复核队列 + 复核结论表单 + 三种 CSV 导出 + 对账读数 + 管理员审计日志。

共用组件：`ui.js` 的 `table` / `drawer` / `filter-bar` 在 4-7 四个页面复用；`badge`、`kv`、`emptyState` 全站复用。

## 7. seed 口径（幂等）

`backend/scripts/demo_config.py` 确定性生成 386 个房源簇：**572 条直达发布**（540 条基线 + 32 条同店重复发布）+ **72 条转载** = **644 条采集记录**，
覆盖 24 个小区、4 种户型；原始写法随机混用 `㎡ / 平米 / 建面 / 建筑面积 / 平方米（套内…）`、`万 / 元 / 总价…万 / 万（可谈）`、`3/18 / 3层/共18层 / 低楼层(共18层)`；
含 2 条脏数据（楼层缺失、价格面议）。转载记录中 **48 条能对齐直达发布**（只作佐证）、**24 条待补齐**（三种原因各 8 条，留来源台账、不进估价清单）。

跑完算法 A/B 后：**386 个房源簇**（41 个多成员簇，去重率 32.52% 按直达口径）、
**540 份报价 = 540 个去重中介计数**（32 条同店重复 + 48 条转载都不重复计数）、
**6 个风险簇**（1 个钓鱼价簇含 2 条 bait、5 条僵尸挂牌）、**6 张待复核单**、**24 条待补齐转载**。
特别注意：`翠湖名邸 3-2-1 89.5㎡` 有 4 家门店各一条直达 + 1 条同店重复发布 + 1 条对齐转载，
价格 598/602/606/610 万，价差 12 万，中位 604 万（4 份报价，未被转载次数放大）。

seed 结束打印一行（连续两次逐字一致）：

```
SEED_SUMMARY brand=listingmerge users=3 sources=9 runs=2 findings=386 pending=6 records=644 quotes=540 unresolved=24
```

## 8. 权限矩阵

| 能力 | admin | analyst | viewer |
| --- | --- | --- | --- |
| 读总览/挂牌/来源台账/簇/复核 | ✅ | ✅ | ✅ |
| 登记/改中介渠道 | ✅ | ❌ 403 | ❌ 403 |
| 发起全量重算 | ✅ | ✅ | ❌ 403 |
| 提交复核结论 | ✅ | ✅ | ❌ 403 |
| 导出 CSV | ✅ | ✅ | ❌ 403 |
| 审计日志 | ✅ | ❌ 403 | ❌ 403 |

无 token 一律 401；非法参数 422；重复编码 409。

## 8.1 对账口径（`GET /api/exports/reconcile`）

| 读数 | 口径 | seed 期望 |
| --- | --- | --- |
| `record_count` | `listings` 总行数 | 644 |
| `direct_count` / `linked_repost_count` / `unresolved_repost_count` | 直达 / 已对齐转载 / 待补齐转载 | 572 / 48 / 24 |
| `agency_count` | 各簇 `agency_count` 之和（去重中介） | 540 |
| `quote_count` / `quote_rows` | 各簇 `quote_count` 之和 / `is_quote=true` 行数 | 540 / 540 |
| `cluster_rows` / `listing_rows` / `review_rows` | 三个 CSV 的数据行数（不含表头） | 386 / 644 / 6 |
| `balanced` | 记录守恒且 中介数 = 报价数 = 采用发布行 | true |

## 9. 目录

```
backend/src/{config,constants,controllers,middlewares,models,routes,schemas,services,utils}
backend/scripts/{seed_demo.py,demo_config.py,worker_healthcheck.sh}
backend/migrations/001_core_tables.sql   # DDL 参考（运行时由 create_all 建表）
frontend/site/{index.html,css/{tokens,app}.css,js/{config,api,store,ui,app}.js,js/pages/*.js}
docker-compose.yml  .env  README.md
```
