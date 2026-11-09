// Headless test of web/ against tests/mock-e2.js: standalone writing, the
// option-setting memory guard, sending to the board, and the relay (server.py
// + rx65nctl driving the page through jobs).
//
//   node tests/run.mjs            (needs playwright; Chromium preinstalled)
import { chromium } from "playwright";
import { spawn, execFileSync } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";
import http from "http";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const web = path.join(root, "web");
const mock = fs.readFileSync(path.join(root, "tests", "mock-e2.js"), "utf8");
let failed = 0;
function check(ok, what) { console.log((ok ? "ok   " : "FAIL ") + what); if (!ok) failed++; }

// A small S-record image: 256 bytes at the start of code flash and the reset
// vector area, the shape a real build has.
function srec(addr, bytes) {
	const lines = [];
	for (let o = 0; o < bytes.length; o += 16) {
		const d = bytes.slice(o, o + 16), a = addr + o;
		const body = [d.length + 5, a >>> 24 & 255, a >>> 16 & 255, a >>> 8 & 255, a & 255, ...d];
		const sum = 255 - (body.reduce((s, x) => s + x, 0) & 255);
		lines.push("S3" + [...body, sum].map(x => x.toString(16).padStart(2, "0").toUpperCase()).join(""));
	}
	return lines.join("\n");
}
const prog = Array.from({ length: 256 }, (_, i) => i & 255);
const good = "S00600004844521B\n" + srec(0xFFF00000, prog) + "\n" + srec(0xFFFFFF80, Array(128).fill(0x5A)) + "\nS70500000000FA\n";
const ofsFF = good + srec(0xFE7F5D00, Array(32).fill(0xFF)) + "\n";
const ofsBad = good + srec(0xFE7F5D00, [0x12, 0x34, 0x56, 0x78]) + "\n";
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rxw-"));
fs.writeFileSync(path.join(tmp, "good.mot"), good);
fs.writeFileSync(path.join(tmp, "ofs-ff.mot"), ofsFF);
fs.writeFileSync(path.join(tmp, "ofs-bad.mot"), ofsBad);

// Static server for the standalone page, with a samples/ list.
const samples = { "index.json": JSON.stringify([{ file: "good.mot", title: "test image" }]), "good.mot": good };
const stat = http.createServer((q, r) => {
	const u = q.url.split("?")[0];
	if (u.startsWith("/samples/") && samples[u.slice(9)]) { r.end(samples[u.slice(9)]); return; }
	const f = path.join(web, u === "/" ? "index.html" : u);
	if (!f.startsWith(web) || !fs.existsSync(f)) { r.statusCode = 404; r.end(); return; }
	r.setHeader("Content-Type", f.endsWith(".js") ? "text/javascript" : "text/html; charset=utf-8");
	r.end(fs.readFileSync(f));
}).listen(8765);

const browser = await chromium.launch();
async function page(url) {
	const ctx = await browser.newContext();
	await ctx.addInitScript(mock);
	const p = await ctx.newPage();
	p.on("pageerror", e => { console.log("page error: " + e); failed++; });
	await p.goto(url);
	await p.waitForFunction(() => document.getElementById("usb").textContent.includes("Connected"), null, { timeout: 5000 });
	return p;
}
const text = (p, id) => p.evaluate(i => document.getElementById(i).textContent, id);
async function until(p, f, ms = 15000) { await p.waitForFunction(f, null, { timeout: ms }); }

