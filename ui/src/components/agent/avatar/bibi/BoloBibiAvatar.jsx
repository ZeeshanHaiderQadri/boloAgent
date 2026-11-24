'use client';

import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VISEMES, faceWeights, normalizeVisemes } from './avatar-motion.js';

const speechShapes = new Set(VISEMES);
const emptyCues = Object.freeze([]);

function lights(scene) {
  scene.add(new THREE.HemisphereLight(0xf1e5ff, 0x8a6f7d, 1.28));
  const sources = [
    [0xffdfbd, 2.57, -3.7, 5.6, 6],
    [0xd0e6ff, 1.30, 4, 2.6, 3],
    [0x5cf1fc, 2.50, 2, 4, -4],
    [0xb1a1ff, 1.75, -4, 4, -2],
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
export default function BoloBibiAvatar({
  modelUrl = '/models/bolo-bibi.glb',
  posterUrl,
  emotion = 'neutral',
  intensity = 1,
  glasses = true,
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
    emotion, intensity, glasses, idleMotion, interactive, headTurn, headTilt,
    audio, visemes, readSpeech, readSpeechLevel, onReady, onError,
  };

  useEffect(() => {
    const container = host.current;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(33, 1, .1, 30);
    const cameraDistance = () => 7.76 * Math.max(1, .72 / camera.aspect);
    const point = new THREE.Vector3(0, 2.02, 0);
    const pointer = new THREE.Vector2();
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const weights = {};
    let renderer;
    let avatar;
    let pivot;
    let frames;
    let meshBindings = [];
    let eyewear;
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
      camera.position.set(d * .075, 2.64, d);
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

      if (pivot) {
        const follows = config.interactive && !reduced.matches;
        const bob = idle ? Math.sin(elapsed * 1.27) : 0;
        const yaw = Math.max(-.7, Math.min(.7, config.headTurn + (follows ? pointer.x * .23 : 0) + bob * .055));
        const pitch = Math.max(-.32, Math.min(.32, config.headTilt + (follows ? pointer.y * .115 : 0) + bob * .025));
        pivot.rotation.y += (yaw - pivot.rotation.y) * interpolation * .50;
        pivot.rotation.x += (pitch - pivot.rotation.x) * interpolation * .50;
      }
      if (eyewear) eyewear.visible = config.glasses;
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
        disposeModel(gltf.scene);
        return;
      }
      avatar = gltf.scene;
      pivot = avatar.getObjectByName('HeadPivot');
      eyewear = avatar.getObjectByName('Glasses');
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
    <div className={className} aria-label="Bolo Bibi 3D avatar" data-avatar-status={status}
      style={{ position: 'relative', width: '100%', aspectRatio: '4 / 5', overflow: 'hidden', ...style }}>
      {posterUrl && status !== 'ready' && (
        <img src={posterUrl} alt="Bolo Bibi preview"
          style={{ position: 'absolute', width: '100%', height: '100%', objectFit: 'contain' }} />
      )}
      <div ref={host} style={{ position: 'absolute', inset: 0 }} />
      {status === 'error' && !posterUrl && <span role="status">The 3D avatar could not load.</span>}
    </div>
  );
}
