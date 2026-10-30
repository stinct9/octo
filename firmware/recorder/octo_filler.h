// The filler cue (B20, rec-24): "you know" spotted on the recorder while it records, from the owner's own model.
// Measured in spikes/b20-filler-cue (docs/learnings.md, Speech, 20-21 Oct): a 1 s window every 0.24 s, 40 log-mel
// bands as torchaudio makes them, a small int8 DS-CNN (esp-tflite-micro with ESP-NN; esp-dsp's FFT). ~46 ms of
// work a step alone on the chip.
//
// The model is the owner's: trained on their voice, so it's never in git. Since rec-27 (B29) it's data: sent by the Mac
// into a slot of the spare flash (octo_kws_blob.h, recorder.ino), with its filler's name and threshold; a build with
// octo_filler_model.h (copied from the lab's data by `octo device flash`) also has that one built in, as before.
//
// The reader task feeds it what it records (after the DC blocker, as in training); its own task scores each step
// and counts cues; loop() writes the manifest line and the screen shows it. Nothing here touches the card.
#pragma once
#include <esp_dsp.h>
#include <esp_heap_caps.h>
#include <freertos/idf_additions.h>
#include <freertos/stream_buffer.h>
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"
#include <new>

namespace filler {

const int WIN = 16000, STEP = 3840;  // 1 s, moved 0.24 s (12 frames), so 37 of 51 frames are reused
const int N_FFT = 512, WIN_LEN = 480, HOP = 320, N_MELS = 40, FRAMES = 51, BINS = N_FFT / 2 + 1;
const int ARENA = 64 * 1024;         // width 32 uses 57,868; in PSRAM (below)
const uint32_t MERGE_MS = 750;       // windows over the threshold closer than this are one cue

volatile bool on = false;            // the owner's setting (kept in prefs by recorder.ino)
volatile float threshold = 0.96f;    // the model's own, or the owner's (recorder.ino)
char name[24] = "";                  // the filler the loaded model is for
volatile bool ready = false, arenaPsram = false;
volatile uint32_t cues = 0;          // this recording's, for the screen (recorder.ino resets it)
volatile uint32_t cueSeq = 0, cueAtMs = 0;
volatile float cueScore = 0;
volatile uint32_t workMaxUs = 0, gaps = 0;
volatile uint32_t invokes = 0;       // good ones on what it heard: a new model's trial ends after some (recorder.ino)
volatile bool gap = false;           // the feed dropped something: start the window again

StreamBufferHandle_t feedBuf = nullptr;
tflite::MicroInterpreter *interp = nullptr;
TfLiteTensor *in = nullptr, *out = nullptr;
float hann[N_FFT];
alignas(16) float fft[2 * N_FFT];    // esp-dsp's S3 assembly wants 16-byte alignment (the spike scored garbage without)
int melLo[N_MELS], melHi[N_MELS];
float (*melf)[FRAMES] = nullptr;     // [N_MELS][FRAMES], in PSRAM
int16_t *win = nullptr;              // PSRAM: the last second, oldest first; each step arrives at its end
const float *fb = nullptr;           // the mel filterbank, [BINS][N_MELS], from the model
uint8_t *arena = nullptr;

// One frame as torchaudio's centred STFT makes it: reflect padding at the window's edges, a 480 Hann window in
// the middle of 512, power, mel, log(+1e-6).
void frame(const int16_t *x, int f) {
  for (int k = 0; k < N_FFT; k++) {
    int j = f * HOP + k - N_FFT / 2;
    if (j < 0) j = -j;
    else if (j >= WIN) j = 2 * (WIN - 1) - j;
    fft[2 * k] = x[j] / 32768.0f * hann[k];
    fft[2 * k + 1] = 0;
  }
  dsps_fft2r_fc32(fft, N_FFT);
  dsps_bit_rev_fc32(fft, N_FFT);
  float pw[BINS];
  for (int b = 0; b < BINS; b++) pw[b] = fft[2 * b] * fft[2 * b] + fft[2 * b + 1] * fft[2 * b + 1];
  for (int m = 0; m < N_MELS; m++) {
    float s = 0;
    for (int b = melLo[m]; b <= melHi[m]; b++) s += pw[b] * fb[b * N_MELS + m];
    melf[m][f] = logf(s + 1e-6f);
  }
}

// `reuse`: x is the last window moved on by STEP. Frames 1-49 see only samples inside the window, so the old
// 13-49 are the new 1-37 exactly; frame 0, 38-49 and 50 are computed (checked against computing all: identical).
float score(const int16_t *x, bool reuse) {
  const int shift = STEP / HOP;
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
  for (int m = 0; m < N_MELS; m++)  // NHWC (1, 40, 51, 1)
    for (int f = 0; f < FRAMES; f++) {
      int q = lroundf((melf[m][f] - mean) / in->params.scale) + in->params.zero_point;
      in->data.int8[m * FRAMES + f] = q < -128 ? -128 : q > 127 ? 127 : q;
    }
  if (interp->Invoke() != kTfLiteOk) return 0;
  invokes++;
  return (out->data.int8[1] - out->params.zero_point) * out->params.scale;
}

// rec-25: the step is received straight into the window's end and the window moved on with one memmove, in place of a
// ring, a step buffer in internal RAM (7.5 KB) and two sample-by-sample copies each step.
void task(void *) {
  int16_t *last = win + WIN - STEP;
  int have = 0, filled = 0;
  bool primed = false;
  uint32_t lastCue = 0;
  for (;;) {
    size_t n = xStreamBufferReceive(feedBuf, (char *)(last + have), (STEP - have) * 2, portMAX_DELAY) / 2;
    if (gap) {  // a hole in what it heard: the frames before it no longer join up
      gap = false;
      have = filled = 0;
      primed = false;
      continue;
    }
    have += n;
    if (have < STEP) continue;
    have = 0;
    filled = min(filled + STEP, WIN);
    if (filled == WIN) {
      uint32_t t0 = micros();
      float s = score(win, primed);
      primed = true;
      uint32_t took = micros() - t0;
      if (took > workMaxUs) workMaxUs = took;
      uint32_t now = millis();
      if (s >= threshold && now - lastCue > MERGE_MS) {
        cueScore = s;
        cueAtMs = now;
        cues++;
        cueSeq++;
      }
      if (s >= threshold) lastCue = now;
    }
    memmove(win, win + STEP, (WIN - STEP) * 2);  // on by a step; the next arrives at the end
  }
}

// From the reader task, what it just recorded. Never waits: if the scorer is behind, the gap is noted.
void feed(const int16_t *x, size_t n) {
  if (!ready || !on) return;
  if (xStreamBufferSpacesAvailable(feedBuf) >= n * 2) xStreamBufferSend(feedBuf, x, n * 2, 0);
  else if (!gap) { gap = true; gaps++; xStreamBufferSend(feedBuf, x, 2, 0); }  // a sample to wake the scorer
}

// Once, at boot: the memory. False (and no filler cue) if there's none for it.
bool init() {
  for (int k = 0; k < N_FFT; k++) hann[k] = 0;
  for (int n = 0; n < WIN_LEN; n++) hann[(N_FFT - WIN_LEN) / 2 + n] = 0.5f - 0.5f * cosf(2 * PI * n / WIN_LEN);
  if (dsps_fft2r_init_fc32(NULL, N_FFT) != ESP_OK) return false;
  // All of it in PSRAM: internal RAM is what Wi-Fi and Bluetooth live on. With the arena there (64 KB, taken
  // before Bluetooth started), Bluetooth couldn't make a semaphore and the recorder restarted at boot (21 Oct).
  // PSRAM costs ~25% (spike: 47 ms a window against 38).
  melf = (float (*)[FRAMES])heap_caps_malloc(N_MELS * FRAMES * sizeof(float), MALLOC_CAP_SPIRAM);
  win = (int16_t *)heap_caps_malloc(WIN * 2, MALLOC_CAP_SPIRAM);
  feedBuf = xStreamBufferCreateWithCaps(STEP * 2 * 2, 2, MALLOC_CAP_SPIRAM);
  arena = (uint8_t *)heap_caps_aligned_alloc(16, ARENA, MALLOC_CAP_SPIRAM);
  arenaPsram = true;
  return melf && win && feedBuf && arena;
}

// A model (16-byte aligned, `len` bytes, kept for as long as it runs) and its filterbank: checked as far as it can be
// before it's trusted with the recorder (rec-27: one sent by the Mac could be anything): a valid TFLite file of this
// schema, only the ops below, the input and output the scorer writes and reads, and one run on silence. False: not it.
// Once a boot (they share the arena): the recorder restarts to load another.
bool load(const uint8_t *model, size_t len, const float *bank, const char *filler) {
  static tflite::MicroMutableOpResolver<6> ops;
  static bool opsAdded = false;
  if (interp || !arena) return false;
  if (!opsAdded) {
    opsAdded = true;
    ops.AddConv2D();
    ops.AddDepthwiseConv2D();
    ops.AddAveragePool2D();
    ops.AddFullyConnected();
    ops.AddPad();
    ops.AddSoftmax();
  }
  flatbuffers::Verifier v(model, len);
  if (!tflite::VerifyModelBuffer(v) || tflite::GetModel(model)->version() != TFLITE_SCHEMA_VERSION) return false;
  auto *it = new (std::nothrow) tflite::MicroInterpreter(tflite::GetModel(model), ops, arena, ARENA);
  if (!it) return false;
  TfLiteTensor *i = nullptr, *o = nullptr;
  bool ok = it->AllocateTensors() == kTfLiteOk && (i = it->input(0)) && (o = it->output(0)) && i->type == kTfLiteInt8 &&
            i->dims->size == 4 && i->dims->data[0] == 1 && i->dims->data[1] == N_MELS && i->dims->data[2] == FRAMES &&
            i->dims->data[3] == 1 && o->type == kTfLiteInt8 && o->dims->size == 2 && o->dims->data[1] == 2;
  if (ok) {
    memset(i->data.int8, 0, N_MELS * FRAMES);
    ok = it->Invoke() == kTfLiteOk;
  }
  if (!ok) { delete it; return false; }
  interp = it;
  in = i;
  out = o;
  fb = bank;
  strlcpy(name, filler, sizeof name);
  for (int m = 0; m < N_MELS; m++) {
    melLo[m] = BINS;
    melHi[m] = -1;
    for (int b = 0; b < BINS; b++)
      if (fb[b * N_MELS + m] != 0) { melLo[m] = min(melLo[m], b); melHi[m] = b; }
  }
  return true;
}

// Once a model is loaded: its task. Core 0 beside Wi-Fi and Bluetooth (higher priority, so they come first); the
// reader and writer have core 1.
bool start() {
  if (!interp || xTaskCreatePinnedToCore(task, "filler", 6144, nullptr, 1, nullptr, 0) != pdPASS) return false;
  ready = true;
  return true;
}

}  // namespace filler
