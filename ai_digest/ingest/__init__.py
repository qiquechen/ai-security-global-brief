"""采集与数据底座（H2 分工）。

本包由 H2 同学负责实现，接口契约见 docs/摘报系统开发协同分工计划_v1.0.md：
- config/sources.json 逐源适配（rss/sitemap/page）；
- 入库表结构（articles 表字段）；
- 每小时增量抓取与代理接入（.env 的 PROXY）。
"""
