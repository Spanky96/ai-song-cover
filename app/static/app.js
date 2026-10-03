"use strict";

const state = {
  assets: [],
  lyrics: [],
  jobs: [],
  status: null,
  settings: null,
  page: "studio",
  songFilter: "all",
  outputFilter: "full",
  search: "",
  selectedLyric: null,
  draft: {
    job_type: "cover",
    source_kind: "song",
    source_id: "",
    voice_id: "",
    instrumental_id: "",
    lyric_id: "",
    name: "",
    diffusion_steps: 30,
    pitch_shift: 0,
    cfg_rate: 0.7,
    vocal_gain_db: 0,
    instrumental_gain_db: -3,
    offset_ms: 0,
    start_seconds: 35,
    preview_length: 18,
    vocal_enhance: false,
    formats: ["mp3"],
  },
  uploading: false,
  audition: null, // { jobId, shown, error, gains, convert, iterations: [] }
};
const pageNames = {
  studio: "创作工作台",
  voices: "我的音色",
  songs: "歌曲与伴奏",
  lyrics: "歌词库",
  outputs: "我的作品",
  jobs: "生成任务",
  settings: "工作室设置",
};
const kindNames = {
  voice: "参考音色",
  song: "原唱歌曲",
  vocal: "干声录唱",
  instrumental: "伴奏",
  output: "作品",
};
const stateNames = {
  queued: "等待中",
  running: "处理中",
  cancelling: "取消中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};
const activeStates = ["queued", "running", "cancelling"];
const $ = (selector, root = document) => root.querySelector(selector);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const icon = (name) => `<i data-lucide="${name}"></i>`;
const time = (value) => {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
};
const size = (bytes) =>
  bytes >= 1024 ** 3
    ? `${(bytes / 1024 ** 3).toFixed(1)} GB`
    : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
const date = (value) =>
  new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
const ext = (asset) => asset.path.split(".").pop().toUpperCase();
const byId = (id) => state.assets.find((asset) => asset.id === id);
const assetsOf = (kind) => state.assets.filter((asset) => asset.kind === kind);
// 整曲成品计入总数；试听片段与短片段折叠展示，不计入。
const isFullOutput = (asset) =>
  asset.duration >= 60 && asset.metadata?.role !== "preview";
const fullOutputs = () => assetsOf("output").filter(isFullOutput);
const fragmentOutputs = () => assetsOf("output").filter((a) => !isFullOutput(a));
const iconButton = (name, label, attributes = "", danger = false) =>
  `<button type="button" class="icon-button${danger ? " danger" : ""}" title="${esc(label)}" aria-label="${esc(label)}" ${attributes}>${icon(name)}</button>`;

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const result = await response.json();
      message =
        typeof result.detail === "string"
          ? result.detail
          : (result.detail || []).map((item) => item.msg).join("；");
    } catch {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

let toastTimer;
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 6000);
}

function finishRender() {
  if (window.lucide) lucide.createIcons();
  document.querySelectorAll("canvas[data-asset]").forEach(drawWave);
  if (combinedPlayback) tickCombinedIndicators();
  else paintElementTrackIndicators();
}

function drawWave(canvas) {
  const asset = byId(canvas.dataset.asset);
  if (!asset) return;
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, rect.width * ratio);
  canvas.height = Math.max(1, rect.height * ratio);
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  const points = asset.waveform;
  const count = Math.min(points.length, Math.floor(rect.width / 4));
  context.fillStyle = asset.kind === "voice" ? "#c97185" : "#699b92";
  for (let i = 0; i < count; i++) {
    const amplitude = points[Math.floor((i * points.length) / count)] || 0;
    const height = Math.max(2, amplitude * rect.height * 0.95);
    context.fillRect(
      (i * rect.width) / count,
      (rect.height - height) / 2,
      2,
      height,
    );
  }
}

function empty(name, text, command = "") {
  return `<div class="empty-state"><span class="empty-icon">${icon(name)}</span><span>${text}</span>${command}</div>`;
}

function pageHeading(title, subtitle, actions = "") {
  return `<div class="page-heading"><div><h1>${title}</h1>${subtitle ? `<p class="page-subtitle">${subtitle}</p>` : ""}</div><div class="heading-actions">${actions}</div></div>`;
}

function uploadButton(kind, label = "导入音频", primary = false) {
  return `<button type="button" class="button ${primary ? "primary" : "secondary"}" data-upload="${kind}">${icon("plus")}${label}</button>`;
}

function badge(job) {
  return `<span class="badge ${job.state}"><span class="status-dot"></span>${stateNames[job.state] || job.state}</span>`;
}

function environmentReady() {
  return (
    state.status?.seed.ready &&
    state.status?.demucs.ready &&
    state.status?.ffmpeg &&
    state.status?.storage.model_space_ok
  );
}

function updateChrome() {
  $("#voice-count").textContent = assetsOf("voice").length;
  $("#song-count").textContent = state.assets.filter((a) =>
    ["song", "instrumental", "vocal"].includes(a.kind),
  ).length;
  $("#output-count").textContent = fullOutputs().length;
  $("#environment-label").textContent = environmentReady()
    ? "推理环境就绪"
    : "推理环境待配置";
  $("#environment-chip").classList.toggle("warning", !environmentReady());
  if (state.status)
    $("#storage-summary").textContent =
      `本地存储 · 可用 ${size(state.status.storage.data_free_bytes)}`;
  $("#page-label").textContent = pageNames[state.page];
  document
    .querySelectorAll("nav a")
    .forEach((link) =>
      link.classList.toggle("active", link.dataset.page === state.page),
    );
}

function render() {
  updateChrome();
  const renderers = {
    studio: renderStudio,
    voices: renderVoices,
    songs: renderSongs,
    lyrics: renderLyrics,
    outputs: renderOutputs,
    jobs: renderJobs,
    settings: renderSettings,
  };
  $("#main").innerHTML = renderers[state.page]();
  finishRender();
}

function optionList(assets, selectedId, placeholder) {
  return (
    `<option value="">${placeholder}</option>` +
    assets
      .map(
        (a) =>
          `<option value="${a.id}" ${a.id === selectedId ? "selected" : ""}>${esc(a.name)} · ${time(a.duration)}</option>`,
      )
      .join("")
  );
}

function preview(asset) {
  if (!asset) return "";
  return `<div class="source-preview"><button type="button" class="play-mini" data-play="${asset.id}" title="试听" aria-label="试听">${icon("play")}</button><div class="asset-meta"><strong>${esc(asset.name)}</strong><small>${ext(asset)} · ${time(asset.duration)} · ${(asset.sample_rate / 1000).toFixed(1)} kHz</small></div><canvas data-asset="${asset.id}" aria-label="音频波形"></canvas></div>`;
}

function fieldTitle(number, title, action = "") {
  return `<div class="field-heading"><span class="field-title"><span class="number">${number}</span>${title}</span>${action}</div>`;
}

function sourceField() {
  const draft = state.draft;
  const kind =
    draft.job_type === "mix"
      ? "vocal"
      : draft.job_type === "separate"
        ? "song"
        : draft.source_kind;
  const sources = assetsOf(kind);
  if (!sources.some((a) => a.id === draft.source_id))
    draft.source_id = sources[0]?.id || "";
  const toggle =
    draft.job_type === "cover"
      ? `<select id="source-kind" aria-label="来源类型" style="width:116px;min-height:29px;padding:4px 8px;font-size:11px"><option value="song" ${kind === "song" ? "selected" : ""}>原唱歌曲</option><option value="vocal" ${kind === "vocal" ? "selected" : ""}>干声录唱</option></select>`
      : `<span class="optional">${kindNames[kind]}</span>`;
  return `<section class="form-section">${fieldTitle("01", "演唱来源", toggle)}${sources.length ? `<div style="display:flex;gap:8px;align-items:center"><select name="source_id" aria-label="选择演唱来源">${optionList(sources, draft.source_id, "选择音频")}</select>${iconButton("upload", "导入来源", `data-upload="${kind}"`)}</div>${preview(byId(draft.source_id))}` : `<button type="button" class="upload-empty" data-upload="${kind}">${icon("cloud-upload")}<span>导入${kindNames[kind]}</span><small>WAV / MP3 / FLAC / M4A</small></button>`}</section>`;
}

