import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useWakeWord } from "../hooks/useWakeWord";

/** A fake speech engine. Tests drive it by calling the handlers the hook installs. */
class FakeRecognition {
  static instances: FakeRecognition[] = [];
  lang = "";
  continuous = false;
  interimResults = false;
  maxAlternatives = 1;
  onstart: (() => void) | null = null;
  onend: (() => void) | null = null;
  onerror: ((event: { error: string }) => void) | null = null;
  onresult: ((event: unknown) => void) | null = null;
  onspeechstart: (() => void) | null = null;
  started = false;
  aborted = false;
  start = vi.fn(() => {
    this.started = true;
    FakeRecognition.instances.push(this);
  });
  stop = vi.fn();
  abort = vi.fn(() => {
    this.aborted = true;
  });
}

function finalResult(transcript: string) {
  return { resultIndex: 0, results: [Object.assign([{ transcript, confidence: 0.9 }], { isFinal: true, length: 1 })] };
}

function latest(): FakeRecognition {
  const engine = FakeRecognition.instances.at(-1);
  if (!engine) throw new Error("no engine was started");
  return engine;
}

function Probe({ enabled = true, paused = false, onCommand }: { enabled?: boolean; paused?: boolean; onCommand: (command: string) => void }) {
  const wake = useWakeWord({ enabled, paused, lang: "en-US", onCommand });
  return (
    <div>
      <p data-testid="status">{wake.status}</p>
      {wake.message ? <p role="alert">{wake.message}</p> : null}
    </div>
  );
}

beforeEach(() => {
  FakeRecognition.instances = [];
  vi.stubGlobal("SpeechRecognition", FakeRecognition);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useWakeWord", () => {
  it("does not listen while the setting is off", () => {
    render(<Probe enabled={false} onCommand={vi.fn()} />);

    expect(screen.getByTestId("status")).toHaveTextContent("off");
    expect(FakeRecognition.instances).toHaveLength(0);
  });

  it("says so when the browser cannot listen", () => {
    vi.unstubAllGlobals();
    render(<Probe onCommand={vi.fn()} />);

    expect(screen.getByTestId("status")).toHaveTextContent("unsupported");
    expect(screen.getByRole("alert")).toHaveTextContent("Use Chrome or Edge");
  });

  it("listens, and hands over a request spoken after the phrase", async () => {
    const onCommand = vi.fn();
    render(<Probe onCommand={onCommand} />);
    await waitFor(() => expect(FakeRecognition.instances).toHaveLength(1));

    act(() => latest().onresult?.(finalResult("Hey JARVIS, open the calendar")));

    expect(onCommand).toHaveBeenCalledWith("open the calendar");
    expect(screen.getByTestId("status")).toHaveTextContent("listening");
  });

  it("waits for the request when the phrase came alone", async () => {
    const onCommand = vi.fn();
    render(<Probe onCommand={onCommand} />);
    await waitFor(() => expect(FakeRecognition.instances).toHaveLength(1));

    act(() => latest().onresult?.(finalResult("hey jarvis")));
    expect(onCommand).not.toHaveBeenCalled();
    expect(screen.getByTestId("status")).toHaveTextContent("heard");

    act(() => latest().onresult?.(finalResult("open my downloads folder")));
    expect(onCommand).toHaveBeenCalledWith("open my downloads folder");
  });

  it("ignores speech that does not start with the phrase", async () => {
    const onCommand = vi.fn();
    render(<Probe onCommand={onCommand} />);
    await waitFor(() => expect(FakeRecognition.instances).toHaveLength(1));

    act(() => latest().onresult?.(finalResult("what a nice afternoon")));

    expect(onCommand).not.toHaveBeenCalled();
  });

  it("stops and explains when the microphone is refused, without restarting", async () => {
    render(<Probe onCommand={vi.fn()} />);
    await waitFor(() => expect(FakeRecognition.instances).toHaveLength(1));

    act(() => latest().onerror?.({ error: "not-allowed" }));
    act(() => latest().onend?.());

    expect(screen.getByTestId("status")).toHaveTextContent("blocked");
    expect(screen.getByRole("alert")).toHaveTextContent("blocked for this site");
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(FakeRecognition.instances).toHaveLength(1);
  });

  it("explains an unreachable speech service and stops", async () => {
    render(<Probe onCommand={vi.fn()} />);
    await waitFor(() => expect(FakeRecognition.instances).toHaveLength(1));

    act(() => latest().onerror?.({ error: "network" }));

    expect(screen.getByTestId("status")).toHaveTextContent("error");
    expect(screen.getByRole("alert")).toHaveTextContent("could not be reached");
  });

  it("starts a new listening session after each utterance ends", async () => {
    render(<Probe onCommand={vi.fn()} />);
    await waitFor(() => expect(FakeRecognition.instances).toHaveLength(1));

    act(() => latest().onend?.());

    await waitFor(() => expect(FakeRecognition.instances).toHaveLength(2));
  });

  it("gives up after repeated unexplained failures", async () => {
    render(<Probe onCommand={vi.fn()} />);
    await waitFor(() => expect(FakeRecognition.instances).toHaveLength(1));

    for (let attempt = 0; attempt < 3; attempt += 1) {
      act(() => latest().onerror?.({ error: "audio-capture" }));
    }

    expect(screen.getByTestId("status")).toHaveTextContent("error");
    expect(screen.getByRole("alert")).toHaveTextContent("No microphone was found");
  });

  it("stops listening when paused or unmounted", async () => {
    const { rerender, unmount } = render(<Probe onCommand={vi.fn()} />);
    await waitFor(() => expect(FakeRecognition.instances).toHaveLength(1));
    const first = latest();

    rerender(<Probe paused onCommand={vi.fn()} />);
    expect(first.aborted).toBe(true);
    expect(screen.getByTestId("status")).toHaveTextContent("off");

    unmount();
    expect(FakeRecognition.instances).toHaveLength(1);
  });
});
