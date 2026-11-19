"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

interface Props {
    className?: string;
    particleCount?: number;
    /** Host opacity; keep this low so text contrast never depends on the canvas. */
    opacity?: number;
}

/**
 * Decorative drifting particle field.
 *
 * three.js is imported lazily inside the effect, so it never enters the
 * initial bundle and is skipped entirely when the visitor prefers reduced
 * motion or has Save-Data on. The renderer pauses while the tab is hidden.
 * Everything is pointer-events-none and aria-hidden: it carries no meaning.
 */
export function AmbientBackdrop({ className, particleCount = 260, opacity = 0.55 }: Props) {
    const hostRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        const host = hostRef.current;
        if (!host) return;

        const motionQuery =
            typeof window.matchMedia === "function"
                ? window.matchMedia("(prefers-reduced-motion: reduce)")
                : null;
        const connection = (navigator as Navigator & { connection?: { saveData?: boolean } })
            .connection;
        if (motionQuery?.matches || connection?.saveData) return;

        let disposed = false;
        let cleanup: (() => void) | undefined;

        const boot = async () => {
            try {
                const THREE = await import("three");
                if (disposed) return;

                const canvas = document.createElement("canvas");
                const gl =
                    canvas.getContext("webgl2") ?? canvas.getContext("webgl");
                if (!gl) return;

                const renderer = new THREE.WebGLRenderer({
                    canvas,
                    alpha: true,
                    antialias: false,
                    powerPreference: "low-power",
                });
                renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
                renderer.setClearAlpha(0);
                renderer.domElement.style.position = "absolute";
                renderer.domElement.style.inset = "0";
                renderer.domElement.style.width = "100%";
                renderer.domElement.style.height = "100%";
                host.appendChild(renderer.domElement);

                const scene = new THREE.Scene();
                const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 100);
                camera.position.z = 6;

                const positions = new Float32Array(particleCount * 3);
                for (let i = 0; i < particleCount; i += 1) {
                    positions[i * 3] = (Math.random() - 0.5) * 9;
                    positions[i * 3 + 1] = (Math.random() - 0.5) * 6;
                    positions[i * 3 + 2] = (Math.random() - 0.5) * 5;
                }
                const geometry = new THREE.BufferGeometry();
                geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));

                const isDark = () => document.documentElement.classList.contains("dark");
                const color = new THREE.Color(isDark() ? "#5eead4" : "#0f766e");
                const material = new THREE.PointsMaterial({
                    color,
                    size: 0.035,
                    sizeAttenuation: true,
                    transparent: true,
                    opacity: 0,
                    depthWrite: false,
                    blending: isDark() ? THREE.AdditiveBlending : THREE.NormalBlending,
                });
                const points = new THREE.Points(geometry, material);
                scene.add(points);

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

                const themeObserver = new MutationObserver(() => {
                    const dark = isDark();
                    color.set(dark ? "#5eead4" : "#0f766e");
                    material.blending = dark
                        ? THREE.AdditiveBlending
                        : THREE.NormalBlending;
                    material.needsUpdate = true;
                });
                themeObserver.observe(document.documentElement, {
                    attributes: true,
                    attributeFilter: ["class"],
                });

                let running = true;
                let frame = 0;
                const startedAt = performance.now();
                const animate = (now: number) => {
                    if (!running) return;
                    const elapsed = (now - startedAt) / 1000;
                    // Fade in once, then drift on a slow sine path.
                    material.opacity = Math.min(0.45, material.opacity + 0.012);
                    points.rotation.y = elapsed * 0.05;
                    points.rotation.x = Math.sin(elapsed * 0.12) * 0.08;
                    points.position.y = Math.sin(elapsed * 0.2) * 0.15;
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
                    themeObserver.disconnect();
                    observer.disconnect();
                    geometry.dispose();
                    material.dispose();
                    renderer.dispose();
                    renderer.domElement.remove();
                };
            } catch {
                // WebGL is unavailable or the chunk failed to load; the
                // backdrop is decorative, so fail silently.
            }
        };

        void boot();
        return () => {
            disposed = true;
            cleanup?.();
        };
    }, [particleCount]);

    return (
        <div
            ref={hostRef}
            aria-hidden
            className={cn(
                "pointer-events-none absolute inset-0 z-0 overflow-hidden",
                className,
            )}
            style={{ opacity }}
        />
    );
}
