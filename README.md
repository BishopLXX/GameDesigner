# GameDesigner

GameDesigner 是一个面向游戏设计师的中文桌面软件，用于在无限画布上编辑节点式设计文档，适合规划开发周期、设计科技树/天赋树、整理数值表格和可视化配置关系。

项目为Vibe Coding。
当前项目只维护 exe 桌面版：Python + PySide6。

## 主要功能

- 无限画布，鼠标滚轮缩放。
- 空格 + 鼠标左键拖动画布。
- 右键空白画布创建节点、画布节点或超链接节点。
- 左键选中节点或连线，`Del` 删除选中项。
- 右键节点可编辑、连接、删除。
- 右键连线可编辑标签、设置曲线/直线/折直样式、删除连线。
- 右键空白画布可创建蓝图组；蓝图组采用虚幻蓝图注释框风格，拖动标题条可带动组内节点。
- 空白画布左键拖拽可框选多个节点，多选后可一起移动，`Del` 可批量删除。
- 节点拖入蓝图组会自动成为组内节点，拖出后会自动脱离蓝图组。
- 节点可拖拽移动，支持网格吸附和节点对齐吸附，按住 `Ctrl` 拖动可临时关闭吸附。
- 节点右下角可拖拽缩放。
- 节点会记录创建顺序，节点右上角显示序号；删除中间节点后，后续序号会自动前移。
- 双击节点打开卡牌式编辑器，可编辑文字、图片、尺寸、颜色和字体大小。
- 字段类型支持文本、长文本、整数、数字、布尔、枚举、颜色、日期、图片等。
- 只有图片类型字段会显示图片选择；字段名、类型、内容默认进入所有画布 CSV，图钉只控制额外属性。
- 节点编辑器内可右键空白区域新增子卡片；子卡片属性右侧图钉用于控制额外属性是否进入所有画布 CSV。
- 支持多个画布标签页，未保存标签显示 `*`，关闭时提示保存。
- 支持项目源目录和输出目录设置。
- 支持保存/打开 `.gdc` 工程清单；画布、模板和超链接文档拆分保存在旁边的 `.gdc.files` 子文件夹里，旧版 `.gdesigner.json` 仍可读取。
- 超链接节点支持 `.md` 和 `.txt`，创建后会在工程子文件夹内生成文件，可双击打开编辑、保存和删除；节点名称会同步作为文档文件名。
- 项目设置可勾选“超链接文件在输入目录保留复制本”，方便 Unity 或其他工具直接读取。
- 支持导出所有画布 CSV：每个画布导出一个 CSV，第一行列名，第二行类型，第三行开始为节点数据，可按画布勾选导出连线、蓝图组归属，以及节点/蓝图位置信息。
- 所有画布 CSV 可按创建顺序、按 X 往右、按 Y 往下三种方式排列节点行。
- 支持黑夜工作模式。

## 项目结构

```text
GameDesigner/
├─ source/                      Python 桌面版源码
│  ├─ main.py                   exe 入口
│  ├─ requirements.txt          运行和打包依赖
│  └─ gamedesigner/
│     ├─ app.py                 主窗口、菜单、标签页、项目保存
│     ├─ qt_canvas.py           无限画布、节点、连线、右键菜单
│     ├─ qt_dialogs.py          项目设置、节点编辑、模板管理
│     ├─ models.py              项目数据结构
│     ├─ storage.py             .gdc 工程持久化
│     ├─ csv_io.py              所有画布 CSV 导出
│     ├─ project_files/         工程子文件夹和外部文件管理
│     └─ ui/                    独立 UI 对话框
├─ tests/                       数据读写测试
├─ release/                     exe 输出目录
└─ build_release.bat            一键打包 exe
```

## 本地运行

```powershell
py -3.13 -m pip install -r .\source\requirements.txt
$env:PYTHONPATH=".\source"
py -3.13 .\source\main.py
```

## 打包 exe

根目录执行：

```powershell
.\build_release.bat
```

输出文件：

```text
release/GameDesigner.exe
```

也可以使用 PowerShell 脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\source\build_release.ps1
```

## 测试

```powershell
$env:PYTHONPATH=".\source"
py -3.13 -m unittest discover -s tests
```
