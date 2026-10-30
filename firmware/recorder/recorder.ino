// Recorder (L3): records from power-on to the SD card in 60 s chunks; the Mac pulls them over
// home Wi-Fi. Plan: docs/conversation-lab-plan.md §3.
//
// Audio path. Three FreeRTOS tasks so a slow SD card never costs audio:
//   reader (highest priority)  I2S mic -> DC blocker -> 8 s ring buffer in PSRAM
//   writer                     ring buffer -> chunk file on the SD card, flushed every second
//   loop (Arduino, lowest)     Wi-Fi, the HTTP API, USB commands
// SD cards pause for tens of milliseconds now and then while they erase blocks internally; the
// ring buffer rides that out. If it ever fills (the card stalled for 8 s), lost samples are counted.
//
// On the card:
//   /rec/b00042/manifest.jsonl   one JSON line per event in boot 42: boot, clock set, chunk open/closed
//   /rec/b00042/s00017.wav       chunk 17 of boot 42: up to 60 s, 16 kHz mono 16-bit
// A chunk is flushed every second, so pulling the power loses about a second. Its WAV header is
// written up front with full-chunk sizes and corrected on close; a chunk cut short by power loss is
// repaired at the next boot (sizes from the file length, checksum by reading it back).
//
// Time. There's no clock battery, so the device only knows milliseconds since it booted. The Mac
// tells it the time on every connection and that goes into the manifest: every chunk of a boot that
// reaches home Wi-Fi gets a real time. A boot that dies before getting home has no time.
//
// Wi-Fi API. The Mac pulls; the device never connects out. GET /hello gives a one-time nonce.
// Every other request is a POST with a JSON body, signed with the key in secrets.h:
//   X-Octo-Nonce: <nonce>   X-Octo-Sig: hex HMAC-SHA256(key, "POST\n<path>\n<nonce>\n<body>")
// A nonce works once and for 60 s, so a recorded request can't be replayed. Each response carries
// the next nonce (X-Octo-Next), and JSON responses are signed with HMAC(key, "RESP\n<path>\n<nonce>\n
// <body>") so a fake device on the network can't feed the Mac a made-up chunk list. Audio is checked
// against the SHA-256 in the (signed) manifest.
//
// Encryption (rec-2, 5 Oct; review 4 Oct M41): with OCTO_ENC_PUBKEY in secrets.h, every chunk is
// encrypted as it's written (octo_crypto.h): each boot agrees a key with the Mac's public key from a
// throwaway key pair whose private half is wiped at once, so only the Mac can read the card. The
// boot's public half goes in its first manifest line ("epk"); the SHA-256 covers the bytes as stored.
// Nonces are kept per client, so another device on the Wi-Fi asking for them can't stall the Mac.
//
// Updates over Wi-Fi (rec-2, 5 Oct): /update/begin, /update/data, /update/finish, each a signed
// request like the rest; the image's SHA-256 is in the signed begin. It's written to the spare
// firmware slot and only switched to when the hash matches. The new firmware counts as good once
// the Mac has made a signed request to it; if it crashes first, or hasn't heard from the Mac within
// 10 minutes, the bootloader goes back to the one before. Refused while recording.
//
// Updates over USB (rec-13, 8 Oct): the same update through the same code, so the screen shows its
// progress and the result, and a firmware that doesn't start goes back. Until rec-13 a USB flash went
// through the chip's own loader instead (esptool): nothing of ours ran, so nothing could be shown and
// nothing could go back. Over USB the Mac keeps the new firmware with K once it's answering (and back
// on Wi-Fi if it was before), or sends it back with X.
//
// USB (BenchProto, for the desk and as a fallback): i identity, s or g status, 1 record, 0 stop,
// P pair with a Mac, N change the Wi-Fi networks (rec-12), U update, K keep the new firmware,
// X go back to the one before (rec-13).
//
// Settings (rec-12, product plan 4.4): one build for every unit. The key shared with the Mac, the Mac's
// public key and up to five Wi-Fi networks live in the board's flash (NVS, octo_settings.h), written
// when it's paired with a Mac over USB (`octo device pair`); networks also from the phone app or the
// Mac. Until it's paired it neither records nor joins Wi-Fi, and its screen says "Set me up". A build
// with secrets.h (the first lab's) takes its key, public key and network from there, as before.
//
// Bluetooth (rec-12): a service for the phone app (docs/recorder-ble.md): status, recording on/off and
// the networks, every value behind a bonded, authenticated link. Pairing shows six digits on the screen
// to type on the phone. It advertises for 10 minutes after starting and after a tap of the button.
//
// Watchdog (rec-20, 20 Oct): on 19 Oct at 23:07 the loop stopped mid-sync: the screen's time froze and the
// button did nothing until Jay reset it, though the writer recorded to the end. Nothing watched the loop. Now
// the loop and the writer must check in within WATCHDOG_MS or the chip restarts (reset "watchdog"), and
// recording carries on in the next boot. Where each was last is kept across that restart and goes on the
// next boot's first lines ("watchdog"), so the next hang says where it was.

#include <BenchProto.h>
#include <ESPmDNS.h>
#include <ESP_I2S.h>
#include <FS.h>
#include <Preferences.h>
#include <SD.h>
#include <SPI.h>
#include <WebServer.h>
#include <Update.h>
#include <WiFi.h>
#include <esp_mac.h>
#include <esp_ota_ops.h>
#include <esp_task_wdt.h>
#include "diskio_impl.h"  // FatFs's driver table (rec-18: card retries)
#include <driver/gpio.h>
#include <mbedtls/base64.h>
#include <freertos/stream_buffer.h>
#include <mbedtls/md.h>
#include <mbedtls/sha256.h>
#include <algorithm>
#include <vector>
#include <BLEDevice.h>
#include <BLESecurity.h>
#include <BLEServer.h>
#include "host/ble_hs.h"
#include "host/ble_store.h"
#include "services/gatt/ble_svc_gatt.h"
#include "driver/i2s_std.h"
#if __has_include("secrets.h")
#include "secrets.h"  // a developer's build (the first lab's): its key, public key and network, as before rec-12
#define OCTO_DEV_SECRETS 1
#endif
#include "octo_crypto.h"
#include "octo_display.h"
#include "octo_settings.h"
#include "octo_update.h"
#include "octo_filler.h"  // the filler cue (B20, rec-24); its model is data since rec-27 (B29), so every build has it
#include "octo_kws_blob.h"
#include <esp_partition.h>
#if __has_include("octo_filler_model.h")
#include "octo_filler_model.h"  // the owner's model built in too, which `octo device flash` copies in (as before rec-27)
#define OCTO_FILLER_BUILTIN 1
#endif
#include <Wire.h>

const char *FW = "rec-27";
// Its name on the network: "octo-" and the last two bytes of its Wi-Fi address, from the chip's fuses, as its
// Bluetooth name (rec-16, review 8 Oct finding 12: every recorder was octo-rec.local, so two in one home collided).
// The Mac learns it from "device" in the status, and finds a recorder that changed name by its Bluetooth name.
char hostName[16] = "octo-rec";
const int PDM_CLK = 42, PDM_DATA = 41;
const int RATE = 16000;
const int SD_CS = 21;
// Status light: an external LED on D0 (GPIO1) through ~330 ohms to GND. The XIAO's own LED is the
// SD card's chip-select and can't be used. With nothing wired to D0 the patterns simply go nowhere.
const int LED_PIN = D0;
const float LED_MAX = 0.6f;         // full brightness is glaring at night; patterns peak here
// The BOOT button (GPIO0, the "B" beside USB-C) is an ordinary input once the board is running.
// Holding it while the board powers up or resets is still "download mode" (that's the chip).
const int BUTTON_PIN = 0;
// The XIAO Expansion Board's user button (D1, to GND when pressed; Seeed's examples): the same as B,
// and much easier to press. With nothing on D1 the pull-up keeps it "not pressed".
const int BUTTON2_PIN = D1;
const uint32_t HOLD_TOGGLE_MS = 1000;  // hold, then let go: recording on/off (and nothing else: power
                                       // off from the button was taken out in rec-8, see buttonLoop).
                                       // 2 s until rec-19 (Jay, 19 Oct: "a little long")
const uint32_t HOLD_CONFIRM_MS = 2000;  // "Allow …?" (rec-16): rare and deliberate, so it stays at 2 s

const uint32_t CHUNK_SAMPLES = 60 * RATE;      // 60 s, 1.92 MB
const uint32_t FLUSH_MS = 1000;                // at most ~1 s lost to a power cut
const size_t RING_BYTES = 8 * RATE * 2;        // 8 s of audio in PSRAM
const size_t BLOCK = 4096;                     // writer's unit, 128 ms
const uint64_t MIN_FREE_BYTES = 64ull << 20;   // "card full" below this; never overwrite audio
const uint32_t NONCE_MS = 60000;
const size_t MANIFEST_PAGE = 32768;
// Longer than anything the loop or the writer does without checking in: a card retry with re-initialising,
// a remount, a chunk piece to a Mac that stopped taking it (up to 10 s, sendChunk).
const uint32_t WATCHDOG_MS = 30000;

// Where the loop and the writer were last, and when (rec-20). RTC memory: kept across a watchdog restart,
// not across power-off. A watchdog boot writes it to its manifest; whichever has the older time was stuck.
struct Stuck { uint32_t magic; char loop[16], writer[16]; uint32_t loopMs, writerMs; };
RTC_NOINIT_ATTR Stuck stuck;
const uint32_t STUCK_MAGIC = 0x0c70d09;
// Macros, not a function: a function here would put Arduino's generated prototypes above the types they use.
#define LOOP_AT(w) (strncpy(stuck.loop, (w), 15), stuck.loopMs = millis())
#define WRITER_AT(w) (strncpy(stuck.writer, (w), 15), stuck.writerMs = millis())

Preferences prefs;
I2SClass i2s;
WebServer server(80);
StreamBufferHandle_t ring;
StaticStreamBuffer_t ringStruct;
SemaphoreHandle_t manifestLock;

uint32_t boot = 0;
char bootDir[16] = "";
volatile bool wantOn = true;       // the UI's on/off, kept across restarts (D9)
volatile bool capturing = false;   // reader feeds the ring buffer
volatile bool sdOk = false;
volatile bool cardFull = false;
volatile bool micOk = true;
volatile const char *lastError = "";
volatile uint32_t droppedSamples = 0;
volatile uint32_t maxFill = 0;
volatile uint32_t openSeq = UINT32_MAX;  // chunk being written; never offered to the Mac
volatile uint32_t closedChunks = 0;
volatile uint32_t seg = 0;               // continuous stretch of recording within this boot
volatile uint32_t writeHist[5] = {0};    // SD write+flush times: <10, <50, <200, <1000, >=1000 ms
volatile uint32_t worstWriteMs = 0;
volatile uint32_t sdFailures = 0;           // card errors this boot
volatile const char *lastSdError = "";
volatile uint32_t lastSdErrorMs = 0, lastSdErrorTook = 0;
volatile bool recovering = false;           // card failed mid-recording: keep capturing into the ring while remounting
// Chunks the writer abandoned after a card error. loop() seals them (header and checksum from what
// reached the card), so the next boot doesn't have to and the Mac can pull them.
volatile uint32_t repairQueue[8];
volatile uint8_t repairHead = 0, repairTail = 0;
// What was going on at each of those errors, written on the chunk's "repaired" line (rec-17): from rec-14 the
// card failed 11-15 times an hour, against 0-4 before, and the manifest didn't say why (learnings, 9 Oct).
// err: errno of the failed call. EIO (5) the card's reply to the write itself; EBADF (9) the open file was
// invalidated, which FatFs does when another task's card call finds the card not answering and re-initialises it.
struct SdErrorAt { const char *why; int err; uint32_t at, took; bool ble, adv, wifi, serving; };
SdErrorAt repairWhy[8];
volatile bool serving = false;  // loop() is answering a Mac's request (a chunk being pulled reads the card)
uint32_t nextSeq = 0;                        // next chunk number in this boot
int mountAttempts = 0;
bool clockSet = false;
uint8_t key[32];
bool keyOk = false;
uint8_t macPub[32];                // the paired Mac's public key: chunks are encrypted to it
bool pubOk = false;
volatile bool paired = false;      // a key and a public key: it may record and join Wi-Fi
oc_nets nets;                      // the Wi-Fi networks, newest first (octo_settings.h)
int netTry = 0;                    // which one the next attempt joins
bool bleUp = false, bleConnected = false, bleAdvertising = false;
void showFor(uint32_t ms);
void setupBle();
void bleTapped();
void bleNetsSnapshot();
void bleGoodbye();
String jsonText(const char *s);
bool setOn(bool on);
extern volatile uint32_t helloUntil;
volatile uint32_t i2sLost = 0;  // samples the I2S driver dropped before the reader took them (rec-15)
String reqNonce;
// One nonce per client (by IP): a phone or laptop calling /hello no longer wipes the Mac's (M41).
struct Nonce { uint32_t ip; String value; uint32_t at; };
Nonce nonces[4];
uint8_t bootKey[32];               // this boot's chunk key (octo_crypto.h); never written anywhere
bool encrypting = false;
char bootId[17] = "";              // random per boot: a reused boot number is still told apart (M42)
// Updates, over Wi-Fi (rec-2) or USB (rec-13), one at a time.
const uint32_t CONFIRM_WITHIN_MS = 10 * 60 * 1000;  // new firmware not reached by the Mac by then: roll back
const uint32_t UPDATE_IDLE_MS = 60000;              // an update with no data for this long is abandoned
bool firmwarePending = false;      // running an update not yet kept (a signed request, or K over USB)
bool updating = false;
enum { UPD_WIFI = 1, UPD_USB = 2 };
int updateVia = 0;                 // which way the update in progress is coming
size_t updateSize = 0, updateDone = 0;
uint8_t updateSha[32];
mbedtls_sha256_context updateCtx;
const size_t UPDATE_BUF = 12288;
uint8_t *updateBuf = nullptr;      // a decoded piece, either way; in PSRAM (rec-25): only an update uses it
uint32_t updateAt = 0, restartAt = 0, rejectAt = 0;
// What the display says after an update (rec-10): an od_update_result, shown until updResultUntil.
// "trying" in prefs is set just before restarting into new firmware and cleared when the Mac confirms
// it, so finding it set on a boot that isn't on trial means the bootloader went back: rolled back.
int updResult = -1;
uint32_t updResultUntil = 0;
bool rolledBack = false;
const uint32_t UPD_DONE_MS = 6000, UPD_BAD_MS = 20000;

// ---- small helpers ----------------------------------------------------------------------------

String hex(const uint8_t *b, size_t n) {
  static const char *d = "0123456789abcdef";
  String s;
  s.reserve(n * 2);
  for (size_t i = 0; i < n; i++) { s += d[b[i] >> 4]; s += d[b[i] & 15]; }
  return s;
}

bool unhex(const char *s, uint8_t *out, size_t n) {
  if (strlen(s) != n * 2) return false;
  for (size_t i = 0; i < n * 2; i++) {
    char c = tolower(s[i]);
    int v = isdigit(c) ? c - '0' : (c >= 'a' && c <= 'f') ? c - 'a' + 10 : -1;
    if (v < 0) return false;
    out[i / 2] = i % 2 ? out[i / 2] | v : v << 4;
  }
  return true;
}

void put16(uint8_t *p, uint16_t v) { p[0] = v; p[1] = v >> 8; }
void put32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = v >> (8 * i); }

void wavHeader(uint8_t *h, uint32_t dataBytes) {
  memcpy(h, "RIFF", 4); put32(h + 4, 36 + dataBytes); memcpy(h + 8, "WAVEfmt ", 8);
  put32(h + 16, 16); put16(h + 20, 1); put16(h + 22, 1); put32(h + 24, RATE); put32(h + 28, RATE * 2);
  put16(h + 32, 2); put16(h + 34, 16); memcpy(h + 36, "data", 4); put32(h + 40, dataBytes);
}

// Signed request bodies are compact JSON from the Mac, so a lookup by key is enough.
long long jnum(const String &body, const char *k, long long dflt = -1) {
  String pat = String("\"") + k + "\":";
  int i = body.indexOf(pat);
  return i < 0 ? dflt : atoll(body.c_str() + i + pat.length());
}

