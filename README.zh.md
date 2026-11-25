# LogScope

一个轻量的本地日志分析工具，支持灵活的高亮、过滤与实时监控。

LogScope 是基于 Python 与 PySide6 开发的桌面日志查看与 tail 工具。

## 功能

- Tail / Follow tail、多标签页、拖放打开、最近文件
- 正则搜索（上一个 / 下一个 / 全部）与各面板内查找（Ctrl+F、F3）
- 过滤视图（F4 或 Ctrl+Shift+F；正则、反选、上下文行、Tail 过滤、过滤结果转新标签页）
- 行高亮规则（整行或仅匹配词，自定义颜色）
- 书签与标签、emoji 标记
- Columnizer 自动识别与用户自定义正则规则
- 支持 UTF-8 / UTF-16 / GBK，截断 / 轮转检测
- 便携模式
- 多文件时间同步与合并视图
- 独立窗口与灵活布局
- 可调字体、颜色、网格线与行间距

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e .[dev]
```

## 快速开始

```bash
logscope path\to\app.log          # 打开文件
logscope                          # 恢复上次会话
logscope --portable app.log       # 配置保存在程序旁边
logscope --version                # 输出版本号
```

测试与基准：

```bash
pytest
python tools/bench.py --mb 500
python tools/logwriter.py demo.log --rate 20000 --seconds 60 --rotate-mb 5
```

## 打包为可执行文件

Windows 下在 CMD 中运行：

```cmd
powershell -ExecutionPolicy Bypass -File "packaging\build_exe.ps1"
```

或在 PowerShell 中运行：

```powershell
.\packaging\build_exe.ps1
```

Linux/macOS：

```bash
./packaging/build_exe.sh
```

在 Windows 的 WSL 中构建：

```bash
cd /mnt/d/Dev/my_projects/logscope
chmod +x packaging/build_exe.sh
./packaging/build_exe.sh
```

为获得更好性能，建议先把项目复制到 WSL 文件系统（例如 `~/logscope`）再构建。

结果位于 `dist/LogScope/`（Windows 为 `LogScope.exe`）。

## 项目结构

```
src/logscope/core/   不依赖 Qt 的核心逻辑
src/logscope/ui/     PySide6 界面层
plugins/             示例 columnizer 插件
tests/               单元测试
packaging/           Windows 打包文件
tools/               基准与演示日志生成工具
```

## 参与贡献

欢迎提交 Issue 或 Pull Request。

## 许可证

本项目采用 MIT 许可证，详见 [LICENSE](LICENSE)。