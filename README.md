# Wishclaim · 礼物愿望认领

发布 → 认领锁定（互斥+TTL）→ 核销/释放。

| 服务 | 端口 |
| --- | --- |
| 前端 | 5200 |
| API | 10200 |

```bash
docker compose up --build
pytest backend/app/tests
```

## 投影一致性门禁（墙列表 vs 详情）

`projection_cache` 是墙卡/详情双通道投影旁路，由 seed 与各写路径同事务维护；
门禁对每个 wish 对照两侧规范字段（标题/status/claimer/expires_at）的校验和。

```bash
cd backend
python -m app.integrity.cli check            # 只报告；检出漂移退出码 1
python -m app.integrity.cli check --fix      # 重投影修复：只重写投影缓存，不动 wishes 业务字段
python -m app.integrity.cli skew             # 夹具：故意写歪一侧（验证门禁会报警）
python -m app.integrity.cli align            # 从源表全量重投影
```

报告含 wish_id、墙侧摘要、详情侧摘要与字段级对照；对齐后连跑退出码 0 且 entries 为空。

0-1：`wish_comment` / `secret_santa` / `price_cap`。