const char *resetReason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_USB: return "usb";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: case ESP_RST_TASK_WDT: case ESP_RST_WDT: return "watchdog";
    default: return "other";
  }
}

bool appendManifest(const char *dir, const String &line) {
  xSemaphoreTake(manifestLock, portMAX_DELAY);
  File f = SD.open(String(dir) + "/manifest.jsonl", FILE_APPEND);
  bool ok = f && f.print(line + "\n") == line.length() + 1;
  if (f) f.close();
  xSemaphoreGive(manifestLock);
  return ok;
}

// ---- SD card ----------------------------------------------------------------------------------

// rec-18: a failed card read or write is tried again here, below FatFs, before it becomes an error. On 9 Oct
// (rec-17, learnings) every card error was EIO from the write itself, 1-13 ms in (not a timeout), at 14-15 an hour
// on the Mac's USB with or without the phone: the card's reply after the data is wrong now and then. FatFs marks a
// file failed after one, so each cost a chunk (split, sealed by repairChunk, sometimes empty). Writing the same
// sectors again is harmless, and the Mac's checksum still catches a chunk that's wrong on the card. Arduino's SD
// driver (sd_diskio.cpp, core 3.3.12) doesn't declare these in a header; they're its own functions.
DSTATUS ff_sd_initialize(uint8_t pdrv);
DSTATUS ff_sd_status(uint8_t pdrv);
DRESULT ff_sd_read(uint8_t pdrv, uint8_t *buffer, DWORD sector, UINT count);
DRESULT ff_sd_write(uint8_t pdrv, const uint8_t *buffer, DWORD sector, UINT count);
DRESULT ff_sd_ioctl(uint8_t pdrv, uint8_t cmd, void *buff);
const int SD_TRIES = 3;
volatile uint32_t sdRetries = 0, sdRetryFails = 0;  // operations that needed another try; those that never worked
struct SdRetryAt { uint32_t at; char op; uint8_t tries; bool ok; };
SdRetryAt retryLog[8];  // written to the manifest by loop()
volatile uint8_t retryHead = 0, retryTail = 0;

void noteRetry(char op, int tries, bool ok) {
  sdRetries++;
  if (!ok) sdRetryFails++;
  if ((uint8_t)(retryHead - retryTail) < 8) {
    retryLog[retryHead % 8] = {millis(), op, (uint8_t)tries, ok};
    retryHead++;
  }
}

// After a failure the driver may have marked the card not ready ("Card Failed!"): initialise it again first
// (a no-op otherwise), as FatFs would on its next mount check.
DRESULT sdWriteTried(uint8_t pdrv, const uint8_t *buf, uint32_t sector, unsigned count) {
  DRESULT r = ff_sd_write(pdrv, buf, sector, count);
  int tries = 1;
  for (; r != RES_OK && tries < SD_TRIES; tries++) {
    ff_sd_initialize(pdrv);
    r = ff_sd_write(pdrv, buf, sector, count);
  }
  if (tries > 1) noteRetry('w', tries, r == RES_OK);
  return r;
}

DRESULT sdReadTried(uint8_t pdrv, uint8_t *buf, uint32_t sector, unsigned count) {
  DRESULT r = ff_sd_read(pdrv, buf, sector, count);
  int tries = 1;
  for (; r != RES_OK && tries < SD_TRIES; tries++) {
    ff_sd_initialize(pdrv);
    r = ff_sd_read(pdrv, buf, sector, count);
  }
  if (tries > 1) noteRetry('r', tries, r == RES_OK);
  return r;
}

// FatFs asks for the status before most calls; a wrong reply there reads as "not initialised", and FatFs then
// initialises the card again, which invalidates every open file (the chunk being written, as errno EBADF).
DSTATUS sdStatusTried(uint8_t pdrv) {
  DSTATUS st = ff_sd_status(pdrv);
  int tries = 1;
  for (; (st & STA_NOINIT) && tries < SD_TRIES; tries++) st = ff_sd_status(pdrv);
  if (tries > 1) noteRetry('s', tries, !(st & STA_NOINIT));
  return st;
}

const ff_diskio_impl_t sdTriedImpl = {&ff_sd_initialize, &sdStatusTried, &sdReadTried, &sdWriteTried, &ff_sd_ioctl};

bool mountCard(int attempts) {
  for (int a = 1; a <= attempts; a++) {
    mountAttempts = a;
    // The drive SD.begin() will take: the first free one (sdcard_init asks the same).
    BYTE pdrv = FF_DRV_NOT_USED;
    ff_diskio_get_drive(&pdrv);
    // 10 open files: the writer holds a chunk and the manifest while the API walks directories.
    if (SD.begin(SD_CS, SPI, 4000000, "/sd", 10)) {
      // Our retrying read and write in place of the driver's. Nothing else touches the card yet: setup() runs
      // before the writer starts, and loop() remounts only while sdOk is false, so the writer waits.
      if (pdrv != FF_DRV_NOT_USED) ff_diskio_register(pdrv, &sdTriedImpl);
      return true;
    }
    SD.end();
    // Bench: twice after a reset the card didn't answer at all, and waiting before mounting fixed
    // it. Cold power-on hasn't been tested yet, so retry with a pause rather than give up.
    delay(300);
  }
  return false;
}

// The loop task's card reads (repairChunk, sendChunk): never at once, so one buffer in internal RAM, not two (rec-25).
uint8_t loopBuf[4096];

// Seal a chunk that power loss left open: header sizes from the file length, checksum read back.
void repairChunk(const String &dir, uint32_t seq, bool newline, const String &why = "") {
  char path[40];
  snprintf(path, sizeof path, "%s/s%05lu.wav", dir.c_str(), (unsigned long)seq);
  File f = SD.open(path, "r+");
  if (!f) return;
  size_t size = f.size();
  uint32_t bytes = size > 44 ? (size - 44) & ~1u : 0;
  String pre = newline ? "\n" : "";
  if (!bytes) {
    f.close();
    SD.remove(path);
    appendManifest(dir.c_str(), pre + "{\"seq\":" + seq + ",\"samples\":0,\"repaired\":true" + why + "}");
    return;
  }
  mbedtls_sha256_context sha;
  mbedtls_sha256_init(&sha);
  mbedtls_sha256_starts(&sha, 0);
  f.seek(44);
  for (uint32_t done = 0; done < bytes;) {
    size_t n = f.read(loopBuf, min((uint32_t)sizeof loopBuf, bytes - done));
    if (!n) break;
    mbedtls_sha256_update(&sha, loopBuf, n);
    done += n;
  }
  uint8_t d[32];
  mbedtls_sha256_finish(&sha, d);
  mbedtls_sha256_free(&sha);
  uint8_t h[44];
  wavHeader(h, bytes);
  f.seek(0);
  f.write(h, 44);
  f.close();
  appendManifest(dir.c_str(), pre + "{\"seq\":" + seq + ",\"samples\":" + bytes / 2 + ",\"sha256\":\"" +
                 hex(d, 32) + "\",\"repaired\":true" + why + "}");
}

// Chunks in earlier boots that have a file but no "closed" line were cut off by power loss.
void repairOld() {
  std::vector<String> dirs;
  File root = SD.open("/rec");
  for (File d = root.openNextFile(); d; d = root.openNextFile())
    if (d.isDirectory() && String(d.path()) != bootDir) dirs.push_back(d.path());
  root.close();
  for (const String &dir : dirs) {
    std::vector<uint32_t> closed, files;
    bool newline = false;
    File m = SD.open(dir + "/manifest.jsonl");
    bool hasManifest = m;  // a closed File tests false, so remember it now
    if (m) {
      String all = m.readString();
      m.close();
      newline = all.length() && !all.endsWith("\n");  // a line cut off mid-write
      for (int i = all.indexOf("{\"seq\":"); i >= 0; i = all.indexOf("{\"seq\":", i + 1))
        closed.push_back(atol(all.c_str() + i + 7));
    }
    File d = SD.open(dir);
    int entries = 0;
    for (File f = d.openNextFile(); f; f = d.openNextFile()) {
      const char *name = f.name();
      entries++;
      if (name[0] == 's' && strstr(name, ".wav")) files.push_back(atol(name + 1));
    }
    d.close();
    if (!hasManifest && !entries) {  // an empty folder left by a power cut mid-delete: it would stall every sync (M40)
      SD.rmdir(dir);
      continue;
    }
    for (uint32_t seq : files) {
      if (std::find(closed.begin(), closed.end(), seq) != closed.end()) continue;
      repairChunk(dir, seq, newline);
      newline = false;
    }
  }
}

// First mount in this boot: pick a fresh directory (the boot counter lives in flash, which a full
// erase resets, so never reuse a directory that's already on the card) and repair earlier boots.
void startBoot() {
  SD.mkdir("/rec");
  if (!bootDir[0]) {
    do {
      boot++;
      snprintf(bootDir, sizeof bootDir, "/rec/b%05lu", (unsigned long)boot);
    } while (SD.exists(bootDir));
    prefs.putUInt("boot", boot);
    SD.mkdir(bootDir);
    uint8_t id[8];
    esp_fill_random(id, sizeof id);
    strcpy(bootId, hex(id, sizeof id).c_str());
    String enc = "";
    if (pubOk) {
      uint8_t eph[32], epk[32], shared[32];
      esp_fill_random(eph, sizeof eph);
      oc_x25519(epk, eph, OC_BASE);
      oc_x25519(shared, eph, macPub);
      mbedtls_sha256_context k;
      mbedtls_sha256_init(&k);
      mbedtls_sha256_starts(&k, 0);
      mbedtls_sha256_update(&k, (const uint8_t *)"octo-rec-v1", 11);
      mbedtls_sha256_update(&k, shared, 32);
      mbedtls_sha256_update(&k, epk, 32);
      mbedtls_sha256_finish(&k, bootKey);
      mbedtls_sha256_free(&k);
      memset(eph, 0, sizeof eph);  // gone: from here on only the Mac can derive the key
      memset(shared, 0, sizeof shared);
      encrypting = true;
      enc = String(",\"enc\":\"x25519-chacha20\",\"epk\":\"") + hex(epk, 32) + "\"";
    }
    appendManifest(bootDir, String("{\"boot\":") + boot + ",\"boot_id\":\"" + bootId + "\",\"fw\":\"" + FW +
                   "\",\"reset\":\"" + resetReason() + "\",\"at_ms\":" + millis() + ",\"mount_attempts\":" +
                   mountAttempts + enc + "}");
    repairOld();
  } else {
    SD.mkdir(bootDir);
    appendManifest(bootDir, String("{\"remounted\":true,\"at_ms\":") + millis() + ",\"after\":\"" +
                   (const char *)lastError + "\",\"failed_at_ms\":" + lastSdErrorMs + "}");
  }
  cardFull = false;
  lastError = "";
  recovering = false;
  sdOk = true;
}

// ---- audio ------------------------------------------------------------------------------------

// The I2S driver's queue overflowed: a DMA buffer of audio was dropped before the reader took it (16-bit
// samples). Before rec-15 nothing counted this (review 8 Oct, C6).
// A hole the reader left (ring full), and where: after the first `holeAt` samples it ever queued (rec-25). The writer
// adds it to the timeline once it has written those, so the audio held in the ring keeps its time; before, the hole
// went in first, and up to 8 s of audio after a card failure was dated late (or, if the remount gave up, put at the
// start of the next recording). shortcut: a second hole before the writer reaches the first joins it, so the audio
// between them comes after both; exact would need a queue of holes, worth it only if `dropped` shows it happening.
portMUX_TYPE dropMux = portMUX_INITIALIZER_UNLOCKED;
volatile uint32_t queuedSamples = 0, holeAt = 0, holeSamples = 0;

static bool IRAM_ATTR onI2sOverflow(i2s_chan_handle_t, i2s_event_data_t *e, void *) {
  i2sLost += e->size / 2;
  return false;
}

void readerTask(void *) {
  static int16_t buf[512];  // 32 ms
  float px = 0, py = 0;
  for (;;) {
    size_t n = i2s.readBytes((char *)buf, sizeof buf) / 2;
    // DC blocker (~13 Hz): the PDM mic's zero level wanders below 5 Hz with more energy than
    // speech (CLAUDE.md). Removing it here keeps headroom; the Mac high-passes at 80 Hz anyway.
    for (size_t i = 0; i < n; i++) {
      float x = buf[i], y = x - px + 0.995f * py;
      px = x;
      py = y;
      buf[i] = (int16_t)constrain(lroundf(y), -32768L, 32767L);
    }
    if (!capturing || !n) continue;
    filler::feed(buf, n);  // only while recording; never waits
    // Whole blocks only, so the stream never splits a sample.
    if (xStreamBufferSpacesAvailable(ring) >= n * 2) {
      xStreamBufferSend(ring, buf, n * 2, 0);
      queuedSamples += n;
    } else {
      portENTER_CRITICAL(&dropMux);
      if (!holeSamples) holeAt = queuedSamples;
      holeSamples += n;
      portEXIT_CRITICAL(&dropMux);
      droppedSamples += n;
    }
  }
}

// Writer-task state.
File chunk;
volatile bool chunkOpen = false;
uint32_t chunkSamples = 0, chunkDropped0 = 0, lastFlush = 0;
uint64_t chunkStartMs = 0, segStartMs = 0, segSamples = 0;
mbedtls_sha256_context sha;
// Audio written since the last flush. A card error can lose it from the file (the size on the card
// is only updated by a flush), so it's kept here and written again at the start of the next chunk.
const size_t TAIL_BYTES = 64 * 1024;  // > 1 s at the recording rate + a block; the writer flushes before it's full
uint8_t *tail = nullptr, *redoBuf = nullptr;
size_t tailBytes = 0;
uint32_t flushedSamples = 0;

void timed(uint32_t t0) {
  uint32_t ms = millis() - t0;
  writeHist[ms < 10 ? 0 : ms < 50 ? 1 : ms < 200 ? 2 : ms < 1000 ? 3 : 4]++;
  if (ms > worstWriteMs) worstWriteMs = ms;
}

// A card error: close the chunk where it is and move on to a new one. Never reopen it -- FILE_WRITE
// would truncate what's already on the card. Returns how many bytes of the chunk didn't make it into
// the file; they're in redoBuf, to go first into the next chunk so nothing is lost.
size_t abandonChunk(const char *why, uint32_t took, int err) {
  lastSdError = why;
  lastSdErrorMs = millis();
  lastSdErrorTook = took;
  sdFailures++;
  Serial.printf("SD error: %s after %lu ms (chunk %lu, at %lu ms)\n", why, (unsigned long)took, (unsigned long)nextSeq,
                (unsigned long)millis());
  size_t redo = 0;
  if (chunkOpen) {
    chunk.close();
    mbedtls_sha256_free(&sha);
    // How much reached the file: at least what was flushed, at most what was written.
    char path[40];
    snprintf(path, sizeof path, "%s/s%05lu.wav", bootDir, (unsigned long)nextSeq);
    File f = SD.open(path);
    uint32_t onCard = f && f.size() > 44 ? (f.size() - 44) / 2 : 0;
    if (f) f.close();
    onCard = constrain(onCard, flushedSamples, chunkSamples);
    size_t from = (onCard - flushedSamples) * 2;
    redo = tailBytes > from ? tailBytes - from : 0;
    memcpy(redoBuf, tail + from, redo);
    segSamples -= chunkSamples - onCard;  // the redo puts them back
  }
  chunkOpen = false;
  openSeq = UINT32_MAX;
  tailBytes = 0;
  if ((uint8_t)(repairHead - repairTail) < 8) {
    repairWhy[repairHead % 8] = {why, err, lastSdErrorMs, took, bleConnected, bleAdvertising, WiFi.status() == WL_CONNECTED, serving};
    repairQueue[repairHead++ % 8] = nextSeq;
  }
  nextSeq++;
  return redo;
}

// Retrying in a new chunk didn't help either: remount. Audio keeps going into the ring buffer.
void cardFailed(const char *why) {
  lastError = why;
  recovering = true;
  sdOk = false;  // loop() remounts
}