function renderStudio() {
  const draft = state.draft;
  const voices = assetsOf("voice");
  if (!voices.some((a) => a.id === draft.voice_id))
    draft.voice_id = voices[0]?.id || "";
  const instrumentals = assetsOf("instrumental");
  const sourceMarkup = sourceField();
  const source = byId(draft.source_id);
  const voiceField =
    draft.job_type === "cover"
      ? `<section class="form-section">${fieldTitle("02", "目标音色", `<button type="button" class="text-link" data-upload="voice">${icon("plus")}导入音色</button>`)}${voices.length ? `<select name="voice_id" aria-label="选择目标音色">${optionList(voices, draft.voice_id, "选择参考音色")}</select>${preview(byId(draft.voice_id))}` : `<button type="button" class="upload-empty" data-upload="voice">${icon("mic")}<span>添加我的参考音色</span><small>1 至 30 秒 · 干净人声</small></button>`}</section>`
      : "";
  const accompaniment =
    draft.job_type === "mix"
      ? `<section class="form-section">${fieldTitle("02", "伴奏与歌词", `<span class="optional">可选</span>`)}<div class="form-grid"><label class="field"><span>伴奏 ${`<button type="button" class="text-link" style="float:right" data-upload="instrumental">${icon("plus")}导入</button>`}</span><select name="instrumental_id" aria-label="选择伴奏">${optionList(instrumentals, draft.instrumental_id, "不添加伴奏")}</select></label><label class="field"><span>歌词归档 <a href="#lyrics" class="text-link" style="float:right">${icon("notebook-pen")}</a></span><select name="lyric_id" aria-label="选择归档歌词"><option value="">不归档歌词</option>${state.lyrics.map((l) => `<option value="${l.id}" ${l.id === draft.lyric_id ? "selected" : ""}>${esc(l.name)} · v${l.version}</option>`).join("")}</select></label></div></section>`
      : "";
  const notice = !environmentReady()
    ? `<div class="notice">${icon("hard-drive")}<span>${state.status?.storage.model_space_ok ? "AI 推理环境尚未安装。" : "磁盘剩余空间不足，模型下载尚未开始。"}</span><a href="#settings" class="text-link">配置环境 ${icon("arrow-up-right")}</a></div>`
    : "";
  return (
    pageHeading("AI 翻唱工作室", "我的声音，我的演唱。") +
    notice +
    `<div class="overview"><div class="overview-item"><div class="overview-icon">${icon("mic")}</div><div><strong>${voices.length}</strong><span>参考音色</span></div></div><div class="overview-item"><div class="overview-icon">${icon("disc-3")}</div><div><strong>${assetsOf("song").length + assetsOf("vocal").length}</strong><span>演唱素材</span></div></div><div class="overview-item"><div class="overview-icon">${icon("headphones")}</div><div><strong>${fullOutputs().length}</strong><span>已导出作品</span></div></div></div><div class="studio-grid"><section class="composer"><div class="section-heading"><h2>新建创作</h2><span class="subtle-label">${draft.job_type === "cover" ? "歌声转换" : draft.job_type === "mix" ? "音频混音" : "双轨分离"}</span></div><div class="mode-tabs" role="group" aria-label="创作模式">${[
      ["cover", "sparkles", "AI 翻唱"],
      ["separate", "split", "人声分离"],
      ["mix", "audio-lines", "直接混音"],
    ]
      .map(
        ([key, name, label]) =>
          `<button type="button" data-mode="${key}" aria-pressed="${draft.job_type === key}" class="${draft.job_type === key ? "active" : ""}">${icon(name)}${label}</button>`,
      )
      .join(
        "",
      )}</div><form id="job-form">${sourceMarkup}${voiceField}<div id="region-panel">${draft.job_type === "cover" ? renderRegionSection() : ""}</div>${accompaniment}${draft.job_type !== "separate" ? renderMixFields() : ""}<label class="field"><span>作品名称</span><input type="text" name="name" maxlength="120" value="${esc(draft.name)}" placeholder="${esc(byId(draft.source_id)?.name || "未命名作品")}"></label><div class="submit-row">${draft.job_type === "cover" ? `<button type="button" class="button primary" data-audition="segment">${icon("headphones")}转换选中区间</button><button type="button" class="button secondary" data-audition="full">${icon("sparkles")}用当前参数生成整曲</button><small>先试听区间（约 1–2 分钟），满意后再生成整曲（约 20 分钟）</small>` : `<button class="button primary" id="generate-button" type="submit">${icon(draft.job_type === "mix" ? "play" : "split")}${draft.job_type === "mix" ? "开始混音" : "分离音频"}</button><small>${draft.job_type === "mix" ? "WAV / MP3" : "人声 / 伴奏"}</small>`}</div><div class="form-error" id="job-error" role="alert"></div></form></section><aside class="activity"><div id="audition-panel">${renderAuditionInner()}</div><section><div class="section-heading"><h2>最近任务</h2><a href="#jobs" class="text-link">全部 ${icon("arrow-right")}</a></div><div id="studio-jobs">${renderRecentJobs()}</div></section><section class="recent-section"><div class="section-heading"><h2>最近作品</h2><a href="#outputs" class="text-link">全部 ${icon("arrow-right")}</a></div><div id="studio-outputs">${renderRecentOutputs()}</div></section></aside></div>`
  );
}

function renderMixFields() {
  const d = state.draft;
  const range = (name, title, min, max, suffix) =>
    `<label class="field"><span>${title}</span><div class="range-field"><input name="${name}" type="range" min="${min}" max="${max}" value="${d[name]}" step="1" aria-label="${title}"><output data-output="${name}">${d[name]} ${suffix}</output></div></label>`;
  return `<section class="form-section"><div class="form-grid">${range("vocal_gain_db", "人声音量", -24, 12, "dB")}${range("instrumental_gain_db", "伴奏音量", -24, 12, "dB")}</div></section><details class="advanced"><summary>高级参数 ${icon("chevron-down")}</summary><div class="form-grid">${d.job_type === "cover" ? `<label class="field"><span>推理步数</span><select name="diffusion_steps" aria-label="推理步数">${[10, 20, 30, 40, 50, 75, 100].map((n) => `<option ${d.diffusion_steps === n ? "selected" : ""} value="${n}">${n}</option>`).join("")}</select></label><label class="field"><span>人声移调 / 半音</span><input name="pitch_shift" type="number" min="-12" max="12" step="1" value="${d.pitch_shift}"><small class="subtle-label">同性别翻唱一般无需调整；男转女通常 +5~+12；女转男通常 -5~-12</small></label><label class="field"><span>音色引导强度</span><input name="cfg_rate" type="number" min="0" max="1" step="0.05" value="${d.cfg_rate}"></label><label class="field"><span>人声增强</span><div class="export-options"><label><input type="checkbox" name="vocal_enhance" ${d.vocal_enhance ? "checked" : ""}> 轻度精修</label></div><small class="subtle-label">去齿音、轻压缩、提升亮度，修复转换瑕疵</small></label>` : ""}<label class="field"><span>人声时间偏移 / ms</span><input name="offset_ms" type="number" min="-10000" max="10000" step="10" value="${d.offset_ms}" title="正值延后人声，负值提前人声"></label></div></details><div class="form-grid" style="margin-bottom:20px"><label class="field"><span>导出格式</span><div class="export-options"><label><input type="checkbox" name="formats" value="wav" ${d.formats.includes("wav") ? "checked" : ""}> WAV</label><label><input type="checkbox" name="formats" value="mp3" ${d.formats.includes("mp3") ? "checked" : ""}> MP3</label></div><small class="subtle-label">MP3 体积小、便于分享试听；WAV 是无损母带，仅需要存档或后期再加工时勾选</small></label></div>`;
}

// ---- 剪辑式音轨与试听区间 ----

function pendingSeparation(songId) {
  return state.jobs.find(
    (j) =>
      j.params.job_type === "separate" &&
      j.params.source_id === songId &&
      activeStates.includes(j.state),
  );
}

function separationFor(songId) {
  const job = state.jobs.find(
    (j) =>
      j.params.job_type === "separate" &&
      j.params.source_id === songId &&
      j.state === "completed",
  );
  if (!job) return null;
  const stems = state.assets.filter((a) => a.metadata?.job_id === job.id);
  const vocal = stems.find((a) => a.kind === "vocal");
  const instrumental = stems.find((a) => a.kind === "instrumental");
  return vocal && instrumental ? { job, vocal, instrumental } : null;
}

// Cover-mode source: separated stems for a song, or the dry vocal itself.
function coverStems() {
  const source = byId(state.draft.source_id);
  if (!source) return { vocal: null, instrumental: null };
  if (source.kind === "song") {
    const separated = separationFor(source.id);
    return separated
      ? { vocal: separated.vocal, instrumental: separated.instrumental }
      : { vocal: null, instrumental: null };
  }
  return { vocal: source, instrumental: byId(state.draft.instrumental_id) || null };
}

function regionOf(duration) {
  const start = Math.max(
    0,
    Math.min(state.draft.start_seconds, Math.max(0, Math.floor(duration) - 5)),
  );
  const length = Math.max(5, Math.min(state.draft.preview_length, duration - start));
  return { start, length };
}

function trackRow(asset, label, selectable) {
  const { start, length } = regionOf(asset.duration);
  const left = (start / asset.duration) * 100;
  const width = (length / asset.duration) * 100;
  return `<div class="track"><div class="track-head"><button type="button" class="play-mini" data-play="${asset.id}" title="试听整轨" aria-label="试听整轨">${icon("play")}</button><div class="asset-meta"><strong>${esc(label)}</strong><small>${time(asset.duration)}</small></div></div><div class="track-body" data-play-track="${asset.id}"${selectable ? ` data-select-track="${asset.id}" title="拖拽框选区间，单击试听当前区间"` : ` title="单击试听当前区间"`}><canvas data-asset="${asset.id}" aria-label="音频波形"></canvas><div class="track-region${selectable ? "" : " readonly"}" style="left:${left}%;width:${width}%"><span class="handle"></span><span class="handle right"></span></div><div class="track-window-progress" hidden></div><div class="track-playhead" hidden></div></div></div>`;
}

