// Host <-> bench-sketch protocol, shared by every sketch in firmware/bench/.
//
// The board is quiet until told to do something, so the host never misses output that
// was printed while USB was still re-enumerating after an upload or reset.
//
//   host sends   i   -> @@ {"v":1,"sketch":"<name>"}            (which firmware is running?)
//                g   -> run the test: human-readable lines, then one final @@ result line
//                ?   -> re-print the last @@ result
//                (sketches may add their own commands, e.g. 02_mic's m and r)
//
// Result lines are "@@ " + a single-line JSON object with "v":1 and "sketch". They carry raw
// measurements only -- the host decides pass/warn/fail, so thresholds never need a reflash.
//
// Compile with:  arduino-cli compile --libraries firmware/bench/libraries ...

#pragma once
#include <Arduino.h>

namespace bench {

static const char *sketchName = "";
static String lastResult;

// Call first in setup(). Prints one ready line; the host doesn't depend on seeing it.
inline void begin(const char *name) {
  sketchName = name;
  Serial.begin(115200);
  Serial.printf("@@ {\"v\":1,\"sketch\":\"%s\",\"ready\":true}\n", name);
}

// Emit a result. `fields` is the JSON body without braces, e.g. "\"ok\":true,\"n\":3".
inline void result(const String &fields) {
  lastResult = String("@@ {\"v\":1,\"sketch\":\"") + sketchName + "\"" +
               (fields.length() ? "," + fields : "") + "}";
  Serial.println(lastResult);
  Serial.flush();
}

// Non-blocking. Handles i and ?, returns any other command character, or -1 if none.
inline int poll() {
  while (Serial.available()) {
    int c = Serial.read();
    if (c == 'i') {
      Serial.printf("@@ {\"v\":1,\"sketch\":\"%s\"}\n", sketchName);
    } else if (c == '?') {
      if (lastResult.length()) Serial.println(lastResult);
      else Serial.printf("@@ {\"v\":1,\"sketch\":\"%s\",\"none\":true}\n", sketchName);
    } else if (c != '\r' && c != '\n') {
      return c;
    }
  }
  return -1;
}

// Tiny JSON field builders -- enough for flat results without pulling in ArduinoJson.
inline String kv(const char *k, long v) { return String("\"") + k + "\":" + v; }
inline String kv(const char *k, unsigned long v) { return String("\"") + k + "\":" + v; }
inline String kv(const char *k, int v) { return kv(k, (long)v); }
inline String kv(const char *k, unsigned int v) { return kv(k, (unsigned long)v); }
inline String kv(const char *k, uint64_t v) { return String("\"") + k + "\":" + String((unsigned long long)v); }
inline String kv(const char *k, float v, int dp = 1) { return String("\"") + k + "\":" + String(v, dp); }
inline String kv(const char *k, bool v) { return String("\"") + k + "\":" + (v ? "true" : "false"); }
// Only for strings we control (no quotes/backslashes): chip names, error codes, MACs.
inline String kvs(const char *k, const char *v) { return String("\"") + k + "\":\"" + v + "\""; }

}  // namespace bench
