'use client';

import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VISEMES, VIEWS, assembleAvatar, faceWeights, normalizeVisemes, sceneVisibility } from './avatar-motion.js';

const speechShapes = new Set(VISEMES);
const emptyCues = Object.freeze([]);

function lights(scene) {
  scene.add(new THREE.HemisphereLight(0xe7e5ff, 0x536083, 1.13));
  const sources = [
    [0xffdfbd, 2.57, -3.7, 5.6, 6],
    [0xd0e6ff, 1.30, 4, 2.6, 3],
    [0x4adaff, 2.76, 2, 4, -4],
    [0xa46cff, 2.08, -4, 4, -2],
  ];
  for (const [color, intensity, x, y, z] of sources) {
    const light = new THREE.DirectionalLight(color, intensity);
    light.position.set(x, y, z);
    scene.add(light);
  }
}

function disposeModel(model) {
  const materials = new Set();
  const geometries = new Set();
  model.traverse((node) => {
    if (!node.isMesh) return;
    geometries.add(node.geometry);
    for (const material of [node.material].flat()) materials.add(material);
  });
  for (const geometry of geometries) geometry.dispose();
  for (const material of materials) material.dispose();
}

/**
 * For timed lips, pass your playing media element as audio and normalized visemes.
 * For streamed playback, supply readSpeech() for the timing or level instead.
 */
