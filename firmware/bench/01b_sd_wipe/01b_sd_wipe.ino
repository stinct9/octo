// SD card: capacity spot-check, then wipe and fresh FAT32. DESTROYS EVERYTHING ON THE CARD.
//
// Protocol (see libraries/BenchProto): send g. The sketch reports what the card claims, prints
//   @@ {"v":1,"sketch":"01b_sd_wipe","confirm":"WIPE",...}
// and then erases only if the next line it receives within 60 s is exactly WIPE. Anything
// else -- or nothing -- aborts with the card untouched. Flashing this by accident is safe.
//
// 1. Spot-check. Fake-capacity cards claim e.g. 32 GB but hold 8 GB. Writes beyond the real
//    size are either dropped or wrap around onto earlier sectors. We write a unique, labelled
//    pattern to SAMPLES sectors spread across the claimed range, then read them all back.
//    Dropped writes read back as junk; wrapped writes show up as an earlier sector holding a
//    later sector's label. Sampling is fast but not proof -- f3write/f3read checks every byte.
// 2. Wipe. Zero the start of the card (partition table, boot sector, FATs) so no filesystem
//    is found, then remount with format-if-empty so the library builds a fresh FAT32.
//    Old data blocks aren't overwritten -- they become unreachable. Zero-filling all 30 GB at
//    this bus speed would take ~20 hours.

#include <BenchProto.h>
#include <FS.h>
#include <SD.h>
#include <SPI.h>

const int SD_CS = 21;
const int SAMPLES = 512;
const uint32_t ZERO_SECTORS = 8192;  // first 4 MB
const uint32_t CONFIRM_TIMEOUT_MS = 60000;

uint8_t buf[512];
uint8_t rbuf[512];
uint32_t nonce;

// Deterministic 512-byte pattern per sector: a readable label, then PRNG bytes.
void fillPattern(uint32_t sector) {
  uint32_t x = sector * 2654435761u ^ nonce;
  for (int i = 0; i < 512; i++) {
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;  // xorshift32
    buf[i] = (uint8_t)x;
  }
  snprintf((char *)buf, 32, "OCTOSD %08lX %08lX", (unsigned long)sector, (unsigned long)nonce);
}

uint32_t sampleSector(int i, uint32_t total) {
  // Evenly spread from sector 1 to the very last sector the card claims to have.
  return 1 + (uint32_t)((uint64_t)i * (total - 2) / (SAMPLES - 1));
}

void failed(const String &soFar, const char *phase, const char *error) {
  Serial.printf("Failed during %s: %s\n", phase, error);
  SD.end();
  bench::result(soFar + bench::kvs("phase", phase) + "," + bench::kvs("error", error));
}

String readLine(uint32_t timeoutMs) {
  String line;
  uint32_t t0 = millis();
  while (millis() - t0 < timeoutMs) {
    while (Serial.available()) {
      char c = Serial.read();
      if (c == '\n') return line;
      if (c != '\r') line += c;
    }
    delay(10);
  }
  return line;
}

void run() {
  if (!SD.begin(SD_CS)) return failed("", "mount", "no_card_or_unreadable");
  uint32_t total = SD.numSectors();
  String r = bench::kv("claimed_sectors", (unsigned long)total) + ",";
  Serial.printf("Card claims %lu sectors = %llu MB\n", (unsigned long)total,
                (uint64_t)total * 512 / (1024 * 1024));
  Serial.printf("@@ {\"v\":1,\"sketch\":\"01b_sd_wipe\",\"confirm\":\"WIPE\",\"claimed_sectors\":%lu}\n",
                (unsigned long)total);

  String cmd = readLine(CONFIRM_TIMEOUT_MS);
  cmd.trim();
  if (cmd != "WIPE") {
    SD.end();
    Serial.println("Not confirmed -- card untouched");
    bench::result(r + bench::kv("wiped", false) + "," + bench::kvs("error", "not_confirmed"));
    return;
  }

  // --- 1. capacity spot-check ---
  nonce = esp_random();
  Serial.printf("Writing %d sample sectors across the card...\n", SAMPLES);
  for (int i = 0; i < SAMPLES; i++) {
    uint32_t s = sampleSector(i, total);
    fillPattern(s);
    if (!SD.writeRAW(buf, s)) {
      Serial.printf("Write failed at sector %lu (%.1f%% into the card)\n", (unsigned long)s,
                    100.0 * s / total);
      return failed(r + bench::kv("failed_sector", (unsigned long)s) + ",", "spotcheck",
                    "write_rejected");
    }
  }
  Serial.println("Reading back...");
  int bad = 0;
  uint32_t firstBad = 0;
  bool firstBadLabelled = false;
  for (int i = 0; i < SAMPLES; i++) {
    uint32_t s = sampleSector(i, total);
    fillPattern(s);
    if (!SD.readRAW(rbuf, s) || memcmp(buf, rbuf, 512) != 0) {
      if (bad == 0) {
        firstBad = s;
        firstBadLabelled = rbuf[0] == 'O';  // holds another sector's label -> wrapped
        rbuf[31] = 0;
        Serial.printf("First mismatch at sector %lu; it holds: \"%s\"\n", (unsigned long)s,
                      firstBadLabelled ? (char *)rbuf : "(no label - write was dropped)");
      }
      bad++;
    }
  }
  r += bench::kv("samples", SAMPLES) + "," + bench::kv("bad_samples", bad) + ",";
  if (bad) {
    return failed(r + bench::kv("first_bad_sector", (unsigned long)firstBad) + "," +
                      bench::kv("first_bad_wrapped", firstBadLabelled) + ",",
                  "spotcheck", "fake_capacity");
  }
  Serial.printf("All %d samples verified across the full claimed capacity\n", SAMPLES);

  // --- 2. wipe + fresh filesystem ---
  Serial.printf("Zeroing first %lu MB...\n", ZERO_SECTORS * 512 / (1024 * 1024));
  memset(buf, 0, sizeof(buf));
  for (uint32_t s = 0; s < ZERO_SECTORS; s++) {
    if (!SD.writeRAW(buf, s)) return failed(r, "zero", "write_failed");
  }
  SD.end();

  Serial.println("Formatting...");
  // format_if_empty only fires when no filesystem is found -- which is now the case.
  // For a card under 32 GB the library picks FAT32.
  if (!SD.begin(SD_CS, SPI, 4000000, "/sd", 5, true)) return failed(r, "format", "format_failed");
  r += bench::kv("fs_total_bytes", (uint64_t)SD.totalBytes()) + "," +
       bench::kv("fs_used_bytes", (uint64_t)SD.usedBytes()) + ",";

  File f = SD.open("/hello.txt", FILE_WRITE);
  if (!f || !f.println("octo")) return failed(r, "verify", "write_failed");
  f.close();
  f = SD.open("/hello.txt");
  String back = f ? f.readStringUntil('\n') : "";
  f.close();
  SD.remove("/hello.txt");
  back.trim();
  if (back != "octo") return failed(r, "verify", "readback_failed");
  SD.end();
  Serial.println("Card wiped, formatted, and read/write verified");
  bench::result(r + bench::kv("wiped", true));
}

void setup() { bench::begin("01b_sd_wipe"); }

void loop() {
  if (bench::poll() == 'g') run();
  delay(10);
}
