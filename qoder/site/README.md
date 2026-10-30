# 展示站（Qoder Sites）

纯静态两文件：`index.html` + `style.css`，无构建、无外部依赖、无 CDN。发布前只需要替换三处截图。

## 本地预览

```powershell
cd qoder\site
python -m http.server 8090
# 打开 http://127.0.0.1:8090
```

## 截图（一条命令）

应用跑起来后：

```powershell
cd qoder
.\scripts\screenshots.ps1        # 用本机 Edge/Chrome 无头模式截四个页签
```

产物写到 `site/assets/{today,replay,compare,evidence}.png`。无头模式被安全软件拦下时，脚本会提示失败，用 `Win+Shift+S` 手工截同样四个页签、存成同名文件即可。

四个页签都支持 URL hash 直达：`#today`、`#replay`、`#compare`、`#evidence`。

## 现状

四张截图已经用真实运行截好（`assets/today.png`、`replay.png`、`compare.png`、`evidence.png`），页面里的数字来自真实 Server 上的运行结果。

## 发布前还要做的两件事

1. **补仓库链接**：页脚可加一行指向代码仓库与 RUNBOOK（当前刻意留空，避免链接指向错误地址）。
2. **核对数字**：改过代码就重跑 `.\scripts\verify.ps1`，再更新页面里的 PASS 数与截图。

## 需要重截时

```powershell
cd qoder
.\scripts\start.ps1
.\scripts\screenshots.ps1
```

## 发布（Qoder Sites）

1. 用**个人账号**登录 Qoder（企业版账号无法设置公开访问）。
2. 在 Qoder 中打开本项目，用 Sites 发布 `qoder/site` 目录。
3. 可见范围设为「任何人都可访问」。
4. 发布后用浏览器**无痕窗口**打开，确认未登录也能访问。
5. 以后改内容要再次点「发布」才会更新线上版本；评审结束前不要暂停或删除站点。

## 不要放上去的东西

- 任何 token、真实姓名、本机绝对路径。
- 未脱敏的使用效果截图（同学姓名、联系方式）。
- 占位图：评审点开看到"待替换"会直接减分，截图没准备好就先别发布这一节。
