import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ExplorePage from "../pages/explore/ExplorePage";
import { AutomationSection } from "../pages/settings/AutomationSection";
import { api } from "../services/api";
import type { SettingsDoc, ToolOut } from "../types/api";
import { renderWithProviders } from "../test/utils";
import { routeFetch } from "./helpers";

// Plugin tools arrive from the API like any other tool. The web app must show their category as the
// API sends it, so these fixtures carry the categories that the plugins declare.
const pluginTools: ToolOut[] = [
  {
    name: "current_time",
    title: "Current time",
    description: "Read the current time, optionally in a timezone.",
    category: "productivity",
    risk_level: "low",
    target: "server",
    requires_device: false,
    approval_required: false,
    status: "working",
    example_commands: ["What time is it in Tokyo?"],
    notes: "Provided by the clock plugin.",
    parameters: {},
  },
  {
    name: "calculate",
    title: "Calculate",
    description: "Evaluate an arithmetic expression.",
    category: "productivity",
    risk_level: "low",
    target: "server",
    requires_device: false,
    approval_required: false,
    status: "working",
    example_commands: ["What is 17 times 23?"],
    notes: "Provided by the calculator plugin.",
    parameters: {},
  },
];

const exploreItems = pluginTools.map((tool) => ({
  id: tool.name,
  title: tool.title,
  category: tool.category,
  status: "working" as const,
  description: tool.description,
  example_commands: tool.example_commands,
  requirements: [],
  enabled_for_user: true,
}));

describe("Explore: plugin tools render with the category the API sends", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows each plugin tool under its category and offers the categories the API lists", async () => {
    routeFetch({ "/explore": () => ({ categories: ["productivity"], items: exploreItems }) });
    renderWithProviders(<ExplorePage />, { route: "/explore" });

    const clock = await screen.findByRole("heading", { name: "Current time" });
    const card = clock.closest("li");
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByText("Productivity")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Calculate" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Productivity" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Automation" })).toBeNull();
  });

  it("does not rename a category: a tool the API files under automation is shown under automation", async () => {
    const filed = exploreItems.map((item) => ({ ...item, category: "automation" }));
    routeFetch({ "/explore": () => ({ categories: ["automation"], items: filed }) });
    renderWithProviders(<ExplorePage />, { route: "/explore" });

    await screen.findByRole("heading", { name: "Current time" });
    expect(screen.getAllByText("Automation").length).toBeGreaterThan(0);
    expect(screen.queryByRole("tab", { name: "Productivity" })).toBeNull();
  });
});

describe("Settings: plugin tools in the Automation tool list", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("lists each tool with its category and risk, and saves a switched-off tool by name", async () => {
    routeFetch({ "/tools": () => ({ items: pluginTools }) });
    const patch = vi.spyOn(api, "patch").mockResolvedValue({} as SettingsDoc);
    const user = userEvent.setup();
    const automation: SettingsDoc["automation"] = {
      max_concurrent_tasks: 3,
      default_task_timeout_seconds: 120,
      disabled_tools: [],
      emergency_stopped: false,
    };
    renderWithProviders(<AutomationSection automation={automation} timezone={null} />, { route: "/settings?tab=automation" });

    const list = await screen.findByRole("list", { name: "Tools" });
    expect(within(list).getAllByText("Productivity. Risk: low.")).toHaveLength(2);

    await user.click(within(list).getByRole("switch", { name: "Current time" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/settings", { automation: { disabled_tools: ["current_time"] } }));
  });
});
