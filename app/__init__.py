"""Life Manager —— 单人本地使用的 Windows 桌面应用。

模块职责：
    paths       项目内路径解析（所有数据必须落在项目文件夹内）
    models      数据模型与枚举
    storage     SQLite 连接、表结构、迁移、初始数据
    services    业务逻辑（节点、关系、活动、历史事件、备份、导入导出）
    heatmap     热力图统计与 GitHub 风格日历网格
    theme       视觉主题（配色与样式表）
    main_window 主窗口与左侧导航
    pages/*     五个页面：总览 / 进程树 / 热力图 / 活动记录 / 数据
"""

__version__ = "1.41.0"

# 版本说明见项目根目录的 CHANGELOG.md。
# 应用版本（__version__）与数据结构版本（app.storage.SCHEMA_VERSION）相互独立：
# 结构版本变化时，启动会自动创建 data/backups/pre-migration-*.sqlite3 备份后再升级。
