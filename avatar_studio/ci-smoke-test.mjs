import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const outputDir = "avatar_studio/test-results";
fs.mkdirSync(outputDir, { recursive: true });

function makeWav(seconds = 2, sampleRate = 16000) {
  const samples = Math.floor(seconds * sampleRate);
  const buffer = Buffer.alloc(44 + samples * 2);
  buffer.write("RIFF", 0); buffer.writeUInt32LE(36 + samples * 2, 4);
  buffer.write("WAVE", 8); buffer.write("fmt ", 12);
  buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22); buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * 2, 28); buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34); buffer.write("data", 36);
  buffer.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) {
    const envelope = Math.min(1, i / 500, (samples - i) / 500);
    buffer.writeInt16LE(Math.round(9000 * envelope * Math.sin(2 * Math.PI * 220 * i / sampleRate)), 44 + i * 2);
  }
  return buffer;
}

const browser = await chromium.launch({
  headless: true,
  args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-webgl", "--autoplay-policy=no-user-gesture-required"]
});
const context = await browser.newContext({ acceptDownloads: true });
const page = await context.newPage({ viewport: { width: 1280, height: 900 } });
const errors = [];
page.on("pageerror", error => errors.push(error.message));
page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });

// GitHub's headless browser cannot show the real screen-share picker. Supply a
// synthetic tab-audio stream so the production MediaRecorder path is exercised.
await page.addInitScript(() => {
  navigator.mediaDevices.getDisplayMedia = async () => {
    const audioContext = new AudioContext();
    await audioContext.resume();
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    const destination = audioContext.createMediaStreamDestination();
    oscillator.frequency.value = 220;
    gain.gain.value = 0.08;
    oscillator.connect(gain).connect(destination);
    oscillator.start();
    window.__avatarTestAudio = { audioContext, oscillator };
    return new MediaStream(destination.stream.getAudioTracks());
  };
});

try {
  const response = await page.goto("http://127.0.0.1:8765/avatar_studio/", { waitUntil: "networkidle", timeout: 90000 });
  if (!response?.ok()) throw new Error(`Studio page returned HTTP ${response?.status()}`);
  await page.locator("#load").click();
  await page.waitForFunction(() => document.querySelector("#status")?.textContent.includes("Presenter ready"), null, { timeout: 120000 });
  const canvas = page.locator("#avatar canvas");
  if (!(await canvas.count())) throw new Error("Talking Avatar did not create a canvas.");
  const canvasSize = await canvas.evaluate(node => ({ width: node.width, height: node.height }));
  if (!canvasSize.width || !canvasSize.height) throw new Error("Avatar canvas has zero dimensions.");

  const wavPath = path.join(outputDir, "synthetic-test.wav");
  fs.writeFileSync(wavPath, makeWav());
  await page.locator("#audio").setInputFiles(wavPath);
  await page.locator("#play").click();
  await page.waitForFunction(() => document.querySelector("#status")?.textContent.includes("Narration finished"), null, { timeout: 30000 });

  // Exercise the actual recording button, canvas.captureStream, MediaRecorder,
  // stop handler, and browser download—not just SDK playback.
  await page.locator("#record").click();
  await page.waitForFunction(() => document.querySelector("#status")?.textContent.includes("Narration finished"), null, { timeout: 30000 });
  if (!(await page.locator("#finish").isEnabled())) throw new Error("Recording did not enter the active state.");
  const downloadPromise = page.waitForEvent("download", { timeout: 30000 });
  await page.locator("#finish").click();
  const download = await downloadPromise;
  const recordingPath = path.join(outputDir, "talking_presenter.webm");
  await download.saveAs(recordingPath);
  const recordingBytes = fs.statSync(recordingPath).size;
  if (recordingBytes < 1000) throw new Error(`Recording file is unexpectedly small: ${recordingBytes} bytes.`);

  // Confirm the downloaded WebM is parseable and has a non-zero duration.
  // The recording is downloaded by Playwright rather than served by the app;
  // validate its container with Chromium by loading the saved bytes as a data URL.
  const webmBase64 = fs.readFileSync(recordingPath).toString("base64");
  const decoded = await page.evaluate(async base64 => {
    const video = document.createElement("video");
    video.src = "data:video/webm;base64," + base64;
    await new Promise((resolve, reject) => {
      video.onloadedmetadata = resolve;
      video.onerror = () => reject(new Error("Downloaded WebM is not a playable video."));
    });
    return { duration: video.duration, width: video.videoWidth, height: video.videoHeight };
  }, webmBase64);
  if (!Number.isFinite(decoded.duration) || decoded.duration <= 0 || decoded.width <= 0 || decoded.height <= 0) {
    throw new Error("Downloaded WebM has invalid video metadata: " + JSON.stringify(decoded));
  }

  await page.screenshot({ path: path.join(outputDir, "avatar-preview.png"), fullPage: true });
  fs.writeFileSync(path.join(outputDir, "browser-console-errors.json"), JSON.stringify(errors, null, 2));
  if (errors.length) throw new Error("Browser console errors: " + errors.join("\\n"));
  console.log("PASS: SDK loaded, avatar rendered, narration played, MediaRecorder recorded the canvas, and a playable WebM downloaded.");
  console.log("Canvas:", JSON.stringify(canvasSize));
  console.log("Recording:", JSON.stringify({ bytes: recordingBytes, ...decoded }));
} catch (error) {
  await page.screenshot({ path: path.join(outputDir, "failure.png"), fullPage: true }).catch(() => {});
  fs.writeFileSync(path.join(outputDir, "browser-console-errors.json"), JSON.stringify(errors, null, 2));
  throw error;
} finally {
  await page.evaluate(() => {
    window.__avatarTestAudio?.oscillator.stop();
    window.__avatarTestAudio?.audioContext.close();
  }).catch(() => {});
  await browser.close();
}
