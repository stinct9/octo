// Bench test 3: Wi-Fi connects, and signal strength is good enough for uploads.
//
// Copy secrets.h.example to secrets.h and fill in your network. secrets.h is gitignored.
// The XIAO ESP32S3 has no onboard antenna -- fit the U.FL antenna first. Check RSSI from the
// room the device will sit in when it's home, since that's where uploads happen.
//
// Protocol (see libraries/BenchProto): send g. Scans, connects, samples RSSI for 5 s,
// disconnects, and reports raw numbers. The host judges them (roughly: > -60 great,
// -60..-70 fine, -70..-80 slow/flaky, < -80 unusable).
//
// The result deliberately leaves out SSIDs -- neighbours' network names don't belong in
// saved run logs, and ours is in secrets.h.

#include <BenchProto.h>
#include <WiFi.h>
#include "secrets.h"

const uint32_t CONNECT_TIMEOUT_MS = 20000;
const int RSSI_SAMPLES = 10;

void run() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  Serial.println("Scanning...");
  int n = WiFi.scanNetworks();
  int ownChannel = -1, ownScanRssi = 0, sameChannel = 0;
  for (int i = 0; i < n; i++) {
    if (WiFi.SSID(i) == WIFI_SSID && ownChannel < 0) {
      ownChannel = WiFi.channel(i);
      ownScanRssi = WiFi.RSSI(i);
    }
  }
  for (int i = 0; i < n; i++) {
    if (WiFi.SSID(i) != WIFI_SSID && WiFi.channel(i) == ownChannel) sameChannel++;
  }
  Serial.printf("%d networks visible; ours %s\n", n,
                ownChannel < 0 ? "NOT visible (5 GHz only? wrong name?)" : "visible");
  String r = bench::kv("networks_visible", n) + "," + bench::kv("own_visible", ownChannel >= 0) + ",";
  if (ownChannel >= 0) {
    r += bench::kv("channel", ownChannel) + "," + bench::kv("scan_rssi", ownScanRssi) + "," +
         bench::kv("others_on_channel", sameChannel) + ",";
  }
  WiFi.scanDelete();

  Serial.print("Connecting");
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < CONNECT_TIMEOUT_MS) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    // ESP32 is 2.4 GHz only. A 5 GHz-only SSID never shows up in the scan.
    WiFi.disconnect(true);
    Serial.println("Not connected -- check credentials, and that the network is 2.4 GHz");
    return bench::result(r + bench::kv("connected", false) + "," + bench::kvs("error", "connect_timeout"));
  }
  uint32_t connectMs = millis() - t0;
  Serial.printf("Connected in %lu ms, IP %s\n", connectMs, WiFi.localIP().toString().c_str());

  int minR = 0, maxR = -127;
  long sum = 0;
  for (int i = 0; i < RSSI_SAMPLES; i++) {
    int v = WiFi.RSSI();
    minR = i == 0 ? v : min(minR, v);
    maxR = max(maxR, v);
    sum += v;
    Serial.printf("rssi %d dBm\n", v);
    delay(500);
  }
  WiFi.disconnect(true);
  bench::result(r + bench::kv("connected", true) + "," + bench::kv("connect_ms", connectMs) + "," +
                bench::kv("rssi_min", minR) + "," + bench::kv("rssi_max", maxR) + "," +
                bench::kv("rssi_mean", (float)sum / RSSI_SAMPLES));
}

void setup() { bench::begin("03_wifi"); }

void loop() {
  if (bench::poll() == 'g') run();
  delay(10);
}