function renderRegionSection() {
  const d = state.draft;
  const source = byId(d.source_id);
  if (!source) return "";
  let body;
  if (source.kind === "song") {
    const pending = pendingSeparation(source.id);
    const separated = separationFor(source.id);
    if (separated) {
      const { start, length } = regionOf(separated.vocal.duration);
      body = `<div class="track-stack">${trackRow(separated.vocal, "原唱人声", true)}${trackRow(separated.instrumental, "伴奏", false)}</div><div class="form-grid" style="margin-top:12px"><label class="field"><span>区间起点 / 秒</span><input name="start_seconds" type="number" min="0" step="1" value="${start}" aria-label="区间起点秒数"></label><label class="field"><span>区间时长 / 秒</span><input name="preview_length" type="number" min="5" max="60" step="1" value="${Math.round(length)}" aria-label="区间时长秒数"></label></div><small class="subtle-label">拖拽框选区间、单击波形从区间开始播放（播完自动继续整曲）；「人声+伴奏」按当前音量合播区间；播放时波形上有播放头与进度指示。</small>`;
    } else if (pending) {
      body = `<div class="separate-progress"><strong>${esc(pending.name)}</strong><div class="progress-track"><span style="width:${pending.progress}%"></span></div><small>${esc(pending.stage)} · ${pending.progress}%</small></div>`;
    } else {
      body = `<button type="button" class="button primary" data-separate-song="${source.id}">${icon("split")}先分离人声与伴奏</button><small class="subtle-label">分离一次即可：之后选区间转换、生成整曲都复用这两条音轨。</small>`;
    }
  } else {
    const instrumental = byId(d.instrumental_id);
    body = `${trackRow(source, "干声", true)}${instrumental ? trackRow(instrumental, "伴奏", false) : `<div class="form-grid"><label class="field"><span>伴奏（可选）</span><select name="instrumental_id" aria-label="选择伴奏">${optionList(assetsOf("instrumental"), d.instrumental_id, "不添加伴奏")}</select></label></div>`}<div class="form-grid" style="margin-top:12px"><label class="field"><span>区间起点 / 秒</span><input name="start_seconds" type="number" min="0" step="1" value="${Math.max(0, Math.min(d.start_seconds, Math.floor(source.duration) - 5))}" aria-label="区间起点秒数"></label><label class="field"><span>区间时长 / 秒</span><input name="preview_length" type="number" min="5" max="60" step="1" value="${Math.max(5, Math.min(d.preview_length, source.duration - d.start_seconds))}" aria-label="区间时长秒数"></label></div>`;
  }
  const stems = coverStems();
  const combinedAction =
    stems.vocal && stems.instrumental
      ? `<button type="button" class="text-link" data-play-combined title="播放区间内人声+伴奏合音；播放中拖动下方音量滑杆可实时调节对应音轨">${icon("layers")}人声+伴奏</button>`
      : `<span class="optional">拖拽框选</span>`;
  return `<section class="form-section">${fieldTitle("03", "试听区间", combinedAction)}${body}</section>`;
}

// ---- 试听调参台：生成完成就地播放，音量秒级重混，音高/CFG 重新转换 ----

function auditionJob() {
  return state.audition ? state.jobs.find((j) => j.id === state.audition.jobId) : null;
}

function summarizeAudition(job) {
  const p = job.params;
  const parts = [`移调 ${p.pitch_shift > 0 ? "+" : ""}${p.pitch_shift}`];
  if (p.job_type === "cover") parts.push(`CFG ${p.cfg_rate}`, `${p.diffusion_steps}步`);
  parts.push(`人声 ${p.vocal_gain_db >= 0 ? "+" : ""}${p.vocal_gain_db}dB`, `伴奏 ${p.instrumental_gain_db}dB`);
  return parts.join(" · ");
}

function renderAuditionInner() {
  const audition = state.audition;
  const job = auditionJob();
  if (!audition || !job) return "";
  if (job.state !== "completed") {
    return `<section class="audition"><div class="section-heading"><h2>试听调参台</h2><button type="button" class="icon-button" data-audition-close title="关闭" aria-label="关闭">${icon("x")}</button></div><div class="audition-body"><strong class="audition-name">${esc(job.name)}</strong>${activeStates.includes(job.state) ? `<div class="progress-track"><span style="width:${job.progress}%"></span></div><small class="subtle-label">${esc(job.stage)} · ${job.progress}%，留在本页即可，完成后自动播放。</small>` : `<div class="job-error">${esc(job.error || stateNames[job.state])}</div>`}</div></section>`;
  }
  const output = job.outputs.map(byId).filter(Boolean);
  const mp3 = output.find((a) => a.path.endsWith(".mp3")) || output[0];
  const gains = audition.gains;
  const convert = audition.convert;
  const mixable = byId(job.params.source_id)?.kind === "vocal";
  const remixable = mixable && (job.params.job_type === "cover" || job.params.job_type === "mix");
  const convertible = job.params.job_type === "cover" && job.params.preview_seconds > 0;
  return `<section class="audition"><div class="section-heading"><h2>试听调参台</h2><button type="button" class="icon-button" data-audition-close title="关闭" aria-label="关闭">${icon("x")}</button></div><div class="audition-body">${mp3 ? `<div class="audition-now"><button type="button" class="play-mini large" data-play="${mp3.id}" title="试听当前版本" aria-label="试听当前版本">${icon("play")}</button><div class="asset-meta"><strong>${esc(job.name)}</strong><small>${summarizeAudition(job)}</small></div></div>` : ""}${remixable ? `<div class="audition-group"><span class="audition-label">音量微调 · 秒级出新版</span><div class="form-grid"><label class="field"><span>人声音量</span><div class="range-field"><input type="range" min="-24" max="12" step="1" value="${gains.vocal_gain_db}" data-audition-gain="vocal_gain_db" aria-label="人声音量"><output>${gains.vocal_gain_db} dB</output></div></label><label class="field"><span>伴奏音量</span><div class="range-field"><input type="range" min="-24" max="12" step="1" value="${gains.instrumental_gain_db}" data-audition-gain="instrumental_gain_db" aria-label="伴奏音量"><output>${gains.instrumental_gain_db} dB</output></div></label></div><button type="button" class="button primary" data-audition-remix>${icon("refresh-cw")}更新混音并试听</button></div>` : ""}${convertible ? `<div class="audition-group"><span class="audition-label">重新转换 · 约 1–2 分钟</span><div class="form-grid"><label class="field"><span>人声移调 / 半音</span><input type="number" min="-12" max="12" step="1" value="${convert.pitch_shift}" data-audition-convert="pitch_shift"></label><label class="field"><span>音色引导强度</span><input type="number" min="0" max="1" step="0.05" value="${convert.cfg_rate}" data-audition-convert="cfg_rate"></label><label class="field"><span>推理步数</span><select data-audition-convert="diffusion_steps">${[10, 20, 30, 40, 50, 75, 100].map((n) => `<option ${Number(convert.diffusion_steps) === n ? "selected" : ""} value="${n}">${n}</option>`).join("")}</select></label></div><button type="button" class="button secondary" data-audition-reconvert>${icon("sparkles")}重新转换选中区间</button></div>` : ""}${convertible || remixable ? `<button type="button" class="button primary full-width" data-audition-full>${icon("disc-3")}满意了，用当前参数生成整曲（约 20 分钟）</button>` : ""}${audition.iterations.length > 1 ? `<div class="audition-history"><span class="audition-label">版本对比</span>${audition.iterations.map((item, index) => `<div class="iteration-row"><button type="button" class="play-mini" data-play="${item.mp3}" title="试听" aria-label="试听">${icon("play")}</button><div class="asset-meta"><strong>版本 ${audition.iterations.length - index}${index === 0 ? " · 最新" : ""}</strong><small>${esc(item.summary)}</small></div></div>`).join("")}</div>` : ""}</div></section>`;
}

function taskItem(job, full = false) {
  const running = activeStates.includes(job.state);
  const preview = (job.params.preview_seconds || 0) > 0;
  return `<div class="task-item"><div class="task-top"><div><div class="task-title">${esc(job.name)}${preview ? ` <span class="mini-tag">试听 ${job.params.start_seconds || 0}秒起</span>` : ""}</div><div class="task-meta">${esc(job.params.voice_name || (job.params.job_type === "mix" ? "直接混音" : "人声分离"))} · ${date(job.created_at)}</div></div>${badge(job)}</div>${running ? `<div class="progress-track"><span style="width:${job.progress}%"></span></div>` : ""}${full && job.error ? `<div class="job-error">${esc(job.error)}</div>` : ""}<div class="task-bottom"><span>${esc(job.stage)}${running ? ` · ${job.progress}%` : ""}</span><div class="task-actions">${iconButton("file-text", "查看日志", `data-log="${job.id}"`)}${running ? iconButton("square", "取消任务", `data-cancel="${job.id}"`) : iconButton("rotate-ccw", "重新执行", `data-retry="${job.id}"`)}${full && job.state === "completed" && job.params.job_type === "cover" ? iconButton("sliders-horizontal", "调整混音（复用转换干声，无需重新转换）", `data-remix-job="${job.id}"`) : ""}${full && !running ? iconButton("trash-2", "删除任务记录与临时文件", `data-delete-job="${job.id}"`, true) : ""}</div></div>${
    full && job.outputs.length
      ? `<div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:14px">${job.outputs
          .map((id) => byId(id))
          .filter(Boolean)
          .map(
            (a) =>
              `<button type="button" class="text-link" data-play="${a.id}">${icon("play")}${ext(a)} · ${esc(a.name)}</button>`,
          )
          .join("")}</div>`
      : ""
  }</div>`;
}

