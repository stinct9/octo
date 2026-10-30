// Bench test 1: the Sense's own microSD slot (CS = GPIO21) mounts, writes, reads back
// correctly, and is fast enough for audio.
//
// Audio budget: 16 kHz x 16-bit mono = 32 KB/s. Write speed needs to be well above that
// because SD cards stall periodically while they erase blocks internally. The host judges
// the numbers; this sketch only measures.
//
// If mount fails: card must be FAT32 and <= 32 GB, fully seated. Only then consider the J3
// jumper -- see CLAUDE.md. Don't cut it on a first failure.
//
// Expect the orange user LED to flicker during this test: it shares GPIO21 with SD chip-select.
//
// Protocol: see libraries/BenchProto. Send g to run. The card is only touched between g and
// the final @@ line, and is unmounted afterwards, so a reset outside that window is safe.

#include <BenchProto.h>
#include <FS.h>
#include <SD.h>
#include <SPI.h>

const int SD_CS = 21;
const int MOUNT_ATTEMPTS = 3;
const char *TEST_FILE = "/bench_sd.bin";
const size_t CHUNK = 4096;
const size_t TOTAL = 2 * 1024 * 1024;  // 2 MB

uint8_t buf[CHUNK];
uint8_t rbuf[CHUNK];

const char *resetReason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "poweron";  // card was power-cycled too
    case ESP_RST_USB:     return "usb";      // card stayed powered
    case ESP_RST_SW:      return "software";
    default:              return "other";
  }
}

// Report a failure with whatever was measured so far, and leave the card unmounted.
void failed(const String &soFar, const char *phase, const char *error) {
  Serial.printf("Failed during %s: %s\n", phase, error);
  SD.end();
  bench::result(soFar + bench::kvs("phase", phase) + "," + bench::kvs("error", error));
}

void run() {
  String r = bench::kvs("boot", resetReason()) + ",";

  // Default SPI pins on this board variant are SCK=D8, MISO=D9, MOSI=D10, which is what the
  // Sense's SD slot is wired to. Only chip-select needs to be named.
  // Retry: some cards ignore the first reset command after the host restarts without cutting
  // their power. How many attempts it takes is part of the result.
  int attempt = 1;
  while (!SD.begin(SD_CS)) {
    SD.end();
    if (++attempt > MOUNT_ATTEMPTS) {
      failed(r + bench::kv("mount_attempts", MOUNT_ATTEMPTS) + ",", "mount",
             "no_card_or_unreadable");
      return;
    }
    delay(200);
  }
  Serial.printf("Mounted on attempt %d\n", attempt);

  const char *types[] = {"NONE", "MMC", "SDSC", "SDHC", "UNKNOWN"};
  uint8_t t = SD.cardType();
  Serial.printf("Card: %s, %llu MB, FS %llu MB total, %llu MB used\n", types[t <= 4 ? t : 4],
                SD.cardSize() / (1024 * 1024), SD.totalBytes() / (1024 * 1024),
                SD.usedBytes() / (1024 * 1024));
  r += bench::kv("mount_attempts", attempt) + "," + bench::kvs("card_type", types[t <= 4 ? t : 4]) +
       "," + bench::kv("card_bytes", (uint64_t)SD.cardSize()) + "," +
       bench::kv("fs_total_bytes", (uint64_t)SD.totalBytes()) + "," +
       bench::kv("fs_used_bytes", (uint64_t)SD.usedBytes()) + ",";

  // A leftover test file means an earlier run was interrupted mid-write.
  bool leftover = SD.exists(TEST_FILE);
  if (leftover) SD.remove(TEST_FILE);
  r += bench::kv("leftover_test_file", leftover) + ",";

  // Write a known pattern and time it.
  for (size_t i = 0; i < CHUNK; i++) buf[i] = (uint8_t)(i * 31 + 7);
  File f = SD.open(TEST_FILE, FILE_WRITE);
  if (!f) return failed(r, "write", "open_failed");
  uint32_t worst = 0;
  uint32_t t0 = millis();
  for (size_t done = 0; done < TOTAL; done += CHUNK) {
    buf[0] = (uint8_t)(done / CHUNK);  // make each chunk distinct so we catch misplaced writes
    uint32_t c0 = millis();
    if (f.write(buf, CHUNK) != CHUNK) {
      f.close();
      return failed(r, "write", "short_write");
    }
    worst = max(worst, millis() - c0);
  }
  f.close();
  uint32_t wms = millis() - t0;
  Serial.printf("Write: %u KB in %lu ms (worst 4 KB chunk: %lu ms)\n", TOTAL / 1024, wms, worst);
  r += bench::kv("test_bytes", (unsigned long)TOTAL) + "," + bench::kv("write_ms", wms) + "," +
       bench::kv("worst_chunk_ms", worst) + "," + bench::kv("chunk_bytes", (unsigned long)CHUNK) + ",";

  // Read back and verify.
  f = SD.open(TEST_FILE, FILE_READ);
  if (!f) return failed(r, "read", "open_failed");
  t0 = millis();
  for (size_t done = 0; done < TOTAL; done += CHUNK) {
    buf[0] = (uint8_t)(done / CHUNK);
    if (f.read(rbuf, CHUNK) != CHUNK) {
      f.close();
      return failed(r, "read", "short_read");
    }
    if (memcmp(rbuf, buf, CHUNK) != 0) {
      f.close();
      Serial.printf("Mismatch at offset %u -- suspect a fake-capacity card, run f3\n", done);
      return failed(r + bench::kv("mismatch_offset", (unsigned long)done) + ",", "verify",
                    "readback_mismatch");
    }
  }
  f.close();
  uint32_t rms = millis() - t0;
  Serial.printf("Read:  %u KB in %lu ms, data verified\n", TOTAL / 1024, rms);
  SD.remove(TEST_FILE);
  SD.end();

  bench::result(r + bench::kv("read_ms", rms) + "," + bench::kv("verified", true));
}

void setup() { bench::begin("01_sd"); }

void loop() {
  if (bench::poll() == 'g') run();
  delay(10);
}
