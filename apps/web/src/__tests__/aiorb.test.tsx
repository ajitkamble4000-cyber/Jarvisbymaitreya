import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AIOrb, orbVisual, type OrbState } from "../components/orb/AIOrb";

const ALL_STATES: OrbState[] = ["idle", "listening", "processing", "speaking", "executing", "awaiting_approval", "disconnected", "error"];

describe("AIOrb state mapping", () => {
  it("gives every state its own label so status is never conveyed by colour alone", () => {
    const labels = ALL_STATES.map((state) => orbVisual(state).label);
    expect(new Set(labels).size).toBe(ALL_STATES.length);
    expect(orbVisual("listening").label).toBe("Listening");
    expect(orbVisual("speaking").label).toBe("Speaking");
  });

  it("uses warning for approvals, danger for errors and muted for disconnected", () => {
    expect(orbVisual("awaiting_approval").tone).toBe("warning");
    expect(orbVisual("error").tone).toBe("danger");
    expect(orbVisual("disconnected").tone).toBe("muted");
    expect(orbVisual("disconnected").motion).toBe("none");
    expect(orbVisual("error").motion).toBe("none");
  });

  it("falls back to idle for an unexpected value", () => {
    expect(orbVisual("unexpected" as OrbState).label).toBe("Ready");
  });

  it("exposes the current state as the accessible name of the orb", () => {
    render(<AIOrb state="awaiting_approval" />);
    expect(screen.getByRole("img", { name: "Assistant status: Waiting for your approval" })).toBeInTheDocument();
    expect(screen.getByText("Waiting for your approval")).toBeVisible();
  });

  it("can hide the visible label while keeping the accessible name", () => {
    render(<AIOrb state="listening" showLabel={false} />);
    expect(screen.getByRole("img", { name: "Assistant status: Listening" })).toBeInTheDocument();
    expect(screen.queryByText("Listening")).toBeNull();
  });
});
