# 眨眼预训练模型来源

使用 Google MediaPipe Face Landmarker 官方 `float16/1` 模型包，不需要用户提供训练图片或训练个人模型。

- [官方任务文档](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker?tab=Python)
- [固定版本下载](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task)
- [官方模型卡](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf)
- 参考现有使用方式：[Google Project Gameface](https://github.com/google/project-gameface)。该仓库已归档，本项目使用当前 MediaPipe Python API，没有安装其整套控制程序。

下载信息与 SHA256 记录在 `face_landmarker.json`。图像只在本机摄像头子进程内存中处理，不保存、不上传；只向桌面程序发送双眼闭合分数、人脸数量和时间戳。