function renderRecentJobs() {
  return state.jobs.length
    ? state.jobs
        .slice(0, 4)
        .map((j) => taskItem(j))
        .join("")
    : empty("list-checks", "还没有生成任务");
}

function renderRecentOutputs() {
  const outputs = fullOutputs().slice(0, 3);
  return outputs.length
    ? outputs
        .map(
          (a) =>
            `<div class="output-mini"><button type="button" class="play-mini" data-play="${a.id}" title="试听作品" aria-label="试听作品">${icon("play")}</button><div class="asset-meta"><strong>${esc(a.name)}</strong><small>${ext(a)} · ${time(a.duration)}</small></div><a class="icon-button" href="/api/assets/${a.id}/audio?download=true" title="下载作品" aria-label="下载作品">${icon("download")}</a></div>`,
        )
        .join("")
    : empty("headphones", "还没有作品");
}

function rowActions(asset) {
  return `<div class="row-actions"><a class="icon-button" href="/api/assets/${asset.id}/audio?download=true" title="下载" aria-label="下载">${icon("download")}</a>${iconButton("pencil", "重命名", `data-rename="${asset.id}"`)}${iconButton("trash-2", "删除", `data-delete-asset="${asset.id}"`, true)}</div>`;
}

function filterBySearch(assets) {
  return assets.filter((a) =>
    a.name.toLowerCase().includes(state.search.toLowerCase()),
  );
}

function renderVoices() {
  const voices = filterBySearch(assetsOf("voice"));
  return (
    pageHeading(
      "我的音色",
      `${assetsOf("voice").length} 个参考音色`,
      uploadButton("voice", "添加音色", true),
    ) +
    renderSearch() +
    `<div id="asset-results">${voices.length ? `<div class="asset-grid">${voices.map((a) => `<article class="voice-item"><div class="voice-top"><span class="voice-avatar">${icon("mic")}</span><div class="asset-meta"><h3 title="${esc(a.name)}">${esc(a.name)}</h3><small>${time(a.duration)} · ${ext(a)}</small></div></div><canvas data-asset="${a.id}" aria-label="参考音色波形"></canvas><div class="voice-bottom"><button type="button" class="play-mini" data-play="${a.id}" title="试听音色" aria-label="试听音色">${icon("play")}</button>${rowActions(a)}</div></article>`).join("")}</div>` : empty("mic", state.search ? "没有匹配的音色" : "音色库为空", !state.search ? `<small>1 至 30 秒 · 干净人声录音</small>` : "")}</div>`
  );
}

function renderSearch() {
  return `<div class="filter-bar"><span class="subtle-label">全部资产</span><label class="search-field">${icon("search")}<input id="asset-search" type="text" value="${esc(state.search)}" placeholder="搜索名称" aria-label="搜索资产"></label></div>`;
}

function renderAssetTable(assets, emptyText = "暂无音频资产") {
  if (!assets.length)
    return `<div class="table-empty">${empty("music-2", state.search ? "没有匹配的音频" : emptyText)}</div>`;
  return `<table class="asset-table"><thead><tr><th>音频名称</th><th>波形</th><th>时长</th><th>文件大小</th><th style="text-align:right">操作</th></tr></thead><tbody>${assets.map((a) => `<tr><td><div class="asset-cell"><button class="play-mini" data-play="${a.id}" title="试听" aria-label="试听">${icon("play")}</button><div class="asset-meta"><strong title="${esc(a.name)}">${esc(a.name)}</strong><small>${kindNames[a.kind]}${a.metadata?.role === "preview" ? " · 试听片段" : ""} · ${ext(a)} · ${(a.sample_rate / 1000).toFixed(1)} kHz</small></div></div></td><td><canvas class="asset-wave" data-asset="${a.id}" aria-label="音频波形"></canvas></td><td>${time(a.duration)}</td><td>${size(a.size)}</td><td>${rowActions(a)}</td></tr>`).join("")}</tbody></table>`;
}

function renderSongs() {
  let assets = state.assets.filter((a) =>
    ["song", "instrumental", "vocal"].includes(a.kind),
  );
  if (state.songFilter !== "all")
    assets = assets.filter((a) => a.kind === state.songFilter);
  return (
    pageHeading(
      "歌曲与伴奏",
      `${state.assets.filter((a) => ["song", "vocal", "instrumental"].includes(a.kind)).length} 个演唱素材`,
      `<button class="button primary" data-import-menu>${icon("plus")}导入素材</button>`,
    ) +
    `<div class="filter-bar"><div class="filter-tabs">${[
      ["all", "全部"],
      ["song", "原唱"],
      ["instrumental", "伴奏"],
      ["vocal", "干声"],
    ]
      .map(
        ([key, label]) =>
          `<button data-filter="${key}" class="${state.songFilter === key ? "active" : ""}">${label}</button>`,
      )
      .join(
        "",
      )}</div><label class="search-field">${icon("search")}<input id="asset-search" type="text" value="${esc(state.search)}" placeholder="搜索名称" aria-label="搜索资产"></label></div><div id="asset-results">${renderAssetTable(filterBySearch(assets), "素材库为空")}</div>`
  );
}

function groupOutputsByJob(assets) {
  const groups = new Map();
  for (const asset of assets) {
    const key = asset.metadata?.job_id || asset.id;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(asset);
  }
  return [...groups.entries()];
}

function outputRow(asset) {
  return `<tr><td><div class="asset-cell"><button class="play-mini" data-play="${asset.id}" title="试听" aria-label="试听">${icon("play")}</button><div class="asset-meta"><strong>${ext(asset)} · ${(asset.sample_rate / 1000).toFixed(1)} kHz</strong><small>${asset.metadata?.role === "preview" ? "试听片段 · " : ""}${size(asset.size)}</small></div></div></td><td><canvas class="asset-wave" data-asset="${asset.id}" aria-label="音频波形"></canvas></td><td>${time(asset.duration)}</td><td>${size(asset.size)}</td><td>${rowActions(asset)}</td></tr>`;
}

function outputGroupTable(groups) {
  return `<table class="asset-table"><thead><tr><th>作品</th><th>波形</th><th>时长</th><th>文件大小</th><th style="text-align:right">操作</th></tr></thead><tbody>${groups
    .map(
      ([, items]) =>
        `<tr class="group-row"><td colspan="5"><strong title="${esc(items[0].name)}">${esc(items[0].name)}</strong><small>${date(items[0].created_at)} · ${items.length} 个文件</small></td></tr>${items
          .map(outputRow)
          .join("")}`,
    )
    .join("")}</tbody></table>`;
}

function renderOutputs() {
  const filter = state.outputFilter || "full";
  const full = fullOutputs();
  const fragments = fragmentOutputs();
  const tabs = `<div class="filter-bar"><div class="filter-tabs">${[
    ["full", `整曲成品 ${full.length}`],
    ["fragments", `试听与片段 ${fragments.length}`],
    ["all", "全部"],
  ]
    .map(
      ([key, label]) =>
        `<button data-output-filter="${key}" class="${filter === key ? "active" : ""}">${label}</button>`,
    )
    .join("")}</div></div>`;
  const emptyBlock = (text) =>
    `<div class="table-empty">${empty("music-2", text)}</div>`;
  let body = "";
  if (filter !== "fragments")
    body += full.length
      ? outputGroupTable(groupOutputsByJob(full))
      : emptyBlock("还没有完成的作品");
  if (filter !== "full") {
    const table = fragments.length
      ? outputGroupTable(groupOutputsByJob(fragments))
      : emptyBlock("没有试听或片段作品");
    body =
      filter === "all"
        ? body +
          `<details class="preview-fold"><summary>试听与片段（${fragments.length}）${icon("chevron-down")}</summary>${table}</details>`
        : table;
  }
  return (
    pageHeading(
      "我的作品",
      `${full.length} 个整曲成品`,
      `<a class="button primary" href="#studio">${icon("plus")}新建创作</a>`,
    ) +
    tabs +
    `<div id="asset-results">${body}</div>`
  );
}

