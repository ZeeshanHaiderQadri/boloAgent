"use client";

import type { RefObject } from "react";
import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

export type BoloFaceStatus = "listening" | "muted" | "hold-to-talk" | "connecting";

interface Props {
    /** Live meter values written by the voice hook (0..1). */
    levels: RefObject<{ mic: number; agent: number }>;
    status: BoloFaceStatus;
    className?: string;
}

const STATUS_LABEL: Record<BoloFaceStatus, string> = {
    listening: "Listening",
    muted: "Mic off",
    "hold-to-talk": "Hold to talk",
    connecting: "Connecting",
};

/**
 * Stylized Bolo head that reacts to the call audio: the mouth opens with the
 * agent's voice and the halo rings pulse with the caller's microphone.
 *
 * three.js is imported inside the effect so it stays out of the initial
 * bundle. Reacting to voice is the point, so motion continues under
 * prefers-reduced-motion, but the idle sway and blinking are skipped.
 */
export function BoloFace({ levels, status, className }: Props) {
    const hostRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        const host = hostRef.current;
        if (!host) return;

        let disposed = false;
        let cleanup: (() => void) | undefined;

        const boot = async () => {
            try {
                const THREE = await import("three");
                if (disposed) return;

                const canvas = document.createElement("canvas");
                const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
                if (!gl) return;

                const reducedMotion =
                    typeof window.matchMedia === "function" &&
                    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

                const renderer = new THREE.WebGLRenderer({
                    canvas,
                    alpha: true,
                    antialias: true,
                    powerPreference: "low-power",
                });
                renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
                renderer.setClearAlpha(0);
                renderer.domElement.style.position = "absolute";
                renderer.domElement.style.inset = "0";
                renderer.domElement.style.width = "100%";
                renderer.domElement.style.height = "100%";
                host.appendChild(renderer.domElement);

                const scene = new THREE.Scene();
                const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 40);
                camera.position.set(0, 0.12, 3.5);

                scene.add(new THREE.AmbientLight(0xffffff, 1.1));
                const key = new THREE.DirectionalLight(0xd7fff5, 1.6);
                key.position.set(2.4, 3, 4);
                scene.add(key);
                const rim = new THREE.PointLight(0x2dd4bf, 14, 12);
                rim.position.set(-2.4, 1.2, 2.2);
                scene.add(rim);

                const headGroup = new THREE.Group();
                headGroup.position.y = 0.05;
                scene.add(headGroup);

                const headGeometry = new THREE.SphereGeometry(0.95, 48, 48);
                const headMaterial = new THREE.MeshStandardMaterial({
                    color: 0x0a2320,
                    roughness: 0.32,
                    metalness: 0.45,
                    emissive: 0x0f766e,
                    emissiveIntensity: 0.22,
                });
                const head = new THREE.Mesh(headGeometry, headMaterial);
                head.scale.set(1, 1.04, 0.9);
                headGroup.add(head);

                const glowMaterial = new THREE.MeshBasicMaterial({ color: 0x5eead4 });
                const eyeGeometry = new THREE.SphereGeometry(0.125, 24, 24);
                const eyes: Array<{
                    mesh: import("three").Mesh;
                    nextBlink: number;
                    blinkStart: number;
                }> = [];
                for (const side of [-1, 1]) {
                    const eye = new THREE.Mesh(eyeGeometry, glowMaterial);
                    eye.position.set(side * 0.3, 0.18, 0.79);
                    eye.scale.set(1, 1, 0.45);
                    headGroup.add(eye);
                    eyes.push({ mesh: eye, nextBlink: 2.5 + Math.random() * 4, blinkStart: -10 });
                }

                const mouthGeometry = new THREE.CapsuleGeometry(0.05, 0.26, 8, 16);
                const mouth = new THREE.Mesh(mouthGeometry, glowMaterial);
                mouth.rotation.z = Math.PI / 2;
                mouth.position.set(0, -0.34, 0.8);
                mouth.scale.set(1, 0.55, 0.5);
                headGroup.add(mouth);

                const ringGeometry = new THREE.TorusGeometry(1.28, 0.012, 8, 128);
                const makeRing = (radiusScale: number) => {
                    const material = new THREE.MeshBasicMaterial({
                        color: 0x2dd4bf,
                        transparent: true,
                        opacity: 0.16,
                        blending: THREE.AdditiveBlending,
                        depthWrite: false,
                    });
                    const mesh = new THREE.Mesh(ringGeometry, material);
                    mesh.position.z = -0.35;
                    mesh.scale.setScalar(radiusScale);
                    scene.add(mesh);
                    return { mesh, material };
                };
                const ringA = makeRing(1);
                const ringB = makeRing(1.16);

                const resize = () => {
                    const width = Math.max(1, host.clientWidth);
                    const height = Math.max(1, host.clientHeight);
                    renderer.setSize(width, height, false);
                    camera.aspect = width / height;
                    camera.updateProjectionMatrix();
                };
                resize();
                const observer = new ResizeObserver(resize);
                observer.observe(host);

                let running = true;
                let frame = 0;
                let smoothedAgent = 0;
                let smoothedMic = 0;
                const startedAt = performance.now();
                const animate = (now: number) => {
                    if (!running) return;
                    const elapsed = (now - startedAt) / 1000;
                    const current = levels.current ?? { mic: 0, agent: 0 };
                    smoothedAgent += (Math.min(1, current.agent * 1.5) - smoothedAgent) * 0.3;
                    smoothedMic += (Math.min(1, current.mic * 1.8) - smoothedMic) * 0.25;

                    mouth.scale.y = 0.55 + smoothedAgent * 2.6;
                    mouth.scale.x = 1 + smoothedAgent * 0.18;

                    if (!reducedMotion) {
                        headGroup.rotation.y =
                            Math.sin(elapsed * 0.5) * 0.07 +
                            smoothedAgent * Math.sin(elapsed * 7) * 0.03;
                        headGroup.rotation.x = Math.sin(elapsed * 0.7) * 0.02;
                        headGroup.position.y = 0.05 + Math.sin(elapsed * 0.9) * 0.03;
                        for (const eye of eyes) {
                            if (elapsed > eye.nextBlink) {
                                eye.blinkStart = elapsed;
                                eye.nextBlink = elapsed + 2.4 + Math.random() * 4;
                            }
                            eye.mesh.scale.y =
                                elapsed - eye.blinkStart < 0.14 ? 0.12 : 1;
                        }
                    }

                    ringA.mesh.scale.setScalar(1 + smoothedMic * 0.26);
                    ringB.mesh.scale.setScalar(1.16 + smoothedMic * 0.38);
                    ringA.material.opacity = 0.14 + smoothedMic * 0.5;
                    ringB.material.opacity = 0.09 + smoothedMic * 0.32;

                    renderer.render(scene, camera);
                    frame = requestAnimationFrame(animate);
                };
                frame = requestAnimationFrame(animate);

                const onVisibility = () => {
                    if (document.hidden) {
                        running = false;
                        cancelAnimationFrame(frame);
                    } else if (!running) {
                        running = true;
                        frame = requestAnimationFrame(animate);
                    }
                };
                document.addEventListener("visibilitychange", onVisibility);

                cleanup = () => {
                    running = false;
                    cancelAnimationFrame(frame);
                    document.removeEventListener("visibilitychange", onVisibility);
                    observer.disconnect();
                    headGeometry.dispose();
                    headMaterial.dispose();
                    eyeGeometry.dispose();
                    mouthGeometry.dispose();
                    ringGeometry.dispose();
                    glowMaterial.dispose();
                    ringA.material.dispose();
                    ringB.material.dispose();
                    renderer.dispose();
                    renderer.domElement.remove();
                };
            } catch {
                // WebGL or the three.js chunk is unavailable; the call still works.
            }
        };

        void boot();
        return () => {
            disposed = true;
            cleanup?.();
        };
    }, [levels]);

    return (
        <div
            className={cn(
                "pointer-events-none flex w-40 select-none flex-col items-center gap-1.5 rounded-3xl border border-teal-400/25 bg-[radial-gradient(circle_at_50%_30%,rgba(15,118,110,0.35),rgba(2,12,11,0.85))] p-2 shadow-[0_0_45px_-10px_rgba(45,212,191,0.55)] backdrop-blur",
                className,
            )}
        >
            <div
                ref={hostRef}
                aria-hidden
                className="relative aspect-square w-full"
            />
            <span className="flex items-center gap-1.5 pb-0.5 text-[11px] font-medium tracking-wide text-teal-200/90">
                <span
                    className={cn(
                        "h-1.5 w-1.5 rounded-full",
                        status === "listening" && "animate-pulse bg-teal-300",
                        status === "muted" && "bg-zinc-500",
                        status === "hold-to-talk" && "bg-teal-400/70",
                        status === "connecting" && "animate-pulse bg-amber-400",
                    )}
                />
                {STATUS_LABEL[status]}
            </span>
        </div>
    );
}
