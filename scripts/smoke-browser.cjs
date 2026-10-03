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
let submittedMixId = null;

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
  assert(
    await page.locator('#job-form button[data-separate-song]').count(),
    "Separate CTA missing for unseparated song",
  );
  await page.locator("#job-form button[data-separate-song]").click();
  await page.waitForSelector("#job-form .track", { timeout: 120000 });
  assert.equal(
    await page.locator("#job-form .track").count(),
    2,
    "Separated tracks missing",
  );
  assert(
    await page.locator('#job-form input[name="start_seconds"]').count(),
    "Region start field missing after separation",
  );
  assert(
    await page.locator('#job-form button[data-audition="segment"]').count(),
    "Audition segment button missing",
  );
  const regionBefore = await page
    .locator("#job-form .track-region")
    .first()
    .getAttribute("style");
  const trackBox = await page
    .locator("#job-form .track-body[data-select-track]")
    .boundingBox();
  assert(trackBox, "Selectable track body missing");
  await page.mouse.move(trackBox.x + trackBox.width * 0.2, trackBox.y + 10);
  await page.mouse.down();
  await page.mouse.move(trackBox.x + trackBox.width * 0.6, trackBox.y + 10, {
    steps: 6,
  });
  await page.mouse.up();
  await page.waitForFunction(
    (before) =>
      document.querySelector("#job-form .track-region")?.getAttribute("style") !==
      before,
    regionBefore,
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
  // UI 默认仅 MP3；冒烟显式补勾 WAV，继续覆盖双格式导出。
  const wavBox = page.locator('#job-form input[name="formats"][value="wav"]');
  if (!(await wavBox.isChecked())) await wavBox.check();
  const submission = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${url}/api/jobs`,
  );
  await page.getByRole("button", { name: "开始混音", exact: true }).click();
  const submitted = await submission;
  assert(submitted.ok(), "Mix job submission failed");
  submittedMixId = (await submitted.json()).id;
  // Poll in Node: waitForFunction treats an async predicate's Promise as truthy.
  const deadline = Date.now() + 30000;
  let completedMix = null;
  while (Date.now() < deadline) {
    const jobs = await (await request.get(`${url}/api/jobs`)).json();
    const job = jobs.find((entry) => entry.id === submittedMixId);
    if (job?.state === "completed") {
      completedMix = job;
      break;
    }
    assert(
      !["failed", "cancelled", "interrupted"].includes(job?.state),
      job?.error,
    );
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  assert(completedMix, "Mix job did not finish");
  await page.goto(`${url}/#outputs`);
  await page.waitForSelector(".asset-table tbody tr");
  await page.evaluate(() => (document.querySelector("#toast").hidden = true));
  const outputResponse = await request.get(`${url}/api/assets`);
  const outputAssets = await outputResponse.json();
  const outputs = outputAssets.filter(
    (a) => a.kind === "output" && a.metadata.job_id === submittedMixId,
  );
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
          ) {
            if (["queued", "running", "cancelling"].includes(job.state)) {
              await request.post(`${url}/api/jobs/${job.id}/cancel`);
              for (let attempt = 0; attempt < 50; attempt++) {
                const jobs = await (
                  await request.get(`${url}/api/jobs`)
                ).json();
                const current = jobs.find((entry) => entry.id === job.id);
                if (
                  !current ||
                  !["queued", "running", "cancelling"].includes(current.state)
                )
                  break;
                await new Promise((resolve) => setTimeout(resolve, 200));
              }
            }
            const response = await request.delete(`${url}/api/jobs/${job.id}`);
            assert(response.ok(), "Test job cleanup failed");
          }
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