bool openChunk() {
  if (SD.totalBytes() - SD.usedBytes() < MIN_FREE_BYTES) {
    cardFull = true;
    return false;
  }
  char path[40];
  snprintf(path, sizeof path, "%s/s%05lu.wav", bootDir, (unsigned long)nextSeq);
  uint32_t t0 = millis();
  errno = 0;
  chunk = SD.open(path, FILE_WRITE);
  uint8_t h[44];
  wavHeader(h, CHUNK_SAMPLES * 2);  // full-chunk sizes until closed; the Mac trusts the file length
  if (encrypting) oc_crypt_at(bootKey, boot, nextSeq, 0, h, 44);
  if (!chunk || chunk.write(h, 44) != 44) {
    int err = errno;
    if (chunk) chunk.close();
    abandonChunk("chunk_open_failed", millis() - t0, err);
    return false;
  }
  chunkOpen = true;
  openSeq = nextSeq;
  chunkSamples = flushedSamples = 0;
  tailBytes = 0;
  chunkDropped0 = droppedSamples;
  chunkStartMs = segStartMs + segSamples * 1000 / RATE;
  mbedtls_sha256_init(&sha);
  mbedtls_sha256_starts(&sha, 0);
  lastFlush = millis();
  appendManifest(bootDir, String("{\"open\":") + nextSeq + ",\"seg\":" + seg + ",\"start_ms\":" + chunkStartMs + "}");
  return true;
}

void closeChunk() {
  uint8_t h[44];
  wavHeader(h, chunkSamples * 2);
  if (encrypting) oc_crypt_at(bootKey, boot, nextSeq, 0, h, 44);
  chunk.seek(0);
  chunk.write(h, 44);
  chunk.close();
  chunkOpen = false;
  uint8_t d[32];
  mbedtls_sha256_finish(&sha, d);
  mbedtls_sha256_free(&sha);
  appendManifest(bootDir, String("{\"seq\":") + nextSeq + ",\"seg\":" + seg + ",\"start_ms\":" + chunkStartMs +
                 ",\"samples\":" + chunkSamples + ",\"sha256\":\"" + hex(d, 32) + "\",\"dropped\":" +
                 (droppedSamples - chunkDropped0) + "}");
  openSeq = UINT32_MAX;
  nextSeq++;
  closedChunks++;
}

// Give up on a block (the card needs remounting). Counted as dropped, and the timeline moves on by it
// here, where it was lost (no chunk is open by now): the time still passed.
bool lose(size_t bytes, const char *why) {
  droppedSamples += bytes / 2;
  segSamples += bytes / 2;
  cardFailed(why);
  return false;
}

bool writeAudio(const uint8_t *p, size_t n) {
  int failures = 0;
  const uint8_t *redo = nullptr;
  size_t redoN = 0;
  while (n || redoN) {
    if (!chunkOpen && !openChunk()) {
      if (cardFull) return false;
      if (++failures > 2) return lose(n + redoN, "chunk_open_failed");
      continue;
    }
    const uint8_t *src = redoN ? redo : p;
    size_t k = min(min(redoN ? redoN : n, (size_t)(CHUNK_SAMPLES - chunkSamples) * 2), BLOCK);
    // What goes on the card: encrypted at its place in the file when the boot has a key.
    static uint8_t enc[BLOCK];
    const uint8_t *out = src;
    if (encrypting) {
      memcpy(enc, src, k);
      oc_crypt_at(bootKey, boot, nextSeq, 44 + chunkSamples * 2, enc, k);
      out = enc;
    }
    uint32_t t0 = millis();
    errno = 0;
    if (chunk.write(out, k) != k) {
      int err = errno;
      // The block is still in RAM, and so is everything since the last flush: start a new chunk with them.
      if (redoN) return lose(n + redoN + abandonChunk("write_failed", millis() - t0, err), "write_failed");
      redoN = abandonChunk("write_failed", millis() - t0, err);
      redo = redoBuf;
      if (++failures > 2) return lose(n + redoN, "write_failed");
      continue;
    }
    timed(t0);
    mbedtls_sha256_update(&sha, out, k);  // from RAM, as stored, so a bad card write shows up as a mismatch
    if (tailBytes + k <= TAIL_BYTES) {
      memcpy(tail + tailBytes, src, k);
      tailBytes += k;
    }
    chunkSamples += k / 2;
    segSamples += k / 2;
    if (redoN) { redo += k; redoN -= k; } else { p += k; n -= k; }
    if (chunkSamples >= CHUNK_SAMPLES) closeChunk();
  }
  return true;
}

void writerTask(void *) {
  static uint8_t blk[BLOCK];
  uint32_t takenSamples = 0;  // every sample taken from the ring, against the reader's queuedSamples
  esp_task_wdt_add(nullptr);
  for (;;) {
    esp_task_wdt_reset();
    bool should = wantOn && paired && (sdOk || recovering) && !cardFull && micOk;
    if (should && !capturing) {
      // Only once the stretch before is all on the card (rec-25): what's still in the ring is its audio, and written
      // into this one it would be dated now. After a card failure that wait lasts until a card mounts again.
      if (!chunkOpen && xStreamBufferIsEmpty(ring)) {
        seg++;
        segSamples = 0;
        segStartMs = millis();
        capturing = true;
      }
    } else if (!should && capturing) {
      capturing = false;  // what's already in the ring buffer is still written below
    }
    if (!sdOk) {  // remounting: leave the audio in the ring buffer
      WRITER_AT("no_card");
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }
    // Samples the reader had to drop (ring full: the card stalled for 8 s) still took time. Once the audio queued
    // before them is written, end the chunk so the next one starts at the right moment; the Mac fills the hole with
    // silence. Until then, take no more than reaches the hole.
    size_t want = BLOCK;
    uint32_t hole = 0;
    portENTER_CRITICAL(&dropMux);
    if (holeSamples) {
      int32_t ahead = (int32_t)(holeAt - takenSamples);
      if (ahead <= 0) { hole = holeSamples; holeSamples = 0; }
      else want = min(want, (size_t)ahead * 2);
    }
    portEXIT_CRITICAL(&dropMux);
    if (hole) {
      if (chunkOpen) closeChunk();
      segSamples += hole;
    }
    WRITER_AT("wait");
    size_t n = xStreamBufferReceive(ring, blk, want, pdMS_TO_TICKS(100));
    if (n) {
      takenSamples += n / 2;
      uint32_t fill = xStreamBufferBytesAvailable(ring) + n;
      if (fill > maxFill) maxFill = fill;
      WRITER_AT("write");
      writeAudio(blk, n);  // all of it: what's in the ring is recorded audio, even after recording stopped
    }
    // Also before the tail overflows (rec-25): emptying a backlog after a stall writes up to 8 s in a second, and the
    // audio past the tail's 64 KB had no copy if the card then failed (lost, uncounted, the timeline cut short).
    if (chunkOpen && (millis() - lastFlush >= FLUSH_MS || tailBytes + BLOCK > TAIL_BYTES)) {
      WRITER_AT("flush");
      uint32_t t0 = millis();
      chunk.flush();  // updates the file's size on the card: this is what survives a power cut
      flushedSamples = chunkSamples;
      tailBytes = 0;
      timed(t0);
      lastFlush = millis();
    }
    if (!capturing && chunkOpen && xStreamBufferIsEmpty(ring)) { WRITER_AT("close"); closeChunk(); }
  }
}

// ---- status -----------------------------------------------------------------------------------

// Review 8 Oct, finding 13 (rec-16): a new firmware on trial is kept once it works, not just once it answers.
// Updates are refused while recording, so it isn't recording yet: it works once its microphone has started and its
// card hasn't failed (no card, or a full one, isn't this firmware's doing). Until then neither the Mac's signed
// requests nor K keep it, and if it never gets there the bootloader goes back after 10 minutes.
bool healthy() { return micOk && !(!sdOk && lastError[0]); }

const char *stateName() {
  if (!paired) return "not_paired";
  if (!micOk) return "mic_error";
  if (!sdOk) return lastError[0] ? "card_error" : "no_card";
  if (cardFull) return "card_full";
  if (!wantOn) return "off";
  return capturing ? "recording" : "starting";
}

// Audio files still on the card (pulled ones are deleted). The bench refuses to reflash while any
// are left: a bench sketch would end recording, and 01b_sd_wipe would erase them.
long chunkFilesOnCard() {
  if (!sdOk) return -1;
  long n = 0;
  File root = SD.open("/rec");
  for (File d = root ? root.openNextFile() : File(); d; d = root.openNextFile())
    if (d.isDirectory())
      for (File f = d.openNextFile(); f; f = d.openNextFile()) n += f.name()[0] == 's';
  return n;
}

#ifndef KWS_NAME
#define KWS_NAME "you know"  // the built-in model's filler: rec-24 to rec-26 had only that one
#endif
const size_t KWS_SLOT = 0x40000;
const uint32_t KWS_TRIAL_RUNS = 50;  // ~12 s of the cue on
const esp_partition_t *kwsPart = nullptr;
int8_t kwsSlot = -1, kwsPrev = -1;
bool kwsTrying = false;
String kwsReverted = "";             // the model this boot went back from, for the status
uint8_t *kwsBlob = nullptr;          // the loaded one, in PSRAM, for as long as it runs
char kwsSha[17] = "";                // the start of its SHA-256 ("" for the built-in)
int8_t kwsTo = -1;                   // a model being received: its slot, size, how much so far, its SHA-256
size_t kwsSize = 0, kwsDone = 0;
uint8_t kwsWantSha[32];

// The filler cue (B20): whether a model is loaded and which (rec-27), the setting, this recording's count, and how
// hard it works (the slowest step, since boot) and whether its memory is the slower kind.
String fillerFields() {
  return bench::kv("filler_model", filler::interp != nullptr) + "," + bench::kv("filler_models", true) + "," +
         bench::kvs("filler_name", filler::name) + "," + bench::kv("filler_slot", (long)kwsSlot) + "," +
         bench::kvs("filler_sha", kwsSha) + "," + bench::kvs("filler_before", kwsBefore().c_str()) + "," +
         bench::kvs("filler_reverted", kwsReverted.c_str()) + "," + bench::kv("filler_on", (bool)filler::on) + "," +
         bench::kv("filler_threshold", (float)filler::threshold, 3) + "," + bench::kv("filler_ready", (bool)filler::ready) +
         "," + bench::kv("fillers", (unsigned long)filler::cues) + "," +
         bench::kv("filler_work_max_ms", (unsigned long)(filler::workMaxUs / 1000)) + "," +
         bench::kv("filler_psram", (bool)filler::arenaPsram) + "," + bench::kv("filler_gaps", (unsigned long)filler::gaps);
}

// This recording's fillers for the screen: none while the cue is off.
uint32_t fillerCount() { return filler::on ? filler::cues : 0; }

// The owner's setting (B20), from the Mac or the phone: on/off, and the threshold if given (0.5-0.999). Kept.
bool setFiller(bool on, float threshold = -1) {
  if (!filler::interp) return false;  // no model
  filler::on = on;
  prefs.putBool("filler", on);
  if (threshold >= 0.5f && threshold < 1.0f) {
    filler::threshold = threshold;
    prefs.putFloat("filler_thr", threshold);
  }
  return true;
}

// ---- the filler cue's model as data (B29, rec-27) --------------------------------------------------------------
//
// Two slots on the spare flash (the partition table's unused 1.5 MB "spiffs", raw: no filesystem to format at boot
// against the watchdog), written by /filler/model/* (octo_kws_blob.h). Prefs keep kws_slot, the one in use (-1: the
// built-in, if this build has one), kws_prev, the one before (Undo), and kws_try, a new one on trial: 1 once it's
// installed, 2 once a boot has started with it, 0 after its first 50 good runs on what it heard. A boot that finds
// 2 after a crash (panic or watchdog) goes back to the one before with the cue off, and says so: a bad model at
// boot would stop all recording, as boots 78-126 did (21 Oct). A full release flash pads to 8 MB and erases both.


bool kwsHead(int slot, oc_kws_head *h, size_t *total) {
  if (!kwsPart) kwsPart = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_SPIFFS, nullptr);
  uint8_t b[OC_KWS_HEAD];
  if (slot < 0 || slot > 1 || !kwsPart || esp_partition_read(kwsPart, slot * KWS_SLOT, b, sizeof b) != ESP_OK) return false;
  *total = oc_kws_parse(b, KWS_SLOT, h);
  return *total > 0;
}

bool kwsBuiltin() {
#ifdef OCTO_FILLER_BUILTIN
  return true;
#else
  return false;
#endif
}

// The filler Undo would put back ("" if nothing).
String kwsBefore() {
  oc_kws_head h;
  size_t total;
  if (kwsPrev == kwsSlot) return "";
  if (kwsPrev < 0) return kwsBuiltin() ? KWS_NAME : "";
  return kwsHead(kwsPrev, &h, &total) ? h.name : "";
}

float kwsThreshold(int slot) {
  oc_kws_head h;
  size_t total;
  if (kwsHead(slot, &h, &total)) return h.threshold;
#ifdef OCTO_FILLER_BUILTIN
  return KWS_THRESHOLD;
#else
  return 0.96f;
#endif
}

// A slot's model (or the built-in) into the cue: its whole SHA-256 checked again, then filler::load's checks.
bool kwsLoad(int slot) {
  if (slot < 0) {
#ifdef OCTO_FILLER_BUILTIN
    return filler::load(kws_model, sizeof kws_model, &kws_fb[0][0], KWS_NAME);
#else
    return false;
#endif
  }
  oc_kws_head h;
  size_t total;
  if (!kwsHead(slot, &h, &total)) return false;
  uint8_t *b = (uint8_t *)heap_caps_aligned_alloc(16, total, MALLOC_CAP_SPIRAM), d[32];
  if (!b) return false;
  size_t fbAt = OC_KWS_HEAD + ((h.model_len + 3) & ~3u);
  if (esp_partition_read(kwsPart, slot * KWS_SLOT, b, total) != ESP_OK ||
      mbedtls_sha256(b + OC_KWS_HEAD, total - OC_KWS_HEAD, d, 0) != 0 || memcmp(d, h.sha, 32) ||
      !filler::load(b + OC_KWS_HEAD, h.model_len, (const float *)(b + fbAt), h.name)) {
    heap_caps_free(b);
    return false;
  }
  kwsBlob = b;
  for (int i = 0; i < 8; i++) snprintf(kwsSha + 2 * i, 3, "%02x", h.sha[i]);
  return true;
}

void kwsUse(int slot, int prev, bool trial) {
  prefs.putChar("kws_slot", slot);
  prefs.putChar("kws_prev", prev);
  prefs.putUChar("kws_try", trial ? 1 : 0);
  prefs.putFloat("filler_thr", kwsThreshold(slot));  // each model brings its own threshold
}

// At boot, after the card (for the manifest line): the model in use, or the one before if it can't be.
void fillerBoot() {
  kwsSlot = prefs.getChar("kws_slot", -1);
  kwsPrev = prefs.getChar("kws_prev", -1);
  uint8_t trying = prefs.getUChar("kws_try", 0);
  esp_reset_reason_t why = esp_reset_reason();
  bool crashed = why == ESP_RST_PANIC || why == ESP_RST_INT_WDT || why == ESP_RST_TASK_WDT || why == ESP_RST_WDT;
  if (!filler::init()) return;  // no memory for it: the status says so ("filler_ready")
  bool ok = !(trying == 2 && crashed) && kwsLoad(kwsSlot);
  if (!ok && kwsSlot >= 0) {
    oc_kws_head h;
    size_t total;
    kwsReverted = kwsHead(kwsSlot, &h, &total) ? h.name : "?";
    if (sdOk) appendManifest(bootDir, String("{\"filler_model_reverted\":\"") + jsonText(kwsReverted.c_str()) + "\",\"crashed\":" +
                                          (crashed ? "true" : "false") + ",\"at_ms\":" + millis() + "}");
    kwsUse(kwsPrev, -1, false);
    kwsSlot = kwsPrev;
    kwsPrev = -1;
    trying = 0;
    filler::on = false;  // the owner turns it on again, knowing
    prefs.putBool("filler", false);
    ok = kwsLoad(kwsSlot);
  } else if (trying == 1) {
    prefs.putUChar("kws_try", 2);
  }
  kwsTrying = ok && trying;
  filler::threshold = prefs.getFloat("filler_thr", kwsThreshold(kwsSlot));
  if (ok) filler::start();
}


