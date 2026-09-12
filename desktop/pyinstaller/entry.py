"""PyInstaller 入口:等价 `python -m data_agent.infrastructure.main`。

frozen 下 `sys.executable <脚本>` 会再次进入本入口(bootloader 把参数放进 argv),
故首参为 .py 脚本时分发执行该脚本(sandbox 的 solver.py 子进程依赖此机制)。
"""

import sys

if getattr(sys, "frozen", False):
    # logfire 的 pydantic 插件无条件 inspect.getsource(pydantic 内部实现),
    # 打包后无源码直接 OSError。禁用该 patch 即可(仅修复其云 pickle 边界 bug,与本服务无关)。
    import logfire.integrations.pydantic as _lp

    _lp._patch_PluggableSchemaValidator = lambda: None

    # 首参为 .py 脚本时分发执行(sandbox 的 solver.py 子进程依赖此机制):
    # frozen 下 `sys.executable <脚本>` 会再次进入本入口,需主动切换到脚本。
    if len(sys.argv) > 1 and sys.argv[1].endswith(".py"):
        import runpy

        sys.argv = [sys.argv[1], *sys.argv[2:]]
        runpy.run_path(sys.argv[0], run_name="__main__")
        sys.exit(0)

from data_agent.infrastructure.main import main

if __name__ == "__main__":
    main()
