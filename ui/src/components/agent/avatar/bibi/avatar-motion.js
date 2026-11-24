export const VISEMES = ['SIL', 'AA', 'EE', 'IH', 'OH', 'OU', 'FV', 'MBP'];
export const EMOTIONS = ['neutral', 'smile', 'angry', 'laugh', 'curious', 'surprised', 'concerned', 'focused'];

const expressions = {
  smile: 'Smile',
  happy: 'Smile',
  angry: 'Angry',
  laugh: 'Laugh',
  curious: 'Curious',
  surprised: 'Surprised',
  concerned: 'Concerned',
  focused: 'Focused',
};

const expressionTargets = [...new Set(Object.values(expressions))];

export const clamp01 = (value) => Math.min(1, Math.max(0, Number(value) || 0));

export function normalizeVisemes(cues = []) {
  return cues
    .filter(({ time, shape }) => Number.isFinite(time) && time >= 0 && VISEMES.includes(shape))
    .map(({ time, shape, strength = 1 }) => ({ time, shape, strength: clamp01(strength) }))
    .sort((a, b) => a.time - b.time);
}

function findCue(cues, time) {
  let lo = 0;
  let hi = cues.length;
  while (lo < hi) {
    const middle = (lo + hi) >>> 1;
    if (cues[middle].time <= time) lo = middle + 1;
    else hi = middle;
  }
  return lo - 1;
}

/** Call once per rendered frame. The output object can be reused. */
export function faceWeights(output, { cues = [], time = 0, speaking = false, level = 0, emotion, intensity = 1, blink = 0 }) {
  for (const key of VISEMES) output[key] = 0;
  for (const key of expressionTargets) output[key] = 0;
  const expression = expressions[emotion];
  output.Blink = clamp01(blink) * (expression === 'Laugh' ? 1 - .85 * clamp01(intensity) : 1);

  if (speaking && cues.length) {
    const index = findCue(cues, time);
    const current = cues[index];
    const next = cues[index + 1];
    const transition = Math.min(0.068, next && current ? (next.time - current.time) * 0.5 : 0.068);
    const blend = next && transition > 0 ? clamp01(1 - (next.time - time) / transition) : 0;
    if (current) output[current.shape] = current.strength * (1 - blend);
    if (next && blend) output[next.shape] += next.strength * blend;
  } else if (speaking) {
    const power = clamp01(level);
    // Live audio only gives amplitude, so synthesize a syllable-ish wobble to
    // spread the motion across jaw, lips and cheeks instead of a flat flap.
    const phase = time * 8.4;
    const openness = power * (0.78 + 0.22 * Math.sin(phase));
    output.AA = clamp01(openness * .92);
    output.EE = clamp01(power * .30 * Math.max(0, Math.sin(phase * 1.27 + .7)));
    output.OH = clamp01(power * .26 * Math.max(0, Math.sin(phase * .83 + 2.4)));
    output.OU = clamp01(power * .18 * Math.max(0, Math.sin(phase * .61 + 4.1)));
    output.FV = clamp01(power * .12 * Math.max(0, Math.sin(phase * 1.9 + 1.6)));
    output.SIL = clamp01((1 - power) * .72);
  }

  const speechWeight = VISEMES.reduce((sum, key) => sum + output[key], 0);
  if (expression) output[expression] = clamp01(intensity);
  return Math.max(0.16, 1 - speechWeight * 0.84);
}

/** Pass an analyser from your existing TTS audio graph. Call read() inside requestAnimationFrame. */
export function createSpeechLevelReader(analyser) {
  const waveform = new Float32Array(analyser.fftSize);
  return () => {
    analyser.getFloatTimeDomainData(waveform);
    let energy = 0;
    for (const amplitude of waveform) energy += amplitude * amplitude;
    const rms = Math.sqrt(energy / waveform.length);
    return clamp01((rms - .016) * 6);
  };
}
