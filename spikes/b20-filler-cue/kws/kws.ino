// SPIKE (B20, step 3): the "you know" model on the recorder's chip. A test sketch, not the recorder: it doesn't
// record. Quiet until commanded over USB serial (as firmware/bench):
//   i            identity
//   w + 32000 B  one 1 s window of int16 samples (little-endian) -> @@ {"score","feat_us","inf_us"}
//   W + 32000 B  the same, for the window 0.24 s after the last one sent: only the new frames computed
//   p            the last window again, time per layer
//   l            live: the board's mic, a 1 s window every 0.24 s -> @@ {"t_ms","db","score","feat_us","inf_us"}
//                each (db: the new 0.24 s's level, dBFS); x stops. No screen here: drawing it is ~25 ms, measured on the recorder.
// model.h comes from convert.py (trained on the owner's voice; gitignored).
// FQBN esp32:esp32:XIAO_ESP32S3:PSRAM=opi, esp32 core 3.3.12 (bundles esp-tflite-micro, esp-nn, esp-dsp).

#include <ESP_I2S.h>
#include <esp_dsp.h>
#include <esp_heap_caps.h>
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/micro/micro_profiler.h"
#include "tensorflow/lite/schema/schema_generated.h"
#include "model.h"

const int SR = 16000, WIN = 16000, HOP_LIVE = 3840;  // 0.24 s: 12 frames, so the rest are reused
const int N_FFT = 512, WIN_LEN = 480, HOP = 320, N_MELS = 40, FRAMES = 51, BINS = N_FFT / 2 + 1;
const int ARENA = 64 * 1024;  // w32 uses 57,868 (w48 85,660, width 64 113,452)
bool arenaInPsram = false;

I2SClass i2s;
tflite::MicroInterpreter *interp;
tflite::MicroProfiler profiler;
TfLiteTensor *in, *out;
float hann[N_FFT];
alignas(16) float fft[2 * N_FFT];  // esp-dsp's S3 assembly wants 16-byte alignment
int melLo[N_MELS], melHi[N_MELS];  // each mel filter's nonzero bins
uint32_t fftUs, melUs;
float melf[N_MELS][FRAMES];
int16_t win[WIN];          // the window being scored
int16_t ring[WIN];         // live: the last second of the mic
int ringAt = 0;
bool live = false, primed = false;