export default function BoloEthicalHackerAvatar({
  modelUrl = '/models/bolo-ethical-hacker.glb',
  posterUrl,
  emotion = 'neutral',
  intensity = 1,
  glasses = true,
  eyewear,
  hood = false,
  action = 'idle',
  viewAngle = VIEWS.front,
  idleMotion = true,
  interactive = true,
  headTurn = 0,
  headTilt = 0,
  audio = null,
  visemes = emptyCues,
  readSpeech,
  readSpeechLevel,
  onReady,
  onError,
  className,
  style,
}) {
  const host = useRef(null);
  const options = useRef({});
  const [status, setStatus] = useState('loading');
  options.current = {
    emotion, intensity, glasses, eyewear, hood, action, viewAngle, idleMotion, interactive, headTurn, headTilt,
    audio, visemes, readSpeech, readSpeechLevel, onReady, onError,
  };

  useEffect(() => {
    const container = host.current;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(33, 1, .1, 30);
    const cameraDistance = () => 8.55 * Math.max(1, .72 / camera.aspect);
    const point = new THREE.Vector3(0, 2.08, 0);
    const pointer = new THREE.Vector2();
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const weights = {};
    let renderer;
    let avatar;
    let pivot;
    let frames;
    let meshBindings = [];
    const pieces = new Map();
    let cameraOrbit = options.current.viewAngle;
    let typingHands = [];
    let signalArcs = [];
    let expired = false;
    let lastTime = performance.now();
    const startTime = lastTime;
    let lastCues;
    let cues = [];

    const fail = (error) => {
      if (expired) return;
      setStatus('error');
      options.current.onError?.(error);
    };

    setStatus('loading');
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
      renderer.setPixelRatio(Math.min(1.75, window.devicePixelRatio || 1));
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.13;
      renderer.domElement.setAttribute('aria-hidden', 'true');
      renderer.domElement.style.display = 'block';
      container.appendChild(renderer.domElement);
    } catch (error) {
      fail(error);
      return undefined;
    }

    lights(scene);
    function resize() {
      const width = container.clientWidth || 1;
      const height = container.clientHeight || 1;
      camera.aspect = width / height;
      const d = cameraDistance();
      camera.position.set(Math.sin(cameraOrbit)*d, 2.73, Math.cos(cameraOrbit)*d);
      camera.lookAt(point);
      camera.updateProjectionMatrix();
      // Keep the visible canvas in CSS pixels while Three.js scales its buffer for HiDPI.
      renderer.setSize(width, height);
    }

    function aim(event) {
      const rect = container.getBoundingClientRect();
      pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, (event.clientY - rect.top) / rect.height * 2 - 1);
    }

    function resetAim() {
      pointer.set(0, 0);
    }

    function render(now) {
      const delta = Math.min(.06, (now - lastTime) / 1000);
      const elapsed = (now - startTime) / 1000;
      const config = options.current;
      lastTime = now;
      if (config.visemes !== lastCues) {
        lastCues = config.visemes;
        cues = normalizeVisemes(lastCues);
      }

      const media = config.audio;
      const speech = config.readSpeech?.();
      const speaking = speech?.playing ?? Boolean(media && !media.paused && !media.ended && !media.seeking);
      const clock = speech?.time ?? media?.currentTime ?? 0;
      const level = speaking ? (speech?.level ?? config.readSpeechLevel?.() ?? 0) : 0;
      const idle = config.idleMotion && !reduced.matches;
      const cycle = elapsed % 4.37;
      const blink = idle ? Math.max(0, 1 - Math.abs(cycle - 3.75) / .096) : 0;
      const speechExpressionMix = faceWeights(weights, {
        cues, time: clock, speaking, level, emotion: config.emotion, intensity: config.intensity, blink,
      });
      const interpolation = 1 - Math.exp(-delta * 26);
      for (const { node, channels, mouth } of meshBindings) {
        for (const [name, index] of channels) {
          const scale = mouth && !speechShapes.has(name) ? speechExpressionMix : 1;
          const desired = (weights[name] || 0) * scale;
          node.morphTargetInfluences[index] += (desired - node.morphTargetInfluences[index]) * interpolation;
        }
      }

      const orbitDelta = Math.atan2(Math.sin(config.viewAngle-cameraOrbit), Math.cos(config.viewAngle-cameraOrbit));
      cameraOrbit += orbitDelta * interpolation * .37;
      const d = cameraDistance();
      camera.position.set(Math.sin(cameraOrbit)*d, 2.73, Math.cos(cameraOrbit)*d);
      camera.lookAt(point);

      const centered = Math.abs(config.headTurn)<.22 && Math.abs(config.headTilt)<.14;
      const visibility = sceneVisibility({ ...config, centered, action: speaking && config.action==='idle' ? 'talking' : config.action });
      for (const [name, piece] of pieces) piece.visible = Boolean(visibility[name]);

      if (pivot) {
        const follows = config.interactive && !reduced.matches;
        const restrained = visibility.HoodUp || visibility.ThinkingHand;
        const bob = idle ? Math.sin(elapsed * 1.27) : 0;
        const pointerGain = restrained ? .27 : 1;
        const pitchOffset = config.action==='typing' ? .13 : config.action==='analyzing' ? .08 : 0;
        const nod = idle && config.action==='listening' ? Math.max(0, Math.sin(elapsed*2.0))*.065 : 0;
        const yaw = Math.max(-.7, Math.min(.7, config.headTurn + (follows ? pointer.x * .23 * pointerGain : 0) + bob * .033));
        const pitch = Math.max(-.32, Math.min(.32, config.headTilt + (follows ? pointer.y * .115 * pointerGain : 0) + bob * .019+pitchOffset+nod));
        const roll = {thinking: .086, confident: -.062, wink: -.047, calm: -.035}[config.emotion] ?? 0;
        pivot.rotation.y += (yaw - pivot.rotation.y) * interpolation * .5;
        pivot.rotation.x += (pitch - pivot.rotation.x) * interpolation * .5;
        pivot.rotation.z += (roll - pivot.rotation.z) * interpolation * .5;
      }

      if (visibility.TypingHands) {
        typingHands.forEach((hand, index) => {
          hand.rotation.x = reduced.matches ? 0 : .080 * Math.sin(elapsed*13 + index*1.8);
          hand.position.y = hand.userData.restY + (reduced.matches ? 0 : .014*Math.sin(elapsed*16 + index*1.22));
        });
      }
      if (visibility.SignalRings) {
        signalArcs.forEach((arc, index) => {
          arc.visible = reduced.matches || (Math.sin(elapsed*4-index*1.1) + 1)*.5 > .24;
        });
      }
      renderer.render(scene, camera);
      frames = requestAnimationFrame(render);
    }

    const observer = new ResizeObserver(resize);
    observer.observe(container);
    container.addEventListener('pointermove', aim);
    container.addEventListener('pointerleave', resetAim);
    resize();

    new GLTFLoader().loadAsync(modelUrl).then((gltf) => {
      if (expired) {
        for (const gltfScene of gltf.scenes) disposeModel(gltfScene);
        return;
      }
      avatar = assembleAvatar(gltf);
      pivot = avatar.getObjectByName('HeadPivot');
      for (const name of Object.keys(sceneVisibility({}))) {
        const piece = avatar.getObjectByName(name);
        if (piece) pieces.set(name, piece);
      }
      typingHands = ['LeftTypingHand','RightTypingHand'].map(name => avatar.getObjectByName(name)).filter(Boolean);
      typingHands.forEach(hand => { hand.userData.restY = hand.position.y; });
      signalArcs = avatar.getObjectByName('SignalRings')?.children ?? [];
      avatar.traverse((node) => {
        if (node.morphTargetDictionary && node.morphTargetInfluences) {
          const channels = Object.entries(node.morphTargetDictionary);
          meshBindings.push({ node, channels, mouth: 'AA' in node.morphTargetDictionary });
        }
      });
      scene.add(avatar);
      frames = requestAnimationFrame(render);
      setStatus('ready');
      options.current.onReady?.();
    }).catch(fail);

    return () => {
      expired = true;
      cancelAnimationFrame(frames);
      observer.disconnect();
      container.removeEventListener('pointermove', aim);
      container.removeEventListener('pointerleave', resetAim);
      if (avatar) disposeModel(avatar);
      meshBindings = [];
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [modelUrl]);

  return (
    <div className={className} aria-label="Bolo Ethical Hacker 3D avatar" data-avatar-status={status}
      style={{ position: 'relative', width: '100%', aspectRatio: '4 / 5', overflow: 'hidden', ...style }}>
      {posterUrl && status !== 'ready' && (
        <img src={posterUrl} alt="Bolo Ethical Hacker preview"
          style={{ position: 'absolute', width: '100%', height: '100%', objectFit: 'contain' }} />
      )}
      <div ref={host} style={{ position: 'absolute', inset: 0 }} />
      {status === 'error' && !posterUrl && <span role="status">The 3D avatar could not load.</span>}
    </div>
  );
}