function renderLyrics() {
  const lyric = state.lyrics.find((l) => l.id === state.selectedLyric);
  return (
    pageHeading(
      "歌词库",
      `${state.lyrics.length} 份歌词`,
      `<button class="button primary" data-new-lyric>${icon("plus")}新建歌词</button>`,
    ) +
    `<div class="lyrics-layout"><aside class="lyric-list">${state.lyrics.length ? state.lyrics.map((l) => `<button class="lyric-list-item ${lyric?.id === l.id ? "active" : ""}" data-select-lyric="${l.id}"><strong>${esc(l.name)}</strong><small>v${l.version} · ${date(l.updated_at)}</small></button>`).join("") : `<div class="empty-state">暂无歌词</div>`}</aside><section class="lyric-editor"><form id="lyric-form" data-id="${lyric?.id || ""}"><label class="field"><span>歌词名称</span><input name="name" required maxlength="120" value="${esc(lyric?.name || "")}" placeholder="歌曲名称"></label><label class="field"><span>歌词正文</span><textarea name="content" required maxlength="100000" placeholder="歌词或 LRC 时间轴">${esc(lyric?.content || "")}</textarea></label><div class="lyric-editor-footer"><small>${lyric ? `当前版本 v${lyric.version}` : "未保存"}</small><div class="heading-actions">${lyric ? `${iconButton("history", "历史版本", `data-versions="${lyric.id}"`)}${iconButton("trash-2", "删除歌词", `data-delete-lyric="${lyric.id}"`, true)}` : ""}<button class="button primary" type="submit">${icon("save")}保存歌词</button></div></div></form></section></div>`
  );
}

function renderJobs() {
  const active = state.jobs.filter((j) =>
    activeStates.includes(j.state),
  ).length;
  return (
    pageHeading(
      "生成任务",
      `${state.jobs.length} 个任务 · ${active} 个处理中`,
      `<button class="button secondary" data-refresh>${icon("refresh-cw")}刷新</button>`,
    ) +
    `<div id="job-list" class="job-list-full">${state.jobs.length ? state.jobs.map((j) => taskItem(j, true)).join("") : empty("list-checks", "还没有生成任务", `<a class="text-link" href="#studio">前往创作 ${icon("arrow-right")}</a>`)}</div>`
  );
}

function renderSettings() {
  const s = state.settings,
    status = state.status;
  const field = (name, label, value) =>
    `<label class="field"><span>${label}</span><input type="text" name="${name}" required value="${esc(value)}"></label>`;
  const engine = (title, ready, message) =>
    `<div class="engine-status"><div class="engine-status-header"><strong>${title}</strong><span class="badge ${ready ? "completed" : ready === null ? "" : "queued"}"><span class="status-dot"></span>${ready === null ? "未验证" : ready ? "已安装" : "待安装"}</span></div><p>${esc(message)}</p></div>`;
  return (
    pageHeading(
      "工作室设置",
      "存储与推理环境",
      `<button class="button secondary" data-refresh>${icon("refresh-cw")}重新检测</button>`,
    ) +
    `<section class="settings-section"><h2>本机环境</h2><div class="engine-grid">${engine("FFmpeg", status.ffmpeg, "音频处理与 WAV / MP3 导出")}${engine("Demucs", status.demucs.ready, `人声分离 · ${s.demucs_device.toUpperCase()}`)}${engine("Seed-VC", status.seed.ready, `歌声转换 · ${status.seed.runtime.mps ? "Apple Metal" : "运行环境待检测"}`)}${engine("模型权重", status.model_weights_ready, status.seed.model_weights)}</div></section><section class="settings-section"><h2>资产存储</h2><div class="storage-band"><strong>${size(status.storage.data_free_bytes)}</strong><span>剩余可用空间</span></div><div class="setting-path">${esc(s.data_dir)}</div></section><form id="settings-form"><section class="settings-section"><h2>模型与运行环境</h2><div class="storage-band"><strong>${size(status.storage.models_free_bytes)}</strong><span>模型目录可用空间</span></div>${field("models_dir", "模型存储目录", s.models_dir)}${field("seed_repo", "Seed-VC 项目目录", s.seed_repo)}${field("seed_python", "Seed-VC Python 环境", s.seed_python)}${field("demucs_python", "Demucs Python 环境", s.demucs_python)}<label class="field"><span>人声分离设备</span><select name="demucs_device">${["cpu", "mps", "cuda"].map((d) => `<option ${s.demucs_device === d ? "selected" : ""} value="${d}">${{ cpu: "CPU", mps: "Apple Metal (MPS)", cuda: "NVIDIA CUDA" }[d]}</option>`).join("")}</select></label><div class="submit-row"><button type="submit" class="button primary">${icon("save")}保存配置</button></div></section></form>`
  );
}

function showDialog(title, body, actions = "") {
  $("#dialog-content").innerHTML =
    `<div class="dialog-title"><h2>${esc(title)}</h2>${iconButton("x", "关闭", "data-close")}</div>${body}${actions ? `<div class="dialog-actions">${actions}</div>` : ""}`;
  finishRender();
  if (!$("#dialog").open) $("#dialog").showModal();
}

function confirmAction(title, message, callback) {
  showDialog(
    title,
    `<p class="dialog-message">${esc(message)}</p>`,
    `<button class="button secondary" data-close>取消</button><button class="button primary" id="confirm-action">${icon("trash-2")}确认删除</button>`,
  );
  $("#confirm-action").onclick = async () => {
    $("#confirm-action").disabled = true;
    try {
      await callback();
      $("#dialog").close();
      await refreshData();
      toast("已删除");
    } catch (error) {
      toast(error.message);
      if ($("#confirm-action")) $("#confirm-action").disabled = false;
    }
  };
}

function selectUpload(kind) {
  if (state.uploading) return toast("正在导入音频，请稍候。");
  $("#dialog").close();
  $("#file-input").dataset.kind = kind;
  $("#file-input").value = "";
  $("#file-input").click();
}

function uploadFile(file, kind) {
  if (file.size > 250 * 1024 ** 2)
    return Promise.reject(new Error("单个音频不得超过 250 MB。"));
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/assets");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && $("#upload-bar"))
        $("#upload-bar").style.width = `${(event.loaded / event.total) * 100}%`;
    };
    xhr.onload = () => {
      let result;
      try {
        result = JSON.parse(xhr.responseText);
      } catch {
        return reject(new Error("导入响应异常。"));
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(result);
      else
        reject(
          new Error(
            typeof result.detail === "string" ? result.detail : "导入失败。",
          ),
        );
    };
    xhr.onerror = () => reject(new Error("无法连接本地应用。"));
    const data = new FormData();
    data.append("kind", kind);
    data.append("file", file);
    xhr.send(data);
  });
}

$("#file-input").addEventListener("change", async (event) => {
  const files = Array.from(event.target.files);
  if (!files.length) return;
  const kind = event.target.dataset.kind;
  state.uploading = true;
  let count = 0,
    lastAsset;
  try {
    for (const file of files) {
      showDialog(
        `导入${kindNames[kind]}`,
        `<p class="dialog-message">${esc(file.name)}</p><small class="subtle-label">${count + 1} / ${files.length}</small><div class="upload-progress"><span id="upload-bar" style="width:0"></span></div>`,
      );
      lastAsset = await uploadFile(file, kind);
      count++;
    }
    if (lastAsset) {
      if (["song", "vocal"].includes(kind)) {
        state.draft.source_id = lastAsset.id;
        state.draft.source_kind = kind;
      }
      if (kind === "voice") state.draft.voice_id = lastAsset.id;
      if (kind === "instrumental") state.draft.instrumental_id = lastAsset.id;
    }
    $("#dialog").close();
    toast(`已导入 ${count} 个音频`);
  } catch (error) {
    showDialog(
      "导入未完成",
      `<p class="dialog-message">${esc(error.message)}</p><p class="dialog-message">已导入 ${count} / ${files.length} 个文件。</p>`,
      `<button class="button secondary" data-close>关闭</button>`,
    );
  } finally {
    state.uploading = false;
    await refreshData();
  }
});

// ---- 参数构建与任务提交 ----

function buildCoverParams(previewSeconds) {
  const d = state.draft;
  const stems = coverStems();
  if (!stems.vocal) throw new Error("请先选择已分离的歌曲或干声来源。");
  if (!d.voice_id) throw new Error("请先导入并选择目标音色。");
  if (!d.formats.length) throw new Error("请选择至少一种导出格式。");
  const region = regionOf(stems.vocal.duration);
  const source = byId(d.source_id);
  const base = `${source?.kind === "song" ? source.name : stems.vocal.name}`;
  return {
    job_type: "cover",
    source_id: stems.vocal.id,
    voice_id: d.voice_id,
    instrumental_id: stems.instrumental?.id || null,
    lyric_id: d.lyric_id || null,
    diffusion_steps: d.diffusion_steps,
    cfg_rate: d.cfg_rate,
    pitch_shift: d.pitch_shift,
    vocal_gain_db: d.vocal_gain_db,
    instrumental_gain_db: d.instrumental_gain_db,
    offset_ms: d.offset_ms,
    vocal_enhance: d.vocal_enhance,
    start_seconds: previewSeconds ? region.start : 0,
    preview_seconds: previewSeconds ? region.length : 0,
    formats: d.formats.length ? d.formats : ["mp3"],
    name:
      d.name.trim() ||
      (previewSeconds
        ? `试听 · ${base} · ${region.start}秒起`
        : `${base} · 我的音色 · 完整版`),
  };
}

