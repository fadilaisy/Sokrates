// Notification sounds, synthesised with the Web Audio API (no audio files).
// Browsers keep audio silent until the first user interaction, so the
// provider calls unlockAudio() on the first click or keypress.

export type SoundKind = "disruption" | "ledger";

const MUTE_KEY = "sf-sound-muted";
// One event often arrives through two paths (e.g. telemetry + safety_alert);
// a short per-kind cooldown keeps that to a single sound.
const COOLDOWN_MS = 1500;

let ctx: AudioContext | null = null;
// The disruption alarm is a recorded paging sound; the synthesised tones
// below are only a fallback if the file can't play.
const alarm = typeof Audio !== "undefined" ? new Audio("/sounds/disruption.mp3") : null;
if (alarm) alarm.preload = "auto";
let muted = readMuted();
const lastPlayed: Record<SoundKind, number> = { disruption: 0, ledger: 0 };

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

export function unlockAudio() {
  if (!ctx) {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return;
    ctx = new AC();
  }
  if (ctx.state === "suspended") void ctx.resume();
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
  lastPlayed[kind] = now;
  if (kind === "disruption" && alarm) {
    // Let a page that is still sounding finish instead of restarting it.
    if (!alarm.paused) return;
    alarm.currentTime = 0;
    alarm.play().catch(() => playTones(kind));
    return;
  }
  playTones(kind);
}

function playTones(kind: SoundKind) {
  if (kind === "disruption") {
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
