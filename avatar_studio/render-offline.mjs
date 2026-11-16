// Offline presenter renderer: one clip per chapter narration file, rendered frame by frame.
//
//   AVATAR_AUDIO_DIR=<dir with chapter_01.wav …> AVATAR_OUTPUT_DIR=<dir> node render-offline.mjs <chapter-count>
//
// Unlike generate-clips.mjs (which records the live canvas in real time), this never depends
// on how fast the machine renders. Every output frame is rendered and encoded, at a constant
// frame rate, and frame k shows the mouth for narration time k / fps.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, spawnSync } from "node:child_process";

const here = path.dirname(fileURLToPath(import.meta.url));
const count = Number(process.argv[2]);
const audioDir = process.env.AVATAR_AUDIO_DIR;
const outputDir = process.env.AVATAR_OUTPUT_DIR;
const gender = (process.env.AVATAR_GENDER || "male").toLowerCase() === "female" ? "female" : "male";
const fps = Number(process.env.AVATAR_FPS || 30);
const width = Number(process.env.AVATAR_WIDTH || 1920);
const height = Number(process.env.AVATAR_HEIGHT || 1080);
const framing = process.env.AVATAR_FRAMING === "close" ? "close" : "medium";
const ffmpeg = process.env.FFMPEG_BINARY || "ffmpeg";
const ffprobe = process.env.FFPROBE_BINARY || "ffprobe";
const modules = process.env.AVATAR_NODE_MODULES || path.join(here, "node_modules");
const BATCH = 6;

function fail(message) { console.error(`::error title=Presenter render::${message}`); process.exit(1); }
if (!Number.isInteger(count) || count < 1 || !audioDir || !outputDir) {
  fail("Usage: node render-offline.mjs <chapter-count> with AVATAR_AUDIO_DIR and AVATAR_OUTPUT_DIR set.");
}
if (![24, 25, 30].includes(fps)) fail(`AVATAR_FPS must be 24, 25 or 30 (got ${process.env.AVATAR_FPS}).`);
for (const [name, folder] of [["three", "three"], ["@interviewflowai/talking-avatar", "@interviewflowai/talking-avatar"]]) {
  if (!fs.existsSync(path.join(modules, folder, "package.json"))) {
    fail(`${name} is not installed in ${modules}. Run "npm ci" in avatar_studio first.`);
  }
}

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
  ".glb": "model/gltf-binary", ".bin": "application/octet-stream", ".wasm": "application/wasm" };
const ROOTS = [
  ["/vendor/three/", path.join(modules, "three")],
  ["/vendor/talking-avatar/", path.join(modules, "@interviewflowai", "talking-avatar")],
  ["/", here],
];
// Everything the page needs is served from this machine: no CDN is contacted while rendering.
const server = http.createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
  for (const [prefix, root] of ROOTS) {
    if (!pathname.startsWith(prefix)) continue;
    const target = path.normalize(path.join(root, pathname.slice(prefix.length) || "offline.html"));
    if (!target.startsWith(root) || !fs.existsSync(target) || !fs.statSync(target).isFile()) break;
    response.writeHead(200, { "Content-Type": TYPES[path.extname(target)] || "application/octet-stream" });
    fs.createReadStream(target).pipe(response);
    return;
  }
  response.writeHead(404); response.end("not found");
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;

function probeDuration(file) {
  const probe = spawnSync(ffprobe, ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file], { encoding: "utf8", timeout: 60000 });
  const seconds = Number(String(probe.stdout).trim());
  if (probe.status !== 0 || !Number.isFinite(seconds) || seconds <= 0) throw new Error(`Could not read the duration of ${file}: ${probe.stderr || probe.stdout}`);
  return seconds;
}

// Loudness of the narration at each video frame, 0..1, used to move the head with the voice.
function loudnessPerFrame(file, frames) {
  const rate = 16000;
  const decoded = spawnSync(ffmpeg, ["-v", "error", "-i", file, "-ac", "1", "-ar", String(rate), "-f", "s16le", "-"], { maxBuffer: 1 << 30, timeout: 120000 });
  if (decoded.status !== 0) throw new Error(`Could not decode ${file}: ${decoded.stderr}`);
  const samples = new Int16Array(decoded.stdout.buffer, decoded.stdout.byteOffset, Math.floor(decoded.stdout.length / 2));
  const perFrame = rate / fps, levels = [];
  for (let frame = 0; frame < frames; frame++) {
    let sum = 0, n = 0;
    for (let i = Math.floor(frame * perFrame); i < Math.min(samples.length, Math.floor((frame + 1) * perFrame)); i++) { sum += samples[i] * samples[i]; n++; }
    levels.push(n ? Math.sqrt(sum / n) / 32768 : 0);
  }
  const peak = Math.max(...levels, 1e-6);
  return levels.map(level => Math.round(Math.min(1, level / peak) * 1000) / 1000);
}

async function launch() {
  const { chromium } = await import(process.env.AVATAR_PLAYWRIGHT_MODULE || "playwright");
  return chromium.launch({
    headless: true,
    executablePath: process.env.AVATAR_CHROMIUM_PATH || undefined,
    args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader",
      "--autoplay-policy=no-user-gesture-required", "--no-sandbox", "--disable-dev-shm-usage"],
  });
}

