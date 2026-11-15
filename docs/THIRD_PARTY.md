# 第三方组件与研究参考

NodLink 的桌面应用层使用 Python。头部运动接入依赖用户单独安装的桥接服务；眨眼识别使用单独下载的预训练模型。本仓库不随源码分发内核驱动、桥接安装程序或模型二进制。

| 项目 | 用途 | 来源与说明 |
| --- | --- | --- |
| WPods | Windows 蓝牙桥接与本地 AAP 管道 | [项目源码](https://github.com/FPNDev/wpods)，本机验证版本为 v0.3.2；协议参考提交 `f02e957d789428b64a6d715410bd71d92aa7b2d5` |
| USB/IP | 已安装桥接使用的虚拟 USB 控制器 | 由兼容的桥接安装流程提供；本机验证版本为 0.9.8.1；本仓库不分发驱动 |
| MediaPipe Face Landmarker | 摄像头双眼闭合分数 | [官方文档](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker?tab=Python)，模型由下载脚本从 Google 官方固定版本地址获取 |
| Project Gameface | 免个人训练面部控制方案参考 | [Google 项目](https://github.com/google/project-gameface)；没有集成其整套桌面程序 |
| LibrePods | AAP 协议公开研究 | [源码与协议文档](https://github.com/librepods-org/librepods)，参考提交 `89356c08cea3db0b6b54792d9329d09a87cc85fa` |
| aap-head-tracker | 头追通道与数据布局参考 | [原项目](https://github.com/FIocker/aap-head-tracker)，提交 `767bbd37b93979084bdcc3e4dc9b9f13c72f82ce` |
| SoundStage for AirPods | 头追协议布局参考 | [原项目](https://github.com/capriqqw/soundstage-for-airpods)，提交 `114143c27670275a5ef6336b91047d84b8a16eb5` |
| NodLink | 耳机头控交互思路与平台实现参考 | [原项目](https://github.com/gintmr/NodLink) |

`research/source-excerpts/` 保存了早期研究用的少量 C# 参考文件，不参与 Python 应用构建。对应目录保留原 GPL-3.0 许可证与源文件声明，具体授权以各目录和上游为准。Python 依赖、模型及外部桥接软件各自遵循其许可证；这些许可证不因本仓库公开而改变。

下载模型来源和使用流程见 [模型说明](../models/MODEL-SOURCE.md)。模型哈希清单在本机运行下载脚本后生成。

本项目是面向 Windows 的实验性社区方案，与 Apple 没有隶属关系，也不代表上述上游项目对本实现的兼容性背书。
