// Notification sounds: a recorded paging file for live alarms, Web Audio
// beeps for the rest. Browsers keep audio silent until the first user
// interaction, so the provider calls unlockAudio() on the first tap or
// keypress. iOS Safari also refuses an audio file the page never played
// during a tap, so unlockAudio() plays the paging file once, muted.

export type SoundKind = "telemetry" | "disruption" | "ledger";

const MUTE_KEY = "sf-sound-muted";
// One event often arrives through two paths (e.g. telemetry + safety_alert);
// a short per-kind cooldown keeps that to a single sound.
const COOLDOWN_MS = 1500;

let ctx: AudioContext | null = null;
// Live telemetry problems (PLC alarms such as ALM-410, safety alerts) play a
// recorded paging sound; the disruption drill keeps the two-tone beep. The
// beep is also the fallback if the file can't play.
const alarm = typeof Audio !== "undefined" ? new Audio("/sounds/paging.mp3") : null;
if (alarm) alarm.preload = "auto";
let muted = readMuted();
let primed = false;
let priming = false;
// Set by playAlarmNow(): the priming play is the real one, so keep it sounding.
let audibleOnPrime = false;
const readyListeners = new Set<() => void>();
const lastPlayed: Record<SoundKind, number> = { telemetry: 0, disruption: 0, ledger: 0 };

function readMuted(): boolean {
  try {
    return window.localStorage.getItem(MUTE_KEY) === "1";
  } catch {
    return false;
  }
}

export function isMuted(): boolean {
  return muted;
}

export function setMuted(v: boolean) {
  muted = v;
  if (v && alarm) {
    alarm.pause();
    alarm.currentTime = 0;
  }
  try {
    window.localStorage.setItem(MUTE_KEY, v ? "1" : "0");
  } catch {
    // storage blocked: the setting just won't survive a reload
  }
}

/** True once both the beeps and the paging file are allowed to play. */
export function audioReady(): boolean {
  return ctx?.state === "running" && (primed || !alarm);
}

/** Calls fn whenever audioReady() may have changed; returns the unsubscribe. */
export function onAudioReady(fn: () => void): () => void {
  readyListeners.add(fn);
  return () => readyListeners.delete(fn);
}

function notifyReady() {
  readyListeners.forEach((fn) => fn());
}

// Must run inside the tap handler: play() outside a gesture is refused on iOS.
function primeAlarm() {
  if (!alarm || primed || priming) return;
  priming = true;
  alarm.muted = true;
  alarm
    .play()
    .then(() => {
      if (!audibleOnPrime) {
        alarm.pause();
        alarm.currentTime = 0;
      }
      primed = true;
    })
    .catch(() => {
      // not a gesture the browser accepts; the next tap tries again
      if (audibleOnPrime) playTones("telemetry");
    })
    .finally(() => {
      alarm.muted = false;
      priming = false;
      audibleOnPrime = false;
      notifyReady();
    });
}

export function unlockAudio() {
  if (!ctx) {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return;
    ctx = new AC();
    ctx.addEventListener("statechange", notifyReady);
  }
  // Safari 17+: play through the silent switch, like a media app.
  try {
    const session = (navigator as unknown as { audioSession?: { type: string } }).audioSession;
    if (session) session.type = "playback";
  } catch {
    // older Safari or another browser: nothing to set
  }
  if (ctx.state === "suspended") void ctx.resume().then(notifyReady, () => {});
  primeAlarm();
}

/** From a tap: unlock audio and play the paging sound right away (the alarm banner's sound button). */
export function playAlarmNow() {
  if (muted) return;
  if (alarm && !primed) {
    audibleOnPrime = true;
    unlockAudio();
    alarm.muted = false;
    return;
  }
  unlockAudio();
  if (!alarm) return playTones("telemetry");
  alarm.currentTime = 0;
  alarm.play().catch(() => playTones("telemetry"));
}

function tone(freq: number, start: number, dur: number, type: OscillatorType, vol: number) {
  if (!ctx) return;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  const t = ctx.currentTime + start;
  gain.gain.setValueAtTime(0, t);
  gain.gain.linearRampToValueAtTime(vol, t + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.001, t + dur);
  osc.connect(gain).connect(ctx.destination);
  osc.start(t);
  osc.stop(t + dur);
}

export function playSound(kind: SoundKind) {
  if (muted || !ctx || ctx.state !== "running") return;
  const now = Date.now();
  if (now - lastPlayed[kind] < COOLDOWN_MS) return;
  // A beep must not play over a page that is still sounding.
  if (alarm && !alarm.paused && kind !== "ledger") return;
  lastPlayed[kind] = now;
  if (kind === "telemetry" && alarm) {
    alarm.currentTime = 0;
    alarm.play().catch(() => playTones(kind));
    return;
  }
  playTones(kind);
}

function playTones(kind: SoundKind) {
  if (kind === "telemetry" || kind === "disruption") {
    // urgent two-tone alarm, played twice
    tone(880, 0, 0.18, "square", 0.15);
    tone(660, 0.2, 0.18, "square", 0.15);
    tone(880, 0.45, 0.18, "square", 0.15);
    tone(660, 0.65, 0.18, "square", 0.15);
  } else {
    // soft confirmation "ding"
    tone(1046, 0, 0.25, "sine", 0.18);
    tone(1568, 0.12, 0.35, "sine", 0.14);
  }
}
