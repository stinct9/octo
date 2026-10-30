// Bench test 0: the board is alive, USB serial works, and the chip is what we paid for.
//
// Expected on a XIAO ESP32S3 Sense: ESP32-S3, 8 MB flash, 8 MB PSRAM.
// If PSRAM reads 0, the sketch was built without PSRAM=opi -- rebuild with it.
// The mic test buffers audio in PSRAM, so this has to pass first.
//
// Protocol: see libraries/BenchProto. Send g to run.

#include <BenchProto.h>
#include <esp_mac.h>

void run() {
  // getEfuseMac() returns the bytes reversed when printed as one number; read them in order.
  uint8_t mac[6];
  esp_efuse_mac_get_default(mac);
  char macStr[18];
  snprintf(macStr, sizeof(macStr), "%02X:%02X:%02X:%02X:%02X:%02X", mac[0], mac[1], mac[2], mac[3],
           mac[4], mac[5]);

  Serial.printf("Chip:  %s rev %d, %d cores @ %lu MHz\n", ESP.getChipModel(), ESP.getChipRevision(),
                ESP.getChipCores(), ESP.getCpuFreqMHz());
  Serial.printf("Flash: %lu MB\n", ESP.getFlashChipSize() / (1024 * 1024));
  Serial.printf("PSRAM: %lu KB\n", ESP.getPsramSize() / 1024);
  Serial.printf("MAC:   %s\n", macStr);

  bench::result(bench::kvs("chip", ESP.getChipModel()) + "," +
                bench::kv("rev", (int)ESP.getChipRevision()) + "," +
                bench::kv("cores", (int)ESP.getChipCores()) + "," +
                bench::kv("cpu_mhz", (unsigned long)ESP.getCpuFreqMHz()) + "," +
                bench::kv("flash_bytes", (unsigned long)ESP.getFlashChipSize()) + "," +
                bench::kv("psram_bytes", (unsigned long)ESP.getPsramSize()) + "," +
                bench::kvs("mac", macStr));
}

void setup() {
  // User LED is GPIO21 and active-LOW. GPIO21 is ALSO the Sense's SD chip-select, so only
  // this sketch touches the LED -- never anything that uses SD.
  pinMode(LED_BUILTIN, OUTPUT);
  bench::begin("00_hello");
}

void loop() {
  if (bench::poll() == 'g') run();
  // Blink as a visible heartbeat, without printing anything.
  digitalWrite(LED_BUILTIN, (millis() / 500) % 2 ? HIGH : LOW);
  delay(10);
}