async function startAuditionJob(params) {
  const job = await api("/jobs", { method: "POST", body: JSON.stringify(params) });
  state.audition = {
    jobId: job.id,
    shown: false,
    error: null,
    gains: {
      vocal_gain_db: params.vocal_gain_db,
      instrumental_gain_db: params.instrumental_gain_db,
    },
    convert: {
      pitch_shift: params.pitch_shift,
      cfg_rate: params.cfg_rate,
      diffusion_steps: params.diffusion_steps,
    },
    // Keep the A/B history across reconvert / remix rounds; closing the panel clears it.
    iterations: state.audition?.iterations || [],
  };
  await refreshData();
  return job;
}

// ---- 波形区间拖拽 ----

let dragSelect = null;
document.addEventListener("pointerdown", (event) => {
  const body = event.target.closest("[data-select-track]");
  if (!body || event.button !== 0) return;
  const duration = byId(body.dataset.selectTrack)?.duration || 0;
  if (!duration) return;
  const rect = body.getBoundingClientRect();
  dragSelect = {
    body,
    rect,
    duration,
    anchor: ((event.clientX - rect.left) / rect.width) * duration,
    range: null,
  };
  body.querySelector(".track-region")?.classList.add("dragging");
  event.preventDefault();
});
window.addEventListener("pointermove", (event) => {
  if (!dragSelect) return;
  const { rect, duration, anchor } = dragSelect;
  const current = Math.max(
    0,
    Math.min(duration, ((event.clientX - rect.left) / rect.width) * duration),
  );
  const a = Math.min(anchor, current);
  const b = Math.max(anchor, current);
  dragSelect.range = [a, b];
  const region = dragSelect.body.querySelector(".track-region");
  if (region) {
    region.style.left = `${(a / duration) * 100}%`;
    region.style.width = `${((b - a) / duration) * 100}%`;
  }
});
window.addEventListener("pointerup", () => {
  if (!dragSelect) return;
  const [a, b] = dragSelect.range || [0, 0];
  const trackId = dragSelect.body.dataset.selectTrack;
  const duration = dragSelect.duration;
  dragSelect.body.querySelector(".track-region")?.classList.remove("dragging");
  dragSelect = null;
  if (b - a >= 5) {
    state.draft.start_seconds = Math.max(0, Math.floor(a * 2) / 2);
    state.draft.preview_length = Math.max(5, Math.round(b - a));
    render();
  } else if (b - a < 0.3 && trackId) {
    // 单击（未形成框选）：试听当前选中区间。
    playAsset(trackId, regionOf(duration));
  }
});

// 只读音轨（如伴奏）单击试听当前区间；可框选音轨由上面的 pointerup 处理。
document.addEventListener("click", (event) => {
  const body = event.target.closest("[data-play-track]");
  if (!body || body.hasAttribute("data-select-track")) return;
  const asset = byId(body.dataset.playTrack);
  if (asset) playAsset(asset.id, regionOf(asset.duration));
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button,[data-play]");
  if (!button) return;
  try {
    if (button.hasAttribute("data-close")) return $("#dialog").close();
    if (button.dataset.upload) return selectUpload(button.dataset.upload);
    if (button.hasAttribute("data-play-combined")) return playCombinedRegion();
    if (button.dataset.play) return playAsset(button.dataset.play);
    if (button.dataset.mode) {
      state.draft.job_type = button.dataset.mode;
      if (button.dataset.mode === "mix") state.draft.source_kind = "vocal";
      if (button.dataset.mode === "separate") state.draft.source_kind = "song";
      render();
      return;
    }
    if (button.dataset.filter) {
      state.songFilter = button.dataset.filter;
      render();
      return;
    }
    if (button.dataset.outputFilter) {
      state.outputFilter = button.dataset.outputFilter;
      render();
      return;
    }
    if (button.hasAttribute("data-import-menu")) {
      showDialog(
        "导入素材",
        `<div style="display:flex;gap:12px;flex-wrap:wrap">${uploadButton("song", "原唱歌曲")}${uploadButton("instrumental", "伴奏音频")}${uploadButton("vocal", "干声录唱")}</div>`,
      );
      return;
    }
    if (button.hasAttribute("data-refresh")) {
      await refreshData(true);
      return toast("已刷新");
    }
    if (button.dataset.cancel) {
      await api(`/jobs/${button.dataset.cancel}/cancel`, { method: "POST" });
      await refreshData();
      return;
    }
    if (button.dataset.retry) {
      await api(`/jobs/${button.dataset.retry}/retry`, { method: "POST" });
      await refreshData();
      return toast("已创建新任务");
    }
    if (button.dataset.remixJob) {
      const job = state.jobs.find((j) => j.id === button.dataset.remixJob);
      const vocal =
        job &&
        state.assets.find(
          (a) =>
            a.metadata?.job_id === job.id && a.metadata?.role === "converted_vocal",
        );
      if (!vocal) return toast("未找到该任务的转换干声。");
      Object.assign(state.draft, {
        job_type: "mix",
        source_kind: "vocal",
        source_id: vocal.id,
        instrumental_id: job.params.instrumental_id || "",
        lyric_id: "",
        vocal_gain_db: job.params.vocal_gain_db ?? 0,
        instrumental_gain_db: job.params.instrumental_gain_db ?? -3,
        offset_ms: job.params.offset_ms ?? 0,
        name: `${job.name} · 混音调整`,
      });
      if (location.hash !== "#studio") location.hash = "studio";
      else render();
      return;
    }
    if (button.dataset.separateSong) {
      await api("/jobs", {
        method: "POST",
        body: JSON.stringify({
          job_type: "separate",
          source_id: button.dataset.separateSong,
        }),
      });
      await refreshData();
      return toast("已开始分离，完成后自动出现两条音轨");
    }
    if (button.hasAttribute("data-audition-close")) {
      state.audition = null;
      render();
      return;
    }
    if (button.dataset.audition === "segment" || button.dataset.audition === "full") {
      const preview = button.dataset.audition === "segment";
      const params = buildCoverParams(preview);
      await startAuditionJob(params);
      toast(preview ? "已开始转换选中区间，完成后自动播放" : "整曲任务已开始，约 20 分钟");
      return;
    }
    if (button.hasAttribute("data-audition-remix")) {
      const job = auditionJob();
      if (!job) return;
      const params = {
        job_type: "mix",
        source_id: job.params.source_id,
        instrumental_id: job.params.instrumental_id || null,
        vocal_gain_db: state.audition.gains.vocal_gain_db,
        instrumental_gain_db: state.audition.gains.instrumental_gain_db,
        offset_ms: job.params.offset_ms,
        formats: state.draft.formats.length ? state.draft.formats : ["mp3"],
        name: `试听重混 · ${state.audition.iterations[0]?.summary || job.name}`,
      };
      await startAuditionJob(params);
      return;
    }
    if (button.hasAttribute("data-audition-reconvert")) {
      const job = auditionJob();
      if (!job) return;
      const params = {
        ...job.params,
        ...state.audition.convert,
        name: `${job.params.name.replace(/ · v\d+$/, "")} · v${state.audition.iterations.length + 1}`,
      };
      await startAuditionJob(params);
      toast("已按新参数重新转换选中区间");
      return;
    }
    if (button.hasAttribute("data-audition-full")) {
      const job = auditionJob();
      const params = job
        ? {
            ...job.params,
            ...state.audition.convert,
            vocal_gain_db: state.audition.gains.vocal_gain_db,
            instrumental_gain_db: state.audition.gains.instrumental_gain_db,
            start_seconds: 0,
            preview_seconds: 0,
            name: job.params.name
              .replace(/^试听 · /, "")
              .replace(/ · v\d+$/, "")
              .replace(/ · \d+秒起$/, " · 我的音色 · 完整版"),
          }
        : buildCoverParams(0);
      await startAuditionJob(params);
      return toast("整曲任务已开始，约 20 分钟，可在任务页查看进度");
    }
    if (button.dataset.log) {
      const result = await api(`/jobs/${button.dataset.log}/log`);
      showDialog(
        "任务日志",
        `<pre class="dialog-log">${esc(result.log)}</pre>`,
      );
      return;
    }
    if (button.dataset.rename) {
      const asset = byId(button.dataset.rename);
      showDialog(
        "重命名音频",
        `<form id="rename-form" data-id="${asset.id}"><label class="field"><span>名称</span><input name="name" required maxlength="120" value="${esc(asset.name)}"></label><div class="dialog-actions"><button class="button primary" type="submit">${icon("save")}保存</button></div></form>`,
      );
      return;
    }
    if (button.dataset.deleteAsset) {
      const asset = byId(button.dataset.deleteAsset);
      return confirmAction(
        "删除音频",
        `删除「${asset.name}」及本地音频文件？`,
        async () => {
          await api(`/assets/${asset.id}`, { method: "DELETE" });
          if ($("#audio").dataset.asset === asset.id) closePlayer();
        },
      );
    }
    if (button.dataset.deleteJob)
      return confirmAction(
        "删除任务记录",
        "删除此任务记录及临时文件？已导出的作品会保留。",
        () => api(`/jobs/${button.dataset.deleteJob}`, { method: "DELETE" }),
      );
    if (button.hasAttribute("data-new-lyric")) {
      state.selectedLyric = null;
      render();
      $("#lyric-form input").focus();
      return;
    }
    if (button.dataset.selectLyric) {
      state.selectedLyric = button.dataset.selectLyric;
      render();
      return;
    }
    if (button.dataset.deleteLyric)
      return confirmAction(
        "删除歌词",
        "删除此歌词及历史版本？已有任务的歌词归档会保留。",
        async () => {
          await api(`/lyrics/${button.dataset.deleteLyric}`, {
            method: "DELETE",
          });
          state.selectedLyric = null;
        },
      );
    if (button.dataset.versions) {
      const versions = await api(`/lyrics/${button.dataset.versions}/versions`);
      showDialog(
        "歌词版本",
        `<div class="versions">${versions.map((v) => `<div class="version-item"><strong>v${v.version} · ${esc(v.name)}</strong><pre>${esc(v.content)}</pre></div>`).join("")}</div>`,
      );
    }
  } catch (error) {
    toast(error.message);
  }
});

