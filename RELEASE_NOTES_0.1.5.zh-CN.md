# More Than Done 0.1.5

## 首次启动与运行组件

完整解压 Windows ZIP 后双击 MoreThanDone.exe。程序会先检测微软 WebView2 界面运行组件；已有组件时直接启动，缺少时询问是否安装，确认后显示下载、数字签名验证和安装进度，完成后自动打开工作台。

补装组件需要联网，下载来自微软官方，执行前验证有效的微软数字签名。自动安装失败时提供微软官方下载页，可选择 Evergreen Standalone Installer x64，在其他电脑下载后拷贝安装，再重新打开软件。当前包不自带完整离线运行时。无需为打开软件另装 Python，也不需要打开 Edge 浏览器；执行 Python 学习任务仍需可用的 Python 解释器。

## 归档与数据位置

项目和对话的归档记录保存在 EXE 同目录的 data/conversations.json，归档不会移动工作区文件夹。点击侧栏“已归档”可以恢复。源码运行时为源码目录下的 data/conversations.json；指定 --data-dir 时使用指定目录。

data/agent_config.json 可能包含明文 API Key。分享程序、提交问题或上传源码时不要带上自己的 data 文件夹。升级前关闭旧版本，将原 data 文件夹保留在新 EXE 旁。

本发行包不含个人 API Key、配置、对话和旧开发历史。项目采用 MIT 许可证，第三方声明见 THIRD_PARTY_NOTICES.md 和 licenses 文件夹。

## 其他功能

保留 0.1.4 的项目/对话管理、流式回复、操作审批、教学步骤与反馈、长代码及表格独立滚动。