// --- standalone ---
{
	const p = await page("http://localhost:8765/");
	check(await p.isVisible("#samplerow"), "sample list shown");
	await p.selectOption("#sample", "good.mot");
	await p.click("#write");
	await until(p, () => document.getElementById("log").textContent.includes("fake rx65n: start"));
	const m = await p.evaluate(() => ({ wrote: window.__mock.wrote, b0: window.__mock.flash.get(0xFFF00001), ofs: window.__mock.ofsWritten }));
	check(m.wrote >= 256 + 128 && m.b0 === 1, "sample written to flash (" + m.wrote + " bytes)");
	check((await text(p, "ev")).includes("verified"), "verified");
	await p.fill("#tx", "hello board");
	await p.press("#tx", "Enter");
	await until(p, () => /=== tx 12 bytes ===[\s\S]*hello board/.test(document.getElementById("log").textContent));
	check(true, "sent text came back (echo)");

	await p.setInputFiles("#file", path.join(tmp, "ofs-ff.mot"));
	await p.click("#write");
	await until(p, () => /ofs-skipped/.test(document.getElementById("ev").textContent));
	check(!(await p.evaluate(() => window.__mock.ofsWritten)), "all-FF option-setting records skipped, not written");

	await p.waitForFunction(() => !document.getElementById("write").disabled);
	await p.setInputFiles("#file", path.join(tmp, "ofs-bad.mot"));
	await p.click("#write");
	await until(p, () => /option-setting memory/.test(document.getElementById("usb").textContent));
	check(!(await p.evaluate(() => window.__mock.ofsWritten)), "image writing option-setting memory refused");

	await p.waitForFunction(() => !document.getElementById("hold").disabled);
	await p.click("#hold");
	await until(p, () => document.getElementById("usb").textContent.includes("Held in reset"));
	check(true, "hold in reset");
}

// --- relay: server.py + rx65nctl, as on a VPS but without the container ---
{
	const data = fs.mkdtempSync(path.join(os.tmpdir(), "rxw-data-"));
	fs.mkdirSync(path.join(data, "app")); fs.mkdirSync(path.join(data, "s"));
	for (const f of ["index.html", "app.js"]) fs.copyFileSync(path.join(web, f), path.join(data, "app", f));
	const env = { ...process.env, RXW_DATA: data, RXW_PORT: "8766", RXW_PREFIX: "/rx65n",
		RXW_URL: "http://localhost:8766/rx65n/", RXW_SERVER: "external" };
	const ctl = (...a) => execFileSync(path.join(web, "rx65nctl"), a, { env, encoding: "utf8" });
	const url = ctl("new", "10").trim();
	const srv = spawn("python3", [path.join(web, "server.py")], { env, stdio: "inherit" });
	await new Promise(r => setTimeout(r, 800));
	const p = await page(url);
	check(!(await p.isVisible("#manual")), "relay mode hides the manual controls");
	ctl("msg", "testing");
	await until(p, () => document.getElementById("msg").textContent === "testing");
	check(true, "message shown");
	ctl("flash", path.join(tmp, "good.mot"), "3", "--tx", "ping\\r\\n");
	const done = ctl("jobwait", "30");
	check(/job-done/.test(done), "flash job done: " + done.trim());
	const log = ctl("log", "2000");
	check(/fake rx65n: start/.test(log) && /=== tx 6 bytes ===[\s\S]*ping/.test(log), "relay log has the program and the echo");
	ctl("run", "2");
	check(/job-done/.test(ctl("jobwait", "20")), "run job done");
	ctl("hold");
	check(/job-done/.test(ctl("jobwait", "20")), "hold job done");
	ctl("wait", "look here");
	await until(p, () => document.title.startsWith("●"));
	check(true, "wait: highlighted");
	ctl("close", "finished");
	await until(p, () => document.title.startsWith("✓"));
	check(true, "close: shown (green, ✓)");
	// A tab that kept a token from a session that no longer exists falls
	// back to the standalone writer instead of waiting for jobs.
	const q = await page("http://localhost:8766/rx65n/#t=" + "x".repeat(32));
	await until(q, () => !document.getElementById("manual").hidden);
	check(/ended/.test(await text(q, "msg")), "stale token: falls back to standalone");
	srv.kill();
}

await browser.close();
stat.close();
console.log(failed ? failed + " failed" : "all passed");
process.exit(failed ? 1 : 0);