String statusFields() {
  uint64_t total = sdOk ? SD.totalBytes() : 0, free = sdOk ? total - SD.usedBytes() : 0;
  return bench::kvs("device", hostName) + "," + bench::kvs("fw", FW) + "," + bench::kv("boot", (unsigned long)boot) +
         "," + bench::kvs("reset", resetReason()) + "," + bench::kv("uptime_ms", (unsigned long)millis()) + "," +
         bench::kvs("state", stateName()) + "," + bench::kv("want_on", (bool)wantOn) + "," +
         bench::kv("seg", (unsigned long)seg) + "," + bench::kv("open_seq", openSeq == UINT32_MAX ? -1L : (long)openSeq) +
         "," + bench::kv("chunks_closed", (unsigned long)closedChunks) + "," +
         bench::kv("dropped_samples", (unsigned long)droppedSamples) + "," +
         bench::kv("ring_max_ms", (unsigned long)(maxFill * 1000 / (RATE * 2))) + "," +
         bench::kv("worst_write_ms", (unsigned long)worstWriteMs) + ",\"write_ms_hist\":[" + writeHist[0] + "," +
         writeHist[1] + "," + writeHist[2] + "," + writeHist[3] + "," + writeHist[4] + "]," +
         bench::kv("card_total", total) + "," + bench::kv("card_free", free) + "," +
         bench::kv("mount_attempts", mountAttempts) + "," + bench::kvs("error", (const char *)lastError) + "," +
         bench::kv("sd_failures", (unsigned long)sdFailures) + "," + bench::kvs("last_sd_error", (const char *)lastSdError) + "," +
         bench::kv("last_sd_error_ms", (unsigned long)lastSdErrorMs) + "," +
         bench::kv("last_sd_error_took_ms", (unsigned long)lastSdErrorTook) + "," +
         bench::kv("clock_set", clockSet) + "," + bench::kv("wifi", WiFi.status() == WL_CONNECTED) + "," +
         bench::kv("rssi", WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0) + "," +
         bench::kv("battery_mv", -1) + "," + bench::kv("psram_free", (unsigned long)ESP.getFreePsram()) + "," +
         bench::kv("paired", (bool)paired) + "," + bench::kv("networks", nets.n) + "," +
         bench::kv("ble_connected", bleConnected) + "," + bench::kv("ble_advertising", bleAdvertising) + "," +
         // Which firmware slot is running, and whether it's on trial (rec-13): the Mac tells a new firmware
         // from one the recorder went back to by the slot, as both may say the same version.
         bench::kvs("slot", esp_ota_get_running_partition()->label) + "," + bench::kv("pending", firmwarePending) +
         "," + bench::kv("heap_free", (unsigned long)ESP.getFreeHeap()) + "," + bench::kv("heap_min", (unsigned long)ESP.getMinFreeHeap()) +
         "," + bench::kv("i2s_lost", (unsigned long)i2sLost) + "," + bench::kv("sd_retries", (unsigned long)sdRetries) +
         "," + bench::kv("sd_retry_fails", (unsigned long)sdRetryFails) + "," + fillerFields();
}

// Recording can't be switched on while an update arrives or the recorder is about to restart for one: the
// restart would cut the recording (review 8 Oct, C9). False when refused.
bool setOn(bool on) {
  if (on && (updating || restartAt || rejectAt)) return false;
  wantOn = on;
  prefs.putBool("on", on);
  return true;
}

// ---- settings (rec-12) -------------------------------------------------------------------------

// From flash; a developer's build then takes secrets.h's key, public key and network as before.
void loadSettings() {
  keyOk = prefs.getBytesLength("key") == sizeof key && prefs.getBytes("key", key, sizeof key) == sizeof key;
  pubOk = prefs.getBytesLength("pub") == sizeof macPub && prefs.getBytes("pub", macPub, sizeof macPub) == sizeof macPub;
  static uint8_t blob[1 + OC_MAX_NETS * (2 + OC_SSID_MAX + OC_PASS_MAX)];
  size_t n = prefs.getBytesLength("nets");
  if (!n || n > sizeof blob || prefs.getBytes("nets", blob, n) != n || !oc_nets_unpack(blob, n, &nets)) memset(&nets, 0, sizeof nets);
#ifdef OCTO_DEV_SECRETS
  keyOk = unhex(OCTO_KEY, key, sizeof key);
#ifdef OCTO_ENC_PUBKEY
  static const uint8_t devPub[32] = OCTO_ENC_PUBKEY;
  memcpy(macPub, devPub, sizeof macPub);
  pubOk = true;
#endif
  oc_nets_add(&nets, WIFI_SSID, WIFI_PASS);  // secrets.h's network, tried first; never stored
  paired = keyOk;  // before rec-2 a developer's build had no public key and recorded unencrypted
#else
  paired = keyOk && pubOk;
#endif
}

// The networks to flash, leaving out a developer build's own (it comes from secrets.h every boot).
void saveNets() {
  oc_nets keep = nets;
#ifdef OCTO_DEV_SECRETS
  oc_nets_del(&keep, WIFI_SSID);
#endif
  static uint8_t blob[1 + OC_MAX_NETS * (2 + OC_SSID_MAX + OC_PASS_MAX)];
  size_t n = oc_nets_pack(&keep, blob, sizeof blob);
  if (n) prefs.putBytes("nets", blob, n);
  netTry = 0;
  if (bleUp) bleNetsSnapshot();
}

// A JSON string's inside: quotes and backslashes escaped, control characters left out. Network names can
// hold anything (an SSID with a quote broke every status read before rec-15, C7).
String jsonText(const char *s) {
  String out;
  for (; *s; s++) {
    if (*s == '"' || *s == '\\') out += '\\';
    if ((uint8_t)*s >= 0x20) out += *s;
  }
  return out;
}

// The networks' names, as JSON (for the phone) or hex (for the Mac: any byte, no escaping).
String netNames(bool asHex) {
  String out = "[";
  for (int i = 0; i < nets.n; i++) {
    if (i) out += ",";
    if (asHex) out += "\"" + hex((const uint8_t *)nets.nets[i].ssid, strlen(nets.nets[i].ssid)) + "\"";
    else out += "\"" + jsonText(nets.nets[i].ssid) + "\"";
  }
  return out + "]";
}

// A line from USB, for the two commands that take one (P and N); empty after `ms` with nothing.
String readLine(uint32_t ms) {
  String s;
  uint32_t t0 = millis();
  while (millis() - t0 < ms) {
    while (Serial.available()) {
      char c = Serial.read();
      if (c == '\n') return s;
      if (c != '\r' && s.length() < 400) s += c;
    }
    esp_task_wdt_reset();  // waiting for the Mac's line can take as long as the watchdog
    delay(5);
  }
  return String();
}

// After a USB command's line: an optional last field, the signature of what came before it (rec-16).
String takeSignature(String &line, int fields) {
  int at = -1;
  for (int i = 0; i < fields; i++) {
    at = line.indexOf(' ', at + 1);
    if (at < 0) return String();
  }
  String sig = line.substring(at + 1);
  line = line.substring(0, at);
  return sig;
}

// P: pair with a Mac. The Mac sends "<key hex> <its public key hex>" (octo/device.py, pair). Only over
// USB: someone has to hold the recorder and the Mac to do it. rec-16 (review 8 Oct, C16): a recorder that's
// already paired wants, after them, HMAC(its key, "PAIR\n" + the two) from the Mac it knows; without that,
// someone holding it has to allow it on its screen, so a computer it's plugged into can't take it over.
void usbPair() {
  bench::result(bench::kvs("send", "pair") + "," + bench::kv("signed", true));  // rec-16: sign it, if you can
  String line = readLine(30000);
  String sig = takeSignature(line, 2);
  uint8_t k[32], pub[32];
  if (!oc_parse_pair(line.c_str(), k, pub)) { bench::result(bench::kv("paired", false) + "," + bench::kvs("error", "bad_pairing_line")); return; }
  if (paired && !sameText(sig, hmacHex("PAIR\n" + line)) && !confirmOnDevice("new Mac?")) {
    memset(k, 0, sizeof k);
    bench::result(bench::kv("paired", false) + "," + bench::kvs("error", "not_confirmed"));
    return;
  }
  prefs.putBytes("key", k, sizeof k);
  prefs.putBytes("pub", pub, sizeof pub);
  memset(k, 0, sizeof k);
  loadSettings();
  if (paired && !bleUp) setupBle();
  bench::result(statusFields());
}

// N: change the networks over USB: "add <ssid hex> <password hex>", "del <ssid hex>" or "list".
void usbNetworks() {
  bench::result(bench::kvs("send", "net"));
  String line = readLine(30000);
  bool ok = line == "list" || oc_net_command(&nets, line.c_str(), line.length(), 1);
  if (ok && line != "list") saveNets();
  bench::result(bench::kv("ok", ok) + ",\"networks_hex\":" + netNames(true));
}

// ---- Bluetooth for the phone app (rec-12; rec-15: docs/recorder-ble.md) -------------------------
//
// rec-15 (docs/review-2026-10-08-firmware.md): NimBLE's callbacks run on its own task (core 0, high priority)
// and no longer touch the card, Wi-Fi, the settings or anything loop() changes. loop() builds the status and
// the network names into the buffers below; the callbacks only copy them. Recording on/off and network
// changes are handed to loop(), which applies them before the phone's write is answered. Notifications go
// from loop() to phones that paired with the code and asked for them. Before a restart it chose (an update,
// going back) it says so and lets the phone go, so the app shows "Restarting" at once.
//
// Advertising (8 Oct): quick for a minute after starting, a tap or a phone leaving; slow (about once a
// second) for as long as a phone is paired, so the app reconnects whenever it's opened; off with none paired
// once the window has closed. New phones pair only within 10 minutes of a tap (or of starting, while none is
// paired): outside that the recorder shows no code, so nobody at the table can pair.

const char *BLE_SERVICE = "6f63746f-0001-4c61-8000-6f63746f7265";
const char *BLE_STATUS = "6f63746f-0002-4c61-8000-6f63746f7265";    // read, notify: JSON
const char *BLE_CONTROL = "6f63746f-0003-4c61-8000-6f63746f7265";   // write: "on" or "off"
const char *BLE_NETWORKS = "6f63746f-0004-4c61-8000-6f63746f7265";  // read: JSON names; write: add/del
const uint32_t BLE_PAIR_MS = 10 * 60 * 1000;  // new phones: within this of a tap (or of starting, none paired)
const uint32_t BLE_FAST_MS = 60 * 1000;       // quick to find after starting, a tap or a phone leaving
const uint16_t ADV_FAST = 244, ADV_SLOW = 1636;  // 152.5 ms and 1022.5 ms: Apple's recommended intervals
const uint32_t BLE_GATT_REV = 1;              // bump with every change to the service's table (Service Changed)
const uint32_t PASSKEY_SHOW_MS = 60000;
const int BLE_CONNS = 4;

BLEServer *bleServer = nullptr;
BLECharacteristic *bleStatusChr = nullptr, *bleNetsChr = nullptr;
char bleName[16] = "";
volatile uint32_t passkeyShown = 0, passkeyAt = 0;
volatile bool bleDropped = false;
uint32_t bleFastUntil = 0, blePairUntil = 0, bleLastNotify = 0, bleLastBuild = 0, bleBondCheck = 0;
bool bleHasBond = false, bleBye = false;
int bleAdvMode = 0;  // 0 off, 1 quick, 2 slow

// What loop() hands the Bluetooth task, and the links it knows (all under bleMux).
portMUX_TYPE bleMux = portMUX_INITIALIZER_UNLOCKED;
char bleFull[600], bleLive[240], bleNets[420];
uint16_t bleFullLen = 0, bleLiveLen = 0, bleNetsLen = 0;
struct BleConn { uint16_t handle; bool secure, subscribed; };
BleConn bleConns[BLE_CONNS];
int bleConnN = 0;

// Writes from the phone, for loop(): 'C' + "on"/"off" (or "filler on"/"filler off", rec-24), or 'N' + a network line.
struct BleCmd { uint32_t seq; uint16_t len; char text[176]; };
QueueHandle_t bleCmds = nullptr;
volatile uint32_t bleCmdSeq = 0, bleCmdDone = 0;

void bleKeep(char *dst, size_t cap, uint16_t &len, const String &v) {
  portENTER_CRITICAL(&bleMux);
  len = min((size_t)v.length(), cap);
  memcpy(dst, v.c_str(), len);
  portEXIT_CRITICAL(&bleMux);
}

// A characteristic's value from a buffer, on the Bluetooth task only (it serves the reads too).
void bleServe(BLECharacteristic *c, const char *src, const uint16_t &len) {
  static char tmp[sizeof bleFull];
  portENTER_CRITICAL(&bleMux);
  uint16_t n = len;
  memcpy(tmp, src, n);
  portEXIT_CRITICAL(&bleMux);
  c->setValue((uint8_t *)tmp, n);
}

int bleFind(uint16_t handle) {  // under bleMux
  for (int i = 0; i < bleConnN; i++)
    if (bleConns[i].handle == handle) return i;
  return -1;
}

int updPercent() {
  if (updating && updateSize) return (int)((uint64_t)updateDone * 100 / updateSize);
  return (restartAt || rejectAt) ? 100 : -1;
}

uint64_t cardTotal = 0, cardFree = 0;  // refreshed by loop(), never from the Bluetooth task
uint32_t cardAt = 0;

// The status: the whole of it for a read, the live part for notifications (at most the 253 bytes an iPhone's
// link carries in one). Both keep rec-12's fields, so an older app still reads them.
void bleBuild() {
  bool up = WiFi.status() == WL_CONNECTED;
  String live = String("{\"v\":2,") + bench::kvs("fw", FW) + "," + bench::kv("boot", (unsigned long)boot) + "," +
                bench::kvs("state", stateName()) + "," + bench::kv("want_on", (bool)wantOn) + "," +
                bench::kv("recording", (bool)capturing) + "," +
                bench::kv("rec_s", capturing ? (unsigned long)((millis() - segStartMs) / 1000) : 0UL) + "," +
                bench::kv("upd", updPercent()) + "," + bench::kv("restart", (bool)(restartAt || rejectAt || bleBye)) + "," +
                bench::kv("pending", firmwarePending) + "," + bench::kv("wifi", up) + "," + bench::kv("rssi", up ? WiFi.RSSI() : 0) + "}";
  String full = live.substring(0, live.length() - 1) + "," + bench::kvs("name", bleName) + "," +
                bench::kv("card_free_mb", (unsigned long)(cardFree >> 20)) + "," +
                bench::kv("card_total_mb", (unsigned long)(cardTotal >> 20)) + ",\"ssid\":\"" +
                jsonText(up ? WiFi.SSID().c_str() : "") + "\"," + bench::kv("networks", nets.n) + "," +
                bench::kv("clock_set", clockSet) + "," + bench::kv("battery_mv", -1) + "," +
                bench::kv("rolled_back", rolledBack) + "," + bench::kv("up_s", (unsigned long)(millis() / 1000)) + "," +
                bench::kvs("slot", esp_ota_get_running_partition()->label) + "," +
                bench::kv("i2s_lost", (unsigned long)i2sLost) + "," + bench::kv("heap_min", (unsigned long)ESP.getMinFreeHeap()) +
                // the filler cue (rec-24): whether this build has it, the setting, this recording's count
                "," + bench::kv("filler_model", filler::interp != nullptr) + "," + bench::kv("filler_on", (bool)filler::on) + "," +
                bench::kv("fillers", (unsigned long)filler::cues) +
                "}";
  bleKeep(bleLive, sizeof bleLive, bleLiveLen, live);
  bleKeep(bleFull, sizeof bleFull, bleFullLen, full);
}

void bleNetsSnapshot() { bleKeep(bleNets, sizeof bleNets, bleNetsLen, netNames(false)); }

// The live status to every phone that paired with the code and asked for it.
void bleNotify() {
  if (!bleStatusChr) return;
  char buf[sizeof bleLive];
  BleConn conns[BLE_CONNS];
  portENTER_CRITICAL(&bleMux);
  uint16_t len = bleLiveLen;
  memcpy(buf, bleLive, len);
  int n = bleConnN;
  memcpy(conns, bleConns, sizeof conns);
  portEXIT_CRITICAL(&bleMux);
  for (int i = 0; i < n; i++) {
    if (!conns[i].secure || !conns[i].subscribed) continue;
    struct os_mbuf *om = ble_hs_mbuf_from_flat(buf, len);
    if (om) ble_gatts_notify_custom(conns[i].handle, bleStatusChr->getHandle(), om);  // takes om either way
  }
}

