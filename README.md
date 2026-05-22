# GameDesigner

GameDesigner 是一个面向游戏设计师的中文桌面软件，用于在无限画布上编辑节点式设计文档，适合规划开发周期、设计科技树/天赋树、整理数值表格和可视化配置关系。

当前项目只维护 exe 桌面版：Python + PySide6。

## 主要功能

- 无限画布，鼠标滚轮缩放。
- 空格 + 鼠标左键拖动画布。
- 右键空白画布创建节点。
- 左键选中节点或连线，`Del` 删除选中项。
- 右键节点可编辑、连接、删除、创建模板。
- 右键连线可编辑标签、设置曲线/直线/折直样式、删除连线。
- 节点可拖拽移动，支持网格吸附和节点对齐吸附，按住 `Ctrl` 拖动可临时关闭吸附。
- 节点右下角可拖拽缩放。
- 双击节点打开卡牌式编辑器，可编辑文字、图片、尺寸、颜色和字体大小。
- 支持多个画布标签页，未保存标签显示 `*`，关闭时提示保存。
- 支持项目源目录和输出目录设置。
- 支持保存/打开 `.gdesigner.json`，导入/导出 CSV。
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
│     ├─ storage.py             JSON 持久化
│     └─ csv_io.py              CSV 导入导出
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
