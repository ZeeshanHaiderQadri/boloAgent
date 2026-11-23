import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BoloFace } from "./BoloFace";

const levels = { current: { mic: 0, agent: 0 } };

describe("BoloFace", () => {
    it("names the current microphone state", () => {
        render(<BoloFace levels={levels} status="listening" />);
        expect(screen.getByText("Listening")).toBeTruthy();
    });

    it("shows the push-to-talk hint while the microphone is closed", () => {
        render(<BoloFace levels={levels} status="hold-to-talk" />);
        expect(screen.getByText("Hold to talk")).toBeTruthy();
    });

    it("survives a browser without WebGL", () => {
        expect(() => render(<BoloFace levels={levels} status="muted" />)).not.toThrow();
        expect(screen.getByText("Mic off")).toBeTruthy();
    });
});