// Hands a phone's write to loop() and waits (half a second at most) until it's applied, so a read right
// after sees it.
void bleHandOver(char kind, const String &v) {
  if (!bleCmds || v.length() + 1 >= sizeof(BleCmd::text)) return;  // too long: a bad write changes nothing
  BleCmd cmd;
  bleCmdSeq = bleCmdSeq + 1;  // only this task writes it
  cmd.seq = bleCmdSeq;
  cmd.text[0] = kind;
  memcpy(cmd.text + 1, v.c_str(), v.length());
  cmd.len = v.length() + 1;
  if (xQueueSend(bleCmds, &cmd, pdMS_TO_TICKS(100)) != pdTRUE) return;
  for (int i = 0; i < 100 && (int32_t)(bleCmdDone - cmd.seq) < 0; i++) vTaskDelay(pdMS_TO_TICKS(5));
}

class SecurityCallbacks : public BLESecurityCallbacks {
  // A new phone: the code, only while pairing is open. Outside the window none is shown, so it can't pair.
  void onPassKeyNotify(uint32_t passKey) override {
    if ((int32_t)(blePairUntil - millis()) <= 0) return;
    passkeyShown = passKey;
    passkeyAt = millis() | 1;
    showFor(PASSKEY_SHOW_MS);
  }
  // Every link that encrypts: kept only when paired with the code (authenticated, Secure Connections, full key).
  // A "Just Works" bond is deleted, so it can't get the status or push a paired phone out of the store.
  void onAuthenticationComplete(ble_gap_conn_desc *d, int status) override {
    passkeyAt = 0;
    bool ok = status == 0 && d->sec_state.encrypted && d->sec_state.authenticated && d->sec_state.key_size >= 16;
    if (ok) {
      struct ble_store_key_sec key = {};
      key.peer_addr = d->peer_id_addr;
      struct ble_store_value_sec val;
      if (ble_store_read_peer_sec(&key, &val) == 0 && !val.sc) ok = false;
    }
    portENTER_CRITICAL(&bleMux);
    int i = bleFind(d->conn_handle);
    if (i >= 0) bleConns[i].secure = ok;
    portEXIT_CRITICAL(&bleMux);
    if (!ok) {
      if (status == 0) ble_store_util_delete_peer(&d->peer_id_addr);
      ble_gap_terminate(d->conn_handle, BLE_ERR_AUTH_FAIL);
      return;
    }
    helloUntil = millis() + 1500;  // the recorder says hello as the phone connects
    showFor(1500);
  }
};

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *, ble_gap_conn_desc *d) override {
    portENTER_CRITICAL(&bleMux);
    if (bleConnN < BLE_CONNS) bleConns[bleConnN++] = {d->conn_handle, false, false};
    portEXIT_CRITICAL(&bleMux);
  }
  void onDisconnect(BLEServer *, ble_gap_conn_desc *d) override {
    portENTER_CRITICAL(&bleMux);
    int i = bleFind(d->conn_handle);
    if (i >= 0) bleConns[i] = bleConns[--bleConnN];
    portEXIT_CRITICAL(&bleMux);
    passkeyAt = 0;
    bleDropped = true;
  }
};

class StatusCallbacks : public BLECharacteristicCallbacks {
  void onRead(BLECharacteristic *c) override { bleServe(c, bleFull, bleFullLen); }
  void onSubscribe(BLECharacteristic *, ble_gap_conn_desc *d, uint16_t sub) override {
    portENTER_CRITICAL(&bleMux);
    int i = bleFind(d->conn_handle);
    if (i >= 0) bleConns[i].subscribed = sub & 1;
    portEXIT_CRITICAL(&bleMux);
  }
};

class ControlCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *c) override { bleHandOver('C', c->getValue()); }
};

class NetworksCallbacks : public BLECharacteristicCallbacks {
  void onRead(BLECharacteristic *c) override { bleServe(c, bleNets, bleNetsLen); }
  void onWrite(BLECharacteristic *c) override {
    bleHandOver('N', c->getValue());
    bleServe(c, bleNets, bleNetsLen);
  }
};

bool bleBonded() {
  ble_addr_t peers[MYNEWT_VAL(BLE_STORE_MAX_BONDS)];
  int n = 0;
  return ble_store_util_bonded_peers(peers, &n, MYNEWT_VAL(BLE_STORE_MAX_BONDS)) == 0 && n > 0;
}

void bleAdvertise(int mode) {
  if (!bleServer || mode == bleAdvMode) return;
  BLEDevice::stopAdvertising();
  if (mode) {
    BLEAdvertising *adv = BLEDevice::getAdvertising();
    uint16_t itvl = mode == 1 ? ADV_FAST : ADV_SLOW;
    adv->setMinInterval(itvl);
    adv->setMaxInterval(itvl + itvl / 8);
    BLEDevice::startAdvertising();
  }
  bleAdvMode = mode;
  bleAdvertising = mode != 0;
}

// A tap of the button: easy to find for a minute, and new phones may pair for ten.
void bleTapped() {
  bleFastUntil = millis() + BLE_FAST_MS;
  blePairUntil = millis() + BLE_PAIR_MS;
}

void setupBle() {
  // "Octo" and the last two bytes of its Wi-Fi address, from the chip's fuses. Until rec-14 this asked the
  // Wi-Fi driver, which had no address yet when Bluetooth started at boot: every recorder was "Octo 0000"
  // (the owner's phone, 8 Oct). Paired over USB later, after Wi-Fi was up, it got the right one.
  uint8_t mac[6] = {0};
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  snprintf(bleName, sizeof bleName, "Octo %02X%02X", mac[4], mac[5]);
  bleCmds = xQueueCreate(4, sizeof(BleCmd));
  BLEDevice::init(bleName);
  BLESecurity *sec = new BLESecurity();
  sec->setCapability(ESP_IO_CAP_OUT);                  // it has a screen, no keyboard: it shows the code
  sec->setAuthenticationMode(true, true, true);        // bond, MITM (the code), LE Secure Connections
  sec->setPassKey(false);                              // a random code ...
  sec->regenPassKeyOnConnect(true);                    // ... a new one each time
  BLEDevice::setSecurityCallbacks(new SecurityCallbacks());
  bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new ServerCallbacks());
  bleServer->advertiseOnDisconnect(false);             // bleLoop() decides
  BLEService *svc = bleServer->createService(BLE_SERVICE);
  const uint32_t R = BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_READ_AUTHEN;
  const uint32_t W = BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_AUTHEN;
  bleBuild();
  bleNetsSnapshot();
  bleStatusChr = svc->createCharacteristic(BLE_STATUS, R | BLECharacteristic::PROPERTY_NOTIFY);
  bleStatusChr->setCallbacks(new StatusCallbacks());
  bleServe(bleStatusChr, bleFull, bleFullLen);
  BLECharacteristic *control = svc->createCharacteristic(BLE_CONTROL, W);
  control->setCallbacks(new ControlCallbacks());
  bleNetsChr = svc->createCharacteristic(BLE_NETWORKS, R | W);
  bleNetsChr->setCallbacks(new NetworksCallbacks());
  bleServe(bleNetsChr, bleNets, bleNetsLen);
  svc->start();
  bleServer->start();
  // A phone that paired with an older table of characteristics keeps its cached copy until told: once per
  // change of BLE_GATT_REV, Service Changed (NimBLE tells paired phones when they next connect).
  if (prefs.getUInt("gatt_rev", 0) != BLE_GATT_REV) {
    ble_svc_gatt_changed(0x0001, 0xffff);
    prefs.putUInt("gatt_rev", BLE_GATT_REV);
  }
  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(BLE_SERVICE);
  adv->setScanResponse(true);
  bleUp = true;
  bleHasBond = bleBonded();
  bleFastUntil = millis() + BLE_FAST_MS;
  if (!bleHasBond) blePairUntil = millis() + BLE_PAIR_MS;  // the first phone: no tap needed
}

// Before a restart it chose (after an update, or going back): one last notification saying so, then the
// links end, so the app shows "Restarting" at once instead of waiting out the link's timeout (C12).
void bleGoodbye() {
  if (!bleUp || bleBye) return;
  bleBye = true;
  bleBuild();
  bleNotify();
  bleAdvertise(0);
  delay(200);
  BleConn conns[BLE_CONNS];
  portENTER_CRITICAL(&bleMux);
  int n = bleConnN;
  memcpy(conns, bleConns, sizeof conns);
  portEXIT_CRITICAL(&bleMux);
  for (int i = 0; i < n; i++) ble_gap_terminate(conns[i].handle, BLE_ERR_REM_USER_CONN_TERM);
  delay(150);
}

void bleLoop() {
  if (!bleUp) return;
  uint32_t now = millis();
  // The phone's writes, in order: recording on/off and the networks.
  BleCmd cmd;
  bool changed = false;
  while (bleCmds && xQueueReceive(bleCmds, &cmd, 0) == pdTRUE) {
    String v;
    v.concat(cmd.text + 1, cmd.len - 1);
    if (cmd.text[0] == 'C' && v.startsWith("filler ")) {  // "filler on" / "filler off" (rec-24): the filler cue
      v.trim();
      setFiller(v == "filler on");
      showFor(2000);
    } else if (cmd.text[0] == 'C') {
      v.trim();
      if ((v == "on" || v == "off") && setOn(v == "on")) {
        if (sdOk) appendManifest(bootDir, String("{\"phone\":\"recording_") + v + "\",\"at_ms\":" + now + "}");
      }
      showFor(4000);
    } else if (oc_net_command(&nets, v.c_str(), v.length(), 0)) {
      saveNets();
    }
    bleCmdDone = cmd.seq;
    changed = true;
  }
  if (sdOk && (now - cardAt > 30000 || !cardTotal)) {
    cardAt = now;
    cardTotal = SD.totalBytes();
    cardFree = cardTotal - SD.usedBytes();
  } else if (!sdOk) {
    cardTotal = cardFree = 0;
  }
  if (now - bleBondCheck > 5000) {
    bleBondCheck = now;
    bleHasBond = bleBonded();
  }
  if (bleDropped) {
    bleDropped = false;
    bleFastUntil = now + BLE_FAST_MS;
  }
  portENTER_CRITICAL(&bleMux);
  int links = bleConnN;
  portEXIT_CRITICAL(&bleMux);
  bleConnected = links > 0;
  int mode = 0;
  if (!links && !bleBye)
    mode = ((int32_t)(bleFastUntil - now) > 0 || (int32_t)(blePairUntil - now) > 0) ? 1 : bleHasBond ? 2 : 0;
  bleAdvertise(mode);
  // The status: rebuilt every second, at once when something the app shows changes (an update's percent, a
  // restart, recording on or off); notified every 2 s, or then.
  static String last;
  String key = String(stateName()) + wantOn + capturing + updPercent() + (restartAt || rejectAt) + firmwarePending;
  if (key != last) changed = true;
  if (changed || now - bleLastBuild > 1000) {
    bleLastBuild = now;
    bleBuild();
  }
  if (bleConnected && ((changed && now - bleLastNotify > 200) || now - bleLastNotify > 2000)) {
    last = key;
    bleLastNotify = now;
    bleNotify();
  }
}

// ---- Wi-Fi API --------------------------------------------------------------------------------

String hmacHex(const String &msg) {
  uint8_t out[32];
  mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), key, sizeof key, (const uint8_t *)msg.c_str(),
                  msg.length(), out);
  return hex(out, 32);
}

bool sameText(const String &a, const String &b) {  // constant time
  if (a.length() != b.length()) return false;
  uint8_t x = 0;
  for (size_t i = 0; i < a.length(); i++) x |= a[i] ^ b[i];
  return x == 0;
}

Nonce &nonceFor(uint32_t ip) {
  Nonce *oldest = &nonces[0];
  for (Nonce &x : nonces) {
    if (x.ip == ip) return x;
    if (x.at < oldest->at) oldest = &x;
  }
  oldest->ip = ip;  // a new client takes the slot used longest ago
  oldest->value = "";
  return *oldest;
}

String freshNonce() {
  uint8_t b[16];
  esp_fill_random(b, sizeof b);
  Nonce &x = nonceFor(server.client().remoteIP());
  x.value = hex(b, sizeof b);
  x.at = millis();
  return x.value;
}

bool authorized() {
  String n = server.header("X-Octo-Nonce");
  Nonce &x = nonceFor(server.client().remoteIP());
  bool fresh = keyOk && x.value.length() && n == x.value && millis() - x.at < NONCE_MS;
  x.value = "";  // one use, pass or fail
  if (!fresh || !sameText(server.header("X-Octo-Sig"), hmacHex("POST\n" + server.uri() + "\n" + n + "\n" + server.arg("plain")))) {
    server.send(401, "application/json", "{\"error\":\"unauthorized\"}");
    return false;
  }
  reqNonce = n;
  LOOP_AT(server.uri().c_str());
  if (healthy()) keepFirmware("wifi");  // the Mac reached this firmware with the right key, and it works
  return true;
}

void reply(const String &body, const char *type = "application/json", int code = 200) {
  server.sendHeader("Cache-Control", "no-store");
  server.sendHeader("X-Octo-Next", freshNonce());
  server.sendHeader("X-Octo-Sig", hmacHex("RESP\n" + server.uri() + "\n" + reqNonce + "\n" + body));
  server.send(code, type, body);
}

void replyError(int code, const char *error) { reply(String("{\"error\":\"") + error + "\"}", "application/json", code); }

String chunkPath(long b, long s) {
  char path[40];
  snprintf(path, sizeof path, "/rec/b%05ld/s%05ld.wav", b, s);
  return path;
}

// ---- updates, over Wi-Fi (rec-2) and USB (rec-13) -----------------------------------------------
//
// One update at a time, either way: begin with the image's size and SHA-256, its pieces in order, then
// finish, which checks the hash, switches slots and restarts. The same checks and the same screens.
//
// New firmware isn't marked good at boot (the core's default): rollback.cpp turns that off, and
// keepFirmware() marks it good once the Mac reaches it: a signed request (authorized(), above) or K over
// USB.

void keepFirmware(const char *via) {
  if (!firmwarePending) return;
  esp_ota_mark_app_valid_cancel_rollback();
  firmwarePending = false;
  prefs.remove("trying");
  updResult = OD_UPD_DONE;
  updResultUntil = millis() + UPD_DONE_MS;
  if (sdOk) appendManifest(bootDir, String("{\"firmware_confirmed\":\"") + FW + "\",\"via\":\"" + via + "\",\"at_ms\":" + millis() + "}");
}

void abandonUpdate() {
  if (updating) Update.abort();
  if (updating) mbedtls_sha256_free(&updateCtx);
  updating = false;
  updateVia = 0;
}

void updateFailed() {
  updResult = OD_UPD_FAILED;
  updResultUntil = millis() + UPD_BAD_MS;
}

// Each step answers nullptr, or why not (the error the Mac sees).
const char *updBegin(long long size, const uint8_t sha[32], int via) {
  if (capturing || wantOn) return "recording";      // switch recording off first
  if (updating && updateVia != via) return "busy";  // one is already coming the other way
  if (size <= 0) return "size_or_sha256";
  if (!updateBuf) return "no_room";  // PSRAM failed at boot (setup stopped there)
  // The slot about to be written must not be the only good one: a firmware still on trial is answering, so
  // it's kept first (over Wi-Fi authorized() has done that already). Arduino's Update doesn't check this.
  keepFirmware(via == UPD_USB ? "usb" : "wifi");
  abandonUpdate();
  if (!Update.begin(size, U_FLASH)) return "no_room";
  memcpy(updateSha, sha, 32);
  mbedtls_sha256_init(&updateCtx);
  mbedtls_sha256_starts(&updateCtx, 0);
  updating = true;
  updateVia = via;
  updateSize = size;
  updateDone = 0;
  updateAt = millis();
  return nullptr;
}

// The next piece, in order.
const char *updData(size_t offset, const uint8_t *buf, size_t n, int via) {
  if (!updating || updateVia != via) return "not_updating";
  if (offset != updateDone) return "offset";
  if (!n || updateDone + n > updateSize) return "data";
  if (Update.write((uint8_t *)buf, n) != n) { abandonUpdate(); return "write_failed"; }
  mbedtls_sha256_update(&updateCtx, buf, n);
  updateDone += n;
  updateAt = millis();
  return nullptr;
}

