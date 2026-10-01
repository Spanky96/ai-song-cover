"use strict";

const state = {
  assets: [],
  lyrics: [],
  jobs: [],
  status: null,
  settings: null,
  page: "studio",
  songFilter: "all",
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
    formats: ["wav", "mp3"],
  },
  uploading: false,
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
  $("#output-count").textContent = assetsOf("output").length;
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
    draft.job_type !== "separate"
      ? `<section class="form-section">${fieldTitle(draft.job_type === "cover" ? "03" : "02", "伴奏与歌词", `<span class="optional">可选</span>`)}<div class="form-grid"><label class="field"><span>伴奏 ${`<button type="button" class="text-link" style="float:right" data-upload="instrumental">${icon("plus")}导入</button>`}</span><select name="instrumental_id" aria-label="选择伴奏">${optionList(instrumentals, draft.instrumental_id, source?.kind === "song" ? "使用原曲分离伴奏" : "不添加伴奏")}</select></label><label class="field"><span>歌词归档 <a href="#lyrics" class="text-link" style="float:right">${icon("notebook-pen")}</a></span><select name="lyric_id" aria-label="选择归档歌词"><option value="">不归档歌词</option>${state.lyrics.map((l) => `<option value="${l.id}" ${l.id === draft.lyric_id ? "selected" : ""}>${esc(l.name)} · v${l.version}</option>`).join("")}</select></label></div></section>`
      : "";
  const notice = !environmentReady()
    ? `<div class="notice">${icon("hard-drive")}<span>${state.status?.storage.model_space_ok ? "AI 推理环境尚未安装。" : "磁盘剩余空间不足，模型下载尚未开始。"}</span><a href="#settings" class="text-link">配置环境 ${icon("arrow-up-right")}</a></div>`
    : "";
  return (
    pageHeading("AI 翻唱工作室", "我的声音，我的演唱。") +
    notice +
    `<div class="overview"><div class="overview-item"><div class="overview-icon">${icon("mic")}</div><div><strong>${voices.length}</strong><span>参考音色</span></div></div><div class="overview-item"><div class="overview-icon">${icon("disc-3")}</div><div><strong>${assetsOf("song").length + assetsOf("vocal").length}</strong><span>演唱素材</span></div></div><div class="overview-item"><div class="overview-icon">${icon("headphones")}</div><div><strong>${assetsOf("output").length}</strong><span>已导出作品</span></div></div></div><div class="studio-grid"><section class="composer"><div class="section-heading"><h2>新建创作</h2><span class="subtle-label">${draft.job_type === "cover" ? "歌声转换" : draft.job_type === "mix" ? "音频混音" : "双轨分离"}</span></div><div class="mode-tabs" role="group" aria-label="创作模式">${[
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
      )}</div><form id="job-form">${sourceMarkup}${voiceField}${accompaniment}${draft.job_type !== "separate" ? renderMixFields() : ""}${draft.job_type !== "separate" ? renderPreviewFields() : ""}<label class="field"><span>作品名称</span><input type="text" name="name" maxlength="120" value="${esc(draft.name)}" placeholder="${esc(byId(draft.source_id)?.name || "未命名作品")}"></label><div class="submit-row">${draft.job_type !== "separate" ? `<button class="button secondary" type="submit" data-preview="1">${icon("headphones")}试听片段</button>` : ""}<button class="button primary" id="generate-button" type="submit">${icon(draft.job_type === "cover" ? "sparkles" : "play")}${draft.job_type === "cover" ? "生成翻唱" : draft.job_type === "mix" ? "开始混音" : "分离音频"}</button><small>${draft.job_type === "cover" ? "原曲旋律与歌词" : draft.job_type === "mix" ? "WAV / MP3" : "人声 / 伴奏"}</small></div><div class="form-error" id="job-error" role="alert"></div></form></section><aside class="activity"><section><div class="section-heading"><h2>最近任务</h2><a href="#jobs" class="text-link">全部 ${icon("arrow-right")}</a></div><div id="studio-jobs">${renderRecentJobs()}</div></section><section class="recent-section"><div class="section-heading"><h2>最近作品</h2><a href="#outputs" class="text-link">全部 ${icon("arrow-right")}</a></div><div id="studio-outputs">${renderRecentOutputs()}</div></section></aside></div>`
  );
}

function renderMixFields() {
  const d = state.draft;
  const range = (name, title, min, max, suffix) =>
    `<label class="field"><span>${title}</span><div class="range-field"><input name="${name}" type="range" min="${min}" max="${max}" value="${d[name]}" step="1" aria-label="${title}"><output data-output="${name}">${d[name]} ${suffix}</output></div></label>`;
  return `<section class="form-section"><div class="form-grid">${range("vocal_gain_db", "人声音量", -24, 12, "dB")}${range("instrumental_gain_db", "伴奏音量", -24, 12, "dB")}</div></section><details class="advanced"><summary>高级参数 ${icon("chevron-down")}</summary><div class="form-grid">${d.job_type === "cover" ? `<label class="field"><span>推理步数</span><select name="diffusion_steps" aria-label="推理步数">${[10, 20, 30, 40, 50, 75, 100].map((n) => `<option ${d.diffusion_steps === n ? "selected" : ""} value="${n}">${n}</option>`).join("")}</select></label><label class="field"><span>人声移调 / 半音</span><input name="pitch_shift" type="number" min="-12" max="12" step="1" value="${d.pitch_shift}"></label><label class="field"><span>音色引导强度</span><input name="cfg_rate" type="number" min="0" max="1" step="0.05" value="${d.cfg_rate}"></label>` : ""}<label class="field"><span>人声时间偏移 / ms</span><input name="offset_ms" type="number" min="-10000" max="10000" step="10" value="${d.offset_ms}" title="正值延后人声，负值提前人声"></label></div></details><div class="form-grid" style="margin-bottom:20px"><label class="field"><span>导出格式</span><div class="export-options"><label><input type="checkbox" name="formats" value="wav" ${d.formats.includes("wav") ? "checked" : ""}> WAV</label><label><input type="checkbox" name="formats" value="mp3" ${d.formats.includes("mp3") ? "checked" : ""}> MP3</label></div></label></div>`;
}

