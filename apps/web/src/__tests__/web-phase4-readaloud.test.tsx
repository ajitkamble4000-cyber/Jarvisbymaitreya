import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReadAloudButton } from "../pages/chat/ReadAloudButton";
import { qk } from "../services/queries";
import { createTestQueryClient, renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

afterEach(() => {
  vi.unstubAllGlobals();
});

class FakeUtterance {
  onend: (() => void) | null = null;
  onerror: (() => void) | null = null;
  lang = "";
  rate = 1;
  pitch = 1;
  voice: unknown = null;
  constructor(public text: string) {}
}

describe("Read reply aloud", () => {
  it("is not offered in a browser that cannot speak", () => {
    renderWithProviders(<ReadAloudButton text="Hello" />);
    expect(screen.queryByRole("button", { name: "Read reply aloud" })).not.toBeInTheDocument();
  });

  it("speaks the reply and offers to stop while it plays", async () => {
    const spoken: string[] = [];
    const cancel = vi.fn();
    vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
    vi.stubGlobal("speechSynthesis", {
      speak: (utterance: FakeUtterance) => spoken.push(utterance.text),
      cancel,
      getVoices: () => [],
    });
    routeFetch({ "/settings": () => ({}) });
    renderWithProviders(<ReadAloudButton text="Here is the plan." />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Read reply aloud" }));

    expect(spoken).toEqual(["Here is the plan."]);
    expect(screen.getByRole("button", { name: "Stop reading" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Stop reading" }));
    expect(cancel).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Read reply aloud" })).toBeInTheDocument();
  });

  it("reads in the saved language, voice, speed and pitch", async () => {
    const spoken: FakeUtterance[] = [];
    vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
    vi.stubGlobal("speechSynthesis", {
      speak: (utterance: FakeUtterance) => spoken.push(utterance),
      cancel: vi.fn(),
      getVoices: () => [
        { name: "Daniel", lang: "en-GB" },
        { name: "Amélie", lang: "fr-FR" },
      ],
    });
    routeFetch({ "/settings": () => ({ voice: { language: "fr-FR", voice_name: "Amélie", speaking_rate: 1.5, pitch: 1.2 } }) });
    const queryClient = createTestQueryClient();
    renderWithProviders(<ReadAloudButton text="Bonjour" />, { queryClient });
    await waitFor(() => expect(queryClient.getQueryData(qk.settings)).toBeDefined());
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Read reply aloud" }));

    expect(spoken).toHaveLength(1);
    expect(spoken[0]).toMatchObject({ text: "Bonjour", lang: "fr-FR", rate: 1.5, pitch: 1.2 });
    expect(spoken[0]?.voice).toEqual(expect.objectContaining({ name: "Amélie" }));
  });

  it("keeps speaking when a button that is not reading unmounts", async () => {
    const cancel = vi.fn();
    vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
    vi.stubGlobal("speechSynthesis", { speak: vi.fn(), cancel, getVoices: () => [] });
    routeFetch({ "/settings": () => ({}) });
    const reading = renderWithProviders(<ReadAloudButton text="Long reply" />);
    const filteredOut = renderWithProviders(<ReadAloudButton text="Other reply" />);
    const user = userEvent.setup();

    await user.click(within(reading.container).getByRole("button", { name: "Read reply aloud" }));
    cancel.mockClear();
    filteredOut.unmount();
    expect(cancel).not.toHaveBeenCalled();

    reading.unmount();
    expect(cancel).toHaveBeenCalledTimes(1);
  });
});