const char *updFinish(int via) {
  if (!updating || updateVia != via || updateDone != updateSize) return "incomplete";
  uint8_t d[32];
  mbedtls_sha256_finish(&updateCtx, d);
  mbedtls_sha256_free(&updateCtx);
  updating = false;
  updateVia = 0;
  if (memcmp(d, updateSha, 32) != 0) { Update.abort(); updateFailed(); return "sha256"; }
  if (!Update.end(true)) { updateFailed(); return "finish_failed"; }
  prefs.putBool("trying", true);
  if (sdOk) appendManifest(bootDir, String("{\"firmware_updated\":true,\"via\":\"") + (via == UPD_USB ? "usb" : "wifi") +
                                    "\",\"at_ms\":" + millis() + "}");
  restartAt = millis() + 1000;  // after the answer has gone out
  return nullptr;
}

int updHttpCode(const char *err) {
  if (!strcmp(err, "size_or_sha256") || !strcmp(err, "data")) return 400;
  if (!strcmp(err, "no_room")) return 507;
  if (!strcmp(err, "sha256")) return 422;
  if (!strcmp(err, "write_failed") || !strcmp(err, "finish_failed")) return 500;
  return 409;  // recording, busy, not_updating, offset, incomplete
}

// From the Mac: {"size","sha256"}, then {"offset","data"} pieces as /update/data, then finish: read back and checked,
// kept in prefs, and loaded by a restart (the interpreter is made once a boot). Refused while recording, as an
// update is; a model never goes into the slot in use.
void setupFillerModel() {
  server.on("/filler/model/begin", HTTP_POST, [] {
    if (!authorized()) return;
    if (capturing || wantOn) return replyError(409, "recording");
    if (updating || restartAt) return replyError(409, "busy");
    String body = server.arg("plain");
    long long size = jnum(body, "size");
    int i = body.indexOf("\"sha256\":\"");
    if (i < 0 || !unhex(body.substring(i + 10, i + 74).c_str(), kwsWantSha, 32) || size < OC_KWS_HEAD || size > (long long)KWS_SLOT)
      return replyError(400, "size_or_sha256");
    oc_kws_head h;
    size_t total;
    kwsHead(0, &h, &total);  // finds the partition
    if (!kwsPart || !updateBuf) return replyError(507, "no_room");
    kwsTo = kwsSlot == 0 ? 1 : 0;
    if (esp_partition_erase_range(kwsPart, kwsTo * KWS_SLOT, KWS_SLOT) != ESP_OK) {
      kwsTo = -1;
      return replyError(500, "write_failed");
    }
    kwsSize = size;
    kwsDone = 0;
    reply("{\"ready\":true}");
  });
  server.on("/filler/model/data", HTTP_POST, [] {
    if (!authorized()) return;
    String body = server.arg("plain");
    int i = body.indexOf("\"data\":\"");
    int j = i < 0 ? -1 : body.indexOf('"', i + 8);
    size_t n = 0;
    if (kwsTo < 0 || updating) return replyError(409, "not_updating");  // updateBuf is the update's then
    if (j < 0 || mbedtls_base64_decode(updateBuf, UPDATE_BUF, &n, (const uint8_t *)body.c_str() + i + 8, j - i - 8) != 0)
      return replyError(400, "data");
    if ((size_t)jnum(body, "offset") != kwsDone) return replyError(409, "offset");
    if (!n || kwsDone + n > kwsSize) return replyError(400, "data");
    if (esp_partition_write(kwsPart, kwsTo * KWS_SLOT + kwsDone, updateBuf, n) != ESP_OK) {
      kwsTo = -1;
      return replyError(500, "write_failed");
    }
    kwsDone += n;
    reply(String("{\"done\":") + kwsDone + "}");
  });
  server.on("/filler/model/finish", HTTP_POST, [] {
    if (!authorized()) return;
    if (kwsTo < 0 || updating || kwsDone != kwsSize) return replyError(409, "incomplete");
    int to = kwsTo;
    kwsTo = -1;
    mbedtls_sha256_context c;  // what's on the flash now, against what the Mac said it sent
    mbedtls_sha256_init(&c);
    mbedtls_sha256_starts(&c, 0);
    bool read = true;
    for (size_t off = 0; read && off < kwsSize; off += UPDATE_BUF) {
      size_t n = std::min(UPDATE_BUF, kwsSize - off);
      read = esp_partition_read(kwsPart, to * KWS_SLOT + off, updateBuf, n) == ESP_OK;
      mbedtls_sha256_update(&c, updateBuf, n);
    }
    uint8_t d[32];
    mbedtls_sha256_finish(&c, d);
    mbedtls_sha256_free(&c);
    oc_kws_head h;
    size_t total;
    if (!read || memcmp(d, kwsWantSha, 32)) return replyError(422, "sha256");
    if (!kwsHead(to, &h, &total) || total != kwsSize) return replyError(422, "not_a_model");
    kwsUse(to, kwsSlot, true);
    if (sdOk) appendManifest(bootDir, String("{\"filler_model\":\"") + h.name + "\",\"slot\":" + to + ",\"at_ms\":" + millis() + "}");
    updateSize = updateDone = kwsSize;  // the screen says "Restarting"
    restartAt = millis() + 1000;        // after the answer has gone out
    reply("{\"restarting\":true}");
  });
  server.on("/filler/model/undo", HTTP_POST, [] {  // the one before back, with its threshold
    if (!authorized()) return;
    if (capturing || wantOn) return replyError(409, "recording");
    if (updating || restartAt || kwsTo >= 0) return replyError(409, "busy");
    String before = kwsBefore();
    if (!before.length()) return replyError(409, "nothing_before");
    kwsUse(kwsPrev, kwsSlot, false);
    if (sdOk) appendManifest(bootDir, String("{\"filler_model\":\"") + before + "\",\"undo\":true,\"at_ms\":" + millis() + "}");
    updateSize = updateDone = 1;  // the screen says "Restarting"
    restartAt = millis() + 1000;
    reply("{\"restarting\":true}");
  });
}

void setupUpdates() {
  server.on("/update/begin", HTTP_POST, [] {
    if (!authorized()) return;
    if (capturing || wantOn) return replyError(409, "recording");  // switch recording off first
    String body = server.arg("plain");
    int i = body.indexOf("\"sha256\":\"");
    uint8_t sha[32];
    if (i < 0 || !unhex(body.substring(i + 10, i + 74).c_str(), sha, 32)) return replyError(400, "size_or_sha256");
    const char *err = updBegin(jnum(body, "size"), sha, UPD_WIFI);
    if (err) return replyError(updHttpCode(err), err);
    reply("{\"ready\":true}");
  });
  // {"offset":N,"data":"<base64>"}: the next piece, in order.
  server.on("/update/data", HTTP_POST, [] {
    if (!authorized()) return;
    String body = server.arg("plain");
    int i = body.indexOf("\"data\":\"");
    int j = i < 0 ? -1 : body.indexOf('"', i + 8);
    size_t n = 0;
    if (j < 0 || mbedtls_base64_decode(updateBuf, UPDATE_BUF, &n, (const uint8_t *)body.c_str() + i + 8, j - i - 8) != 0)
      return replyError(400, "data");
    const char *err = updData((size_t)jnum(body, "offset"), updateBuf, n, UPD_WIFI);
    if (err) return replyError(updHttpCode(err), err);
    reply(String("{\"done\":") + updateDone + "}");
  });
  server.on("/update/finish", HTTP_POST, [] {
    if (!authorized()) return;
    const char *err = updFinish(UPD_WIFI);
    if (err) return replyError(updHttpCode(err), err);
    reply("{\"restarting\":true}");
  });
}

// U: an update over USB. After the prompt the Mac sends "<size> <sha256 hex>"; once it's accepted, each
// piece as a line "<offset> <base64>" (octo_update.h), waiting for the answer before the next, then
// "end" (or "abort"). The pieces are read by usbUpdateLoop() from loop(), so the screen keeps drawing.
const size_t USB_PIECE = 2048;                         // bytes of firmware per line
const size_t USB_LINE = 16 + 4 * ((USB_PIECE + 2) / 3);  // its offset, a space and its base64
char *usbLine = nullptr;                               // in PSRAM, as updateBuf
size_t usbLen = 0;
// The USB receive buffer must hold a whole line: what doesn't fit is dropped by the driver (HWCDC.cpp).
const size_t USB_RX_BYTES = 4096;
static_assert(USB_LINE + 2 <= USB_RX_BYTES, "a piece's line must fit the USB receive buffer");

void usbUpdateError(const char *err) {
  bench::result(bench::kvs("error", err) + "," + bench::kv("done", (unsigned long)updateDone));
}

// U: an update over USB. rec-16 (C16): from the Mac it's paired with, signed (HMAC(key, "UPDATE\n<size> <sha>")
// after the SHA-256); from anything else, only once someone holding it allows it on its screen.
void usbUpdate() {
  bench::result(bench::kvs("send", "update") + "," + bench::kv("signed", true));
  String head = readLine(30000);
  String sig = takeSignature(head, 2);
  uint32_t size;
  uint8_t sha[32];
  if (!oc_parse_update_head(head.c_str(), head.length(), 16u << 20, &size, sha)) return usbUpdateError("size_or_sha256");
  if (paired && !sameText(sig, hmacHex("UPDATE\n" + head)) && !confirmOnDevice("update?")) return usbUpdateError("not_confirmed");
  if (!usbLine) return usbUpdateError("no_room");  // as updateBuf
  const char *err = updBegin(size, sha, UPD_USB);
  if (err) return usbUpdateError(err);
  usbLen = 0;
  bench::result(bench::kv("accepted", true) + "," + bench::kv("piece", (unsigned long)USB_PIECE));
}

void usbUpdateLoop() {
  while (updating && updateVia == UPD_USB && Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      if (usbLen < USB_LINE) usbLine[usbLen] = c;
      usbLen++;  // past the end too: a line that long is refused whole
      continue;
    }
    size_t len = usbLen;
    usbLen = 0;
    if (len >= USB_LINE) { usbUpdateError("data"); continue; }
    usbLine[len] = 0;
    if (!strcmp(usbLine, "end")) {
      const char *err = updFinish(UPD_USB);
      if (err) usbUpdateError(err);
      else bench::result(bench::kv("restarting", true));
    } else if (!strcmp(usbLine, "abort")) {
      abandonUpdate();
      updateFailed();
      bench::result(bench::kv("aborted", true));
    } else {
      uint32_t offset;
      size_t n;
      const char *err = oc_parse_piece(usbLine, len, &offset, updateBuf, USB_PIECE, &n) ? updData(offset, updateBuf, n, UPD_USB) : "data";
      if (err) usbUpdateError(err);
      else bench::result(bench::kv("done", (unsigned long)updateDone));
    }
  }
}

// K: the Mac reached the new firmware over USB (and, if the recorder was on Wi-Fi before, it's back on
// it): keep it. Its status after.
void usbKeep() {
  bool was = firmwarePending;
  if (healthy()) keepFirmware("usb");  // rec-16: not one whose microphone or card doesn't work
  bench::result(bench::kv("kept", was && !firmwarePending) + "," + statusFields());
}

// X: the Mac won't keep the firmware on trial: back to the one before, now rather than in 10 minutes.
void usbReject() {
  if (!firmwarePending) return bench::result(bench::kvs("error", "not_on_trial"));
  bench::result(bench::kv("going_back", true));
  rejectAt = millis() + 300;  // after the answer has gone out
}

void buttonLoop();
void lightLoop();
void displayLoop();

// What the screen says about the Mac's copy (rec-22, od_sync_status in octo_display.h). Copying: a piece went in the
// last 10 s, so it stays on through the moment between chunks and goes off soon after audio stops moving (never
// stuck on). Waiting: audio on the card and no chunk pulled for 15 min (at home the Mac pulls every 5).
uint32_t lastPieceMs = 0, lastPullMs = 0, closedSeen = 0;
long cardChunks = -1;  // closed chunks on the card: counted by loop() once a mount, then plus the writer's, less deletes
const uint32_t COPYING_MS = 10000, WAITING_MS = 15 * 60 * 1000;

int syncKind() {
  uint32_t now = millis();
  if (lastPieceMs && now - lastPieceMs < COPYING_MS) return OD_SYNC_COPYING;
  if (cardChunks > 0 && now - lastPullMs > WAITING_MS) return OD_SYNC_WAITING;
  return cardChunks == 0 ? OD_SYNC_ALL_ON : OD_SYNC_NONE;
}

// A chunk file to the Mac (rec-20), in place of WebServer's streamFile. That one carries on to the end of the file
// when the Mac stops taking it, each 1.4 KB piece waiting up to 10 s (NetworkClient::write): an hour and more with
// the loop stuck. Here a piece that doesn't go ends the reply. A chunk takes 15-20 s over the home Wi-Fi, so the
// button, light and screen keep running between pieces; before, they froze for every chunk of a sync.
void sendChunk(File &f) {
  NetworkClient &c = server.client();
  for (size_t n; (n = f.read(loopBuf, sizeof loopBuf)) > 0;) {
    if (c.write(loopBuf, n) != n) { c.stop(); return; }
    lastPieceMs = millis();
    esp_task_wdt_reset();
    buttonLoop();
    lightLoop();
    displayLoop();
  }
}

