// The recorder's screen (octo_display.h, beside this file), ported line for line so the landing page
// and the phone app draw exactly what the board draws (docs/landing/check_oled.py compares every pixel). C's float maths is single precision: every float step goes through
// Math.fround, int division truncates, and lroundf rounds half away from zero.
const OD = (() => {
  const W = 128, H = 64;
  const F = Math.fround;
  const lroundf = (v) => (v < 0 ? -Math.floor(-v + 0.5) : Math.floor(v + 0.5));
  const idiv = (a, b) => Math.trunc(a / b);
  const sinf = (x) => F(Math.sin(x));
  const cosf = (x) => F(Math.cos(x));
  const powf = (x, y) => F(Math.pow(x, y));
  const K = {
    deg: F(0.0174533), p45: F(0.45), p16: F(1.6), p11: F(1.1), p105: F(1.05), p2: F(0.2), p6: F(0.6),
    awake: F(0.004), asleep: F(0.0015), alert: F(0.01), hello: F(0.006), busy: F(0.012), calm: F(0.005),
    stopPhase: F(0.003), c17: F(1.7), p35: F(0.35), p65: F(0.65),
  };
  const FONT_HEX = '000000000000005f00000007000700147f147f14242a7f2a12231308646236495522500005030000001c2241000041221c0014083e081408083e080800503000000808080808006060000020100804023e5149453e00427f400042615149462141454b311814127f1027454545393c4a49493001710905033649494936064949291e003636000000563600000814224100141414141400412214080201510906324979413e7e1111117e7f494949363e414141227f4141221c7f494949417f090909013e4149497a7f0808087f00417f41002040413f017f081422417f404040407f020c027f7f0408107f3e4141413e7f090909063e4151215e7f09192946464949493101017f01013f4040403f1f2040201f3f4038403f631408146307087008076151494543007f41410002040810200041417f0004020102044040404040000102040020545454787f484444383844444420384444487f3854545418087e09010218a4a4a47c7f0804047800447d40004080847d007f1028440000417f40007c041804787c080404783844444438fc2424241818242418fc7c080404084854545420043f4440203c4040207c1c2040201c3c4030403c44281028441ca0a0a07c4464544c44';
  const FONT = new Uint8Array(FONT_HEX.length / 2);
  for (let i = 0; i < FONT.length; i++) FONT[i] = parseInt(FONT_HEX.substr(i * 2, 2), 16);

  // A frame in the SSD1306's own layout: 8 pages of 8 rows, one byte per column per page.
  const frame = () => new Uint8Array(W * H / 8);
  const clear = (f) => f.fill(0);
  const px = (f, x, y, on) => {
    if (x < 0 || y < 0 || x >= W || y >= H) return;
    const i = (y >> 3) * W + x;
    if (on) f[i] |= 1 << (y & 7); else f[i] &= ~(1 << (y & 7));
  };
  const lit = (f, x, y) => (f[(y >> 3) * W + x] >> (y & 7)) & 1;
  const fill = (f, x, y, w, h, on) => {
    for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) px(f, i, j, on);
  };
  const rect = (f, x, y, w, h) => {
    for (let i = x; i < x + w; i++) { px(f, i, y, 1); px(f, i, y + h - 1, 1); }
    for (let j = y; j < y + h; j++) { px(f, x, j, 1); px(f, x + w - 1, j, 1); }
  };
  const disc = (f, cx, cy, r, on) => {
    const lim = r * r + idiv(r, 2);
    for (let j = -r; j <= r; j++) for (let i = -r; i <= r; i++) if (i * i + j * j <= lim) px(f, cx + i, cy + j, on);
  };
  const arc = (f, cx, cy, r, a0, a1) => {
    for (let a = a0; a <= a1; a += 2) {
      const ang = F(a * K.deg);
      px(f, cx + lroundf(F(r * cosf(ang))), cy + lroundf(F(r * sinf(ang))), 1);
    }
  };
  const textWidth = (t, s) => t.length * 6 * s - s;
  const text = (f, x, y, t, s) => {
    for (const ch of t) {
      let c = ch.charCodeAt(0);
      if (c < 32 || c > 122) c = 63;
      const g = (c - 32) * 5;
      for (let col = 0; col < 5; col++)
        for (let row = 0; row < 8; row++)
          if ((FONT[g + col] >> row) & 1) fill(f, x + col * s, y + row * s, s, s, 1);
      x += 6 * s;
    }
    return x;
  };

  const AWAKE = 0, ASLEEP = 1, ALERT = 2;

  // A round head with a little body, two eyes, six arms that wave with `phase`.
  const octopusEyes = (f, cx, cy, r, open, alert, phase) => {
    disc(f, cx, cy, r, 1);
    fill(f, cx - r, cy, 2 * r + 1, idiv(r * 2, 3), 1);
    const base = cy + idiv(r * 2, 3);
    for (let i = 0; i < 6; i++) {
      const x0 = F((cx - r + 1) + F(F(F(F(2 * r) - 2) * F(i + 0.5)) / 6));
      const out = F(F(i - 2.5) * K.p45);
      const len = r - (i === 0 || i === 5 ? 2 : 0);
      const thin = idiv(len * 2, 3);
      for (let k = 0; k < len; k++) {
        const s = sinf(F(F(phase + F(k * K.p45)) + F(i * K.p11)));
        const x = F(F(x0 + F(out * k)) + F(K.p16 * s));
        fill(f, lroundf(x) - 1, base + k, 3 - (k > thin ? 1 : 0), 1, 1);
      }
      const s = sinf(F(F(phase + F(len * K.p45)) + F(i * K.p11)));
      disc(f, lroundf(F(F(x0 + F(out * len)) + F(K.p16 * s))), base + len, 1, 1);
    }
    const ex = idiv(r * 2, 5), ey = cy + idiv(r, 6), er = idiv(r, 4) + 1;
    for (let sgn = -1; sgn <= 1; sgn += 2) {
      if (open < K.p2) {
        for (let i = -er; i <= er; i++) px(f, cx + sgn * ex + i, ey + idiv(i * i, er + 1) - 1, 0);
        for (let i = -er; i <= er; i++) px(f, cx + sgn * ex + i, ey + idiv(i * i, er + 1), 0);
      } else {
        const ry = F(er * (open > 1 ? 1 : open));
        const ry2 = F(ry * ry);
        for (let j = -er; j <= er; j++)
          for (let i = -er; i <= er; i++)
            if (F(F(F(i * i) / (er * er)) + F(F(j * j) / ry2)) <= K.p105) px(f, cx + sgn * ex + i, ey + j, 0);
        if (open > K.p6) disc(f, cx + sgn * ex + (alert ? 0 : 1), ey + (alert ? -1 : 0), idiv(er, 2), 1);
      }
    }
  };
  const octopus = (f, cx, cy, r, mood, phase) =>
    octopusEyes(f, cx, cy, r, mood === ASLEEP ? 0 : 1, mood === ALERT, phase);

  const pad2 = (n) => String(n).padStart(2, '0');
  const clock = (seconds) => seconds < 100 * 60
    ? `${pad2(Math.floor(seconds / 60))}:${pad2(seconds % 60)}`
    : `${Math.floor(seconds / 3600)}h ${pad2(Math.floor(seconds / 60) % 60)}m`;

  const recording = (f, seconds, ms) => {
    clear(f);
    octopus(f, 22, 28, 14, AWAKE, F(F(ms) * K.awake));
    const waves = 1 + (Math.floor(ms / 400) % 3);
    for (let k = 0; k < waves; k++) arc(f, 22, 28, 18 + 4 * k, 225, 315);
    if (ms % 1000 < 600) disc(f, 56, 18, 5, 1);
    text(f, 66, 8, 'REC', 3);
    const t = clock(seconds);
    text(f, 128 - textWidth(t, 2) - 4, 40, t, 2);
  };
  const stopped = (f, ms) => {
    clear(f);
    octopus(f, 20, 24, 14, ASLEEP, F(F(ms) * K.asleep));
    text(f, 43, 10, 'Stopped', 2);
    text(f, 50, 34, 'hold 1s', 1);
    text(f, 50, 44, 'to record', 1);
    text(f, 34, Math.floor(ms / 700) % 2 ? 2 : 4, 'z', 1);
  };
  const attention = (f, what, ms) => {
    clear(f);
    octopus(f, 24, 24, 15, ALERT, F(F(ms) * K.alert));
    text(f, 52, 6, '!', 3);
    text(f, 52, 36, what, 1);
  };
  const hold = (f, held, toggleMs, rec) => {
    clear(f);
    const ready = held >= toggleMs;
    const now = ready ? 'Let go to' : 'Keep holding to';
    const what = rec ? 'stop' : 'record';
    const p = ready ? 1 : F(F(held) / F(toggleMs));
    text(f, idiv(128 - textWidth(now, 1), 2), 6, now, 1);
    text(f, idiv(128 - textWidth(what, 2), 2), 18, what, 2);
    rect(f, 8, 42, 112, 12);
    fill(f, 10, 44, Math.trunc(F(108 * p)), 8, 1);
  };
  const hello = (f, ms) => {
    clear(f);
    octopus(f, 26, 24, 17, AWAKE, F(F(ms) * K.hello));
    text(f, 52, 18, 'octo', 3);
  };
  // Not paired with a Mac yet (product plan 4.4).
  const setup = (f, ms) => {
    clear(f);
    octopus(f, 20, 26, 13, AWAKE, F(F(ms) * K.awake));
    text(f, 44, 6, 'Set me up', 1);
    text(f, 44, 20, 'USB', 3);
    text(f, 44, 48, 'to your Mac', 1);
  };
  // A phone pairing over Bluetooth: the six digits to type on the phone.
  const passkey = (f, code, ms) => {
    clear(f);
    text(f, idiv(128 - textWidth('Phone code', 1), 2), 4, 'Phone code', 1);
    const t = String(code % 1000000).padStart(6, '0');
    text(f, idiv(128 - textWidth(t, 3), 2), 22, t, 3);
    text(f, idiv(128 - textWidth('type it on the phone', 1), 2), 52, 'type it on the phone', 1);
    if (ms % 1000 < 500) disc(f, 122, 7, 2, 1);
  };
  // Over USB, from a computer that can't sign with the recorder's key (rec-16, C16): hold its button to allow.
  const confirm = (f, what, held, holdMs, leftMs) => {
    clear(f);
    const ready = held >= holdMs;
    const p = ready ? 1 : F(F(held) / F(holdMs));
    text(f, idiv(128 - textWidth('Allow', 1), 2), 2, 'Allow', 1);
    text(f, idiv(128 - textWidth(what, 2), 2), 13, what, 2);
    rect(f, 8, 33, 112, 12);
    fill(f, 10, 35, Math.trunc(F(108 * p)), 8, 1);
    const t = `hold the button ${Math.floor((leftMs + 999) / 1000)}s`;
    text(f, idiv(128 - textWidth(t, 1), 2), 52, t, 1);
  };

  // Always on, between interactions: dim, simplified, and walked round a small square each minute.
  const WALK = [[0, 0], [3, 1], [6, 2], [6, 5], [3, 6], [0, 5], [-2, 3], [-1, 1]];
  const shift = (ms) => WALK[Math.floor(ms / 60000) % 8];
  const ambientRecording = (f, seconds, ms) => {
    const [dx, dy] = shift(ms);
    clear(f);
    octopus(f, 20 + dx, 22 + dy, 8, AWAKE, 0);
    if (ms % 1000 < 500) disc(f, 46 + dx, 16 + dy, 4, 1);
    text(f, 55 + dx, 9 + dy, 'REC', 2);
    text(f, 42 + dx, 30 + dy, clock(seconds), 2);
  };
  const ambientStopped = (f, ms) => {
    const [dx, dy] = shift(ms);
    clear(f);
    octopus(f, 20 + dx, 24 + dy, 9, ASLEEP, 0);
    text(f, 36 + dx, 18 + dy, 'Stopped', 2);
  };

  // The Mac's copy, under the main line (octo_display.h's od_sync_status, 20 Oct).
  const SYNC_NONE = 0, SYNC_COPYING = 1, SYNC_WAITING = 2, SYNC_ALL_ON = 3;
  const ON_RECORDING = 0, ON_STOPPED = 1, ON_AMBIENT_RECORDING = 2, ON_AMBIENT_STOPPED = 3;
  const syncStatus = (f, layout, kind, minutes, ms) => {
    const ambient = layout === ON_AMBIENT_RECORDING || layout === ON_AMBIENT_STOPPED;
    if (kind === SYNC_NONE || (ambient && kind === SYNC_ALL_ON)) return;
    const [dx, dy] = ambient ? shift(ms) : [0, 0];
    let x = 2, y = 56;
    if (layout === ON_AMBIENT_RECORDING) { x = 42 + dx; y = 49 + dy; }
    if (layout === ON_AMBIENT_STOPPED) { x = 36 + dx; y = 40 + dy; }
    let t;
    if (kind === SYNC_COPYING) t = ambient ? 'To Mac' : 'Copying to Mac';
    else if (kind === SYNC_ALL_ON) t = 'All on your Mac';
    else if (minutes < 100) t = ambient ? `${minutes}m waiting` : `${minutes} min waiting`;
    else if (minutes < 48 * 60) t = `${Math.floor(minutes / 60)}h waiting`;
    else t = `${Math.floor(minutes / 1440)}d waiting`;
    text(f, x, y, t, 1);
  };

  const ANIM_MS = 1200;
  const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
  const easeOut = (t) => { t = clamp01(t); const u = F(1 - t); return F(1 - F(F(u * u) * u)); };
  const easeBack = (t) => {
    t = clamp01(t);
    const c = K.c17, d = F(t - 1);
    return F(F(1 + F(F(c + 1) * powf(d, 3))) + F(c * powf(d, 2)));
  };
  const animStart = (f, ms) => {
    const t = F(ms);
    clear(f);
    const cx = 22, cy = 28;
    if (t < 650) arc(f, cx, cy, 4 + Math.trunc(F(80 * easeOut(F(t / 650)))), 0, 358);
    const r = 8 + lroundf(F(6 * easeBack(F(t / 450))));
    octopusEyes(f, cx, cy, r, clamp01(F(F(t - 250) / 250)), 0, F(t * K.awake));
    let waves = t < 500 ? 0 : 1 + Math.trunc(F(F(t - 500) / 150));
    if (waves > 3) waves = 3;
    for (let k = 0; k < waves && k < 3; k++) arc(f, cx, cy, 18 + 4 * k, 225, 315);
    const x = 128 - Math.trunc(F(62 * easeOut(F(F(t - 550) / 450))));
    if (t > 550) text(f, x, 8, 'REC', 3);
    if (t > 950) disc(f, 56, 18, 5, 1);
    if (t > 950) text(f, 128 - textWidth('00:00', 2) - 4, 40, '00:00', 2);
  };
  const animStop = (f, ms) => {
    const t = F(ms);
    clear(f);
    const move = easeOut(F(t / 900));
    const cx = 22 - lroundf(F(2 * move)), cy = 28 - lroundf(F(4 * move));
    octopusEyes(f, cx, cy, 14, F(1 - clamp01(F(F(t - 300) / 350))), 0, F(t * K.stopPhase));
    const waves = t < 300 ? 3 - Math.trunc(F(t / 100)) : 0;
    for (let k = 0; k < waves; k++) arc(f, cx, cy, 18 + 4 * k, 225, 315);
    if (t < 450) text(f, 66 + Math.trunc(F(70 * easeOut(F(t / 450)))), 8, 'REC', 3);
    if (t > 650) text(f, 128 - Math.trunc(F(85 * easeOut(F(F(t - 650) / 450)))), 10, 'Stopped', 2);
    if (t > 1000) text(f, 34, 8 - Math.trunc(F(F(t - 1000) / 60)), 'z', 1);
    if (t > 950) { text(f, 50, 34, 'hold 1s', 1); text(f, 50, 44, 'to record', 1); }
  };

  const updating = (f, done, size, ms) => {
    clear(f);
    octopus(f, 20, 22, 12, AWAKE, F(F(ms) * K.busy));
    const restarting = size && done >= size;
    text(f, 42, 6, restarting ? 'Restarting' : 'Updating', 1);
    text(f, 42, 18, `${size ? Math.floor(done * 100 / size) : 0}%`, 2);
    rect(f, 8, 46, 112, 10);
    fill(f, 10, 48, Math.trunc(F(108 * (size ? F(F(done) / F(size)) : 0))), 6, 1);
    for (let i = 0; i < 3; i++) disc(f, 100 + i * 7, 24, Math.floor(ms / 200) % 3 === i ? 2 : 1, 1);
  };
  const CHECKING = 0, DONE = 1, FAILED = 2, UNDONE = 3;
  const line = (f, x0, y0, x1, y1, thick) => {
    const n = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0));
    for (let i = 0; i <= n; i++) {
      const x = x0 + (n ? idiv((x1 - x0) * i, n) : 0), y = y0 + (n ? idiv((y1 - y0) * i, n) : 0);
      fill(f, x - idiv(thick, 2), y - idiv(thick, 2), thick, thick, 1);
    }
  };
  const updateResult = (f, kind, fw, ms) => {
    clear(f);
    const bad = kind === FAILED || kind === UNDONE;
    octopus(f, 20, 24, 13, bad ? ALERT : AWAKE, F(F(ms) * (kind === CHECKING ? K.busy : K.calm)));
    text(f, 44, 6, ['Checking', 'Updated', 'Update failed', 'Rolled back'][kind], 1);
    text(f, 44, 20, fw, 2);
    if (kind === CHECKING) {
      text(f, 50, 46, 'with the Mac', 1);
      for (let i = 0; i < 3; i++) disc(f, 104 + i * 7, 9, Math.floor(ms / 200) % 3 === i ? 2 : 1, 1);
    } else if (kind === DONE) {
      const t = clamp01(F(F(ms) / 500));
      const a = clamp01(F(t / K.p35)), b = clamp01(F(F(t - K.p35) / K.p65));
      line(f, 54, 47, 54 + Math.trunc(F(7 * a)), 47 + Math.trunc(F(7 * a)), 3);
      if (b > 0) line(f, 61, 54, 61 + Math.trunc(F(15 * b)), 54 - Math.trunc(F(15 * b)), 3);
      if (t >= 1) text(f, 80, 46, 'all good', 1);
    } else {
      text(f, 50, 46, kind === FAILED ? 'no change' : 'update failed', 1);
    }
  };

  return {
    W, H, ANIM_MS, CHECKING, DONE, FAILED, UNDONE, AWAKE, ASLEEP, ALERT, frame, lit,
    recording, stopped, attention, hold, hello, setup, passkey, confirm, ambientRecording, ambientStopped, syncStatus,
    SYNC_NONE, SYNC_COPYING, SYNC_WAITING, SYNC_ALL_ON, ON_RECORDING, ON_STOPPED, ON_AMBIENT_RECORDING, ON_AMBIENT_STOPPED,
    animStart, animStop, updating, updateResult,
    // The pieces, for the phone app's own scenes (mobile/src/scenes.ts)
    clear, px, fill, rect, disc, arc, text, textWidth, octopus, octopusEyes, clamp01, easeOut, easeBack,
  };
})();
if (typeof module !== 'undefined') module.exports = OD;
