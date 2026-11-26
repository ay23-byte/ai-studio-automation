// Capture a real source webpage for an evidence-led documentary beat.
// Input is one JSON object on stdin. Uses the pinned local Playwright install.
import fs from "node:fs";
import path from "node:path";
import net from "node:net";
import dns from "node:dns/promises";
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
async function isPublicHost(hostname) {
  const host = String(hostname || "").toLowerCase();
  if (!host || host === "localhost" || host.endsWith(".localhost") ||
      host.endsWith(".local") || host.endsWith(".internal")) return false;
  let addresses;
  if (net.isIP(host)) addresses = [{ address: host }];
  else addresses = await dns.lookup(host, { all: true, verbatim: true });
  if (!addresses.length) return false;
  return addresses.every(({ address }) => {
    if (net.isIPv4(address)) {
      const p = address.split(".").map(Number);
      return !(p[0] === 0 || p[0] === 10 || p[0] === 127 || p[0] >= 224 ||
        (p[0] === 169 && p[1] === 254) || (p[0] === 172 && p[1] >= 16 && p[1] <= 31) ||
        (p[0] === 192 && p[1] === 168) || (p[0] === 100 && p[1] >= 64 && p[1] <= 127) ||
        (p[0] === 198 && (p[1] === 18 || p[1] === 19)));
    }
    const v6 = address.toLowerCase();
    return !(v6 === "::1" || v6.startsWith("fc") || v6.startsWith("fd") ||
      v6.startsWith("fe80:") || v6.startsWith("::ffff:127.") ||
      v6.startsWith("::ffff:10.") || v6.startsWith("::ffff:192.168."));
  });
}
if (!(await isPublicHost(target.hostname))) {
  throw new Error("Refusing a private-network or unresolved source URL.");
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
  await page.route("**/*", async route => {
    try {
      const requestUrl = new URL(route.request().url());
      if (!["http:", "https:"].includes(requestUrl.protocol) ||
          !(await isPublicHost(requestUrl.hostname))) {
        await route.abort("blockedbyclient");
      } else {
        await route.continue();
      }
    } catch {
      await route.abort("blockedbyclient");
    }
  });
  page.setDefaultNavigationTimeout(30000);
  page.setDefaultTimeout(10000);
  const response = await page.goto(target.href, { waitUntil: "domcontentloaded", timeout: 30000 });
  const httpStatus = response?.status() ?? 0;
  if (!(httpStatus >= 200 && httpStatus < 400)) {
    throw new Error(`Source page returned HTTP ${httpStatus}; refusing an error-page screenshot.`);
  }
  await page.waitForTimeout(1200);
  const pageInfo = await page.evaluate(() => ({
    title: document.title || "",
    canonical: document.querySelector('link[rel="canonical"]')?.href || location.href,
    description: document.querySelector('meta[name="description"]')?.content || "",
    bodyTextLength: (document.body?.innerText || "").trim().length,
    articleFound: Boolean(document.querySelector("article, main [itemprop='articleBody'], main")),
    bodyText: (document.body?.innerText || "").trim().slice(0, 12000),
  }));
  const errorTitle = /^(page not found|not found|this page does not exist|coming soon|access denied|forbidden|service unavailable|error)(\b|\s|[-:])/i;
  const errorBody = /\b(page not found|this page does not exist|temporarily unavailable|site is under maintenance|access denied)\b/i;
  if (!pageInfo.title || pageInfo.bodyTextLength < 100 ||
      errorTitle.test(pageInfo.title.trim()) || errorBody.test(pageInfo.bodyText)) {
    throw new Error(`Source URL displayed an error/placeholder page, not the cited article (title=${pageInfo.title || "untitled"}).`);
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
    http_status: httpStatus,
    content_validated: true,
    captured_at: new Date().toISOString(),
    screenshot_viewport: "1600x900",
    notice: "A screenshot records what this webpage displayed at capture time; it does not independently verify the truth of the claim.",
  }, null, 2));
  process.stdout.write(JSON.stringify({ status: "CAPTURED", path: output, page_title: pageInfo.title }));
} finally {
  if (browser) await browser.close();
}
