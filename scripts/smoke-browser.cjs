const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");

const url = process.env.COVER_URL || "http://127.0.0.1:8765";
const artifacts = path.resolve(__dirname, "../artifacts");
fs.mkdirSync(artifacts, { recursive: true });

function installedBrowser() {
  if (process.env.COVER_BROWSER_PATH) return process.env.COVER_BROWSER_PATH;
  const system = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  if (fs.existsSync(system)) return system;
  const cache = path.join(os.homedir(), "Library/Caches/ms-playwright");
  if (fs.existsSync(cache)) {
    const dirs = fs
      .readdirSync(cache)
      .filter((n) => /^chromium-\d+$/.test(n))
      .sort((a, b) => Number(b.split("-")[1]) - Number(a.split("-")[1]));
    for (const dir of dirs) {
      const executable = path.join(
        cache,
        dir,
        "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
      );
      if (fs.existsSync(executable)) return executable;
    }
  }
  return undefined;
}

const reference = path.join(artifacts, "browser-check-vocal.wav");
const accompaniment = path.join(artifacts, "browser-check-instrumental.wav");
for (const [file, freq] of [
  [reference, 440],
  [accompaniment, 220],
]) {
  execFileSync("ffmpeg", [
    "-v",
    "error",
    "-y",
    "-f",
    "lavfi",
    "-i",
    `aevalsrc=0.15*sin(2*PI*${freq}*t)*(0.7+0.3*sin(2*PI*3*t)):s=44100:d=3`,
    file,
  ]);
}

let browser;
let context;
let originalAssets = new Set();
let originalLyrics = new Set();
let originalJobs = new Set();
let baselineLoaded = false;

async function noHorizontalOverflow(page, label) {
  const dimensions = await page.evaluate(() => ({
    viewport: innerWidth,
    content: document.documentElement.scrollWidth,
  }));
  assert(
    dimensions.content <= dimensions.viewport + 1,
    `${label} has horizontal overflow: ${JSON.stringify(dimensions)}`,
  );
  const overlap = await page.evaluate(() => {
    const visible = (el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && !el.closest("details:not([open])");
    };
    const fields = [...document.querySelectorAll(".form-grid .field")].filter(
      visible,
    );
    const result = [];
    for (let i = 0; i < fields.length; i++)
      for (let j = i + 1; j < fields.length; j++) {
        const a = fields[i].getBoundingClientRect(),
          b = fields[j].getBoundingClientRect();
        if (
          Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
          Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1
        )
          result.push([i, j]);
      }
    return result;
  });
  assert.deepEqual(overlap, [], `${label}: fields overlap`);
}