function renderPreviewFields() {
  const d = state.draft;
  const source = byId(d.source_id);
  return `<section class="form-section"><div class="field-heading"><span class="field-title">${icon("headphones")}试听片段</span><span class="optional">快速调参</span></div><div class="form-grid"><label class="field"><span>起点 / 秒${source ? `（全长 ${time(source.duration)}）` : ""}</span><input name="start_seconds" type="number" min="0" step="1" value="${d.start_seconds}" aria-label="片段起点秒数"></label><label class="field"><span>片段时长</span><select name="preview_length" aria-label="片段时长">${[10, 18, 30].map((n) => `<option ${Number(d.preview_length) === n ? "selected" : ""} value="${n}">${n} 秒</option>`).join("")}</select></label></div><small class="subtle-label">只转换选中片段，快速比较参数；满意后再生成整曲。</small></section>`;
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
  const outputs = assetsOf("output").slice(0, 3);
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

function renderOutputs() {
  return (
    pageHeading(
      "我的作品",
      `${assetsOf("output").length} 个音频文件`,
      `<a class="button primary" href="#studio">${icon("plus")}新建创作</a>`,
    ) +
    renderSearch() +
    `<div id="asset-results">${renderAssetTable(filterBySearch(assetsOf("output")), "还没有完成的作品")}</div>`
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

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button,[data-play]");
  if (!button) return;
  try {
    if (button.hasAttribute("data-close")) return $("#dialog").close();
    if (button.dataset.upload) return selectUpload(button.dataset.upload);
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
  if (field.closest("#job-form") && field.name) {
    if (field.name === "formats")
      state.draft.formats = [
        ...document.querySelectorAll('input[name="formats"]:checked'),
      ].map((f) => f.value);
    else
      state.draft[field.name] = ["range", "number"].includes(field.type)
        ? Number(field.value)
        : field.value;
    const output = document.querySelector(`[data-output="${field.name}"]`);
    if (output) output.textContent = `${field.value} dB`;
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
    ["source_id", "voice_id"].includes(event.target.name) &&
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
      const params = { ...state.draft };
      delete params.source_kind;
      delete params.preview_length;
      for (const key of ["voice_id", "instrumental_id", "lyric_id"])
        params[key] ||= null;
      if (!params.source_id) throw new Error("请先导入并选择演唱来源。");
      if (params.job_type === "cover" && !params.voice_id)
        throw new Error("请先导入并选择目标音色。");
      if (!params.formats.length) throw new Error("请选择至少一种导出格式。");
      const previewMode = event.submitter?.dataset.preview === "1";
      if (previewMode) {
        params.preview_seconds = Number(state.draft.preview_length) || 18;
        params.start_seconds = Math.max(0, Number(state.draft.start_seconds) || 0);
        if (!params.name)
          params.name = `试听 · ${byId(params.source_id)?.name || "未命名"} · ${params.start_seconds}秒起`;
      } else {
        params.start_seconds = 0;
        params.preview_seconds = 0;
      }
      await api("/jobs", { method: "POST", body: JSON.stringify(params) });
      await refreshData();
      toast("任务已加入队列");
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

function playAsset(id) {
  const asset = byId(id);
  if (!asset) return;
  const audio = $("#audio");
  if (audio.dataset.asset === id) {
    audio.paused ? audio.play().catch((e) => toast(e.message)) : audio.pause();
    return;
  }
  audio.dataset.asset = id;
  audio.src = `/api/assets/${id}/audio`;
  $("#player-title").textContent = asset.name;
  $("#player-subtitle").textContent =
    `${kindNames[asset.kind]} · ${ext(asset)}`;
  $("#duration-time").textContent = time(asset.duration);
  $("#player").hidden = false;
  document.body.classList.add("has-player");
  audio.play().catch(() => toast("音频暂时无法播放，请尝试下载文件。"));
}

function closePlayer() {
  $("#audio").pause();
  $("#audio").removeAttribute("src");
  $("#audio").dataset.asset = "";
  $("#player").hidden = true;
  document.body.classList.remove("has-player");
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
};
$("#audio").onloadedmetadata = () =>
  ($("#duration-time").textContent = time($("#audio").duration));
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
      if (state.page === "studio") {
        $("#studio-jobs").innerHTML = renderRecentJobs();
        $("#studio-outputs").innerHTML = renderRecentOutputs();
        finishRender();
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
