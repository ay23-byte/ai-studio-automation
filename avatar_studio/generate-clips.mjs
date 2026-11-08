import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";

const count = Number(process.argv[2]);
const audioDir = process.env.AVATAR_AUDIO_DIR;
const outputDir = process.env.AVATAR_OUTPUT_DIR;
const gender = process.env.AVATAR_GENDER || "female";
if (!Number.isInteger(count) || count < 1 || !audioDir || !outputDir) {
  throw new Error("Usage: node generate-clips.mjs <chapter-count> with AVATAR_AUDIO_DIR and AVATAR_OUTPUT_DIR.");
}
fs.mkdirSync(outputDir, { recursive: true });
const server = spawn("python", ["-m", "http.server", "8765", "--bind", "127.0.0.1"], { stdio: "ignore" });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser;
try {
  await sleep(1200);
  browser = await chromium.launch({
    headless: true,
    args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-webgl", "--autoplay-policy=no-user-gesture-required"]
  });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage({ viewport: { width: 1280, height: 900 } });
  page.setDefaultTimeout(120000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("http://127.0.0.1:8765/avatar_studio/", { waitUntil: "networkidle", timeout: 120000 });

  for (let i = 1; i <= count; i++) {
    const audio = path.join(audioDir, `chapter_${String(i).padStart(2, "0")}.wav`);
    if (!fs.existsSync(audio)) throw new Error(`Missing chapter audio: ${audio}`);
    const durationProbe = spawnSync("ffprobe", [
      "-v", "error", "-show_entries", "format=duration",
      "-of", "default=noprint_wrappers=1:nokey=1", audio
    ], { encoding: "utf8" });
    if (durationProbe.status !== 0) {
      throw new Error(`Could not read chapter ${i} narration duration: ${durationProbe.stderr}`);
    }
    const audioDuration = Number(durationProbe.stdout.trim());
    if (!Number.isFinite(audioDuration) || audioDuration <= 0) {
      throw new Error(`Chapter ${i} narration has an invalid duration: ${durationProbe.stdout}`);
    }

    await page.locator("#gender").selectOption(gender);
    await page.locator("#audio").setInputFiles(audio);
    await page.locator("#load").click();
    await page.waitForFunction(() => document.querySelector("#status")?.textContent.includes("Presenter ready"), null, { timeout: 120000 });

    const downloadPromise = page.waitForEvent("download", { timeout: 20 * 60 * 1000 });
    await page.locator("#record").click();
    await page.waitForFunction(() => {
      const s = document.querySelector("#status")?.textContent || "";
      return s.includes("Recording avatar and narration together") || s.includes("Could not record");
    }, null, { timeout: 30000 });
    await page.waitForFunction(() => {
      const s = document.querySelector("#status")?.textContent || "";
      return s.includes("Saved talking_presenter.webm") || s.includes("Could not record") || s.includes("Recording error");
    }, null, { timeout: 20 * 60 * 1000 });
    const status = await page.locator("#status").textContent();
    if (!status?.includes("Saved talking_presenter.webm")) throw new Error(`Chapter ${i} avatar recording failed: ${status}`);
    const download = await downloadPromise;
    const webm = path.join(outputDir, `presenter_section_${String(i).padStart(2, "0")}.webm`);
    await download.saveAs(webm);
    if (fs.statSync(webm).size < 10000) throw new Error(`Chapter ${i} WebM recording is too small.`);

    // MediaRecorder intentionally waits after audio "ended" so Chromium can flush
    // its final packet. That creates a silent video tail. Trim to the exact source
    // narration duration and mux the original chapter WAV, preserving sync and
    // preventing the avatar clip from overrunning the chapter in the editor.
    const mp4 = webm.replace(/\.webm$/, ".mp4");
    const conversion = spawn("ffmpeg", [
      "-y", "-i", webm, "-i", audio,
      "-map", "0:v:0", "-map", "1:a:0", "-t", audioDuration.toFixed(3),
      "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
      "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
      "-shortest", "-movflags", "+faststart", mp4
    ], { stdio: "inherit" });
    const exitCode = await new Promise((resolve, reject) => {
      conversion.on("error", reject);
      conversion.on("close", resolve);
    });
    if (exitCode !== 0 || !fs.existsSync(mp4) || fs.statSync(mp4).size < 10000) {
      throw new Error(`Chapter ${i} WebM-to-MP4 conversion failed.`);
    }

    const finalProbe = spawnSync("ffprobe", [
      "-v", "error", "-show_entries", "format=duration",
      "-of", "default=noprint_wrappers=1:nokey=1", mp4
    ], { encoding: "utf8" });
    const finalDuration = Number(finalProbe.stdout.trim());
    if (finalProbe.status !== 0 || !Number.isFinite(finalDuration) ||
        Math.abs(finalDuration - audioDuration) > 0.25) {
      throw new Error(
        `Chapter ${i} avatar duration ${finalProbe.stdout.trim()}s does not match narration ${audioDuration.toFixed(3)}s.`
      );
    }
    fs.unlinkSync(webm);
    console.log(`Generated duration-aligned 3D avatar clip for chapter ${i}: ${mp4} (${finalDuration.toFixed(2)}s)`);
  }
  if (errors.length) throw new Error("Browser runtime errors: " + errors.join("; "));
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
}
