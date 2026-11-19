"use client";

import { useEffect, useState } from "react";

import { usePrefersReducedMotion } from "@/lib/useReducedMotion";

interface Props {
    value: number;
    duration?: number;
    className?: string;
}

/**
 * Counts up to `value` once the number is known. The final value is always in
 * the accessibility tree; the animated digits are aria-hidden so screen
 * readers never hear intermediate numbers.
 */
export function CountUp({ value, duration = 900, className }: Props) {
    const reduced = usePrefersReducedMotion();
    const [display, setDisplay] = useState(0);

    useEffect(() => {
        if (reduced) {
            setDisplay(value);
            return;
        }
        let frame = 0;
        const start = performance.now();
        setDisplay(0);
        const tick = (now: number) => {
            const progress = Math.min(1, (now - start) / duration);
            const eased = 1 - Math.pow(1 - progress, 5);
            setDisplay(Math.round(value * eased));
            if (progress < 1) frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(frame);
    }, [value, duration, reduced]);

    return (
        <span className={className}>
            <span aria-hidden className="tabular-nums">
                {display.toLocaleString()}
            </span>
            <span className="sr-only">{value.toLocaleString()}</span>
        </span>
    );
}