document.addEventListener("input", (event) => {
  const field = event.target;
  if (field.dataset.auditionGain && state.audition) {
    state.audition.gains[field.dataset.auditionGain] = Number(field.value);
    const output = field.parentElement.querySelector("output");
    if (output) output.textContent = `${field.value} dB`;
    return;
  }
  if (field.dataset.auditionConvert && state.audition) {
    state.audition.convert[field.dataset.auditionConvert] = Number(field.value);
    return;
  }
  if (field.closest("#job-form") && field.name) {
    if (field.name === "formats") {
      state.draft.formats = [
        ...document.querySelectorAll('input[name="formats"]:checked'),
      ].map((f) => f.value);
      // 记住用户的导出格式偏好，下次打开沿用。
      try {
        localStorage.setItem("cover.formats", JSON.stringify(state.draft.formats));
      } catch {}
    }
    else
      state.draft[field.name] =
        field.type === "checkbox"
          ? field.checked
          : ["range", "number"].includes(field.type)
            ? Number(field.value)
            : field.value;
    const output = document.querySelector(`[data-output="${field.name}"]`);
    if (output) output.textContent = `${field.value} dB`;
    if (["vocal_gain_db", "instrumental_gain_db"].includes(field.name))
      applyCombinedGainsLive();
  }
  if (field.id === "asset-search") {
    state.search = field.value;
    const cursor = field.selectionStart;
    render();
    $("#asset-search").focus();
    $("#asset-search").setSelectionRange(cursor, cursor);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.id === "source-kind") {
    state.draft.source_kind = event.target.value;
    state.draft.source_id = "";
    render();
  } else if (
    ["source_id", "voice_id", "instrumental_id"].includes(event.target.name) &&
    event.target.closest("#job-form")
  ) {
    if (event.target.name === "instrumental_id")
      state.draft.instrumental_id = event.target.value;
    render();
  } else if (
    ["start_seconds", "preview_length"].includes(event.target.name) &&
    event.target.closest("#job-form")
  )
    render();
});

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (
    !["job-form", "lyric-form", "settings-form", "rename-form"].includes(
      form.id,
    )
  )
    return;
  event.preventDefault();
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    if (form.id === "job-form") {
      $("#job-error").textContent = "";
      const d = state.draft;
      if (!d.source_id) throw new Error("请先导入并选择演唱来源。");
      if (d.job_type === "cover") {
        await startAuditionJob(buildCoverParams(0));
        toast("整曲任务已开始，约 20 分钟");
      } else if (d.job_type === "mix") {
        if (!d.instrumental_id) throw new Error("直接混音需要选择伴奏。");
        await api("/jobs", {
          method: "POST",
          body: JSON.stringify({
            job_type: "mix",
            source_id: d.source_id,
            instrumental_id: d.instrumental_id,
            lyric_id: d.lyric_id || null,
            vocal_gain_db: d.vocal_gain_db,
            instrumental_gain_db: d.instrumental_gain_db,
            offset_ms: d.offset_ms,
            formats: d.formats,
            name: d.name,
          }),
        });
        await refreshData();
        toast("混音任务已加入队列");
      } else {
        await api("/jobs", {
          method: "POST",
          body: JSON.stringify({ job_type: "separate", source_id: d.source_id }),
        });
        await refreshData();
        toast("分离任务已加入队列");
      }
    } else if (form.id === "lyric-form") {
      const body = {
        name: form.elements.name.value,
        content: form.elements.content.value,
      };
      const id = form.dataset.id;
      const lyric = await api(`/lyrics${id ? `/${id}` : ""}`, {
        method: id ? "PUT" : "POST",
        body: JSON.stringify(body),
      });
      state.selectedLyric = lyric.id;
      await refreshData();
      toast(`歌词已保存 · v${lyric.version}`);
    } else if (form.id === "settings-form") {
      await api("/settings", {
        method: "PUT",
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      await refreshData(true);
      toast("配置已保存");
    } else if (form.id === "rename-form") {
      await api(`/assets/${form.dataset.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: form.elements.name.value }),
      });
      $("#dialog").close();
      await refreshData();
      toast("名称已更新");
    }
  } catch (error) {
    if (form.id === "job-form" && $("#job-error"))
      $("#job-error").textContent = error.message;
    else toast(error.message);
  } finally {
    if (button.isConnected) button.disabled = false;
  }
});

// ---- 区间播放：单轨走底部播放器，人声+伴奏用 WebAudio 同步合播 ----

function playAsset(id, region) {
  const asset = byId(id);
  if (!asset) return;
  stopCombined();
  const audio = $("#audio");
  if (!region && audio.dataset.asset === id && audio.src) {
    audio.paused ? audio.play().catch((e) => toast(e.message)) : audio.pause();
    return;
  }
  audio.dataset.asset = id;
  audio.src = `/api/assets/${id}/audio`;
  if (region) {
    audio.dataset.regionStart = String(region.start);
    audio.dataset.regionEnd = String(region.start + region.length);
  } else {
    delete audio.dataset.regionStart;
    delete audio.dataset.regionEnd;
  }
  $("#player-title").textContent = asset.name;
  $("#player-subtitle").textContent = `${kindNames[asset.kind]} · ${ext(asset)}${
    region ? ` · ${Math.round(region.start)}–${Math.round(region.start + region.length)}秒区间` : ""
  }`;
  $("#duration-time").textContent = time(asset.duration);
  $("#player").hidden = false;
  document.body.classList.add("has-player");
  audio.play().catch(() => toast("音频暂时无法播放，请尝试下载文件。"));
}

let audioContext = null;
let combinedPlayback = null;
const combinedCache = new Map();

// ---- 播放指示：波形上的播放头 + 正在播放区间的进度填充 ----

function paintTrackIndicators(trackIds, windowStart, position, duration) {
  const active = new Set(trackIds);
  for (const body of document.querySelectorAll("[data-play-track]")) {
    const playhead = body.querySelector(".track-playhead");
    const progress = body.querySelector(".track-window-progress");
    if (!playhead || !progress) continue;
    if (!active.has(body.dataset.playTrack) || !(duration > 0)) {
      playhead.hidden = true;
      progress.hidden = true;
      continue;
    }
    const percent = Math.max(0, Math.min(100, (position / duration) * 100));
    playhead.hidden = false;
    playhead.style.left = `${percent}%`;
    const from = Math.max(0, Math.min(windowStart, duration));
    progress.hidden = false;
    progress.style.left = `${(from / duration) * 100}%`;
    progress.style.width = `${Math.max(0, percent - (from / duration) * 100)}%`;
  }
}

function paintElementTrackIndicators() {
  const audio = $("#audio");
  if (!audio.dataset.asset || !audio.src) {
    paintTrackIndicators([], 0, 0, 0);
    return;
  }
  paintTrackIndicators(
    [audio.dataset.asset],
    Number(audio.dataset.regionStart || 0),
    audio.currentTime,
    audio.duration || byId(audio.dataset.asset)?.duration || 0,
  );
}

// 播放中拖动音量滑杆，立即作用到对应音轨（不重新播放）。
function applyCombinedGainsLive() {
  if (!combinedPlayback || !audioContext) return;
  const dbs = [state.draft.vocal_gain_db, state.draft.instrumental_gain_db];
  combinedPlayback.channels.forEach((channel, index) => {
    const value = 10 ** ((dbs[index] ?? 0) / 20);
    channel.gain.setTargetAtTime(value, audioContext.currentTime, 0.03);
  });
}

function tickCombinedIndicators() {
  if (!combinedPlayback) return;
  const elapsed = audioContext.currentTime - combinedPlayback.startedAt;
  const position = combinedPlayback.start + Math.max(0, elapsed);
  paintTrackIndicators(
    combinedPlayback.trackIds,
    combinedPlayback.start,
    Math.min(position, combinedPlayback.start + combinedPlayback.length),
    combinedPlayback.duration,
  );
  combinedPlayback.frame = requestAnimationFrame(tickCombinedIndicators);
}

function stopCombined() {
  if (combinedPlayback) {
    cancelAnimationFrame(combinedPlayback.frame);
    for (const source of combinedPlayback.sources) {
      try {
        source.stop();
      } catch {}
      source.disconnect();
    }
    combinedPlayback.gain.disconnect();
    combinedPlayback = null;
  }
  const button = document.querySelector("[data-play-combined]");
  if (button && button.dataset.playing) {
    delete button.dataset.playing;
    button.innerHTML = `${icon("layers")}人声+伴奏`;
    if (window.lucide) lucide.createIcons();
  }
  if ($("#audio").dataset.asset && $("#audio").src) paintElementTrackIndicators();
  else paintTrackIndicators([], 0, 0, 0);
}

async function playCombinedRegion() {
  const stems = coverStems();
  if (!stems.vocal) return toast("请先选择音源。");
  if (!stems.instrumental)
    return playAsset(stems.vocal.id, regionOf(stems.vocal.duration));
  if (combinedPlayback) return stopCombined();
  const button = document.querySelector("[data-play-combined]");
  if (button) {
    button.dataset.playing = "1";
    button.innerHTML = `${icon("square")}停止合播`;
    if (window.lucide) lucide.createIcons();
  }
  try {
    $("#audio").pause();
    audioContext = audioContext || new AudioContext();
    await audioContext.resume();
    const context = audioContext;
    const decode = async (asset) => {
      if (combinedCache.has(asset.id)) return combinedCache.get(asset.id);
      const response = await fetch(`/api/assets/${asset.id}/audio`);
      const buffer = await context.decodeAudioData(await response.arrayBuffer());
      if (combinedCache.size >= 4)
        combinedCache.delete(combinedCache.keys().next().value);
      combinedCache.set(asset.id, buffer);
      return buffer;
    };
    const [vocalBuffer, instrumentalBuffer] = await Promise.all([
      decode(stems.vocal),
      decode(stems.instrumental),
    ]);
    const region = regionOf(stems.vocal.duration);
    const start = Math.max(
      0,
      Math.min(region.start, vocalBuffer.duration - 1, instrumentalBuffer.duration - 1),
    );
    const end = Math.min(
      region.start + region.length,
      vocalBuffer.duration,
      instrumentalBuffer.duration,
    );
    if (end - start < 0.5) {
      stopCombined();
      return toast("选中区间超出音频范围，请重新框选。");
    }
    const gain = context.createGain();
    gain.connect(context.destination);
    const channels = [state.draft.vocal_gain_db, state.draft.instrumental_gain_db].map((db) => {
      const channel = context.createGain();
      channel.gain.value = 10 ** (db / 20);
      channel.connect(gain);
      return channel;
    });
    const sources = [vocalBuffer, instrumentalBuffer].map((buffer, index) => {
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(channels[index]);
      source.start(0, start, end - start);
      return source;
    });
    combinedPlayback = {
      sources,
      gain,
      channels,
      trackIds: [stems.vocal.id, stems.instrumental.id],
      start,
      length: end - start,
      duration: vocalBuffer.duration,
      startedAt: context.currentTime,
      frame: 0,
    };
    sources[0].onended = () => stopCombined();
    tickCombinedIndicators();
  } catch (error) {
    stopCombined();
    toast(`合播失败：${error.message}`);
  }
}

function closePlayer() {
  $("#audio").pause();
  $("#audio").removeAttribute("src");
  $("#audio").dataset.asset = "";
  $("#player").hidden = true;
  document.body.classList.remove("has-player");
  paintTrackIndicators([], 0, 0, 0);
}
$("#close-player").onclick = closePlayer;
$("#play-toggle").onclick = () => {
  const audio = $("#audio");
  audio.paused
    ? audio.play().catch(() => toast("无法播放此音频。"))
    : audio.pause();
};
$("#audio").volume = 0.8;
$("#volume").oninput = (event) =>
  ($("#audio").volume = Number(event.target.value));
$("#seek").oninput = (event) => {
  if (Number.isFinite($("#audio").duration))
    $("#audio").currentTime = (event.target.value / 100) * $("#audio").duration;
};
$("#audio").ontimeupdate = () => {
  const audio = $("#audio");
  $("#current-time").textContent = time(audio.currentTime);
  $("#seek").value = audio.duration
    ? (audio.currentTime / audio.duration) * 100
    : 0;
  // 到区间末尾后自动继续播放整轨；播放头滑出高亮带即可看出已超区间。
  if (audio.dataset.regionEnd && audio.currentTime >= Number(audio.dataset.regionEnd))
    delete audio.dataset.regionEnd;
  paintElementTrackIndicators();
};
$("#audio").onloadedmetadata = () => {
  const audio = $("#audio");
  $("#duration-time").textContent = time(audio.duration);
  const regionStart = Number(audio.dataset.regionStart || 0);
  if (regionStart && Math.abs(audio.currentTime - regionStart) > 0.5)
    audio.currentTime = regionStart;
};
for (const event of ["play", "pause", "ended"])
  $("#audio").addEventListener(event, () => {
    $("#play-toggle").innerHTML = icon($("#audio").paused ? "play" : "pause");
    if (window.lucide) lucide.createIcons();
  });
$("#environment-chip").onclick = () => (location.hash = "settings");
$("#dialog").addEventListener("click", (event) => {
  if (event.target === $("#dialog") && !state.uploading) $("#dialog").close();
});
window.addEventListener("resize", () =>
  document.querySelectorAll("canvas[data-asset]").forEach(drawWave),
);

async function refreshData(includeStatus = false) {
  const [assets, lyrics, jobs] = await Promise.all([
    api("/assets"),
    api("/lyrics"),
    api("/jobs"),
  ]);
  state.assets = assets;
  state.lyrics = lyrics;
  state.jobs = jobs;
  if (includeStatus) {
    [state.status, state.settings] = await Promise.all([
      api("/status"),
      api("/settings"),
    ]);
  }
  render();
}

window.addEventListener("hashchange", () => {
  const page = location.hash.slice(1);
  state.page = pageNames[page] ? page : "studio";
  state.search = "";
  if (state.page === "lyrics" && !state.selectedLyric)
    state.selectedLyric = state.lyrics[0]?.id || null;
  render();
  window.scrollTo(0, 0);
});

async function poll() {
  try {
    const jobs = await api("/jobs");
    const changed = JSON.stringify(jobs) !== JSON.stringify(state.jobs);
    if (changed) {
      state.jobs = jobs;
      state.assets = await api("/assets");
      updateChrome();
      const audition = state.audition;
      let auditionFinished = false;
      if (audition) {
        const job = jobs.find((j) => j.id === audition.jobId);
        if (job && !activeStates.includes(job.state) && !audition.shown) {
          audition.shown = true;
          auditionFinished = true;
          if (job.state === "completed") {
            const output = job.outputs.map(byId).filter(Boolean);
            const mp3 = (output.find((x) => x.path.endsWith(".mp3")) || output[0])?.id;
            if (mp3)
              audition.iterations.unshift({
                mp3,
                summary: summarizeAudition(job),
              });
            render();
            if (mp3) playAsset(mp3);
          }
        }
      }
      if (state.page === "studio") {
        if (auditionFinished && state.audition) {
          // render() already ran above; refresh canvases and icons once more.
          finishRender();
        } else {
          // Region section follows separation progress without touching form inputs.
          const source = byId(state.draft.source_id);
          const separation =
            source?.kind === "song"
              ? (pendingSeparation(source.id) || separationFor(source.id)?.job)
              : null;
          const regionKey = [
            state.draft.job_type,
            state.draft.source_id,
            separation?.id || "",
            separation?.state || "",
            separation?.progress || 0,
          ].join("|");
          const regionPanel = $("#region-panel");
          if (regionPanel && regionPanel.dataset.key !== regionKey) {
            regionPanel.dataset.key = regionKey;
            regionPanel.innerHTML = renderRegionSection();
          }
          const panel = $("#audition-panel");
          const running = state.audition
            ? jobs.find((j) => j.id === state.audition.jobId)
            : null;
          const key = [
            state.audition?.jobId || "",
            running?.state || "",
            running?.progress || 0,
            state.audition?.iterations.length || 0,
          ].join("|");
          if (panel && panel.dataset.key !== key) {
            panel.dataset.key = key;
            panel.innerHTML = renderAuditionInner();
            finishRender();
          }
          $("#studio-jobs").innerHTML = renderRecentJobs();
          $("#studio-outputs").innerHTML = renderRecentOutputs();
          finishRender();
        }
      } else if (["jobs", "outputs"].includes(state.page)) render();
    }
  } catch {}
  setTimeout(poll, 2500);
}

(async () => {
  try {
    state.page = pageNames[location.hash.slice(1)]
      ? location.hash.slice(1)
      : "studio";
    try {
      const saved = JSON.parse(localStorage.getItem("cover.formats") || "null");
      if (Array.isArray(saved) && saved.length && saved.every((f) => ["wav", "mp3"].includes(f)))
        state.draft.formats = saved;
    } catch {}
    await refreshData(true);
    if (state.page === "lyrics" && state.lyrics.length) {
      state.selectedLyric = state.lyrics[0].id;
      render();
    }
    poll();
  } catch (error) {
    $("#main").innerHTML =
      `${pageHeading("工作室暂时无法连接", "")}<div class="notice">${icon("circle-alert")}<span>${esc(error.message)}</span><button class="text-link" onclick="location.reload()">重新连接</button></div>`;
    finishRender();
  }
})();
