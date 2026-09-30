## 目标

  实现类似“全民 K 歌”AI K 歌的效果：

  1. 使用自己的声音替换歌曲原唱；
  2. 后续支持输入或修改歌词；
  3. 维护自己的音色、原唱歌曲、伴奏、歌词和产出物；
  4. 生成阶段选择音色、原唱/伴奏和歌词，然后一键生成。

  ## 推荐技术路线

  第一阶段采用：

  - UVR 或 Demucs：分离人声和伴奏；
  - Seed-VC：歌声转换，优先支持零样本音色转换；
  - FFmpeg / librosa / sox：音频格式处理和混音；
  - 自定义 Web 或桌面界面：管理音色、歌曲、歌词、任务和产出物；
  - SQLite：保存资产和任务元数据。

  基础流程：

  原歌曲
  → 分离人声和伴奏
  → 使用 Seed-VC 转换人声音色
  → 与伴奏重新混音
  → 输出 WAV/MP3

  ## 开源项目

  ### Seed-VC

  适合第一阶段快速验证。

  特点：

  - 支持零样本声音转换；
  - 参考音频约 1 至 30 秒；
  - 有专门的歌声转换模型；
  - 支持 Apple Silicon Mac；
  - 不需要一开始训练自己的模型。

  ### Applio

  适合后续训练和管理自己的音色模型。

  特点：

  - 支持声音转换；
  - 支持模型训练；
  - 支持实时变声和批量处理；
  - 更偏向 NVIDIA CUDA 环境。

  ### So-VITS-SVC

  经典歌声转换方案，适合保留原始旋律、节奏和演唱表现。

  ### UVR / Demucs

  用于分离人声、伴奏和其他 stems，是 AI 翻唱前处理的重要组件。

  ### DiffSinger / OpenVPI DiffSinger

  用于后续实现“输入歌词，自动生成歌声”。

  ## 改歌词的两条路线

  ### 路线 A：用户重新唱新歌词，再进行音色转换

  用户唱新歌词，提供旋律、节奏、发音和情绪，再使用 Seed-VC 或 Applio 转换成目标音色。

  这是最容易实现、效果最稳定的路线。

  ### 路线 B：只输入歌词，自动生成歌声

  需要：

  - 歌词转音素；
  - 音符和时值；
  - 旋律或 MIDI；
  - 音高、力度、气声和颤音控制；
  - DiffSinger 或 OpenVPI；
  - 目标音色适配或训练。

  这一条路线难度明显更高，建议在第一版之后实现。

  ## 开发阶段

  ### 第一阶段：离线 Demo

  功能：

  - 上传歌曲；
  - 分离人声和伴奏；
  - 上传自己的参考音色；
  - Seed-VC 转换；
  - 自动混音；
  - 导出 WAV/MP3。

  第一阶段不训练模型，先验证整条流程。

  ### 第二阶段：自己的音色模型

  如果零样本效果不够稳定，再采集自己的干净人声，使用 Applio 或 So-VITS-SVC 训练音色模型。

  训练音频要求：

  - 无伴奏；
  - 尽量无混响；
  - 覆盖不同音高、母音、强弱和唱法；
  - 使用高质量 WAV。

  ### 第三阶段：支持新歌词

  优先采用：

  用户输入新歌词
  → 用户跟伴奏重新唱一遍
  → AI 转换为目标音色
  → 自动混音

  ### 第四阶段：完全自动歌声生成

  接入歌词、音符、音素、旋律和 DiffSinger/OpenVPI。

  ## 资源预估

  ### 最低可用配置

  - 内存：16 GB；
  - 可用磁盘：30 GB；
  - Apple Silicon 或 NVIDIA GPU；
  - 适合离线处理。

  ### 比较舒服的配置

  - 内存：24 至 32 GB；
  - 可用磁盘：60 至 100 GB；
  - Apple Silicon M 系列或 NVIDIA 8 GB 以上显存；
  - 适合批量处理和资产管理。

  ### 不同配置

  #### Apple Silicon，16 GB 以上

  适合本地开发和运行 Seed-VC 离线推理。

  #### Intel Mac，16 GB 以上

  可以尝试，但处理速度可能较慢，建议只做离线处理。

  #### 8 GB 内存

  可以测试短音频，但不适合建立完整资产库或处理长歌曲。

  #### 本地训练

  本地 Mac 不建议作为主要训练机器。训练可以使用 NVIDIA CUDA、Colab 或云 GPU。

  ## 磁盘空间估算

  Seed-VC 模型和相关模型可能占用数 GB。

  实际安装空间包括：

  - Python 虚拟环境和 PyTorch：约 3 至 8 GB；
  - 歌声转换模型：约 1 至 4 GB；
  - Whisper、BigVGAN、音高模型：约 1 至 5 GB；
  - UVR/Demucs 模型：约 2 至 8 GB；
  - pip、Hugging Face 和临时缓存：约 3 至 10 GB；
  - 音频素材、中间文件和最终产出物。

  建议：

  - 最低启动空间：30 GB；
  - 稳妥空间：60 GB；
  - 长期维护歌曲库和多个音色：100 GB 以上；
  - 如果内置硬盘不足，将 models、assets 和 jobs 放到外置 SSD。

  ## 单首歌曲空间

  以 4 分钟歌曲为例：

  - 原始 WAV：约 40 至 65 MB；
  - 人声 stem：约 20 至 60 MB；
  - 伴奏 stem：约 20 至 60 MB；
  - 转换结果：约 20 至 65 MB；
  - 临时文件和不同参数结果：几十至几百 MB。

  开发阶段按 100 至 300 MB/首估算比较稳妥。

   歌曲数量       预计空间
  ━━━━━━━━━━  ━━━━━━━━━━━━━
      20 首      2 至 6 GB
  ──────────  ─────────────
     100 首    10 至 30 GB
  ──────────  ─────────────
     300 首    30 至 90 GB

  ## 建议的资产目录

  project/
  ├── app/
  ├── models/
  ├── assets/
  │   ├── voices/
  │   ├── songs/
  │   ├── instrumentals/
  │   ├── lyrics/
  │   └── outputs/
  ├── jobs/
  └── database/

  其中：

  - `voices/`：自己的音色参考录音和模型；
  - `songs/`：原唱歌曲；
  - `instrumentals/`：伴奏；
  - `lyrics/`：歌词和歌词版本；
  - `outputs/`：最终生成结果；
  - `models/`：Seed-VC、Demucs、UVR 等模型；
  - `jobs/`：单次生成的临时文件；
  - `database/`：SQLite 元数据。

  模型和资产路径应该设计成可配置，以便以后迁移到外置 SSD。

  ## 还需要确认的本机信息

  需要检查：

  - macOS 版本；
  - CPU 架构：Apple Silicon 或 Intel；
  - 芯片型号；
  - 内存；
  - GPU/Metal；
  - 根目录可用空间；
  - Python 版本；
  - FFmpeg 是否安装；
  - PyTorch 是否支持 MPS 或 CUDA。

  检查命令：

  ```bash
  sw_vers
  uname -m
  system_profiler SPHardwareDataType | egrep "Chip|Memory|Model Identifier"
  system_profiler SPDisplaysDataType | egrep "Chipset Model|VRAM|Metal"
  df -h /
  python3 --version
  command -v ffmpeg && ffmpeg -version | head -n 1

  PyTorch 检查：

  python3 - <<'PY'
  try:
      import torch
      print("torch:", torch.__version__)
      print("mps:", hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
      print("cuda:", torch.cuda.is_available())
  except Exception as e:
      print("torch unavailable:", e)
  PY

  ## 当前结论

  建议先做：

  UVR/Demucs + Seed-VC + FFmpeg + 自定义资产管理界面

  先实现用户自己唱新歌词，再转换成目标音色。完全自动输入歌词生成歌声放到后续阶段。

  第一版重点是验证：

  1. 人声分离质量；
  2. 音色转换相似度；
  3. 高音和气声稳定性；
  4. 转换后与伴奏的同步；
  5. 资产、任务和产出物管理流程。