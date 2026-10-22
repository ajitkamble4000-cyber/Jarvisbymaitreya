import { describe, expect, it } from "vitest";
import { buildCommands, filterCommands } from "../layouts/commandPalette";
import { NAV_ITEMS } from "../layouts/nav";

const commands = buildCommands(NAV_ITEMS);

function idsFor(query: string): string[] {
  return filterCommands(commands, query).map((command) => command.id);
}

describe("settings shortcuts in the command palette", () => {
  it.each([
    ["daily limit", "action.ai-settings", "/settings?tab=ai"],
    ["temperature", "action.ai-settings", "/settings?tab=ai"],
    ["high contrast", "action.appearance", "/settings?tab=appearance"],
    ["theme", "action.appearance", "/settings?tab=appearance"],
    ["clear conversations", "action.privacy", "/settings?tab=privacy"],
    ["download my data", "action.privacy", "/settings?tab=privacy"],
  ])("finds %s and opens the right settings tab", (query, id, path) => {
    const matches = filterCommands(commands, query);
    expect(matches.map((command) => command.id)).toContain(id);
    expect(matches.find((command) => command.id === id)?.path).toBe(path);
  });

  it("keeps the shortcuts under the Actions group", () => {
    for (const id of ["action.ai-settings", "action.appearance", "action.privacy"]) {
      expect(commands.find((command) => command.id === id)?.group).toBe("Actions");
    }
  });

  it("does not add a match for a word that means nothing here", () => {
    expect(idsFor("zebra")).toEqual([]);
  });
});