fs.mkdirSync(outputDir, { recursive: true });
let browser;
const summary = [];
try {
  browser = await launch();
  const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
  for (let chapter = 1; chapter <= count; chapter++) {
    const label = String(chapter).padStart(2, "0");
    const audio = path.join(audioDir, `chapter_${label}.wav`);
    if (!fs.existsSync(audio)) throw new Error(`Missing chapter audio: ${audio}`);
    const seconds = probeDuration(audio);
    const frames = Math.max(1, Math.round(seconds * fps));
    const mp4 = path.join(outputDir, `presenter_section_${label}.mp4`);
    const started = Date.now();
    // A fresh page per chapter: no audio or animation state carries over.
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    try {
      await page.goto(`${origin}/offline.html`, { waitUntil: "load", timeout: 120000 });
      await page.waitForFunction(() => window.studioReady === true, null, { timeout: 120000 });
      const stage = await page.evaluate(options => window.studio.load(options), { gender, width, height, framing, seed: 7919 * chapter });
      if (stage.canvas[0] !== width || stage.canvas[1] !== height) throw new Error(`Canvas is ${stage.canvas.join("x")}, expected ${width}x${height}.`);
      if (!stage.morphTargets) throw new Error("The avatar has no face morph targets; the mouth cannot move.");
      const prepared = await page.evaluate(options => window.studio.prepare(options),
        { audioBase64: fs.readFileSync(audio).toString("base64"), fps, envelope: loudnessPerFrame(audio, frames) });
      if (!prepared.visemeEvents) throw new Error(`The lip-sync model found no speech sounds in chapter ${chapter}.`);

      // Frames go straight into the encoder; the chapter's own narration is the sound track.
      const encoder = spawn(ffmpeg, ["-y", "-v", "error", "-f", "image2pipe", "-framerate", String(fps), "-c:v", "mjpeg", "-i", "-",
        "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
        "-r", String(fps), "-c:a", "aac", "-b:a", "192k", "-t", seconds.toFixed(3), "-movflags", "+faststart", mp4],
        { stdio: ["pipe", "inherit", "pipe"] });
      let encoderError = "";
      encoder.stderr.on("data", chunk => { encoderError += chunk; });
      const encoded = new Promise((resolve, reject) => { encoder.on("error", reject); encoder.on("close", resolve); });
      const mouth = [];
      for (let first = 0; first < frames; first += BATCH) {
        const batch = await page.evaluate(([from, size]) => window.studio.frames(from, size), [first, Math.min(BATCH, frames - first)]);
        if (batch.errors.length) throw new Error(`Renderer reported: ${batch.errors.join("; ")}`);
        mouth.push(...batch.mouth);
        for (const image of batch.images) {
          const data = Buffer.from(image, "base64");
          if (data.length < 2000) throw new Error(`Frame ${first} of chapter ${chapter} is empty (${data.length} bytes).`);
          if (!encoder.stdin.write(data)) await new Promise(resolve => encoder.stdin.once("drain", resolve));
        }
      }
      encoder.stdin.end();
      const exitCode = await encoded;
      if (exitCode !== 0) throw new Error(`FFmpeg could not encode chapter ${chapter}: ${encoderError.slice(-400)}`);
      if (pageErrors.length) throw new Error(`Browser errors: ${pageErrors.join("; ")}`);

      // Count the frames that are really in the file. A constant-rate file with fewer decoded
      // frames than narration seconds x fps would be padded, not rendered.
      const counted = spawnSync(ffprobe, ["-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
        "stream=nb_read_frames,avg_frame_rate,width,height", "-of", "json", mp4], { encoding: "utf8", timeout: 300000 });
      const stream = (JSON.parse(counted.stdout || "{}").streams || [])[0] || {};
      const decoded = Number(stream.nb_read_frames || 0);
      const duration = probeDuration(mp4);
      if (Math.abs(decoded - frames) > 1) throw new Error(`Chapter ${chapter}: ${decoded} frames in the file, ${frames} were rendered.`);
      if (Math.abs(duration - seconds) > 0.25) throw new Error(`Chapter ${chapter}: video is ${duration.toFixed(2)}s, narration is ${seconds.toFixed(2)}s.`);
      const record = {
        chapter, file: path.basename(mp4), renderer: "offline-frame-accurate", gender, framing,
        fps, width: Number(stream.width), height: Number(stream.height), frames_rendered: frames, frames_decoded: decoded,
        narration_seconds: Math.round(seconds * 1000) / 1000, video_seconds: Math.round(duration * 1000) / 1000,
        render_seconds: Math.round((Date.now() - started) / 100) / 10, viseme_events: prepared.visemeEvents,
        mouth_box: stage.mouth, mouth_open_per_frame: mouth, bones: stage.bones, webgl_renderer: stage.webgl,
        sdk: "@interviewflowai/talking-avatar " + JSON.parse(fs.readFileSync(path.join(modules, "@interviewflowai", "talking-avatar", "package.json"), "utf8")).version,
        avatar: gender === "female" ? "Microsoft Rocketbox business female 04 (MIT)" : "Microsoft Rocketbox business male 01 (MIT)",
      };
      fs.writeFileSync(mp4.replace(/\.mp4$/, ".render.json"), JSON.stringify(record, null, 2));
      summary.push(record);
      console.log(`Presenter chapter ${chapter}/${count}: ${frames} frames at ${fps} fps, ${seconds.toFixed(2)}s, rendered in ${record.render_seconds}s`);
    } finally { await page.close(); }
  }
} catch (error) {
  console.error(`::error title=Presenter render::${String(error?.message || error).replace(/\r?\n/g, " ")}`);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  server.close();
}
if (!process.exitCode) {
  const total = summary.reduce((sum, item) => sum + item.render_seconds, 0);
  console.log(`Presenter clips complete: ${summary.length} chapters, ${summary.reduce((sum, item) => sum + item.frames_decoded, 0)} frames, ${total.toFixed(0)}s of rendering.`);
}
