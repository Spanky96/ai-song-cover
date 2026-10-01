const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

const root = path.resolve(__dirname, "../artifacts/personal-cover");
const session = JSON.parse(fs.readFileSync(path.join(root, "session.json")));
const url = process.env.COVER_URL || "http://127.0.0.1:8765";

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath:
      process.env.COVER_BROWSER_PATH ||
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1040 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${url}/#outputs`);
    const output = session.deliverables.find((asset) =>
      asset.path.endsWith(".mp3"),
    );
    assert(output, "Full MP3 export is missing");
    const play = page.locator(`[data-play="${output.asset_id}"]`);
    await play.waitFor();
    await play.click();
    await page.waitForFunction(
      () => document.querySelector("#audio").currentTime > 0.1,
    );
    const duration = await page
      .locator("#audio")
      .evaluate((audio) => audio.duration);
    assert(
      Math.abs(duration - output.duration) < 0.1,
      "Browser audio duration mismatch",
    );
    for (const start of [35, 85, 135, 175]) {
      await page.locator("#audio").evaluate(async (audio, time) => {
        audio.currentTime = time;
        await audio.play();
      }, start);
      await page.waitForFunction(
        (time) => document.querySelector("#audio").currentTime > time + 0.15,
        start,
      );
    }
    await page.locator("#audio").evaluate((audio) => audio.pause());
    const row = play.locator("xpath=ancestor::tr");
    const paintedPixels = await row.locator("canvas").evaluate((canvas) => {
      const data = canvas
        .getContext("2d")
        .getImageData(0, 0, canvas.width, canvas.height).data;
      let count = 0;
      for (let i = 3; i < data.length; i += 4) if (data[i]) count++;
      return count;
    });
    assert(paintedPixels > 100, "Full output waveform is blank");
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({
        width,
        height: width === 1440 ? 1040 : 844,
      });
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 1,
      );
      assert(!overflow, `Output page overflow at ${width}px`);
      if (width < 500) {
        const clippedNames = await page
          .locator(".asset-cell strong")
          .evaluateAll(
            (names) =>
              names.filter((name) => name.scrollWidth > name.clientWidth + 1)
                .length,
          );
        assert.equal(clippedNames, 0, `Truncated output names at ${width}px`);
      }
      await page.screenshot({
        path: path.join(root, `result-${width}.png`),
        fullPage: true,
      });
    }
    for (const asset of session.deliverables) {
      const response = await page.request.get(
        `${url}/api/assets/${asset.asset_id}/audio?download=true`,
      );
      assert(response.ok(), "Full output download failed");
      assert.equal((await response.body()).length, asset.bytes);
    }
    assert.deepEqual(errors, []);
    const result = {
      result: "passed",
      duration,
      seekPlaybackSeconds: [35, 85, 135, 175],
      waveformPaintedPixels: paintedPixels,
      viewportWidths: [1440, 390, 320],
      browserErrors: errors,
    };
    fs.writeFileSync(
      path.join(root, "browser-validation.json"),
      JSON.stringify(result, null, 2),
    );
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
