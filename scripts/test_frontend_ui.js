/**
 * End-to-End Frontend UI Strategy Configuration Verification Script
 * Uses puppeteer-core connecting to Microsoft Edge.
 */

const puppeteer = require("puppeteer-core");
const http = require("http");
const { execSync } = require("child_process");

const EDGE_PATH = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const FRONTEND_URL = "http://localhost:3000/strategies";
const BACKEND_BASE = "http://127.0.0.1:8000/api/v1";

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

function fetchJson(url, options = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const reqOptions = {
      hostname: u.hostname,
      port: u.port,
      path: u.pathname + u.search,
      method: options.method || "GET",
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {})
      }
    };
    const req = http.request(reqOptions, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        try {
          resolve({ status: res.statusCode, body: JSON.parse(data) });
        } catch (e) {
          resolve({ status: res.statusCode, body: data });
        }
      });
    });
    req.on("error", reject);
    if (options.body) {
      req.write(typeof options.body === "string" ? options.body : JSON.stringify(options.body));
    }
    req.end();
  });
}

async function run() {
  console.log("=== CHECK 1: FRONTEND CONFIGURATION VERIFICATION ===");
  console.log("\n[A] Launching headless browser and opening frontend at " + FRONTEND_URL + "...");
  const browser = await puppeteer.launch({
    executablePath: EDGE_PATH,
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--window-size=1280,900"]
  });

  try {
    const page = await browser.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error") console.log("   [Browser Console Error]", msg.text());
    });

    await page.goto(FRONTEND_URL, { waitUntil: "networkidle0", timeout: 15000 });
    console.log("   Frontend page loaded successfully.");

    await page.waitForSelector('input[type="number"]', { timeout: 10000 });

    console.log("\n[B] Modifying strategy and risk parameters in the frontend UI...");
    const updateResult = await page.evaluate(() => {
      function setNativeValue(element, value) {
        const lastValue = element.value;
        element.value = value;
        const event = new Event("input", { bubbles: true });
        const tracker = element._valueTracker;
        if (tracker) {
          tracker.setValue(lastValue);
        }
        element.dispatchEvent(event);
        element.dispatchEvent(new Event("change", { bubbles: true }));
      }

      const numInputs = Array.from(document.querySelectorAll('input[type="number"]'));
      const timeInputs = Array.from(document.querySelectorAll('input[type="time"]'));

      // 1. Execution Quantity (Lots)
      const lotsInput = numInputs[0];
      if (lotsInput) setNativeValue(lotsInput, "4");

      // 2. Profit Protection Activation Trigger (₹)
      const profitTriggerInput = numInputs[1];
      if (profitTriggerInput) setNativeValue(profitTriggerInput, "4000");

      // 3. Initial Minimum Locked Profit Floor (₹)
      const minLockInput = numInputs[2];
      if (minLockInput) setNativeValue(minLockInput, "1000");

      // 4. Hard Daily Loss Limit (₹)
      const maxLossInput = document.querySelector('input.text-rose-300') || numInputs[5];
      if (maxLossInput) setNativeValue(maxLossInput, "5000");

      // 5. ORB Start Time
      if (timeInputs.length > 0) {
        setNativeValue(timeInputs[0], "09:18");
      }

      return {
        lots: lotsInput ? lotsInput.value : null,
        profitTrigger: profitTriggerInput ? profitTriggerInput.value : null,
        minLock: minLockInput ? minLockInput.value : null,
        maxLoss: maxLossInput ? maxLossInput.value : null,
        orbStart: timeInputs.length > 0 ? timeInputs[0].value : null
      };
    });

    console.log("   Form inputs updated in UI:", JSON.stringify(updateResult));

    // Wait for validation debouncing
    await sleep(600);

    console.log("\n[C] Clicking 'PUBLISH AS NEW VERSION' button in the UI...");
    const clicked = await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll("button"));
      const saveBtn = buttons.find(b => b.textContent && b.textContent.includes("PUBLISH AS NEW VERSION"));
      if (saveBtn) {
        saveBtn.click();
        return true;
      }
      return false;
    });

    if (!clicked) {
      throw new Error("Could not find enabled 'PUBLISH AS NEW VERSION' button");
    }

    // Wait for the success notification in the UI
    await page.waitForFunction(() => {
      const banner = document.querySelector(".border-emerald-500\\/40, .bg-emerald-950\\/40");
      return banner && banner.textContent.includes("Published Version");
    }, { timeout: 10000 });

    const notificationText = await page.evaluate(() => {
      const banner = document.querySelector(".border-emerald-500\\/40, .bg-emerald-950\\/40");
      return banner ? banner.textContent.trim() : "";
    });
    console.log("   UI Banner Message:", notificationText);

    console.log("\n[D] Verifying backend returns updated values immediately...");
    const activeBackend = await fetchJson(`${BACKEND_BASE}/strategies/active`);
    console.log("   Active config returned by backend:", JSON.stringify({
      config_id: activeBackend.body.config_id,
      version: activeBackend.body.version,
      lots: activeBackend.body.parameters.lots,
      max_daily_loss: activeBackend.body.parameters.max_daily_loss,
      profit_protection_trigger: activeBackend.body.parameters.profit_protection_trigger,
      minimum_profit_lock: activeBackend.body.parameters.minimum_profit_lock,
      orb_start_time: activeBackend.body.parameters.orb_start_time
    }, null, 2));

    const p = activeBackend.body.parameters;
    if (p.lots !== 4) throw new Error(`Expected lots == 4, got ${p.lots}`);
    if (p.max_daily_loss !== 5000) throw new Error(`Expected max_daily_loss == 5000, got ${p.max_daily_loss}`);
    if (p.profit_protection_trigger !== 4000) throw new Error(`Expected profit_protection_trigger == 4000, got ${p.profit_protection_trigger}`);
    if (p.minimum_profit_lock !== 1000) throw new Error(`Expected minimum_profit_lock == 1000, got ${p.minimum_profit_lock}`);
    if (p.orb_start_time !== "09:18:00") throw new Error(`Expected orb_start_time == 09:18:00, got ${p.orb_start_time}`);

    console.log("   [PASS] Backend verified: Strategy and risk settings updated dynamically via frontend!");

    return {
      published_version: activeBackend.body.version,
      config_id: activeBackend.body.config_id
    };
  } finally {
    await browser.close();
  }
}

run()
  .then(res => {
    console.log("\n[SUCCESS] UI verification step completed successfully.");
    process.exit(0);
  })
  .catch(err => {
    console.error("\n[FAILURE]", err);
    process.exit(1);
  });
