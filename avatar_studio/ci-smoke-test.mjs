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
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = [];
page.on("pageerror", error => errors.push(error.message));
page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });

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

  await page.screenshot({ path: path.join(outputDir, "avatar-preview.png"), fullPage: true });
  fs.writeFileSync(path.join(outputDir, "browser-console-errors.json"), JSON.stringify(errors, null, 2));
  if (errors.length) throw new Error("Browser console errors: " + errors.join("\n"));
  console.log("PASS: SDK loaded, avatar canvas rendered, and synthetic WAV playback completed.");
  console.log("Canvas:", JSON.stringify(canvasSize));
} catch (error) {
  await page.screenshot({ path: path.join(outputDir, "failure.png"), fullPage: true }).catch(() => {});
  fs.writeFileSync(path.join(outputDir, "browser-console-errors.json"), JSON.stringify(errors, null, 2));
  throw error;
} finally {
  await browser.close();
}
