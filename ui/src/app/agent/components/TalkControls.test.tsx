import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { LatencyBadge, TalkControls } from "./TalkControls";

const levels = { current: { mic: 0, agent: 0 } };

function setup(overrides: Partial<ComponentProps<typeof TalkControls>> = {}) {
  const props = {
    micMode: "hands-free" as const,
    onMicModeChange: vi.fn(),
    micMuted: false,
    onToggleMute: vi.fn(),
    holding: false,
    onStartTalking: vi.fn(),
    onStopTalking: vi.fn(),
    levels,
    latency: null,
    ...overrides,
  };
  render(<TalkControls {...props} />);
  return props;
}

describe("TalkControls", () => {
  it("switches between hands-free and push-to-talk", () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: /push to talk/i }));
    expect(props.onMicModeChange).toHaveBeenCalledWith("push-to-talk");
    fireEvent.click(screen.getByRole("button", { name: /hands-free/i }));
    expect(props.onMicModeChange).toHaveBeenCalledWith("hands-free");
  });

  it("toggles the microphone in hands-free mode", () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: /mute/i }));
    expect(props.onToggleMute).toHaveBeenCalledTimes(1);
  });

  it("shows the mute state instead of the toggle when muted", () => {
    setup({ micMuted: true });
    expect(screen.getByRole("button", { name: /mic off/i })).toBeTruthy();
  });

  it("holds the microphone open only while the button is pressed", () => {
    const props = setup({ micMode: "push-to-talk" });
    const button = screen.getByRole("button", { name: /hold to talk/i });
    fireEvent.pointerDown(button);
    expect(props.onStartTalking).toHaveBeenCalledTimes(1);
    fireEvent.pointerUp(button);
    expect(props.onStopTalking).toHaveBeenCalledTimes(1);
  });

  it("releases the microphone when the pointer is cancelled", () => {
    const props = setup({ micMode: "push-to-talk", holding: true });
    fireEvent.pointerCancel(screen.getByRole("button", { name: /talking/i }));
    expect(props.onStopTalking).toHaveBeenCalledTimes(1);
  });

  it("hides the mute toggle while push-to-talk is active", () => {
    setup({ micMode: "push-to-talk" });
    expect(screen.queryByRole("button", { name: /mute/i })).toBeNull();
  });

  it("shows the last turn's total delay and its breakdown", () => {
    setup({ latency: { stt: 220, llm: 700, tts: 300 } });
    expect(screen.getByText("1.2s")).toBeTruthy();
    expect(
      screen.getByText("listen 220 · think 700 · speak 300"),
    ).toBeTruthy();
  });

  it("renders no latency badge before the first reply", () => {
    const { container } = render(<LatencyBadge latency={null} />);
    expect(container.firstChild).toBeNull();
  });
});
