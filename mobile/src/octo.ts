// The recorder's own drawing code (firmware/recorder/octo_display.js, a pixel-exact port of octo_display.h that
// docs/landing/check_oled.py checks), so the octopus in the app is the one on the recorder's screen.
import src from '../../firmware/recorder/octo_display.js?raw'

export type Frame = Uint8Array
export type Mood = number

export interface Od {
  W: number
  H: number
  ANIM_MS: number
  CHECKING: number; DONE: number; FAILED: number; UNDONE: number
  AWAKE: Mood; ASLEEP: Mood; ALERT: Mood
  frame(): Frame
  lit(f: Frame, x: number, y: number): number
  recording(f: Frame, seconds: number, ms: number): void
  stopped(f: Frame, ms: number): void
  attention(f: Frame, what: string, ms: number): void
  hold(f: Frame, held: number, toggleMs: number, rec: number): void
  hello(f: Frame, ms: number): void
  setup(f: Frame, ms: number): void
  passkey(f: Frame, code: number, ms: number): void
  animStart(f: Frame, ms: number): void
  animStop(f: Frame, ms: number): void
  updating(f: Frame, done: number, size: number, ms: number): void
  updateResult(f: Frame, kind: number, fw: string, ms: number): void
  clear(f: Frame): void
  px(f: Frame, x: number, y: number, on: number): void
  fill(f: Frame, x: number, y: number, w: number, h: number, on: number): void
  rect(f: Frame, x: number, y: number, w: number, h: number): void
  disc(f: Frame, cx: number, cy: number, r: number, on: number): void
  arc(f: Frame, cx: number, cy: number, r: number, a0: number, a1: number): void
  text(f: Frame, x: number, y: number, t: string, s: number): number
  textWidth(t: string, s: number): number
  octopus(f: Frame, cx: number, cy: number, r: number, mood: Mood, phase: number): void
  octopusEyes(f: Frame, cx: number, cy: number, r: number, open: number, alert: number, phase: number): void
  clamp01(x: number): number
  easeOut(t: number): number
  easeBack(t: number): number
}

// The file is a plain script (the landing page inlines it; node runs it for the check), so it's evaluated here
// rather than imported as a module.
export const OD = new Function(`${src}\nreturn OD;`)() as Od
