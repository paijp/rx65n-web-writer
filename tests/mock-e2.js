// A stand-in for navigator.usb with an E2 Lite and an RX65N behind it, for
// testing the page in headless Chromium without hardware. It models only what
// the page relies on: replies echo "28 <cmd>", 0x68 passes bytes to a fake MCU,
// 0x61/0x64 move it between boot mode and running, and boot mode keeps a
// flash image that write and read-back go through.
(function () {
	"use strict";
	// What a real RX65N (R5F565NE) reports: code flash in four areas (small
	// blocks at each end of the two banks), data flash, option-setting memory.
	var AREAS = [
		[0x00, 0xFFFF0000, 0xFFFFFFFF, 8192, 128],
		[0x00, 0xFFF00000, 0xFFFEFFFF, 32768, 128],
		[0x00, 0xFFEF0000, 0xFFEFFFFF, 8192, 128],
		[0x00, 0xFFE00000, 0xFFEEFFFF, 32768, 128],
		[0x20, 0x00100000, 0x00107FFF, 64, 4],
		[0x30, 0xFE7F5D00, 0xFE7F5D7F, 128, 16]];
	var flash = new Map();          // address -> byte (what was written)
	var mode = "run", uartOut = [], replies = [], waiting = null;
	var acc = [], pendingData = null, writeRange = null, writePos = 0, ticker = null, tick = 0;
	window.__mock = { flash: flash, log: [], wrote: 0, ofsWritten: false };

	function be32(v) { return [v >>> 24 & 255, v >>> 16 & 255, v >>> 8 & 255, v & 255]; }
	function rd32(b, o) { return ((b[o] << 24) >>> 0) + (b[o + 1] << 16) + (b[o + 2] << 8) + b[o + 3]; }
	function frame(cmd, data) {
		var len = data.length + 1, f = [0x81, len >> 8, len & 255, cmd].concat(data), s = 0;
		for (var i = 1; i < f.length; i++) s += f[i];
		return f.concat([(-s) & 255, 0x03]);
	}
	function out(bytes) { for (var i = 0; i < bytes.length; i++) uartOut.push(bytes[i]); }
	function ack(cmd) { out(frame(cmd, [])); }

	function program(text) { out(Array.from(new TextEncoder().encode(text))); }
	function startProgram() {
		clearInterval(ticker);
		// The fake program: a start line, a tick a second, and an echo.
		program("fake rx65n: start\r\n");
		ticker = setInterval(function () { if (mode === "run") program("tick " + (tick++) + "\r\n"); }, 1000);
	}

	function bootFrame(f) {
		var cmd = f[3], data = f.slice(4, f.length - 2);
		if (f[0] === 0x81) {
			if (data.length === 0 && pendingData && pendingData.cmd === cmd) {   // ack echoed: send the data
				out(frame(cmd, pendingData.data)); pendingData = null; return;
			}
			if (cmd === 0x13 && writeRange) {                                   // write data
				for (var i = 0; i < data.length; i++) {
					var a = writeRange[0] + writePos++;
					if (a >= 0xFE7F5D00 && a <= 0xFE7F5D7F) window.__mock.ofsWritten = true;
					flash.set(a, data[i]);
				}
				window.__mock.wrote += data.length;
				ack(0x13); return;
			}
			return;
		}
		switch (cmd) {
		case 0x38: ack(cmd); pendingData = { cmd: cmd, data: [1, 2, 3, 4] }; break;
		case 0x32: ack(cmd); pendingData = { cmd: cmd, data: [0] }; break;
		case 0x2C: ack(cmd); pendingData = { cmd: cmd, data: [0] }; break;
		case 0x54:
			var k = data[0];
			if (k >= AREAS.length) { out(frame(0xD4, [0x2A])); break; }
			var a = AREAS[k];
			ack(cmd); pendingData = { cmd: cmd, data: [a[0]].concat(be32(a[1]), be32(a[2]), be32(a[3]), be32(a[4])) }; break;
		case 0x12: ack(cmd); break;                                              // erase: forget the block
		case 0x13: writeRange = [rd32(data, 0), rd32(data, 4)]; writePos = 0; ack(cmd); break;
		case 0x15:
			var s = rd32(data, 0), e = rd32(data, 4), b = [];
			for (var x = s; x <= e; x++) b.push(flash.has(x) ? flash.get(x) : 0xFF);
			ack(cmd); pendingData = { cmd: cmd, data: b }; break;
		default: ack(cmd);                                                       // 0x36 0x34 0x00 0x30
		}
	}
	function bootBytes(tx) {
		for (var i = 0; i < tx.length; i++) {
			var b = tx[i];
			if (!acc.length) {
				if (b === 0x55) { out([0xC2]); continue; }
				if (b !== 0x01 && b !== 0x81) continue;    // zeros before sync
			}
			acc.push(b);
			if (acc.length >= 3 && acc.length === (acc[1] << 8 | acc[2]) + 5) { var f = acc; acc = []; bootFrame(f); }
		}
	}

	function handle(pkt) {
		var cmd = pkt[1], arg = pkt.slice(2), reply = [0x28, cmd];
		window.__mock.log.push(cmd);
		if (cmd === 0x61) {                       // power
			if (arg[1] === 0) { mode = "off"; clearInterval(ticker); }
			else if (mode === "off") { mode = "boot"; uartOut = []; acc = []; }
		} else if (cmd === 0x64) {                // reset / mode pins
			if (arg[2] === 0x04) { mode = "reset"; clearInterval(ticker); }
			else if (mode === "reset") { mode = "run"; uartOut = []; startProgram(); }
		} else if (cmd === 0x68) {                // UART pass-through
			var tl = arg[0] << 8 | arg[1], n = arg[2] << 8 | arg[3], tx = arg.slice(4, 4 + tl);
			if (mode === "boot") bootBytes(tx);
			else if (mode === "run") out(tx);      // the fake program echoes
			var got = uartOut.splice(0, n);
			var st = got.length === n ? [0, 0, 0, 0] : [3, 0x58, 0, 0];
			while (got.length < n) got.push(0xFF);
			reply = reply.concat(got, st);
		}
		return reply;
	}

	var dev = {
		vendorId: 0x045B, productId: 0x82A0, productName: "E2 Lite (mock)", serialNumber: "MOCK0001",
		configuration: null,
		open: async function () {},
		close: async function () {},
		selectConfiguration: async function () {
			dev.configuration = { configurationValue: 1, interfaces: [{ interfaceNumber: 0, claimed: false,
				alternate: { interfaceClass: 0xFF, endpoints: [
					{ endpointNumber: 1, direction: "in", type: "bulk", packetSize: 64 },
					{ endpointNumber: 2, direction: "out", type: "bulk", packetSize: 64 }] } }] };
		},
		claimInterface: async function () {},
		transferOut: async function (ep, data) {
			var r = handle(Array.from(data));
			replies.push(r);
			if (waiting) { var w = waiting; waiting = null; w(); }
			return { status: "ok", bytesWritten: data.length };
		},
		transferIn: async function (ep, len) {
			while (!replies.length) await new Promise(function (r) { waiting = r; });
			// A real probe answers after a USB round trip (and a 0x68 waits for
			// data up to its timeout); without some delay the page's polling
			// would never yield to the event loop.
			await new Promise(function (r) { setTimeout(r, 2); });
			var b = new Uint8Array(replies.shift());
			return { status: "ok", data: new DataView(b.buffer) };
		}
	};
	var usb = {
		getDevices: async function () { return window.__mockRemembered === false ? [] : [dev]; },
		requestDevice: async function () { return dev; },
		addEventListener: function () {}
	};
	Object.defineProperty(navigator, "usb", { value: usb, configurable: true });
})();
