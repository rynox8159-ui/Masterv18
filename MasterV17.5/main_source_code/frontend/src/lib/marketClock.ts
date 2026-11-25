// Deriv-corrected clock: the backend measures how far this PC clock drifts from Deriv server time.
let offsetMs = 0;
export function setMarketClockOffset(seconds: number | undefined | null) {
  if (typeof seconds === "number" && Number.isFinite(seconds)) offsetMs = seconds * 1000;
}
export function marketNow(): number {
  return Date.now() + offsetMs;
}
