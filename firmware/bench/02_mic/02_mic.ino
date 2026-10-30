// Bench test 2: the onboard PDM mic produces real audio, not silence or noise.
//
// Protocol (see libraries/BenchProto), quiet until asked:
//   g  2-second self-test: level stats as one @@ result (catches a dead, stuck or silent mic)
//   m  toggle a live level stream: @@ {"level":{"dbfs":..,"peak":..,"dc":..}} ten times a second
//   r  record 10 s into PSRAM, then dump it:  WAVSTART <bytes> <rate>\n <raw PCM> \nWAVEND\n
//      followed by an @@ result. Stops the level stream first.
//   R  the same, 35 s (voice enrolment: ~30 s of reading after a 3 s silent lead-in)
//
// PDM (pulse-density modulation) mics output a 1-bit stream at a high clock rate; the S3's
// I2S peripheral has a hardware filter that turns that into ordinary 16-bit PCM, which is
// why only two wires (clock + data) are needed.
//
// ESP32 Arduino core 3.x API (ESP_I2S / I2SClass). The core 2.x `I2S.begin(PDM_MONO_MODE, ...)`
// no longer exists.

#include <BenchProto.h>
#include <ESP_I2S.h>

const int PDM_CLK = 42;
const int PDM_DATA = 41;
const int RATE = 16000;
const int RECORD_SECONDS = 10;
const int RECORD_LONG_SECONDS = 35;  // 1.1 MB; PSRAM has 8 MB
const int SELFTEST_BLOCKS = 20;  // 2 s

I2SClass i2s;
bool i2sOk = false;
bool streaming = false;
int16_t block[RATE / 10];  // 100 ms

struct Level {
  size_t n;
  float dbfs;
  int peak;
  float dc;
  size_t longestRun;  // identical consecutive samples: a stuck data line shows up here
};

// Reading continuously (even when nobody is listening) keeps the I2S buffer fresh, so a
// recording starts with current audio rather than whatever was left in DMA.
Level readBlock() {
  Level l = {0, -120, 0, 0, 0};
  l.n = i2s.readBytes((char *)block, sizeof(block)) / sizeof(int16_t);
  if (l.n == 0) return l;
  // PDM mics carry a DC offset; remove the mean so it doesn't read as loudness.
  int64_t sum = 0;
  for (size_t i = 0; i < l.n; i++) sum += block[i];
  l.dc = (float)sum / l.n;
  double sq = 0;
  size_t run = 1;
  for (size_t i = 0; i < l.n; i++) {
    float s = block[i] - l.dc;
    sq += s * s;
    l.peak = max(l.peak, abs((int)s));
    run = (i && block[i] == block[i - 1]) ? run + 1 : 1;
    l.longestRun = max(l.longestRun, run);
  }
  float rms = sqrt(sq / l.n);
  l.dbfs = rms > 0 ? 20 * log10(rms / 32768.0) : -120;
  return l;
}

void selfTest() {
  if (!i2sOk) return bench::result(bench::kvs("error", "i2s_begin_failed"));
  float minDb = 0, maxDb = -120;
  double power = 0;
  int peak = 0, empty = 0;
  size_t samples = 0, longestRun = 0;
  for (int b = 0; b < SELFTEST_BLOCKS; b++) {
    Level l = readBlock();
    if (l.n == 0) { empty++; continue; }
    samples += l.n;
    minDb = b == 0 ? l.dbfs : min(minDb, l.dbfs);
    maxDb = max(maxDb, l.dbfs);
    power += pow(10, l.dbfs / 10);
    peak = max(peak, l.peak);
    longestRun = max(longestRun, l.longestRun);
  }
  int good = SELFTEST_BLOCKS - empty;
  float meanDb = good ? 10 * log10(power / good) : -120;
  Serial.printf("Self-test: %u samples, level %.1f..%.1f dBFS, peak %d\n", samples, minDb, maxDb, peak);
  bench::result(bench::kv("rate", RATE) + "," + bench::kv("samples", (unsigned long)samples) + "," +
                bench::kv("empty_blocks", empty) + "," + bench::kv("dbfs_min", minDb) + "," +
                bench::kv("dbfs_max", maxDb) + "," + bench::kv("dbfs_mean", meanDb) + "," +
                bench::kv("peak", peak) + "," + bench::kv("longest_run", (unsigned long)longestRun));
}

void recordAndDump(int seconds) {
  streaming = false;
  if (!i2sOk) return bench::result(bench::kvs("error", "i2s_begin_failed"));
  size_t bytes = (size_t)RATE * seconds * sizeof(int16_t);
  // 320 KB+ is too big for internal RAM alongside everything else; PSRAM is 8 MB.
  uint8_t *buf = (uint8_t *)ps_malloc(bytes);
  if (!buf) return bench::result(bench::kvs("error", "psram_alloc_failed"));

  Serial.printf("REC %d s\n", seconds);
  digitalWrite(LED_BUILTIN, LOW);  // LED on marks the recording window
  uint32_t t0 = millis();
  size_t got = 0;
  while (got < bytes) got += i2s.readBytes((char *)buf + got, min((size_t)4096, bytes - got));
  uint32_t ms = millis() - t0;
  digitalWrite(LED_BUILTIN, HIGH);

  // Framed binary dump: a text header with the length, raw little-endian PCM, a text trailer.
  Serial.printf("WAVSTART %u %d\n", got, RATE);
  Serial.flush();
  for (size_t off = 0; off < got; off += 1024) Serial.write(buf + off, min((size_t)1024, got - off));
  Serial.flush();
  Serial.println("\nWAVEND");
  free(buf);
  bench::result(bench::kv("rate", RATE) + "," + bench::kv("recorded_bytes", (unsigned long)got) +
                "," + bench::kv("record_ms", ms));
}

void setup() {
  // User LED (active-LOW) marks the recording window. Safe here: this sketch doesn't use SD,
  // which shares GPIO21.
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);
  bench::begin("02_mic");

  i2s.setPinsPdmRx(PDM_CLK, PDM_DATA);
  i2sOk = i2s.begin(I2S_MODE_PDM_RX, RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
  // The PDM filter needs a moment to settle; the first few hundred ms are junk.
  if (i2sOk) for (int i = 0; i < 5; i++) readBlock();
}

void loop() {
  switch (bench::poll()) {
    case 'g': selfTest(); break;
    case 'm': streaming = !streaming; break;
    case 'r': recordAndDump(RECORD_SECONDS); break;
    case 'R': recordAndDump(RECORD_LONG_SECONDS); break;
  }
  if (!i2sOk) { delay(50); return; }
  Level l = readBlock();
  if (streaming) {
    Serial.printf("@@ {\"v\":1,\"sketch\":\"02_mic\",\"level\":{\"dbfs\":%.1f,\"peak\":%d,\"dc\":%.0f}}\n",
                  l.dbfs, l.peak, l.dc);
  }
}
