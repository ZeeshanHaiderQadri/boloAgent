import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AvatarProfile } from "@/client/types.gen";

vi.mock("./AgentAvatar", () => ({
  AgentAvatar: () => <div data-testid="avatar-canvas" />,
  avatarName: (character: string) =>
    character === "male" ? "Bolo Agent" : "Bolo Bibi",
  avatarPreview: () => "/avatars/preview.png",
}));

import { AgentAvatarPet } from "./AgentAvatarPet";

const profile: AvatarProfile = {
  character: "female",
  setup_completed: true,
  enabled: true,
  appearance: { glasses: true },
  action: "idle",
  size: "compact",
  pet_state: "expanded",
};

function mount(overrides: Partial<AvatarProfile> = {}) {
  const onSave = vi.fn().mockResolvedValue(true);
  const onCustomize = vi.fn();
  render(
    <AgentAvatarPet
      profile={{ ...profile, ...overrides }}
      audio={null}
      readSpeechLevel={() => 0}
      live
      onSave={onSave}
      onCustomize={onCustomize}
    />,
  );
  return { onSave, onCustomize };
}

describe("AgentAvatarPet", () => {
  it("renders the companion and saves size, minimize and customize actions", () => {
    const { onSave, onCustomize } = mount();

    expect(screen.getByTestId("avatar-canvas")).toBeTruthy();
    expect(screen.getByText("Bolo Bibi is on the call.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Enlarge Bolo Bibi" }));
    expect(onSave).toHaveBeenCalledWith({ size: "large" });

    fireEvent.click(screen.getByRole("button", { name: "Minimize Bolo Bibi" }));
    expect(onSave).toHaveBeenCalledWith({ pet_state: "minimized" });

    fireEvent.click(screen.getByRole("button", { name: "Customize Bolo Bibi" }));
    expect(onCustomize).toHaveBeenCalledTimes(1);
  });

  it("shrinks a large companion back", () => {
    const { onSave } = mount({ size: "large" });
    fireEvent.click(screen.getByRole("button", { name: "Shrink Bolo Bibi" }));
    expect(onSave).toHaveBeenCalledWith({ size: "compact" });
  });

  it("expands back from the minimized tray chip", () => {
    const { onSave } = mount({ pet_state: "minimized" });

    expect(screen.queryByTestId("avatar-canvas")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Expand Bolo Bibi" }));
    expect(onSave).toHaveBeenCalledWith({ pet_state: "expanded" });
  });

  it("disappears when the user hid it or disabled the pet", () => {
    const hidden = mount({ pet_state: "hidden" });
    expect(hidden.onSave).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Expand Bolo Bibi" })).toBeNull();
    expect(screen.queryByTestId("avatar-canvas")).toBeNull();

    const disabled = mount({ enabled: false });
    expect(disabled.onCustomize).not.toHaveBeenCalled();
    expect(screen.queryByTestId("avatar-canvas")).toBeNull();
  });
});
