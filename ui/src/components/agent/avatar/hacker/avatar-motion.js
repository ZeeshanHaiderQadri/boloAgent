export const VISEMES = ['SIL', 'AA', 'EE', 'IH', 'OH', 'OU', 'FV', 'MBP'];
export const EMOTIONS = [
  'neutral', 'smile', 'laugh', 'thinking', 'surprised', 'excited',
  'angry', 'serious', 'confident', 'wink', 'focused', 'calm',
];
export const ACTIONS = ['idle', 'talking', 'listening', 'typing', 'analyzing', 'presenting'];
export const VIEWS = { front: .06, '¾ view': -.56, side: -Math.PI / 2, back: Math.PI };
export const HEAD_DIRECTIONS = {
  left: { yaw: -.51, pitch: 0 },
  center: { yaw: 0, pitch: 0 },
  right: { yaw: .51, pitch: 0 },
  up: { yaw: 0, pitch: -.29 },
  down: { yaw: 0, pitch: .29 },
};

const expressionTargets = EMOTIONS.slice(1).map(name => name[0].toUpperCase() + name.slice(1));
const expressions = new Map(EMOTIONS.slice(1).map((name, i) => [name, expressionTargets[i]]));
expressions.set('happy', 'Smile');

/** Mount the optional meshes on the matching avatar groups. */
export function assembleAvatar(gltf) {
  const avatar = gltf.scene;
  const extras = gltf.scenes.find(scene => scene.name === 'OptionalPieces');
  if (extras) {
    for (const node of [...extras.children]) {
      (avatar.getObjectByName(node.userData.attachTo) ?? avatar).add(node);
    }
  }
  return avatar;
}

export function sceneVisibility({ action, glasses, eyewear, hood, centered, emotion }) {
  const hud = eyewear === 'hud';
  const upright = hood && centered;
  return {
    HoodUp: upright,
    HoodDown: !upright,
    Glasses: eyewear ? eyewear !== 'off' : glasses,
    EyeHUD: hud,
    Laptop: action === 'typing' || action === 'analyzing',
    TypingHands: action === 'typing',
    Presenting: action === 'presenting',
    ThinkingHand: action === 'idle' && centered && emotion === 'thinking',
    ExcitedGesture: action === 'idle' && centered && emotion === 'excited',
    DataHUD: action === 'analyzing',
    SignalRings: action === 'listening' || action === 'talking',
  };
}

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
  const expression = expressions.get(emotion);
  output.Blink = clamp01(blink) * (['Laugh','Wink','Calm'].includes(expression) ? 1 - .94 * clamp01(intensity) : 1);

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
    output.AA = power * .90;
    output.SIL = (1 - power) * .84;
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
