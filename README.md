# Japan Grocery Intelligence

面向日本超市的自托管价格清单。系统支持按门店查看当期优惠、在地图圈选同类比价、采集公开价格及导入传单。个人坐标、模型配置和价格数据库存放在本地。

新安装请从[自托管指南](SELF_HOSTING.md)开始。

## 安装和运行

```bash
uv sync --locked
uv run alembic upgrade head
uv run grocery discover
uv run grocery check-sources
uv run grocery crawl --all
uv run grocery scheduler
uv run grocery report
```

从项目根目录执行。可复制 `.env.example` 自定义数据库、来源文件和请求间隔。已有早期本地数据库时，先备份并比对初始版本表结构，再运行 `uv run alembic stamp 4c21091ad53b`。如需验证动态页面，运行 `uv run playwright install chromium`。Scrapling 0.5.1 已移除 `DynamicFetcher`。此版本用普通 Playwright 渲染本地测试页面，并交给 Scrapling 解析。

## 本机导入

图片解析需要 Tesseract 和 `jpn.traineddata`。macOS 可安装 `brew install tesseract tesseract-lang`，用 `tesseract --list-langs` 验证日文模型。PDF 优先用 PyMuPDF 提取文本，纯图页回退到同一日文 OCR。

```bash
uv run grocery import-flyer ./flyer.jpg --store kasumi-chiba-minato
uv run grocery import-receipt ./receipt.jpg --store ok-chiba-saiwaicho
uv run grocery import-receipt ./receipt-reviewed.txt --store ok-chiba-saiwaicho --accept-reviewed
uv run grocery search '牛乳'
uv run grocery history '牛乳'
uv run grocery compare '卵'
uv run grocery export --format json
uv run grocery export --format csv
```

导入默认只预览。核对照片与候选商品、价格和税口径后再保存。本地 UTF-8 转写支持传单格式 `牛乳 1L 税込 213円`，以及小票格式 `牛乳 1L 税込 ¥213`，可附 `2026/11/26 12:32` 购买时间行。小票需要人工核对其商品行的税口径。遇到整张小票统一计税或跨行折扣，先人工转写并保留未知字段为空。当前只保留可解析商品行和购买时间，会员编号、支付和合计行会被剔除。原图及 OCR 全文保留在调用者提供的本机文件。

价格比较按已知 JAN、渠道、税口径和销售条件分组。观测时间和有效期以 JST 保存，历史查询可查看来源与证据哈希。

## 实际数据示例

```bash
uv run python scripts/estat_baseline.py --download
```

该命令获取公开发布的千叶市 2026 年 10 月零售统计表。输出为 `data/exports/estat-chiba-2026-10.json`、同名 CSV 和 `data/estat-baseline.db`。门店库为 `data/grocery.db`。导出数据、数据库、原始文件和凭证均在 Git 忽略范围内。

## 商品网页

在项目目录运行 `uv run python -m grocery_intelligence.web --database`，然后打开 <http://127.0.0.1:8787/>。从顶部进入地图，可以修改比价半径并点击地图移动圆心。位置与数据配置见[自托管指南](SELF_HOSTING.md)。

「全部商品」可以按门店、类别、商品名或 JAN 查找。点击商品可看价格记录和证据。页面读取 `data/exports/` 中的本机传单和门店价格 CSV。放入新导出后刷新即可看到。

## Docker

```bash
docker build -t grocery-intelligence:phase1 .
docker run --rm -v "$PWD/data:/app/data" grocery-intelligence:phase1 alembic upgrade head
docker run --rm -v "$PWD/data:/app/data" grocery-intelligence:phase1 grocery report
```

镜像自带日文 Tesseract 模型。普通浏览器渲染测试在 macOS 上执行。

## 验证

```bash
uv run ruff check .
uv run pytest --cov=grocery_intelligence --cov-fail-under=80
uvx --from pyright pyright -p pyrightconfig.json
```
