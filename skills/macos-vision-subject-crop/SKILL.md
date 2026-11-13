---
name: macos-vision-subject-crop
description: 在 macOS 上让视频裁剪窗口跟随主体（人物）——用系统 Vision 人体检测生成位置轨迹，编译成 ffmpeg crop 动态表达式，实现横屏转竖屏时人物始终居中。当需要智能裁剪、主体跟踪、人物居中、自动构图、speaker framing、竖屏重构图时使用。
agent_created: true
---

# macOS 视频主体跟踪裁剪（人物居中）

用 macOS 原生 Vision 检测人体 → 生成横向位置轨迹 → 编译成 ffmpeg crop 的动态 x 表达式，
让固定宽高的裁剪窗口（如 9:16 竖屏）跟着主体移动。无需 OpenCV / 无额外模型文件。

## 何时用
- 横屏转竖屏时人物被裁到画外 / 贴边，希望人物始终居中。
- 固定机位视频想输出跟随主体的竖屏/方形裁剪。
- 需要"自动构图""智能裁剪""主体跟踪"。

## 为什么用 Vision 而不是 OpenCV
- **OpenCV 5.x 已移除 Haar 级联 xml**（`cv2.data.haarcascades` 下没有 `haarcascade_*.xml`），
  人脸/人体检测直接不可用；改用 DNN 又要额外下载模型文件。
- Vision 的 `VNDetectHumanRectanglesRequest` 系统自带、约 6ms/帧、侧身/背影/舞蹈姿态都准。
- 依赖只有 PyObjC（约 14MB），比打进 OpenCV（100MB+）轻得多。

```bash
pip install pyobjc-framework-Vision   # 会带上 Cocoa/Quartz/CoreML
```
运行所需最小包：`objc Vision Foundation AppKit Quartz CoreML CoreFoundation PyObjCTools`

## 流程
1. **抽帧**：一次 ffmpeg 调用导出小图序列到临时目录（控制采样点数，别按帧全导）。
   ```python
   cmd = [ffmpeg, "-v", "error"] + (["-ss", f"{start}"] if start else []) \
       + ["-i", src, "-t", f"{dur}", "-an", "-sn",
          "-vf", f"fps={n/dur},scale={W}:{h}", "-q:v", "6",
          "-f", "image2", os.path.join(tmp, "%06d.jpg")]
   ```
   采样 4fps、上限 240 点即可（长视频自动降密度）。
2. **检测**（后台线程必须自建 autorelease pool）：
   ```python
   import Vision
   from Foundation import NSURL, NSAutoreleasePool
   pool = NSAutoreleasePool.alloc().init()
   try:
       h = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(p), None)
       req = Vision.VNDetectHumanRectanglesRequest.alloc().init()
       ok, _ = h.performRequests_error_([req], None)
       for r in (req.results() or []):
           b = r.boundingBox()          # 归一化，原点在左下
           cx = float(b.origin.x) + float(b.size.width) / 2
           area = float(b.size.width) * float(b.size.height)
   finally:
       del pool
   ```
3. **挑主体**（多人场景关键）：
   - 丢掉面积 < 最大者 30% 的远景小人；
   - 剩下的人横向间距 ≤ 0.30 → 按面积加权取整体中心（同框共舞时居中于两人之间）；
   - 分散时若有上一帧位置，选**离它最近**的人，避免镜头在多人之间来回跳。
4. **平滑**：EMA（alpha≈0.6）+ 每采样点限速（≈0.06 画面宽）+ 边界收敛（0.10~0.90）
   + 3 点移动平均。检测缺失时沿用上一位置并向中心缓慢回拉（pull≈0.03）。
5. **编译表达式**：轨迹 → 分段线性插值的 crop x 表达式（ramp 求和，避免深层嵌套 if 爆栈）：
   ```
   x = X0 + Σ dX_i * min(max((t - T_i)/DT_i, 0), 1)
   ```

## 必踩的坑（照抄即可避）
1. **表达式里的逗号必须转义成 `\,`**。滤镜语法以逗号分隔参数，不转义会报
   `No such filter: '0)'`。写成 `min(max((t-0.3333)/0.3333\,0)\,1)`。
2. **crop 必须排在 hflip 之前**。镜像会翻转画面坐标系，若在 hflip 之后按原图坐标裁剪，
   人物会被裁到画外、输出近乎全黑（实测 8 秒视频只有 16KB）。
3. **x 结果偶数化**：`2*floor((expr)/2)`，避免 yuv420p 色度错位。
4. **首帧直接采用检测值**，别从 0.5 用 EMA 慢慢挪，否则开头几秒人物明显偏。
5. **同文件只分析一次**：整片轨迹缓存，切割分段按时间切片（`[(t-start, cx) for t, cx in track if start<=t<=end]`），
   否则 N 段就要重复解码 N 次。
6. **降级**：无 Vision / 未检到人 → 退回固定居中裁剪并写日志，绝不阻断处理。

## 验证方法（用检测本身打分）
对产物按固定 fps 抽帧，再跑一遍 Vision，统计人物中心的平均值：
- 固定居中裁剪：人物偏在左侧的素材约 **0.09**（0.5 为正中）
- 开启跟踪后：约 **0.48**
这个"闭环自测"比肉眼可靠，且能顺带验证表达式语法是否被 ffmpeg 接受。

## 打包进 .app（PyInstaller 之外的手工 bundle）
把 venv site-packages 里的 `objc Vision Foundation AppKit Quartz CoreML CoreFoundation PyObjCTools`
复制到 app 内置 Python 的 `site-packages`，构建时跑一次 `import Vision; from Foundation import NSURL` 自检。
注意：Python 主版本必须与 venv 一致（.so 不走跨版本兼容）。
