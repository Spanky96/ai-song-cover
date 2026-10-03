# AI 翻唱工作室

根据《全民 K 歌 AI 翻唱项目讨论记录》实现的第一阶段本地应用。Web 应用与 AI 推理环境分离，上传的音频、歌词、任务和作品保存在本机。

## 已实现

- 音色库：上传 1 至 30 秒参考录音、试听、重命名和删除。
- 素材库：管理原唱歌曲、伴奏、干声录唱；读取实际音频时长、采样率和波形。
- 歌词库：编辑歌词或 LRC、保留历史版本、保存任务创建时的歌词快照。
- 创作：原唱分离后转换音色，或上传自己重新录唱的干声进行转换；可指定单独伴奏。
- 独立的人声分离、直接混音模式。
- 调整人声/伴奏增益、移调、时间偏移、推理步数和音色引导强度。
- 试听片段：只转换选中片段快速比较参数，满意后再生成整曲；已完成翻唱可复用转换干声一键重混增益，无需重新推理。
- 单任务串行队列、阶段进度、执行日志、取消、重试、重启中断检测。
- 44.1 kHz / 24-bit WAV 和 320 kbps MP3 导出，试听与下载。
- SQLite 持久化、本机访问限制、同源写入校验、上传大小和磁盘余量检查。

歌词在第一版中用于管理和归档，**编辑文字不会自动改变音频中的歌词**。新歌词按讨论记录的路线 A：先按伴奏重新演唱，导入干声后进行音色转换。歌词自动生成歌声、DiffSinger 和模型训练属于后续阶段。

## 启动

需要 `uv`、Python 3.12 和 FFmpeg。此机器已安装 uv、Python 3.12 与 FFmpeg。

```sh
npm start
```

等价于 `sh scripts/run.sh`（用 uv 固定依赖启动）。

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

当前硬件是 macOS 15.3.2、Apple M4 Pro、24 GB 内存。用户已授权使用系统盘继续安装；项目 `models/` 内的推理环境和全部模型已安装、校验，MPS 可用。已使用上游真实音频完成 6 秒、10 步的分离、转换和 WAV/MP3 导出测试，样例保留在作品库中。

已取消没有实测依据的 30 GiB 固定门槛。安装脚本依据存储清单中的模型、依赖和源码字节数检查空间，另保留 2 GiB 构建工作余量及默认 5 GiB 系统余量。模型与配置文件合计为 **2,570,455,151 字节**；完整清单见 [存储清单](docs/STORAGE_INVENTORY.md)。系统盘安装命令：

```sh
uv run python scripts/setup_ai.py --models-dir models --source-cdn
models/runtime/bin/python scripts/download_models.py --models-dir models --hf-endpoint https://hf-mirror.com
```

安装脚本创建独立 Python 3.10 环境，优先复用本机已有解释器，安装锁定的 104 个依赖；固定 Seed-VC 提交为 `51383efd921027683c89e5348211d93ff12ac2a8`。`--source-cdn` 仅取得推理所需的 64 个源码文件，使用官方 GitHub 文件清单逐个验证 Git 对象哈希，省去未使用的示例、附带权重和 Git 历史。网络可直连 GitHub 时可省略该参数使用 Git，或用 `--source-archive` 下载官方完整压缩包。

脚本完成依赖导入检查后才保存 `settings.json`。模型下载脚本只下载清单中的必要文件并校验大小与 SHA256，在实际加载的缓存目录留下 `install-manifest.json`；文件齐全时应用自动启用离线加载。下载未完成时不会宣称权重就绪，可重复运行下载脚本继续已有缓存。

上游安装与命令参数参考 [Seed-VC 官方仓库](https://github.com/Plachtaa/seed-vc) 和 [Demucs 官方仓库](https://github.com/facebookresearch/demucs)。Seed-VC 仓库已归档，因此采用固定版本并隔离依赖。

歌声转换使用 V1 的 44.1 kHz F0 模型，参数为 `--f0-condition True --length-adjust 1.0 --auto-f0-adjust False --fp16 False`。Seed-VC 按上游实现自动选择 MPS/CUDA/CPU；Demucs 默认 CPU，以保持 Mac 上的兼容性，可在设置中切换 MPS。项目通过 `scripts/seed_inference.py` 运行未修改的上游代码：将 RMVPE 音高数组转为 float32，避免 MPS 不支持 float64；在 MPS 模式下将 BigVGAN 波形生成放到 CPU，避开 PyTorch 2.5.1 的卷积尺寸限制，其余转换计算仍使用 MPS。上游 Seed-VC 的部分检查点写在其项目 `checkpoints/` 下，所以项目本身也放在指定模型目录内。

2026-09-30 16:56 CST 的安装实测：`models/` 目录分配空间为 **4,581,351,424 字节（4.58 GB）**，包含模型、独立运行环境及 uv 缓存；当时系统盘可用空间为 **12,375,764,992 字节（12.38 GB）**。目录统计可能包含 APFS 共享块，不代表唯一物理占用；各项明细见存储清单。

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

完成任务自动清理重复的临时音频，保留正式分离素材、转换后干声和导出作品。转换后干声可在素材库试听、下载，也可用“直接混音”调整增益与同步，无需重新推理。失败或取消任务的临时文件保留用于排查，可在任务列表删除任务记录时清理；删除任务记录不会删除已发布的作品。

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

真实模型流程检查（会在素材库和作品库留下样例）：

```sh
uv run python scripts/smoke_ai.py
```

默认取得校验过的上游音频，截取 6 秒并以 10 步运行完整流程；通过 `--source '/实际歌曲路径' --reference '/实际参考人声路径'` 使用自己的素材。结果写入 `artifacts/ai-validation/result.json`，检查真实模型执行、分离与导出时长、非空波形及下载。当前样例的分离人声能量较低，主要用于验证流程联通，不评价音色相似度或完整歌曲听感；短片段耗时也不能直接推算整首歌或默认 30 步的耗时。

## 当前限制

- 已导入用户的录音、三份音色成品和目标歌曲，完成四组短片段对照及《我和我的祖国》整首转换，详见 [个人翻唱结果](docs/PERSONAL_COVER_RESULTS.md)。用户主观音色相似度、高音、气声及咬字听感仍需试听验收。
- 本轮 188.6 秒歌曲、50 步全曲流程耗时约 17 分钟；不同歌曲和参数的耗时、严格存储峰值不能据此固定。
- 阶段进度表示流程阶段，不是模型推理剩余时间估算；详细进度见任务日志。
- 当前只支持单进程启动，避免同一任务队列被多个应用进程同时执行。
- 上游组件各自保留原有许可：Seed-VC 为 GPL-3.0，Demucs 为 MIT，Lucide 为 ISC。

## SSD 接入后继续

当前已改为在系统盘推进。日后接入 SSD 时，可将模型和音频迁移到外置盘；迁移准备和提示词见 [SSD 续接提示词](docs/RESUME_AFTER_SSD.md)。
