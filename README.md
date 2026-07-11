# LOL Helper

一个贴靠《英雄联盟》客户端侧边显示的 Windows 选角工具。它通过本机 LCU API 与 Riot Data Dragon 实现：

- 找到对局后自动接受；
- 自动读取当前选角界面的候选英雄和公共英雄池；
- 点击一个英雄后将其设为本局唯一目标，服务器允许时立即选择或交换；
- 可随时点击另一个英雄来替换旧目标。
- 使用 Data Dragon 英雄头像展示本局选择，悬停查看 ID、定位、被动和 Q/W/E/R 技能说明。

> LCU 是 Riot 未正式支持的本地接口，版本更新可能改变字段或端点。本工具不会修改客户端、绕过服务器冷却，也不进行任何游戏内操作。使用前请自行确认并遵守 Riot 当前规则。

## 运行

要求 Windows 10/11 与 Python 3.11+，无需安装第三方包。

```powershell
python main.py
```

程序启动时会自动请求 Windows 管理员权限。通过 UAC 确认后，管理员实例会继续启动；原普通权限进程会立即退出。

先启动英雄联盟客户端并登录，再启动本工具。首次使用建议：

1. 启动工具，全部自动化功能会默认开启；
2. 进入选角后，从自动刷新的头像网格中点击目标英雄；
3. 如需换目标，直接点击另一个头像；
4. 鼠标悬停头像可查看英雄和技能信息；
5. 如字段未正确识别，点击底部“诊断”保存会话；
4. 若新版客户端字段不兼容，将 `diagnostics` 目录中的 JSON 用于排查（文件会自动遮盖常见身份字段）。

配置保存在 `config/settings.json`，日志与诊断分别位于 `logs`、`diagnostics`。

## WeGame / 国服权限

WeGame 可能以管理员权限启动客户端，导致普通权限的助手无法读取 LCU 启动参数。此时界面会显示明确提示并出现“管理员重启”按钮；点击后通过 Windows UAC 重启即可。标准 Riot 客户端也支持从 `lockfile` 自动发现连接信息。

英雄头像、中文名称及技能信息来自 Riot Data Dragon，并缓存到本地 `cache/ddragon`。本局候选、公共池和当前选择来自实时选角会话。目标只在本局内有效，离开选角后自动清空。

## 打包（可选）

使用项目内的 PyInstaller 配置生成带管理员权限清单和 Windows 版本信息的单文件程序：

```powershell
python -m pip install -r requirements-dev.txt
python -m PyInstaller --clean --noconfirm LOL-Helper.spec
```

输出文件位于 `dist/LOL-Helper.exe`。打包版本会把配置、日志、诊断和 Data Dragon 头像缓存写入 `%LOCALAPPDATA%\LOL Helper`。

## 开发与测试

```powershell
python -m unittest discover -s tests -v
```
