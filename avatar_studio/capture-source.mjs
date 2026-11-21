// Capture a real source webpage for an evidence-led documentary beat.
// Input is one JSON object on stdin. Uses the pinned local Playwright install.
import fs from "node:fs";
import path from "node:path";
import net from "node:net";
import { chromium } from "playwright";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const record = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const target = new URL(String(record.url || ""));
if (!["http:", "https:"].includes(target.protocol) || !target.hostname ||
    target.username || target.password ||
    ["localhost", "127.0.0.1", "::1"].includes(target.hostname.toLowerCase())) {
  throw new Error("Refusing a non-public or unsupported source URL.");
}
const ipVersion = net.isIP(target.hostname);
if (ipVersion === 4 && (target.hostname.startsWith("10.") || target.hostname.startsWith("192.168.") ||
    target.hostname.startsWith("169.254.") || target.hostname.startsWith("127.") ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(target.hostname))) {
  throw new Error("Refusing a private-network source URL.");
}
const output = path.resolve(record.image_path);
const metadata = path.resolve(record.metadata_path);
fs.mkdirSync(path.dirname(output), { recursive: true });
let browser;
try {
  browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
  const page = await browser.newPage({
    viewport: { width: 1600, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "light",
    reducedMotion: "reduce",
  });
  page.setDefaultNavigationTimeout(30000);
  page.setDefaultTimeout(10000);
  await page.goto(target.href, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForTimeout(1200);
  const pageInfo = await page.evaluate(() => ({
    title: document.title || "",
    canonical: document.querySelector('link[rel="canonical"]')?.href || location.href,
    description: document.querySelector('meta[name="description"]')?.content || "",
    bodyTextLength: (document.body?.innerText || "").trim().length,
    articleFound: Boolean(document.querySelector("article, main [itemprop='articleBody'], main")),
  }));
  if (!pageInfo.title || pageInfo.bodyTextLength < 100) {
    throw new Error("Source page did not expose enough readable content for a meaningful screenshot.");
  }
  const article = page.locator("article").first();
  if (await article.count()) {
    await article.scrollIntoViewIfNeeded().catch(() => {});
  } else {
    const main = page.locator("main").first();
    if (await main.count()) await main.scrollIntoViewIfNeeded().catch(() => {});
  }
  await page.screenshot({ path: output, type: "png", fullPage: false, animations: "disabled" });
  const stat = fs.statSync(output);
  if (stat.size < 5000) throw new Error("Captured source screenshot is unexpectedly small.");
  fs.writeFileSync(metadata, JSON.stringify({
    asset_type: "source_webpage_screenshot",
    ai_generated: false,
    screenshot_is_real_source_page: true,
    source_id: record.source_id,
    source_title: record.title,
    publisher: record.publisher,
    published_at: record.published_at,
    requested_url: target.href,
    final_url: page.url(),
    page_title: pageInfo.title,
    canonical_url: pageInfo.canonical,
    captured_at: new Date().toISOString(),
    screenshot_viewport: "1600x900",
    notice: "A screenshot records what this webpage displayed at capture time; it does not independently verify the truth of the claim.",
  }, null, 2));
  process.stdout.write(JSON.stringify({ status: "CAPTURED", path: output, page_title: pageInfo.title }));
} finally {
  if (browser) await browser.close();
}
