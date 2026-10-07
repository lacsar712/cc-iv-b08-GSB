# 光伏组串IV扫描台

扫描员提交组串开路电压、短路电流与填充因子。写入后走 PostgreSQL 通知通道叫醒独立工人，工人不轮询空转。填充因子不低于 0.72 为合格，否则衰减。页面是 Vue 3。

## 清洗超期一刀切暂停

顶栏可点开「清洗超期专页」，阈值区、暂停记录流水区与说明区并排：

- 清洗周期超期（越过 上次清洗 + 阈值天数）时可一刀切暂停整场扫描：写入通道拒收新单，工人同时停止认领。
- 超期检测在保存阈值时触发，一旦超期自动落闸并记入流水。
- 闸口关闭时交单被拒收，拦住痕迹与真实拒收同批落库（同事务写入 `pause_events` 后返回 409）。
- 阈值空着不许开闸：未设阈值时闸口保持关闭，`POST /api/cleaning/resume` 直接 400。
- 观察员（watcher）能看阈值与暂停记录，不能扳暂停钮（暂停/开闸/改阈值接口仅扫描员可用，前端按钮也不渲染）。

接口：`GET /api/cleaning`（登录即可看）、`PUT /api/cleaning/threshold`、`POST /api/cleaning/pause`、`POST /api/cleaning/resume`（后三个仅扫描员）。

验收流：把清洗阈值压得很低（如 0.0001 天）→ 超期自动落闸 → 交单 409 且流水有拒收记录 → 开闸恢复 → 再送 201 成功。

## 技术栈

- 后端：Litestar、Uvicorn、psycopg 同步写入
- 工人：`LISTEN/NOTIFY` 唤醒后认领
- 前端：Vue 3、Vite、nginx 反代 `/api`

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3202 |
| 接口 | http://localhost:8202 |
| PostgreSQL | localhost:54402（库名 `pvivscan`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| scanner | scan123456 | 可提交 |
| watcher | watch123456 | 只读 |

## 启动

```bash
cd projects/22-pv-string-iv-scan
docker compose up --build
```

健康检查：`GET http://localhost:8202/api/health`

## 种子

| 组串 | 填充因子 | 结论 |
|------|----------|------|
| 阵列A-串03 | 0.78 | 合格 |
| 阵列B-串11 | 0.61 | 衰减 |