void setupServer() {
  static const char *headers[] = {"X-Octo-Nonce", "X-Octo-Sig"};
  server.collectHeaders(headers, 2);
  server.on("/hello", HTTP_GET, [] {
    server.sendHeader("Cache-Control", "no-store");
    server.send(200, "application/json", String("{\"device\":\"") + hostName + "\",\"nonce\":\"" + freshNonce() + "\"}");
  });
  server.on("/status", HTTP_POST, [] { if (authorized()) reply("{" + statusFields() + "}"); });
  server.on("/clock", HTTP_POST, [] {
    if (!authorized()) return;
    long long epoch = jnum(server.arg("plain"), "epoch_ms");
    if (epoch <= 0) return replyError(400, "epoch_ms");
    if (sdOk) appendManifest(bootDir, String("{\"clock_ms\":") + String(epoch) + ",\"at_ms\":" + millis() + "}");
    clockSet = sdOk;
    reply("{" + statusFields() + "}");
  });
  server.on("/filler", HTTP_POST, [] {  // {"on": true, "threshold": 0.96} (B20, rec-24)
    if (!authorized()) return;
    String body = server.arg("plain");
    int t = body.indexOf("\"threshold\":");
    if (!setFiller(body.indexOf("\"on\":true") >= 0, t >= 0 ? body.substring(t + 12).toFloat() : -1))
      return replyError(409, "no_model");
    reply("{" + statusFields() + "}");
  });
  server.on("/recording", HTTP_POST, [] {
    if (!authorized()) return;
    if (!setOn(server.arg("plain").indexOf("\"on\":true") >= 0)) return replyError(409, "updating");
    reply("{" + statusFields() + "}");
  });
  // The Wi-Fi networks (rec-12): {"cmd": "list"}, or a USB-style command in "cmd" ("add <ssid hex>
  // <password hex>", "del <ssid hex>"). Names come back as hex: any byte, no escaping.
  server.on("/networks", HTTP_POST, [] {
    if (!authorized()) return;
    String body = server.arg("plain");
    int at = body.indexOf("\"cmd\":\"");
    String cmd = at < 0 ? "" : body.substring(at + 7, body.indexOf('"', at + 7));
    bool ok = cmd == "list" || oc_net_command(&nets, cmd.c_str(), cmd.length(), 1);
    if (ok && cmd != "list") saveNets();
    reply(String("{") + bench::kv("ok", ok) + ",\"networks_hex\":" + netNames(true) + "}");
  });
  server.on("/boots", HTTP_POST, [] {
    if (!authorized()) return;
    if (!sdOk) return replyError(503, "no_card");
    String out = "[";
    File root = SD.open("/rec");
    for (File d = root.openNextFile(); d; d = root.openNextFile()) {
      if (!d.isDirectory() || d.name()[0] != 'b') continue;
      unsigned long b = atol(d.name() + 1);
      int chunks = 0;
      for (File f = d.openNextFile(); f; f = d.openNextFile()) chunks += f.name()[0] == 's';
      // The boot's id from its first manifest line, so the Mac can tell a reused boot number apart.
      String id = "";
      File m = SD.open(String(d.path()) + "/manifest.jsonl");
      if (m) {
        String first = m.readStringUntil('\n');
        m.close();
        int i = first.indexOf("\"boot_id\":\"");
        if (i >= 0) id = first.substring(i + 11, i + 27);
      }
      out += String(out.length() > 1 ? "," : "") + "{\"boot\":" + b + ",\"current\":" + (b == boot ? "true" : "false") +
             ",\"chunk_files\":" + chunks + (id.length() ? ",\"boot_id\":\"" + id + "\"" : String("")) + "}";
    }
    root.close();
    reply(out + "]");
  });
  // Up to MANIFEST_PAGE bytes from `from`, cut at the last whole line; the Mac moves `from` on.
  server.on("/manifest", HTTP_POST, [] {
    if (!authorized()) return;
    String body = server.arg("plain");
    long b = jnum(body, "boot");
    long long from = jnum(body, "from", 0);
    if (!sdOk) return replyError(503, "no_card");
    char path[40];
    snprintf(path, sizeof path, "/rec/b%05ld/manifest.jsonl", b);
    xSemaphoreTake(manifestLock, portMAX_DELAY);
    File f = SD.open(path);
    bool found = f;
    String page;
    if (found) {
      f.seek(from);
      size_t want = min((size_t)MANIFEST_PAGE, (size_t)max(0LL, (long long)f.size() - from));
      std::vector<char> buf(want + 1, 0);
      f.read((uint8_t *)buf.data(), want);
      f.close();
      page = buf.data();
      page = page.substring(0, page.lastIndexOf('\n') + 1);
    }
    xSemaphoreGive(manifestLock);
    if (!found) return replyError(404, "no_manifest");
    reply(page, "application/x-ndjson");
  });
  // The chunk file as stored. Not signed: the Mac checks it against the manifest's SHA-256.
  server.on("/chunk", HTTP_POST, [] {
    if (!authorized()) return;
    String body = server.arg("plain");
    long b = jnum(body, "boot"), s = jnum(body, "seq");
    if ((uint32_t)b == boot && (uint32_t)s == openSeq) return replyError(409, "still_recording");
    File f = sdOk ? SD.open(chunkPath(b, s)) : File();
    if (!f) return replyError(404, "no_chunk");
    lastPullMs = millis();
    server.sendHeader("Cache-Control", "no-store");
    server.sendHeader("X-Octo-Next", freshNonce());
    server.setContentLength(f.size());
    server.send(200, "audio/wav", "");  // the headers, as streamFile sends them
    server.setContentLength(CONTENT_LENGTH_NOT_SET);
    sendChunk(f);
    f.close();
  });
  // {"boot":B,"seq":S} removes one chunk; {"boot":B} removes a finished boot's whole directory.
  server.on("/delete", HTTP_POST, [] {
    if (!authorized()) return;
    String body = server.arg("plain");
    long b = jnum(body, "boot"), s = jnum(body, "seq");
    if (!sdOk) return replyError(503, "no_card");
    bool ok;
    if (s >= 0) {
      if ((uint32_t)b == boot && (uint32_t)s == openSeq) return replyError(409, "still_recording");
      String path = chunkPath(b, s);
      bool there = SD.exists(path);
      ok = !there || SD.remove(path);  // already gone counts as deleted (M42)
      if (ok && there && cardChunks > 0) cardChunks--;  // "All on your Mac" without counting again
    } else {
      if ((uint32_t)b == boot) return replyError(409, "current_boot");
      char dir[16];
      snprintf(dir, sizeof dir, "/rec/b%05ld", b);
      std::vector<String> files;
      File d = SD.open(dir);
      for (File f = d ? d.openNextFile() : File(); f; f = d.openNextFile()) files.push_back(f.path());
      if (d) d.close();
      for (const String &p : files) SD.remove(p);
      ok = SD.rmdir(dir);
      cardChunks = -1;  // a finished boot's chunks, if any were left: count again
    }
    cardFull = false;  // space may be back; the writer checks again
    reply(String("{\"deleted\":") + (ok ? "true" : "false") + "}");
  });
  setupUpdates();
  setupFillerModel();
  server.onNotFound([] { server.send(404, "text/plain", ""); });
}

// ---- the button and the status light ------------------------------------------------------------
//
// One button, held for different lengths, the light confirming as you go (Apple's pattern: AirPods
// case, Apple Watch side button). The light follows Apple's habits: dark when all is well, smooth
// fades rather than blinks, low brightness, and a truthful recording indicator that can't be
// switched off (the iPhone's orange mic dot): a soft pulse every few seconds while recording.
//
// B on the XIAO, or the expansion board's button (D1):
//   tap                 show the state for 2 s: a steady glow if recording, one dim pulse if not
//                       (after DOUBLE_TAP_MS, to know it isn't a double tap)
//   double tap          while recording: mark this moment (B16, rec-26), a flag on the screen for a second
//   hold 1 s, let go    recording on/off. While holding: a rising glow, then a bright tick at 1 s;
//                       holding longer changes nothing. One press, one action.
// No power off from the button (rec-8, 5 Oct): on a power bank, the few mA left in power-off
// made the bank cut out, and then nothing could turn it back on. Off is unplugging, or the expansion
// board's ON/OFF switch with a LiPo.
//
// Recording starts: glow up, hold, fade (2.4 s). Stops: one short soft pulse. Needs attention (no
// card, card full, mic fault): a double pulse every 3 s.

const uint32_t SHOW_MS = 6000;  // how long the display stays on after something happens
void showFor(uint32_t ms);

enum Cue { CUE_NONE, CUE_ON, CUE_OFF, CUE_STATUS };
Cue cue = CUE_NONE;
uint32_t cueAt = 0, downSince = 0, pressAt = 0;
bool pressed = false, wasCapturing = false;

float bump(uint32_t t, uint32_t len) { return t < len ? sinf(PI * t / len) : 0.0f; }  // 0 -> 1 -> 0

void light(float level) {  // perceptual brightness: the eye sees roughly the square of the duty
  level = constrain(level, 0.0f, 1.0f) * LED_MAX;
  ledcWrite(LED_PIN, (uint32_t)(255.0f * level * level + 0.5f));
}

float holdLevel(uint32_t h) {
  if (h < HOLD_TOGGLE_MS) return 0.15f + 0.45f * h / HOLD_TOGGLE_MS;  // keep holding...
  if (h < HOLD_TOGGLE_MS + 250) return 1.0f;                         // tick: let go now for recording on/off
  return 0.6f;                                                       // and it stays there until you do
}

void lightLoop() {
  uint32_t now = millis();
  if (capturing != wasCapturing) {  // whatever switched it (button, Mac, card), say so
    cue = capturing ? CUE_ON : CUE_OFF;
    cueAt = now;
    wasCapturing = capturing;
    showFor(SHOW_MS);
  }
  static bool hadAttention = false;
  bool needs = (!sdOk && !recovering) || cardFull || !micOk;
  if (needs && !hadAttention) showFor(SHOW_MS);  // something went wrong: show it once
  hadAttention = needs;
  float level = 0.0f;
  uint32_t t = now - cueAt;
  bool attention = (!sdOk && !recovering) || cardFull || !micOk;
  if (pressed) level = holdLevel(now - pressAt);
  else if (cue == CUE_ON && t < 2400) level = t < 400 ? t / 400.0f : t < 1600 ? 1.0f : 1.0f - (t - 1600) / 800.0f;
  else if (cue == CUE_OFF && t < 600) level = 0.5f * bump(t, 600);
  else if (cue == CUE_STATUS && t < 2000) level = capturing ? 0.6f * min(1.0f, min(t, 2000 - t) / 300.0f) : 0.35f * bump(t, 700);
  else if (attention) level = 0.7f * (bump(now % 3000, 250) + bump(now % 3000 - 400, 250));
  else if (capturing) level = 0.3f * bump(now % 5000, 400);  // the recording indicator
  light(level);
}


// ---- the display (the expansion board's OLED, SSD1306 at 0x3C on D4/D5) ------------------------
//
// Full and bright for a few seconds after anything happens, then always on but dim (5 Oct; see
// octo_display.h): the recorder's state when it changes,
// a tap, the hold progress while the button is down, hello at power-on. Screens
// are drawn by octo_display.h (rendered and checked on the Mac). No display: nothing happens.

const uint8_t OLED = 0x3C;
const uint32_t FRAME_MS = 150;
bool oledOk = false, oledOn = false, oledDim = false;
// Brightness, full screens vs always-on. The SSD1306's contrast register alone barely shows (5 Oct:
// "no difference"): with a long pre-charge and a high VCOMH the pixels are driven near full whatever it
// says. Dim lowers all three: contrast, pre-charge period (0xD9) and VCOMH deselect level (0xDB).
// Tried on the board: pre-charge 0x11 + VCOMH 0x00 was unreadable; 0x22 + 0x20 is right (5 Oct).
const uint8_t BRIGHT = 0xCF, DIM = 0x01;
volatile uint32_t helloUntil = 0;
uint32_t showUntil = 0, lastFrame = 0;
const uint32_t FILLER_FLASH_MS = 1500;  // "you know" on a bright screen (B20)
uint32_t fillerFlashUntil = 0;
const uint32_t MARK_FLASH_MS = 1000;  // the flag after a double tap (B16)
uint32_t markFlashUntil = 0;
uint32_t animAt = 0;              // when the start or stop animation began
bool animStart = false, animPending = false;
od_frame frame;

void oledCmd(std::initializer_list<uint8_t> cmds) {
  Wire.beginTransmission(OLED);
  Wire.write(0x00);
  for (uint8_t c : cmds) Wire.write(c);
  Wire.endTransmission();
}

void oledPower(bool on) {
  if (!oledOk || on == oledOn) return;
  if (on) oledCmd({0x8D, 0x14, 0xAF});   // charge pump on, display on
  else oledCmd({0xAE, 0x8D, 0x10});      // display off, charge pump off: ~nothing drawn from it
  oledOn = on;
}

void oledPush() {
  oledCmd({0x21, 0, 127, 0x22, 0, 7});  // the whole screen, page by page
  for (size_t i = 0; i < sizeof frame.fb; i += 32) {
    Wire.beginTransmission(OLED);
    Wire.write(0x40);
    Wire.write(frame.fb + i, 32);
    Wire.endTransmission();
  }
}

void oledBegin() {
  Wire.begin();  // the XIAO's SDA/SCL: D4/D5
  Wire.setClock(400000);
  Wire.beginTransmission(OLED);
  oledOk = Wire.endTransmission() == 0;
  if (!oledOk) return;
  oledCmd({0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12, 0x81, BRIGHT,
           0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0x2E});
  helloUntil = millis() + 2500;
  showUntil = millis() + SHOW_MS;
}

void showFor(uint32_t ms) { showUntil = max(showUntil, millis() + ms); }

const char *attentionText() {
  if (!micOk) return "Mic problem";
  if (cardFull) return "Card full";
  if (!sdOk && recovering) return "Card error";
  if (!sdOk) return "No SD card";
  return nullptr;
}

void oledContrast(bool dim) {
  if (dim == oledDim) return;
  oledCmd({0x81, dim ? DIM : BRIGHT, 0xD9, (uint8_t)(dim ? 0x22 : 0xF1), 0xDB, (uint8_t)(dim ? 0x20 : 0x40)});
  oledDim = dim;
}

void displayLoop() {
  if (!oledOk) return;
  uint32_t now = millis();
  // Updating, over Wi-Fi or USB: progress, then "Restarting" (5 Oct; USB since rec-13).
  if (updating || restartAt) {
    if (now - lastFrame < 200) return;
    lastFrame = now;
    od_screen_updating(&frame, restartAt ? updateSize : updateDone, updateSize, now);
    oledContrast(false);
    oledPush();
    oledPower(true);
    return;
  }
  // After an update: checking, then updated / failed / rolled back (rec-10). A hold still shows the bar.
  if (!pressed && (int32_t)(helloUntil - now) <= 0 &&
      (updResult == OD_UPD_CHECKING ? firmwarePending : updResult >= 0 && (int32_t)(updResultUntil - now) > 0)) {
    if (now - lastFrame < 50) return;
    lastFrame = now;
    static int shownResult = -1;
    static uint32_t resultAt = 0;
    if (updResult != shownResult) { shownResult = updResult; resultAt = now; }
    od_screen_update_result(&frame, updResult, FW, updResult == OD_UPD_CHECKING ? now : now - resultAt);
    oledContrast(false);
    oledPush();
    oledPower(true);
    showFor(1000);
    return;
  }
  // Recording started or stopped (button, Mac, card): the animation, at 20 frames a second.
  static bool shownCapturing = false;
  if (capturing != shownCapturing) {
    shownCapturing = capturing;
    animStart = capturing;
    animAt = now;
    animPending = true;
    showFor(SHOW_MS);
  }
  if (animPending && !pressed) {
    uint32_t t = now - animAt;
    if (t < OD_ANIM_MS) {
      if (now - lastFrame < 50) return;
      lastFrame = now;
      if (animStart) od_anim_start(&frame, t); else od_anim_stop(&frame, t);
      oledContrast(false);
      oledPush();
      oledPower(true);
      return;
    }
    animPending = false;
  }
  bool full = pressed || (int32_t)(showUntil - now) > 0 || (passkeyAt && now - passkeyAt < PASSKEY_SHOW_MS);
  if (!full) {  // always on, Apple Watch style: dim, simplified, once a second, shifting
    uint32_t every = paired && capturing && !attentionText() ? 500 : 1000;  // recording: twice, so REC's dot blinks
    if (now / every == lastFrame / every) return;  // on the boundary, so the dot's on and off halves stay even
    lastFrame = now;
    if (!paired) od_screen_setup(&frame, now);
    else if (const char *why = attentionText()) od_screen_attention(&frame, why, now);
    else if (capturing) {
      od_ambient_recording(&frame, (uint32_t)((now - segStartMs) / 1000), now);
      od_sync_status(&frame, OD_ON_AMBIENT_RECORDING, syncKind(), cardChunks, now);
      if (syncKind() == OD_SYNC_NONE || syncKind() == OD_SYNC_ALL_ON)  // the dim screen draws nothing for "all on"
        od_filler_count(&frame, OD_ON_AMBIENT_RECORDING, filler::name, fillerCount(), now);
    } else {
      od_ambient_stopped(&frame, now);
      od_sync_status(&frame, OD_ON_AMBIENT_STOPPED, syncKind(), cardChunks, now);
    }
    oledPush();
    oledContrast(true);
    oledPower(true);
    return;
  }
  if (now - lastFrame < FRAME_MS) return;
  lastFrame = now;
  oledContrast(false);
  if (passkeyAt && now - passkeyAt < PASSKEY_SHOW_MS && !pressed) od_screen_passkey(&frame, passkeyShown, now);
  else if (!paired && !pressed) od_screen_setup(&frame, now);
  else if (pressed) od_screen_hold(&frame, now - pressAt, HOLD_TOGGLE_MS, wantOn);
  else if ((int32_t)(helloUntil - now) > 0) od_screen_hello(&frame, now);
  else if ((int32_t)(markFlashUntil - now) > 0) od_screen_mark(&frame, now);  // the owner's own press, over the rest
  else if (const char *why = attentionText()) od_screen_attention(&frame, why, now);
  else if (capturing && (int32_t)(fillerFlashUntil - now) > 0) od_screen_filler(&frame, filler::name, fillerCount(), now);
  else if (capturing) {
    od_screen_recording(&frame, (uint32_t)((now - segStartMs) / 1000), now);
    od_sync_status(&frame, OD_ON_RECORDING, syncKind(), cardChunks, now);
    if (syncKind() == OD_SYNC_NONE) od_filler_count(&frame, OD_ON_RECORDING, filler::name, fillerCount(), now);  // the footer's free
  } else {
    od_screen_stopped(&frame, now);
    od_sync_status(&frame, OD_ON_STOPPED, syncKind(), cardChunks, now);
  }
  oledPush();
  oledPower(true);
}

bool buttonDown() { return digitalRead(BUTTON_PIN) == LOW || digitalRead(BUTTON2_PIN) == LOW; }

