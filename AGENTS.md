# 开发约定

- 使用 Python 3.12 与 `uv sync --locked`。提交前运行 `uv run ruff check .` 和 `uv run pytest`。
- 默认测试使用本地 fixture。采集公开价格时按来源审核条款、robots.txt 与实际访问限制。启用定时任务前在 `access_review` 记录判断依据。
- 价格以 Decimal 表示，时间统一使用 Asia/Tokyo。未知属性保留空值。记录商品价格的门店和渠道。
- 不提交数据、原图、数据库、凭证或个人小票。小票默认在本机处理。
