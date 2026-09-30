# AI 翻唱工作室

根据《全民 K 歌 AI 翻唱项目讨论记录》实现的第一阶段本地应用。Web 应用与 AI 推理环境分离，上传的音频、歌词、任务和作品保存在本机。

## 已实现

- 音色库：上传 1 至 30 秒参考录音、试听、重命名和删除。
- 素材库：管理原唱歌曲、伴奏、干声录唱；读取实际音频时长、采样率和波形。
- 歌词库：编辑歌词或 LRC、保留历史版本、保存任务创建时的歌词快照。
- 创作：原唱分离后转换音色，或上传自己重新录唱的干声进行转换；可指定单独伴奏。
- 独立的人声分离、直接混音模式。
- 调整人声/伴奏增益、移调、时间偏移、推理步数和音色引导强度。
- 单任务串行队列、阶段进度、执行日志、取消、重试、重启中断检测。
- 44.1 kHz / 24-bit WAV 和 320 kbps MP3 导出，试听与下载。
- SQLite 持久化、本机访问限制、同源写入校验、上传大小和磁盘余量检查。

歌词在第一版中用于管理和归档，**编辑文字不会自动改变音频中的歌词**。新歌词按讨论记录的路线 A：先按伴奏重新演唱，导入干声后进行音色转换。歌词自动生成歌声、DiffSinger 和模型训练属于后续阶段。

## 启动

需要 `uv`、Python 3.12 和 FFmpeg。此机器已安装 uv、Python 3.12 与 FFmpeg。

```sh
sh scripts/run.sh
```

打开 <http://127.0.0.1:8765>。已有服务占用端口时：

```sh
COVER_PORT=8766 sh scripts/run.sh
```

应用依赖由 `uv.lock` 固定，无需 Node.js 或前端构建。Lucide 图标保存在项目内，界面不依赖 CDN。

后台启动（占用端口时自动选择后续端口，并打印实际地址与进程号）：

```sh
uv run python scripts/start_background.py
```

后台日志在 `artifacts/server.log`；停止服务时使用打印的进程号执行 `kill <PID>`。

## 安装 AI 推理环境

本轮检测到的硬件是 macOS 15.3.2、Apple M4 Pro、24 GB 内存。首次检测磁盘仅剩约 3.9 GB，因此未安装 PyTorch 或下载大型模型。资产管理和直接混音可立即使用；AI 任务会显示具体的缺失环境或空间错误，不会生成模拟结果。

先在容量充足的位置预留至少 30 GB，建议 60 GB，然后运行：

```sh
uv run python scripts/setup_ai.py --models-dir '/Volumes/你的SSD/ai-cover/models'
```

安装脚本创建独立 Python 3.10 环境，安装固定的 PyTorch 2.5.1、torchaudio 2.5.1、Demucs 4.0.1 和歌声转换所需依赖；固定 Seed-VC 提交为 `51383efd921027683c89e5348211d93ff12ac2a8`。`scripts/ai-requirements.lock` 已在 Apple Silicon / Python 3.10 上完成依赖解析，固定全部间接依赖。脚本完成依赖导入检查后才保存 `settings.json`。权重由上游在首次推理时下载，首次使用需要网络；下载完成后相同模型可离线运行。

上游安装与命令参数参考 [Seed-VC 官方仓库](https://github.com/Plachtaa/seed-vc) 和 [Demucs 官方仓库](https://github.com/facebookresearch/demucs)。Seed-VC 仓库已归档，故采用固定版本并隔离依赖；当前机器受空间限制，尚未实际验证该环境的安装或模型推理。

歌声转换使用 V1 的 44.1 kHz F0 模型，参数为 `--f0-condition True --length-adjust 1.0 --auto-f0-adjust False --fp16 False`。Seed-VC 按上游实现自动选择 MPS/CUDA/CPU；Demucs 默认 CPU，以保持 Mac 上的兼容性，可在设置中切换 MPS。上游 Seed-VC 的部分检查点写在其项目 `checkpoints/` 下，所以项目本身也放在指定模型目录内。

## 存储位置

默认目录：

```text
app/                  后端与静态界面
data/
  assets/
    voices/           参考音色
    songs/            原唱歌曲
    instrumentals/    伴奏（含分离结果）
    vocals/           干声（含分离结果）
    outputs/          导出作品
  jobs/               每次任务的参数、歌词快照、日志和临时音频
  database/           SQLite 元数据
models/               推理环境、上游代码和模型缓存
settings.json         本机模型与环境路径
```

首次启动前可通过环境变量将整个资产库放到外置 SSD：

```sh
COVER_DATA_DIR='/Volumes/你的SSD/ai-cover/data' sh scripts/run.sh
```

`COVER_DATA_DIR` 必须在每次启动时保持一致。迁移已有资产库时先停止应用，将整个 `data/` 目录（包含数据库）移动到新位置，再用新路径启动。模型目录可由安装脚本配置，也可设置 `COVER_MODELS_DIR`；环境变量优先于设置页面的值。

完成任务自动清理重复的临时音频，保留正式分离素材和导出作品。失败或取消任务的临时文件保留用于排查，可在任务列表删除任务记录时清理；删除任务记录不会删除已发布的作品。

## 验证

```sh
uv run pytest
uv run ruff check app scripts tests
```

测试使用临时资产目录及合成测试音频，覆盖上传验证、实际 FFmpeg 混音/导出/时长、歌词版本与快照、任务取消/恢复、存储路径和跨站请求限制。测试不下载模型，也不将合成音频当作 AI 翻唱结果。

```sh
npm ci
node scripts/smoke-browser.cjs
```

浏览器检查使用 Playwright，覆盖桌面/手机布局、素材上传、播放、歌词编辑和混音导出，截图写入 `artifacts/`。需先启动应用，默认地址为 `http://127.0.0.1:8765`；通过 `COVER_URL` 可覆盖。

## 当前限制

- 尚未使用用户实际的参考人声和歌曲做音色相似度、高音、气声及同步听感验收。
- 完整 AI 流程需要足够的磁盘空间、完成推理环境安装和模型下载。
- 阶段进度表示流程阶段，不是模型推理剩余时间估算；详细进度见任务日志。
- 当前只支持单进程启动，避免同一任务队列被多个应用进程同时执行。
- 上游组件各自保留原有许可：Seed-VC 为 GPL-3.0，Demucs 为 MIT，Lucide 为 ISC。

## SSD 接入后继续

目前等待外置 SSD，暂停模型安装与下载。后续推进步骤和可直接使用的提示词见 [SSD 续接提示词](docs/RESUME_AFTER_SSD.md)。
