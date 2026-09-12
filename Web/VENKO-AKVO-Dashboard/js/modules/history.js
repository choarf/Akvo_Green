export const ringBuffer = [];

export function add(sample) {
  ringBuffer.push(sample);
  if (ringBuffer.length > 3600) ringBuffer.shift();
}

export function recent(n) {
  return ringBuffer.slice(-n);
}
