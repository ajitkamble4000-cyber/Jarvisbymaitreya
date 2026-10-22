import { useEffect, useId, useMemo, useState, type KeyboardEvent } from "react";
import { Dialog } from "../components/ui/Dialog";
import { Input } from "../components/ui/Input";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { useConversationsQuery, useGlobalSearch } from "../services/queries";
import { buildCommands, filterCommands, recentCommands, searchCommands, stepIndex, type PaletteCommand } from "./commandPalette";
import type { NavItem } from "./nav";

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  navItems: readonly NavItem[];
  /** Called with the chosen command. The shell navigates and closes the palette. */
  onRun: (command: PaletteCommand) => void;
}

/** Search over pages and actions. Arrow keys move, Enter runs, Escape closes (the Dialog handles Escape). */
export function CommandPalette({ open, onClose, navItems, onRun }: CommandPaletteProps) {
  return (
    <Dialog open={open} onClose={onClose} title="Command palette" description="Type to find a page or an action. Arrow keys move, Enter opens it." size="sm">
      {open ? <PaletteBody navItems={navItems} onRun={onRun} /> : null}
    </Dialog>
  );
}

function PaletteBody({ navItems, onRun }: Pick<CommandPaletteProps, "navItems" | "onRun">) {
  const listId = useId();
  const commands = useMemo(() => buildCommands(navItems), [navItems]);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  // Typed text also searches the account. The search waits briefly so that typing does not query on every key.
  const searchText = useDebouncedValue(query, 250);
  const search = useGlobalSearch(searchText);
  // Before anything is typed, the newest conversations come first. The sidebar loads the same list, so this is usually cached.
  const recent = useConversationsQuery({ query: "", archived: false });
  const matches = useMemo(
    () =>
      query.trim()
        ? [...filterCommands(commands, query), ...searchCommands(search.data)]
        : [...recentCommands(recent.data?.items), ...commands],
    [commands, query, search.data, recent.data],
  );

  useEffect(() => {
    setActive(0);
  }, [query]);

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setActive((current) => stepIndex(current, event.key === "ArrowDown" ? 1 : -1, matches.length));
    } else if (event.key === "Enter") {
      const chosen = matches[active];
      if (chosen) {
        event.preventDefault();
        onRun(chosen);
      }
    }
  };

  const activeId = matches[active] ? `${listId}-${matches[active].id}` : undefined;

  return (
    <div className="flex flex-col gap-3">
      <Input
        type="search"
        autoFocus
        aria-label="Search pages and actions"
        aria-controls={listId}
        aria-activedescendant={activeId}
        autoComplete="off"
        placeholder="Go to a page or start an action"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={onKeyDown}
      />
      {matches.length === 0 ? (
        <p role="status" className="text-sm text-muted">
          Nothing matches “{query.trim()}”.
        </p>
      ) : (
        <ul id={listId} role="listbox" aria-label="Results" className="flex max-h-80 flex-col gap-1 overflow-y-auto">
          {matches.map((command, index) => {
            const selected = index === active;
            return (
              <li
                key={command.id}
                id={`${listId}-${command.id}`}
                role="option"
                // Named with a separator, so a screen reader does not run the label into the group ("OverviewGo to").
                aria-label={`${command.label}, ${command.group}`}
                aria-selected={selected}
                onMouseEnter={() => setActive(index)}
                onClick={() => onRun(command)}
                className={
                  "flex cursor-pointer items-center justify-between gap-3 rounded-lg px-3 py-2 text-sm " +
                  (selected ? "bg-accent/15 text-ink" : "text-ink hover:bg-raised")
                }
              >
                <span>{command.label}</span>
                <span className="text-xs text-muted">{command.group}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
