// Hand-written mirror of bench/events.py. Keep the two in step.

export type Status = 'pass' | 'warn' | 'fail' | 'skipped' | 'cancelled' | 'aborted'
export type StepState = 'queued' | 'running' | Status
export type RunState = 'idle' | 'running' | 'waiting_for_user' | 'cancelling'
export type Verdict = 'pass' | 'warn' | 'fail' | null

export type EventType =
  | 'run.started' | 'run.state' | 'run.finished'
  | 'step.started' | 'step.log' | 'step.progress' | 'step.metric' | 'step.level'
  | 'step.prompt' | 'step.artifact' | 'step.finished'

export interface RunEvent {
  seq: number
  run_id: string
  t: number // unix seconds
  type: EventType
  step: string | null
  data: Record<string, any>
}

export interface StepInfo {
  id: string
  title: string
  needs_user: boolean
  skip_reason: string | null
}

export interface RunSummary {
  run_id: string
  started_at: number | null
  status: Status | 'running'
  steps: Record<string, Status>
}

export interface Metric {
  key: string
  label: string
  value: unknown
  unit?: string | null
  verdict?: Verdict
  previous?: unknown
}

export interface LogLine {
  line: string
  stream: 'device' | 'tool'
}

export interface Progress {
  phase: string
  pct?: number | null
  detail?: string | null
}

export interface Levels {
  levels: number[]
  window_s: number
  quiet_window: [number, number]
  speech_window: [number, number]
  cue_at: number | null
  seconds: number
}

export interface Transcript {
  mode: string
  language: string
  language_p: number
  seconds: number
  text: string
  mean_logprob: number | null
}

export type PromptPhase = 'ready' | 'silent' | 'speak' | 'done' | 'transfer'

export interface Prompt {
  step: string
  phase: PromptPhase
  message?: string
  at: number // event time, unix seconds
}

export interface StepView {
  id: string
  title: string
  needsUser: boolean
  state: StepState
  summary?: string
  hint?: string | null
  duration?: number
  metrics: Metric[]
  logs: LogLine[]
  progress?: Progress | null
  levels?: Levels
  recording?: string // run ID whose recording to play
  transcripts?: Transcript[]
}

export interface RunView {
  runId: string
  lastSeq: number
  state: RunState
  status?: Status
  reason?: string
  startedAt: number
  finishedAt?: number
  steps: StepView[]
  prompt?: Prompt | null
  recordingAt?: number | null // when the board started recording (the 'silent' cue)
  level?: number | null
}