void setupModel() {
  static tflite::MicroMutableOpResolver<8> ops;
  ops.AddAveragePool2D();
  ops.AddReshape();
  ops.AddConv2D();
  ops.AddDepthwiseConv2D();
  ops.AddFullyConnected();
  ops.AddMean();
  ops.AddPad();
  ops.AddSoftmax();
  uint8_t *arena = (uint8_t *)heap_caps_aligned_alloc(16, ARENA, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  if (!arena) {  // slower, but there's 8 MB of it
    arena = (uint8_t *)heap_caps_aligned_alloc(16, ARENA, MALLOC_CAP_SPIRAM);
    arenaInPsram = true;
  }
  static tflite::MicroInterpreter it(tflite::GetModel(kws_model), ops, arena, ARENA, nullptr, &profiler);
  interp = &it;
  if (interp->AllocateTensors() != kTfLiteOk) Serial.println("@@ {\"error\":\"allocate\"}");
  in = interp->input(0);
  out = interp->output(0);
}

// As convert.py's features() and train.py's: centre-padded (reflect) frames of 512 with a 480 Hann window in the
// middle, power spectrum, mel, log(+1e-6), minus the mean.
// `reuse`: x is the last window moved on by HOP_LIVE (12 frames). Frames 1-49 see only samples inside the window
// (the reflect padding touches 0 and 50), so the old 13-49 are the new 1-37, exactly; 14 frames are computed.
void frame(const int16_t *x, int f);
void features(const int16_t *x, bool reuse = false) {
  fftUs = melUs = 0;
  const int shift = HOP_LIVE / HOP;
  if (reuse) {
    for (int m = 0; m < N_MELS; m++) memmove(&melf[m][1], &melf[m][1 + shift], (FRAMES - 2 - shift) * sizeof(float));
    frame(x, 0);
    for (int f = FRAMES - 1 - shift; f < FRAMES; f++) frame(x, f);
  } else {
    for (int f = 0; f < FRAMES; f++) frame(x, f);
  }
  float mean = 0;
  for (int m = 0; m < N_MELS; m++)
    for (int f = 0; f < FRAMES; f++) mean += melf[m][f];
  mean /= N_MELS * FRAMES;
  float sc = in->params.scale;
  int zp = in->params.zero_point;
  for (int m = 0; m < N_MELS; m++)      // NHWC (1, 40, 51, 1)
    for (int f = 0; f < FRAMES; f++) {
      int q = lroundf((melf[m][f] - mean) / sc) + zp;
      in->data.int8[m * FRAMES + f] = q < -128 ? -128 : q > 127 ? 127 : q;
    }
}

void frame(const int16_t *x, int f) {
  {
    for (int k = 0; k < N_FFT; k++) {
      int j = f * HOP + k - N_FFT / 2;                // index into x, before reflect padding
      if (j < 0) j = -j;
      else if (j >= WIN) j = 2 * (WIN - 1) - j;
      fft[2 * k] = x[j] / 32768.0f * hann[k];
      fft[2 * k + 1] = 0;
    }
    uint32_t a = micros();
    dsps_fft2r_fc32(fft, N_FFT);
    dsps_bit_rev_fc32(fft, N_FFT);
    uint32_t b0 = micros();
    float pw[BINS];
    for (int b = 0; b < BINS; b++) pw[b] = fft[2 * b] * fft[2 * b] + fft[2 * b + 1] * fft[2 * b + 1];
    for (int m = 0; m < N_MELS; m++) {
      float s = 0;
      for (int b = melLo[m]; b <= melHi[m]; b++) s += pw[b] * kws_fb[b][m];
      melf[m][f] = logf(s + 1e-6f);
    }
    fftUs += b0 - a;
    melUs += micros() - b0;
  }
}

float score(const int16_t *x, uint32_t *featUs, uint32_t *infUs, bool reuse = false) {
  uint32_t t0 = micros();
  features(x, reuse);
  uint32_t t1 = micros();
  interp->Invoke();
  uint32_t t2 = micros();
  *featUs = t1 - t0;
  *infUs = t2 - t1;
  return (out->data.int8[1] - out->params.zero_point) * out->params.scale;
}

void setup() {
  Serial.begin(115200);
  Serial.setRxBufferSize(40000);
  for (int k = 0; k < N_FFT; k++) hann[k] = 0;
  for (int n = 0; n < WIN_LEN; n++) hann[(N_FFT - WIN_LEN) / 2 + n] = 0.5f - 0.5f * cosf(2 * PI * n / WIN_LEN);
  dsps_fft2r_init_fc32(NULL, N_FFT);
  for (int m = 0; m < N_MELS; m++) {
    melLo[m] = BINS;
    melHi[m] = -1;
    for (int b = 0; b < BINS; b++)
      if (kws_fb[b][m] != 0) { melLo[m] = min(melLo[m], b); melHi[m] = b; }
  }
  setupModel();
}

void readWindow(bool reuse) {  // W: the window after the last one sent, HOP_LIVE on (a test of the reuse)
  size_t got = Serial.readBytes((char *)win, sizeof win);
  uint32_t fu, iu;
  float s = got == sizeof win ? score(win, &fu, &iu, reuse) : -1;
  Serial.printf("@@ {\"v\":1,\"sketch\":\"kws\",\"got\":%u,\"score\":%.5f,\"feat_us\":%u,\"inf_us\":%u,\"fft_us\":%u,\"mel_us\":%u}\n",
                (unsigned)got, s, fu, iu, fftUs, melUs);
}

void startLive() {
  i2s.setPinsPdmRx(42, 41);
  live = i2s.begin(I2S_MODE_PDM_RX, SR, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
  memset(ring, 0, sizeof ring);
  ringAt = 0;
  primed = false;
  if (!live) Serial.println("@@ {\"error\":\"mic\"}");
}

void liveStep() {
  // The recorder's DC blocker (recorder.ino readerTask), so the model hears what it was trained on.
  static float px = 0, py = 0;
  static int16_t buf[HOP_LIVE];
  size_t n = i2s.readBytes((char *)buf, sizeof buf) / 2;
  for (size_t i = 0; i < n; i++) {
    float x = buf[i], y = x - px + 0.995f * py;
    px = x;
    py = y;
    ring[ringAt] = (int16_t)constrain(lroundf(y), -32768L, 32767L);
    ringAt = (ringAt + 1) % WIN;
  }
  if (n < HOP_LIVE) return;
  float e = 0;
  for (int i = 0; i < HOP_LIVE; i++) {
    float v = ring[(ringAt - HOP_LIVE + i + WIN) % WIN] / 32768.0f;
    e += v * v;
  }
  float db = 10 * log10f(e / HOP_LIVE + 1e-12f);
  for (int i = 0; i < WIN; i++) win[i] = ring[(ringAt + i) % WIN];  // oldest first
  uint32_t fu, iu;
  float s = score(win, &fu, &iu, primed);
  primed = true;
  Serial.printf("@@ {\"v\":1,\"sketch\":\"kws\",\"t_ms\":%lu,\"db\":%.1f,\"score\":%.4f,\"feat_us\":%u,\"inf_us\":%u}\n",
                millis(), db, s, fu, iu);
}

void loop() {
  if (Serial.available()) {
    int c = Serial.read();
    if (c == 'i') Serial.printf("@@ {\"v\":1,\"sketch\":\"kws\",\"arena_used\":%u,\"psram\":%d,\"internal_free\":%u,\"threshold\":%.2f}\n",
                                (unsigned)interp->arena_used_bytes(), arenaInPsram,
                                (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL), KWS_THRESHOLD);
    else if (c == 'w' || c == 'W') readWindow(c == 'W');
    else if (c == 'p') { profiler.ClearEvents(); uint32_t a, b; score(win, &a, &b); profiler.LogTicksPerTagCsv(); }
    else if (c == 'f') Serial.write((const uint8_t *)in->data.int8, N_MELS * FRAMES);  // after a w: the input as quantized
    else if (c == 'l' && !live) startLive();
    else if (c == 'x' && live) { i2s.end(); live = false; }
  }
  if (live) liveStep();
}