// A let-go counts only once the button has stayed up this long. On a long hold, finger pressure on a
// tactile switch can open it for a few ms; before rec-7 that counted as letting go, so one hold stopped
// and then restarted recording (5 Oct: recording_off and recording_on 8 s apart, one press).
const uint32_t RELEASE_MS = 80;
uint32_t upSince = 0;
// A second press starting this soon after a tap is a double tap (B16): a single tap waits this long to act.
const uint32_t DOUBLE_TAP_MS = 350;
uint32_t tapAt = 0;  // when a tap let go, not acted on yet

void tapped(uint32_t now) {
  cue = CUE_STATUS;
  cueAt = now;
  showFor(4000);  // a tap: show the state
  bleTapped();  // and let the phone app find it (and a new phone pair) for a while (rec-12, rec-15)
}

void buttonLoop() {
  uint32_t now = millis();
  bool down = buttonDown();
  if (tapAt && !down && !pressed && now - tapAt > DOUBLE_TAP_MS) {  // no second press: it was a tap
    tapAt = 0;
    tapped(now);
  }
  if (down) {
    upSince = 0;  // a glitch mid-hold: still the same press
    if (!pressed) {
      if (!downSince) downSince = now;
      if (now - downSince >= 30) { pressed = true; pressAt = downSince; }  // 30 ms: contact bounce
    }
    return;
  }
  downSince = 0;
  if (!pressed) return;
  if (!upSince) upSince = now;
  if (now - upSince < RELEASE_MS) return;
  pressed = false;
  uint32_t held = upSince - pressAt;  // to when it went up, not to now
  uint32_t lastTap = tapAt;
  tapAt = 0;  // a hold after a tap: the hold wins
  upSince = 0;
  if (held >= HOLD_TOGGLE_MS) {
    if (!setOn(!wantOn)) { cue = CUE_STATUS; cueAt = now; showFor(4000); return; }  // updating: not now
    if (sdOk) appendManifest(bootDir, String("{\"button\":\"recording_") + (wantOn ? "on" : "off") + "\",\"at_ms\":" + now + "}");
    if (wantOn == capturing) { cue = CUE_STATUS; cueAt = now; }  // nothing will change (no card, say): show why
  } else if (lastTap && pressAt - lastTap <= DOUBLE_TAP_MS) {  // a double tap
    if (capturing) {  // mark this moment (B16): the Mac places it by the boot's clock, as chunks
      if (sdOk) appendManifest(bootDir, String("{\"button\":\"mark\",\"at_ms\":") + now + "}");
      markFlashUntil = now + MARK_FLASH_MS;
      showFor(MARK_FLASH_MS);
      return;
    }
    tapped(now);  // stopped: nothing to mark, so show the state now
  } else {
    tapAt = pressAt + held;  // a tap, perhaps the first of two: buttonLoop() acts on it once DOUBLE_TAP_MS pass
    return;
  }
  showFor(SHOW_MS / 2);  // after any press, a moment to read the result
}

// rec-16 (C16): over USB, a re-pairing or an update the Mac didn't sign waits for someone holding the recorder.
// "Allow …?" on the screen, and a 2 s hold of its button within 30 s; the Mac is told to say so ("confirm").
// Blocking, like the USB commands' own reads: capture and the card carry on in their tasks meanwhile.
const uint32_t CONFIRM_MS = 30000;
bool confirmOnDevice(const char *what) {
  bench::result(bench::kvs("confirm", "hold"));
  uint32_t t0 = millis(), downAt = 0, upAt = 0;
  bool yes = false;
  while (millis() - t0 < CONFIRM_MS && !yes) {
    uint32_t now = millis();
    if (buttonDown()) {
      upAt = 0;
      if (!downAt) downAt = now;
    } else if (downAt) {
      if (!upAt) upAt = now;
      if (now - upAt >= RELEASE_MS) downAt = upAt = 0;  // let go (a few ms open mid-hold isn't letting go)
    }
    uint32_t held = downAt ? now - downAt : 0;
    yes = held >= HOLD_CONFIRM_MS;
    od_screen_confirm(&frame, what, held, HOLD_CONFIRM_MS, CONFIRM_MS - (now - t0));
    oledContrast(false);
    oledPush();
    oledPower(true);
    esp_task_wdt_reset();  // 30 s to hold the button: as long as the watchdog
    delay(30);
  }
  // That hold isn't a recording on/off: wait for the let-go, then forget the press.
  uint32_t t1 = millis();
  while (yes && buttonDown() && millis() - t1 < 5000) delay(10);
  pressed = false;
  downSince = upSince = 0;
  showFor(SHOW_MS / 2);
  return yes;
}

bool wifiUp = false, serverUp = false, wifiDisabled = false;
uint32_t wifiNextTry = 0, wifiBackoff = 15000;

// Away from home the network isn't there: retry with backoff (15 s doubling to 5 min) so the
// radio isn't scanning constantly. Recording never waits on any of this.
void wifiLoop() {
  if (!keyOk || wifiDisabled) return;  // no key, no API: don't join at all
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !wifiUp) {
    wifiUp = true;
    wifiBackoff = 15000;
    netTry = 0;
    if (MDNS.begin(hostName)) MDNS.addService("octo", "tcp", 80);
    if (!serverUp) { server.begin(); serverUp = true; }
  } else if (!up && wifiUp) {
    wifiUp = false;
    MDNS.end();
    wifiNextTry = millis() + 5000;
  }
  int count = nets.n;
  if (!up && count > 0 && (int32_t)(millis() - wifiNextTry) >= 0) {
    // The saved networks in turn, newest first (rec-12); the backoff grows once all have been tried.
    const oc_net &n = nets.nets[netTry % count];
    WiFi.disconnect();
    WiFi.begin(n.ssid, n.pass[0] ? n.pass : nullptr);
    netTry++;
    wifiNextTry = millis() + (netTry % count ? 15000 : wifiBackoff);
    if (netTry % count == 0) wifiBackoff = min(wifiBackoff * 2, (uint32_t)300000);
  }
  if (serverUp) {
    serving = true;
    server.handleClient();
    serving = false;
  }
}

// ---- setup and loop ---------------------------------------------------------------------------

void setup() {
  Serial.setRxBufferSize(USB_RX_BYTES);  // before begin: a USB update's line must fit whole
  bench::begin("recorder");
  ledcAttach(LED_PIN, 1000, 8);
  ledcWrite(LED_PIN, 0);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(BUTTON2_PIN, INPUT_PULLUP);
  oledBegin();
  prefs.begin("octo", false);
  boot = prefs.getUInt("boot", 0);
  wantOn = prefs.getBool("on", true);  // factory default: on (D9)
  filler::on = prefs.getBool("filler", false);  // off until the owner turns it on (B20); the threshold in fillerBoot
  loadSettings();
  manifestLock = xSemaphoreCreateMutex();
  esp_ota_img_states_t ota;  // just updated (Wi-Fi or USB): on trial until the Mac reaches it (keepFirmware)
  firmwarePending = esp_ota_get_state_partition(esp_ota_get_running_partition(), &ota) == ESP_OK &&
                    ota == ESP_OTA_IMG_PENDING_VERIFY;
  if (firmwarePending) updResult = OD_UPD_CHECKING;
  else if (prefs.getBool("trying", false)) {  // new firmware was tried and didn't stick
    prefs.remove("trying");
    rolledBack = true;
    updResult = OD_UPD_UNDONE;
    updResultUntil = millis() + UPD_BAD_MS;
  }

  uint8_t *storage = (uint8_t *)ps_malloc(RING_BYTES + 1);
  ring = storage ? xStreamBufferCreateStatic(RING_BYTES, BLOCK, storage, &ringStruct) : nullptr;
  tail = (uint8_t *)ps_malloc(TAIL_BYTES);
  redoBuf = (uint8_t *)ps_malloc(TAIL_BYTES);
  updateBuf = (uint8_t *)ps_malloc(UPDATE_BUF);
  usbLine = (char *)ps_malloc(USB_LINE);
  if (!ring || !tail || !redoBuf || !updateBuf || !usbLine) {
    ring = nullptr;
    lastError = "psram_alloc_failed";
    return;  // loop() still answers USB status
  }

  if (mountCard(10)) startBoot();
  else lastError = "";  // plain "no_card"; loop() keeps trying
  esp_reset_reason_t why = esp_reset_reason();
  if (stuck.magic == STUCK_MAGIC && sdOk && (why == ESP_RST_TASK_WDT || why == ESP_RST_INT_WDT || why == ESP_RST_WDT || why == ESP_RST_PANIC)) {
    stuck.loop[15] = stuck.writer[15] = 0;
    appendManifest(bootDir, String("{\"watchdog\":{\"loop\":\"") + jsonText(stuck.loop) + "\",\"loop_ms\":" + stuck.loopMs +
                   ",\"writer\":\"" + jsonText(stuck.writer) + "\",\"writer_ms\":" + stuck.writerMs + "},\"at_ms\":" + millis() + "}");
  }
  memset(&stuck, 0, sizeof stuck);
  stuck.magic = STUCK_MAGIC;
  if (rolledBack && sdOk) appendManifest(bootDir, String("{\"firmware_rolled_back\":\"") + FW + "\",\"at_ms\":" + millis() + "}");

  i2s.setPinsPdmRx(PDM_CLK, PDM_DATA);
  if (!i2s.begin(I2S_MODE_PDM_RX, RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    lastError = "i2s_begin_failed";
    micOk = false;
  }
  if (micOk) {  // count what the driver drops if the reader falls behind (rec-15, C6)
    i2s_chan_handle_t rx = i2s.rxChan();
    i2s_event_callbacks_t cbs = {};
    cbs.on_recv_q_ovf = onI2sOverflow;
    i2s_channel_disable(rx);
    i2s_channel_register_event_callback(rx, &cbs, nullptr);
    i2s_channel_enable(rx);
  }
  if (micOk) {
    for (int i = 0; i < 3; i++) i2s.readBytes((char *)tail, 3200);  // PDM filter settling, into tail: unused until the writer starts
    xTaskCreatePinnedToCore(readerTask, "reader", 4096, nullptr, 6, nullptr, 1);
    fillerBoot();
  }
  esp_task_wdt_config_t wdt = {WATCHDOG_MS, 1 << 0, true};  // core 0's idle task stays watched, as by default
  esp_task_wdt_reconfigure(&wdt);
  xTaskCreatePinnedToCore(writerTask, "writer", 8192, nullptr, 4, nullptr, 1);

  WiFi.persistent(false);  // the networks live in our own settings; don't write the driver's to flash each try (C10)
  WiFi.mode(WIFI_STA);
  uint8_t mac[6] = {0};
  esp_read_mac(mac, ESP_MAC_WIFI_STA);  // the fuses: the same before Wi-Fi starts (rec-14's lesson)
  snprintf(hostName, sizeof hostName, "octo-%02x%02x", mac[4], mac[5]);
  WiFi.setHostname(hostName);
  WiFi.setAutoReconnect(false);  // wifiLoop() decides when to retry
  WiFi.setSleep(true);           // modem sleep between beacons: the big Wi-Fi power saving
  setupServer();
  if (paired) setupBle();
  enableLoopWDT();  // the core checks in for the loop at every pass
}

uint32_t lastMountTry = 0;
int remountFailures = 0;

void loop() {
  LOOP_AT("usb");
  if (updating && updateVia == UPD_USB) usbUpdateLoop();  // an update's lines, not commands
  else switch (bench::poll()) {
    case 's': case 'g': bench::result(statusFields() + "," + bench::kv("chunk_files", chunkFilesOnCard())); break;
    case '1': setOn(true); bench::result(statusFields()); break;  // refused while updating: want_on stays false
    case '0': setOn(false); bench::result(statusFields()); break;
    case 'P': usbPair(); break;
    case 'N': usbNetworks(); break;
    case 'U': usbUpdate(); break;
    case 'K': usbKeep(); break;
    case 'X': usbReject(); break;
    // Tests only, not kept across restarts: does the radio disturb the card or the mic?
    case 'w': wifiDisabled = true; WiFi.disconnect(true); WiFi.mode(WIFI_OFF); wifiUp = false; bench::result(statusFields()); break;
    case 'W': wifiDisabled = false; WiFi.mode(WIFI_STA); wifiNextTry = millis(); bench::result(statusFields()); break;
    case 'H': bench::result(bench::kvs("hang", "loop")); for (;;) {} break;  // rec-20: the watchdog restarts it in 30 s
  }
  // Remount: every second while recovering from an error (audio waits in the ring buffer), every
  // 10 s when there's no card. After 5 quick failures, stop capturing and say so.
  if (ring && !sdOk && !chunkOpen && millis() - lastMountTry > (recovering ? 1000u : 10000u)) {
    lastMountTry = millis();
    LOOP_AT("mount");
    SD.end();
    if (mountCard(1)) {
      startBoot();
      remountFailures = 0;
    } else if (recovering && ++remountFailures >= 5) {
      recovering = false;
    }
  }
  LOOP_AT("manifest");
  {
    static uint32_t seenSeq = 0, countSeg = UINT32_MAX;
    if (seg != countSeg) { countSeg = seg; filler::cues = 0; }  // a count per recording
    if (filler::cueSeq != seenSeq) {
      seenSeq = filler::cueSeq;  // the name is checked JSON-safe (oc_kws_name_ok)
      if (sdOk) appendManifest(bootDir, String("{\"filler\":\"") + filler::name + "\",\"score\":" +
                                            String((float)filler::cueScore, 3) + ",\"at_ms\":" + filler::cueAtMs + "}");
      fillerFlashUntil = millis() + FILLER_FLASH_MS;
      showFor(FILLER_FLASH_MS);
    }
    if (kwsTrying && filler::invokes >= KWS_TRIAL_RUNS) {  // the new model ran: off trial
      kwsTrying = false;
      prefs.putUChar("kws_try", 0);
    }
  }
  if (sdOk && repairTail != repairHead) {
    uint32_t seq = repairQueue[repairTail % 8];
    const SdErrorAt &e = repairWhy[repairTail % 8];
    // A card error cut it (rec-17): when, how long the failed write took (a timeout waits 500 ms for the card),
    // and what else was running then.
    repairChunk(bootDir, seq, false,
                String(",\"sd_error\":\"") + e.why + "\",\"errno\":" + e.err + ",\"error_at_ms\":" + e.at + ",\"took_ms\":" + e.took +
                    ",\"ble\":\"" + (e.ble ? "connected" : e.adv ? "advertising" : "off") + "\"" + ",\"wifi\":" + (e.wifi ? "true" : "false") +
                    ",\"serving\":" + (e.serving ? "true" : "false"));
    repairTail++;
  }
  if (sdOk && retryTail != retryHead) {  // rec-18: each card read or write that needed another try
    const SdRetryAt &r = retryLog[retryTail % 8];
    appendManifest(bootDir, String("{\"sd_retry\":\"") + (r.op == 'w' ? "write" : r.op == 'r' ? "read" : "status") + "\",\"tries\":" + r.tries +
                   ",\"ok\":" + (r.ok ? "true" : "false") + ",\"at_ms\":" + r.at + "}");
    retryTail++;
  }
  if (!sdOk) cardChunks = -1;
  else if (cardChunks < 0) {  // counted once a mount (thousands of files after days away: seconds), then kept up
    LOOP_AT("count");
    closedSeen = closedChunks;
    long n = chunkFilesOnCard();
    cardChunks = n < 0 ? -1 : max(0L, n - (chunkOpen ? 1 : 0));  // the one being written isn't the Mac's yet
  } else {
    cardChunks += closedChunks - closedSeen;  // the writer's closed chunks since
    closedSeen = closedChunks;
  }
  LOOP_AT("http");
  if (ring) wifiLoop();
  if (updating && millis() - updateAt > UPDATE_IDLE_MS) { abandonUpdate(); updateFailed(); }
  if (restartAt && millis() > restartAt) { bleGoodbye(); ESP.restart(); }
  if (rejectAt && (int32_t)(millis() - rejectAt) >= 0) { bleGoodbye(); rejectAt = 0; esp_ota_mark_app_invalid_rollback_and_reboot(); }
  if (firmwarePending && millis() > CONFIRM_WITHIN_MS) { bleGoodbye(); esp_ota_mark_app_invalid_rollback_and_reboot(); }
  LOOP_AT("button");
  buttonLoop();
  lightLoop();
  LOOP_AT("ble");
  bleLoop();
  LOOP_AT("display");
  displayLoop();
  delay(2);
}