(async () => {
  browser = await chromium.launch({
    headless: true,
    executablePath: installedBrowser(),
  });
  context = await browser.newContext({
    viewport: { width: 1440, height: 1040 },
    locale: "zh-CN",
  });
  const request = context.request;
  originalAssets = new Set(
    (await (await request.get(`${url}/api/assets`)).json()).map((a) => a.id),
  );
  originalLyrics = new Set(
    (await (await request.get(`${url}/api/lyrics`)).json()).map((a) => a.id),
  );
  originalJobs = new Set(
    (await (await request.get(`${url}/api/jobs`)).json()).map((a) => a.id),
  );
  baselineLoaded = true;
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(url);
  await page.waitForSelector("#job-form");
  await noHorizontalOverflow(page, "Desktop studio");
  await page.screenshot({
    path: path.join(artifacts, "studio-empty-desktop.png"),
    fullPage: true,
  });

  async function importAudio(button, file) {
    const chooserPromise = page.waitForEvent("filechooser");
    await button.click();
    const chooser = await chooserPromise;
    await chooser.setFiles(file);
    await page.waitForFunction(() => !document.querySelector("#dialog").open);
  }

  await page.goto(`${url}/#voices`);
  await importAudio(
    page.getByRole("button", { name: "添加音色", exact: true }),
    reference,
  );
  await page.waitForSelector(".voice-item canvas");
  const paintedPixels = await page
    .locator(".voice-item canvas")
    .first()
    .evaluate((canvas) => {
      const data = canvas
        .getContext("2d")
        .getImageData(0, 0, canvas.width, canvas.height).data;
      let count = 0;
      for (let i = 3; i < data.length; i += 4) if (data[i]) count++;
      return count;
    });
  assert(paintedPixels > 30, "Uploaded waveform is blank");
  await page
    .getByRole("button", { name: "试听音色", exact: true })
    .last()
    .click();
  await page.waitForFunction(
    () => document.querySelector("#audio").currentTime > 0.1,
  );
  assert(
    await page.locator("#player").isVisible(),
    "Audio player did not open",
  );
  await page.getByRole("button", { name: "关闭播放器", exact: true }).click();

  for (const [label, file] of [
    ["干声录唱", reference],
    ["伴奏音频", accompaniment],
    ["原唱歌曲", reference],
  ]) {
    await page.goto(`${url}/#songs`);
    await page.getByRole("button", { name: "导入素材", exact: true }).click();
    await importAudio(
      page.getByRole("button", { name: label, exact: true }),
      file,
    );
    await page.waitForSelector(".asset-table tbody tr");
  }

  await page.goto(`${url}/#lyrics`);
  await page.getByRole("button", { name: "新建歌词", exact: true }).click();
  await page.locator('#lyric-form input[name="name"]').fill("浏览器验收歌词");
  await page
    .locator('#lyric-form textarea[name="content"]')
    .fill("第一版歌词\n测试归档，不生成歌声");
  await page.getByRole("button", { name: "保存歌词", exact: true }).click();
  await page.waitForFunction(() =>
    Boolean(document.querySelector("#lyric-form").dataset.id),
  );
  await page
    .locator('#lyric-form textarea[name="content"]')
    .fill("第二版歌词\n测试归档，不生成歌声");
  await page.getByRole("button", { name: "保存歌词", exact: true }).click();
  await page.waitForFunction(() =>
    document
      .querySelector(".lyric-editor-footer small")
      .textContent.includes("v2"),
  );
  await page.getByRole("button", { name: "历史版本", exact: true }).click();
  await page.waitForSelector(".version-item");
  assert.equal(await page.locator(".version-item").count(), 2);
  await page.locator("#dialog [data-close]").click();

  await page.goto(`${url}/#studio`);
  await page.waitForSelector("#job-form");
  assert.equal(
    await page
      .locator('select[name="instrumental_id"] option')
      .first()
      .textContent(),
    "使用原曲分离伴奏",
  );
  await page.getByRole("button", { name: "直接混音", exact: true }).click();
  const assets = await (await request.get(`${url}/api/assets`)).json();
  const vocal = assets.find(
    (a) => a.kind === "vocal" && !originalAssets.has(a.id),
  );
  const instrumental = assets.find(
    (a) => a.kind === "instrumental" && !originalAssets.has(a.id),
  );
  await page.locator('select[name="source_id"]').selectOption(vocal.id);
  await page
    .locator('select[name="instrumental_id"]')
    .selectOption(instrumental.id);
  await page
    .locator('#job-form input[name="name"]')
    .fill("浏览器验收混音（测试音频）");
  await page.getByRole("button", { name: "开始混音", exact: true }).click();
  await page.waitForFunction(
    async () => {
      const jobs = await (await fetch("/api/jobs")).json();
      return jobs.some(
        (j) =>
          j.name === "浏览器验收混音（测试音频）" && j.state === "completed",
      );
    },
    null,
    { timeout: 30000 },
  );
  await page.goto(`${url}/#outputs`);
  await page.waitForSelector(".asset-table tbody tr");
  await page.evaluate(() => (document.querySelector("#toast").hidden = true));
  const outputs = (
    await (await request.get(`${url}/api/assets`)).json()
  ).filter((a) => a.kind === "output" && !originalAssets.has(a.id));
  assert.equal(outputs.length, 2);
  for (const output of outputs) {
    const response = await request.get(
      `${url}/api/assets/${output.id}/audio?download=true`,
    );
    assert(response.ok(), "Output download failed");
    assert((await response.body()).length > 1000, "Output audio is empty");
  }
  await page.screenshot({
    path: path.join(artifacts, "outputs-desktop.png"),
    fullPage: true,
  });

  for (const viewport of [
    { width: 1440, height: 1040 },
    { width: 390, height: 844 },
    { width: 320, height: 740 },
  ]) {
    await page.setViewportSize(viewport);
    for (const route of [
      "studio",
      "voices",
      "songs",
      "lyrics",
      "outputs",
      "jobs",
      "settings",
    ]) {
      await page.goto(`${url}/#${route}`);
      await page.waitForSelector("h1");
      await noHorizontalOverflow(page, `${viewport.width}px ${route}`);
      await page.screenshot({
        path: path.join(artifacts, `${route}-${viewport.width}.png`),
        fullPage: true,
      });
    }
  }
  assert.deepEqual(errors, [], "Uncaught browser errors");
  console.log(
    JSON.stringify(
      {
        result: "passed",
        viewports: 3,
        pages: 7,
        waveformPaintedPixels: paintedPixels,
        audioPlayback: true,
        lyricVersions: 2,
        exportedFormats: ["wav", "mp3"],
        browserErrors: errors,
      },
      null,
      2,
    ),
  );
})()
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(async () => {
    if (context && baselineLoaded) {
      const request = context.request;
      try {
        for (const job of await (await request.get(`${url}/api/jobs`)).json()) {
          if (
            !originalJobs.has(job.id) &&
            job.name === "浏览器验收混音（测试音频）"
          )
            await request.delete(`${url}/api/jobs/${job.id}`);
        }
        for (const asset of await (
          await request.get(`${url}/api/assets`)
        ).json()) {
          if (
            !originalAssets.has(asset.id) &&
            (asset.name.startsWith("browser-check-") ||
              asset.name === "浏览器验收混音（测试音频）")
          )
            await request.delete(`${url}/api/assets/${asset.id}`);
        }
        for (const lyric of await (
          await request.get(`${url}/api/lyrics`)
        ).json()) {
          if (!originalLyrics.has(lyric.id) && lyric.name === "浏览器验收歌词")
            await request.delete(`${url}/api/lyrics/${lyric.id}`);
        }
      } catch (error) {
        console.error("Cleanup failed:", error.message);
        process.exitCode = 1;
      }
    }
    if (browser) await browser.close();
  });
